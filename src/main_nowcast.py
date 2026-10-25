"""Genera public/v1/status_quebradas.json con el contrato de las apps.

Cada fuente falla por separado: si GOES-19, GLM u Open-Meteo no responden, sus campos se publican
como `null` y las quebradas sin lectura de lluvia quedan en UNKNOWN. Solo se aborta (código 1) si el
archivo resultante no cumple el esquema.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import jsonschema
import s3fs

import config
import goes
import openmeteo
import official_warnings
import risk
from areas import Area, load_areas


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def warn(message: str) -> None:
    # Formato de anotación de GitHub Actions; en local se lee igual.
    print(f"::warning::{message}", file=sys.stderr)


def build_feed(
    areas: list[Area],
    now: datetime,
    rain: goes.RainResult,
    lightning: dict[str, int | None],
    accum: dict[str, float | None],
    official_notices: dict | None = None,
) -> dict:
    quebradas = []
    for area in areas:
        stats = rain.per_area.get(area.id)
        rain_max = stats.max_mmh if stats else None
        status = risk.classify(
            rain_max, lightning.get(area.id), accum.get(area.id), config.THRESHOLD_PROFILES[area.threshold_profile]
        )
        quebradas.append(
            {
                "id": area.id,
                "name": area.name,
                "region": area.region,
                "district": area.district,
                "centroid": {"lat": area.centroid_lat, "lon": area.centroid_lon},
                "observation": {
                    # Sin lectura de lluvia para el área, tampoco se publica hora de lectura.
                    "scan_time_utc": iso(rain.scan_time) if stats else None,
                    "source": config.OBSERVATION_SOURCE,
                    "rain_rate_max_mmh": rain_max,
                    "rain_rate_mean_mmh": stats.mean_mmh if stats else None,
                    "lightning_flashes_15m": lightning.get(area.id),
                    "rain_accum_72h_mm": accum.get(area.id),
                },
                "status": status.as_dict(),
                "official_notice": (official_notices or {}).get(area.id),
            }
        )

    return {
        "schema_version": config.SCHEMA_VERSION,
        "min_app_version": config.MIN_APP_VERSION,
        "processed_at_utc": iso(now),
        "stale_after_minutes": config.STALE_AFTER_MINUTES,
        "global_notice": config.GLOBAL_NOTICE,
        "official_links": config.OFFICIAL_LINKS,
        "quebradas": quebradas,
    }


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    os.replace(tmp, path)


def select_areas(areas: list[Area], region: str | None) -> list[Area]:
    selected = areas if region is None else [a for a in areas if a.region.casefold() == region.casefold()]
    if not selected:
        raise ValueError(f"No hay áreas configuradas para la región {region!r}")
    return selected


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=config.OUTPUT_DIR)
    parser.add_argument("--region", help="Limita el procesamiento a una región configurada, por ejemplo Lima")
    args = parser.parse_args()
    started = time.monotonic()

    now = datetime.now(timezone.utc).replace(microsecond=0)
    areas = select_areas(load_areas(config.QUEBRADAS_GEOJSON), args.region)
    fs = s3fs.S3FileSystem(anon=True)

    try:
        rain = goes.read_rrqpe(fs, areas, now)
    except Exception as exc:  # noqa: BLE001 — una fuente caída no debe tumbar el resto
        warn(f"RRQPE no disponible: {exc!r}")
        rain = goes.RainResult(scan_time=None, s3_key=None, per_area={a.id: None for a in areas})

    try:
        lightning = goes.read_glm(fs, areas, now)
    except Exception as exc:  # noqa: BLE001
        warn(f"GLM no disponible: {exc!r}")
        lightning = {a.id: None for a in areas}

    accum = openmeteo.fetch_rain_72h(areas, now)

    try:
        notices, notice_report = official_warnings.fetch_notices(config.ROOT / "data_static" / "monitored_channels.geojson", now)
    except Exception as exc:
        warn(f"Consulta directa SENAMHI no disponible: {exc!r}")
        try:
            notices, notice_report = official_warnings.read_verified_snapshot(
                config.ROOT / "data_static" / "senamhi_verified_snapshot.json.gz",
                config.ROOT / "data_static" / "monitored_channels.geojson", now)
            warn("Se utiliza respaldo oficial con fecha de consulta original y vencimiento, no una consulta en directo.")
        except Exception as backup_exc:
            warn(f"Sin respaldo vigente: {backup_exc!r}")
            notices, notice_report = {}, {"state":"unavailable", "assigned_zones":[]}
    feed = build_feed(areas, now, rain, lightning, accum, notices)
    if args.region:
        for q in feed["quebradas"]:
            q["status"]["headline"] = q["status"]["headline"].replace("en la parte alta", "en el área observada")
            q["status"]["advice"] = q["status"]["advice"].replace("en la parte alta", "en el área observada")
        feed["global_notice"] = {
            "severity": "INFO", "title": f"Piloto {args.region} · experimental",
            "body": "Datos reales sobre áreas provisionales; umbrales sin calibrar. No es un aviso oficial ni un pronóstico de desborde. El aviso SENAMHI, cuando está verificado, aparece separado de la lluvia. Sigue las indicaciones de las autoridades.",
            "url": None,
        }
    schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    try:
        jsonschema.Draft202012Validator(schema).validate(feed)
    except jsonschema.ValidationError as exc:
        print(f"::error::El JSON no cumple el contrato: {exc.message}", file=sys.stderr)
        return 1

    write_atomic(args.out / "status_quebradas.json", feed)
    write_atomic(args.out / "run_report.json", {
        "processed_at_utc": iso(now), "finished_at_utc": iso(datetime.now(timezone.utc)),
        "processing_seconds": round(time.monotonic() - started, 2),
        "scope": args.region or "all configured regions", "area_ids": [a.id for a in areas],
        "geometry_status": "PROVISIONAL", "thresholds_status": "UNCALIBRATED",
        "rrqpe_s3_key": rain.s3_key, "rain_scan_time_utc": iso(rain.scan_time),
        "rain_areas_with_data": sum(rain.per_area.get(a.id) is not None for a in areas),
        "lightning_areas_with_data": sum(lightning.get(a.id) is not None for a in areas),
        "model_accumulation_areas_with_data": sum(accum.get(a.id) is not None for a in areas),
        "official_notices": notice_report,
        "status_json_bytes": (args.out / "status_quebradas.json").stat().st_size,
    })

    age = f"{(now - rain.scan_time).total_seconds() / 60:.0f} min" if rain.scan_time else "sin lectura"
    print(f"RRQPE: {rain.s3_key or 'ninguno'} ({age})")
    for q in feed["quebradas"]:
        obs = q["observation"]
        print(
            f"  {q['id']:<22} {q['status']['level']:<7} lluvia={obs['rain_rate_max_mmh']} mm/h "
            f"rayos={obs['lightning_flashes_15m']} 72h={obs['rain_accum_72h_mm']} mm"
        )
    if all(q["status"]["level"] == "UNKNOWN" for q in feed["quebradas"]):
        warn("Ninguna quebrada tiene lectura satelital: se publican todas en UNKNOWN.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
