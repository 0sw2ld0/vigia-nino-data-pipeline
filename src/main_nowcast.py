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
import lima
import north
import regional_accumulation
import watersheds
import exposure
import calibration
import historical_events
import fire_hotspots
import air_temperature
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
    geometry_started = time.monotonic()
    regional_areas = lima.observation_areas(config.ROOT)
    observation_areas = areas + regional_areas
    geometry_seconds = time.monotonic()-geometry_started
    fs = s3fs.S3FileSystem(anon=True)

    rain_started = time.monotonic()
    try:
        rain = goes.read_rrqpe(fs, observation_areas, now)
    except Exception as exc:  # noqa: BLE001 — una fuente caída no debe tumbar el resto
        warn(f"RRQPE no disponible: {exc!r}")
        rain = goes.RainResult(scan_time=None, s3_key=None, per_area={a.id: None for a in observation_areas})
    rain_seconds = time.monotonic()-rain_started

    fire_started = time.monotonic()
    try:
        fire_feed = fire_hotspots.fetch(fs, config.ROOT, now)
    except Exception as exc:
        warn(f'Focos de calor no disponibles: {exc!r}')
        fire_feed = fire_hotspots.empty(now)
    fire_seconds = time.monotonic() - fire_started
    write_atomic(args.out/'fire_hotspots.json', fire_feed)

    try:
        temperature_feed = air_temperature.fetch(config.ROOT, now, config.ROOT / '.cache' / 'air-temperature.json')
    except Exception as exc:
        warn(f'Temperatura del aire no disponible: {exc!r}')
        temperature_feed = air_temperature.empty(now)
    write_atomic(args.out/'air_temperature.json', temperature_feed)

    lightning_started = time.monotonic()
    try:
        lightning = goes.read_glm(fs, observation_areas, now)
    except Exception as exc:  # noqa: BLE001
        warn(f"GLM no disponible: {exc!r}")
        lightning = {a.id: None for a in observation_areas}
    lightning_seconds = time.monotonic()-lightning_started

    accum = openmeteo.fetch_rain_72h(areas, now)
    regional_accum_started = time.monotonic()
    regional_accum = regional_accumulation.fetch_regional_accumulation(
        regional_areas, now, config.ROOT / '.cache' / 'regional-accumulation.json')
    regional_accum_seconds = time.monotonic() - regional_accum_started
    context_started = time.monotonic()
    try:
        units, watershed_assignments, watershed_report = watersheds.load_catalog(config.ROOT)
        basin_layer = {'type':'FeatureCollection', 'features':watersheds.map_features(units, watershed_report['retrieved_at_utc'])}
    except Exception as exc:
        warn(f"Catálogo ANA no disponible: {exc!r}")
        watershed_assignments, watershed_report = {}, {'state':'unavailable'}
        basin_layer = {'type':'FeatureCollection', 'features':[]}
    try:
        exposure_layer = exposure.get_export(config.ROOT)
    except Exception as exc:
        warn(f"Huellas documentadas no disponibles: {exc!r}")
        exposure_layer = {'type':'FeatureCollection', 'features':[], 'coverage':{'state':'unavailable'}}
    context_seconds = time.monotonic() - context_started

    notice_started = time.monotonic()
    channels_path = config.ROOT / "data_static" / "monitored_channels.geojson"
    collector_url = os.environ.get("SENAMHI_COLLECTOR_URL")
    try:
        if collector_url:
            try:
                notices, notice_report = official_warnings.fetch_collector_snapshot(collector_url, channels_path, now)
            except Exception as collector_exc:
                warn(f"Colector no disponible: {collector_exc!r}; se intenta consulta directa.")
                notices, notice_report = official_warnings.fetch_notices(channels_path, now)
        else:
            notices, notice_report = official_warnings.fetch_notices(channels_path, now)
    except Exception as exc:
        warn(f"Consulta SENAMHI no disponible: {exc!r}")
        try:
            notices, notice_report = official_warnings.read_verified_snapshot(
                config.ROOT / "data_static" / "senamhi_verified_snapshot.json.gz", channels_path, now)
            warn("Se utiliza copia oficial con fecha de consulta original y vencimiento.")
        except Exception as backup_exc:
            warn(f"Sin respaldo vigente: {backup_exc!r}")
            notices, notice_report = {}, {"state":"unavailable", "assigned_zones":[]}
    feed = build_feed(areas, now, rain, lightning, accum, notices)
    notice_seconds = time.monotonic() - notice_started
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
    regional_started = time.monotonic()
    regional_feed, regional_report = lima.build_feed(
        config.ROOT, feed, notices, notice_report, rain, lightning,
        accumulation=regional_accum.values,
        accumulation_context=regional_accum.report['context'],
        accumulation_retrieved_at=regional_accum.report['retrieved_at_utc'],
        watershed_context=watershed_assignments)
    jsonschema.Draft202012Validator(schema).validate(regional_feed)
    write_atomic(args.out / "status_lima.json", regional_feed)
    northern_feed, northern_report = north.build_feed(config.ROOT, feed, notices, notice_report)
    jsonschema.Draft202012Validator(schema).validate(northern_feed)
    write_atomic(args.out / 'status_norte.json', northern_feed)
    write_atomic(args.out / 'watersheds_lima.geojson', basin_layer)
    write_atomic(args.out / 'exposure_lima.geojson', exposure_layer)
    if rain.overlay:
        overlay_manifest, overlay_png = rain.overlay
        (args.out / overlay_manifest['image']).write_bytes(overlay_png)
        write_atomic(args.out / 'rain_overlay.json', overlay_manifest)
    else:
        write_atomic(args.out / 'rain_overlay.json', {'version':1,'state':'UNAVAILABLE','scan_time_utc':None,'context':'No current satellite raster available; missing data is not zero rainfall.'})
    calibration_report = calibration.evaluate_windows([])
    calibration_report['scope'] = 'Lima: documented positives exist; no reviewed paired rainfall evaluation windows or negatives available'
    try:
        calibration_report['historical_inventory'] = historical_events.summarize(historical_events.load_inventory(config.ROOT))
    except Exception as exc:
        warn(f'Inventario histórico no disponible: {exc!r}')
        calibration_report['historical_inventory'] = {'state':'unavailable'}
    write_atomic(args.out / 'calibration_report.json', calibration_report)
    regional_report['serialization_validation_seconds'] = round(time.monotonic()-regional_started,3)
    regional_report['json_bytes'] = (args.out/'status_lima.json').stat().st_size
    write_atomic(args.out / "run_report.json", {
        "processed_at_utc": iso(now), "finished_at_utc": iso(datetime.now(timezone.utc)),
        "processing_seconds": round(time.monotonic() - started, 2),
        "fire_hotspots": {"state":fire_feed["state"], "scan_time_utc":fire_feed["scan_time_utc"], "detections":len(fire_feed["detections"]), "regions":fire_feed["regions"], "processing_seconds":round(fire_seconds,3)},
        "scope": args.region or "all configured regions", "area_ids": [a.id for a in areas],
        "geometry_status": "PROVISIONAL", "thresholds_status": "UNCALIBRATED",
        "rrqpe_s3_key": rain.s3_key, "rain_scan_time_utc": iso(rain.scan_time),
        "rain_areas_with_data": sum(rain.per_area.get(a.id) is not None for a in areas),
        "lightning_areas_with_data": sum(lightning.get(a.id) is not None for a in areas),
        "model_accumulation_areas_with_data": sum(accum.get(a.id) is not None for a in areas),
        "regional_accumulation": {**regional_accum.report, 'processing_seconds':round(regional_accum_seconds,3)},
        "watersheds":watershed_report, "exposure":exposure_layer.get('coverage'),
        "calibration":calibration_report, "context_layers_seconds":round(context_seconds,3),
        "official_notices": notice_report,
        "lima_coverage":regional_report, "north_coverage":northern_report, "official_download_and_matching_seconds":round(notice_seconds,3),
        "observation_timings_seconds":{'geometry':round(geometry_seconds,3),'rrqpe_download_and_sampling':round(rain_seconds,3),'glm_download_and_counting':round(lightning_seconds,3)},
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
