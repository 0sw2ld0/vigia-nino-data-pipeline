"""Corre el semáforo sobre datos históricos de GOES-16 (ciclón Yaku, marzo 2023) para calibrar umbrales.

Pasos:
1. Descarga cada barrido RRQPE del periodo (cada `--step-minutes`) y guarda la lluvia por quebrada en
   `out/rain_timeseries.csv`. Es reanudable: si se corta, vuelve a correrlo y sigue donde quedó.
2. Añade el acumulado de 72 h (Open-Meteo histórico) y, con `--lightning`, los rayos GLM.
3. Compara con los eventos de `events.csv` (huaicos reales con fuente) y prueba varios umbrales.
4. Escribe `out/report.md`.

Ejemplo (prueba corta):
    .venv/bin/python backtest/backtest_yaku.py --start 2023-03-15 --end 2023-03-16 --step-minutes 60
"""

import argparse
import csv
import io
import sys
from bisect import bisect_right
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

import requests
import s3fs

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import config  # noqa: E402
import goes  # noqa: E402
import openmeteo  # noqa: E402
import risk  # noqa: E402
from areas import Area, load_areas  # noqa: E402

HERE = Path(__file__).resolve().parent
BUCKET = "noaa-goes16"  # GOES-16 era GOES-Este en 2023
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
CSV_FIELDS = ["time_utc", "quebrada_id", "rain_max_mmh", "rain_mean_mmh", "lightning_15m", "s3_key"]
LEVEL_RANK = {"UNKNOWN": -1, "GREEN": 0, "YELLOW": 1, "RED": 2}

# Ventanas para comparar con eventos reales.
LEAD_WINDOW = timedelta(hours=6)  # una alerta cuenta si llega hasta 6 h antes del huaico
AFTER_WINDOW = timedelta(hours=3)  # y no es falsa alarma si ocurre hasta 3 h después
SWEEP_THRESHOLDS = [2, 5, 10, 15, 20, 30, 40]


def utc(text: str) -> datetime:
    """Fecha ISO a UTC. Sin zona horaria se asume UTC; los eventos usan la hora de Perú (`-05:00`)."""
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def opt_float(value: str) -> float | None:
    return float(value) if value not in ("", None) else None


def opt_int(value: str) -> int | None:
    return int(value) if value not in ("", None) else None


# ---------------------------------------------------------------------------
# 1. Barridos RRQPE (+ GLM opcional)
# ---------------------------------------------------------------------------


def scan_keys(fs, start: datetime, end: datetime, step_minutes: int) -> list[str]:
    """Un archivo RRQPE por cada `step_minutes`, el primero que empieza en cada intervalo."""
    keys, seen = [], set()
    hour = start.replace(minute=0, second=0)
    while hour < end:
        prefix = f"{BUCKET}/{config.RRQPE_PRODUCT}/{hour:%Y/%j/%H}/"
        try:
            listing = sorted(fs.ls(prefix), key=goes.start_time_from_key)
        except FileNotFoundError:
            listing = []
        for key in listing:
            t = goes.start_time_from_key(key)
            if not start <= t < end:
                continue
            slot = int((t - start).total_seconds() // (step_minutes * 60))
            if slot not in seen:
                seen.add(slot)
                keys.append(key)
        hour += timedelta(hours=1)
    return keys


def load_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open(newline="") as f:
        return {row["s3_key"] for row in csv.DictReader(f)}


def process_scan(fs, key: str, areas: list[Area], lightning: bool) -> list[dict]:
    rain = goes.rain_from_key(fs, key, areas)
    scan_time = rain.scan_time or goes.start_time_from_key(key)
    flashes = goes.read_glm(fs, areas, scan_time, bucket=BUCKET) if lightning else {}
    rows = []
    for area in areas:
        stats = rain.per_area[area.id]
        flash = flashes.get(area.id)
        rows.append(
            {
                "time_utc": iso(scan_time),
                "quebrada_id": area.id,
                "rain_max_mmh": "" if stats is None else stats.max_mmh,
                "rain_mean_mmh": "" if stats is None else stats.mean_mmh,
                "lightning_15m": "" if flash is None else flash,
                "s3_key": key,
            }
        )
    return rows


def collect_scans(fs, areas, start, end, step, lightning, workers, out_csv: Path) -> None:
    keys = scan_keys(fs, start, end, step)
    done = load_done(out_csv)
    pending = [k for k in keys if k not in done]
    print(f"Barridos RRQPE: {len(keys)} en el periodo, {len(pending)} por descargar (~{len(pending) * 1.8:.0f} MB).")
    if not pending:
        return

    new_file = not out_csv.exists()
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    failures = 0
    with out_csv.open("a", newline="") as f, ThreadPoolExecutor(max_workers=workers) as pool:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if new_file:
            writer.writeheader()
        futures = {pool.submit(process_scan, fs, k, areas, lightning): k for k in pending}
        for i, future in enumerate(as_completed(futures), 1):
            try:
                writer.writerows(future.result())
                f.flush()
            except Exception as exc:  # noqa: BLE001 — un archivo dañado no detiene el backtest
                failures += 1
                print(f"  ! {futures[future].split('/')[-1]}: {exc!r}", file=sys.stderr)
            if i % 50 == 0 or i == len(pending):
                print(f"  {i}/{len(pending)}")
    if failures:
        print(f"  {failures} barridos fallaron; vuelve a correr el script para reintentarlos.")


# ---------------------------------------------------------------------------
# 2. Acumulado 72 h (Open-Meteo histórico, ERA5)
# ---------------------------------------------------------------------------


def fetch_archive(areas: list[Area], start: datetime, end: datetime) -> dict[str, tuple[list[str], list]]:
    params = {
        "latitude": ",".join(str(a.centroid_lat) for a in areas),
        "longitude": ",".join(str(a.centroid_lon) for a in areas),
        "start_date": (start - timedelta(hours=config.RAIN_ACCUM_HOURS + 1)).date().isoformat(),
        "end_date": end.date().isoformat(),
        "hourly": "precipitation",
        "timezone": "GMT",
    }
    try:
        response = requests.get(ARCHIVE_URL, params=params, timeout=60)
        response.raise_for_status()
        items = response.json()
    except (requests.RequestException, ValueError) as exc:
        print(f"Open-Meteo histórico no disponible ({exc!r}); se omite el acumulado.", file=sys.stderr)
        return {}
    items = items if isinstance(items, list) else [items]
    return {a.id: (it["hourly"]["time"], it["hourly"]["precipitation"]) for a, it in zip(areas, items)}


# ---------------------------------------------------------------------------
# 3. Evaluación
# ---------------------------------------------------------------------------


def load_series(path: Path, archive, areas: list[Area]) -> dict[str, list[dict]]:
    """Serie por quebrada con el nivel calculado con el perfil de umbrales de esa quebrada."""
    profiles = {a.id: config.THRESHOLD_PROFILES[a.threshold_profile] for a in areas}
    series: dict[str, list[dict]] = defaultdict(list)
    with path.open(newline="") as f:
        for row in csv.DictReader(f):
            t = utc(row["time_utc"])
            rain_max = opt_float(row["rain_max_mmh"])
            lightning = opt_int(row["lightning_15m"])
            accum = None
            if row["quebrada_id"] in archive:
                times, values = archive[row["quebrada_id"]]
                accum = openmeteo.accumulate_hourly(times, values, t)
            if row["quebrada_id"] not in profiles:
                continue  # quebrada que ya no está en el GeoJSON
            level = risk.classify(rain_max, lightning, accum, profiles[row["quebrada_id"]]).level
            series[row["quebrada_id"]].append(
                {"t": t, "rain": rain_max, "lightning": lightning, "accum": accum, "level": level}
            )
    for rows in series.values():
        rows.sort(key=lambda r: r["t"])
    return series


def load_events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip() and not ln.startswith("#")]
    events = []
    for row in csv.DictReader(io.StringIO("\n".join(lines))):
        events.append(
            {
                "id": row["quebrada_id"],
                "t": utc(row["event_time"]),
                # Las noticias rara vez dan la hora: el evento ocurrió entre t - incertidumbre y t.
                "uncertainty": timedelta(hours=float(row.get("uncertainty_hours") or 0)),
                "source": row["source_url"],
                "note": row.get("note", ""),
            }
        )
    return events


def scan_hours(rows: list[dict], step_minutes: int) -> float:
    return len(rows) * step_minutes / 60


def first_alert(rows: list[dict], start: datetime, end: datetime, test) -> datetime | None:
    times = [r["t"] for r in rows]
    i = bisect_right(times, start - timedelta(seconds=1))
    for r in rows[i:]:
        if r["t"] > end:
            break
        if test(r):
            return r["t"]
    return None


def event_window_start(e: dict) -> datetime:
    return e["t"] - e["uncertainty"] - LEAD_WINDOW


def near_event(t: datetime, events: list[dict]) -> bool:
    return any(event_window_start(e) <= t <= e["t"] + AFTER_WINDOW for e in events)


def evaluate(series, events, areas, step_minutes) -> str:
    names = {a.id: a.name for a in areas}
    profile_of = {a.id: a.threshold_profile for a in areas}
    out = ["# Backtest del semáforo — GOES-16, ciclón Yaku (marzo 2023)", ""]
    out.append(
        "> Cada quebrada se evalúa con su perfil de `config.THRESHOLD_PROFILES`. Sin `--lightning`, los rayos cuentan "
        "como \"sin dato\" y solo puede subir el nivel la lluvia. El acumulado de 72 h viene de ERA5 (≈ 25 km), más "
        "grueso que el satélite."
    )
    out.append("")

    out.append("## Perfiles de umbral usados")
    out.append("")
    out.append("| Perfil | Quebradas | Amarillo desde (mm/h) | Rojo desde (mm/h) |")
    out.append("| :--- | :--- | ---: | ---: |")
    for profile, t in config.THRESHOLD_PROFILES.items():
        members = ", ".join(names[a.id] for a in areas if a.threshold_profile == profile) or "—"
        out.append(f"| {profile} | {members} | {t['yellow_rain_mmh']} | {t['red_rain_mmh']} |")
    out.append("")

    out.append("## Tiempo en cada nivel")
    out.append("")
    out.append("| Quebrada | Perfil | Horas | Verde | Amarillo | Rojo | Sin dato | Lluvia máx. (mm/h) |")
    out.append("| :--- | :--- | ---: | ---: | ---: | ---: | ---: | ---: |")
    for qid, rows in series.items():
        counts = defaultdict(int)
        for r in rows:
            counts[r["level"]] += 1
        rains = [r["rain"] for r in rows if r["rain"] is not None]
        pct = {k: f"{100 * counts[k] / len(rows):.1f}%" for k in LEVEL_RANK}
        out.append(
            f"| {names.get(qid, qid)} | {profile_of.get(qid, '—')} | {scan_hours(rows, step_minutes):.0f} | "
            f"{pct['GREEN']} | {pct['YELLOW']} | "
            f"{pct['RED']} | {pct['UNKNOWN']} | {max(rains) if rains else '—'} |"
        )
    out.append("")

    out.append("## Barridos con más lluvia")
    out.append("")
    for qid, rows in series.items():
        top = sorted((r for r in rows if r["rain"]), key=lambda r: r["rain"], reverse=True)[:5]
        if not top:
            continue
        hits = ", ".join(f"{r['rain']} mm/h ({(r['t'] - timedelta(hours=5)):%d/%m %H:%M} h Perú)" for r in top)
        out.append(f"- **{names.get(qid, qid)}:** {hits}")
    out.append("")

    out.append("## Comparación con huaicos reales (`events.csv`)")
    out.append("")
    if not events:
        out.append("No hay eventos cargados. Agrega huaicos con fuente en `backtest/events.csv` y vuelve a correr el script.")
        return "\n".join(out) + "\n"

    by_area = defaultdict(list)
    for e in events:
        by_area[e["id"]].append(e)

    out.append(
        "Anticipación medida contra la hora **más tardía** posible del evento. Con incertidumbre, la ventana "
        "de búsqueda empieza 6 h antes de la hora más temprana posible."
    )
    out.append("")
    out.append("| Quebrada | Evento (h Perú) | Incertidumbre | Primer amarillo | Primer rojo | Lluvia máx. en la ventana | Fuente |")
    out.append("| :--- | :--- | ---: | :--- | :--- | ---: | :--- |")
    for e in events:
        rows = series.get(e["id"], [])
        start = event_window_start(e)
        yellow = first_alert(rows, start, e["t"], lambda r: LEVEL_RANK[r["level"]] >= 1)
        red = first_alert(rows, start, e["t"], lambda r: r["level"] == "RED")
        in_window = [r["rain"] for r in rows if start <= r["t"] <= e["t"] and r["rain"] is not None]
        fmt = lambda t: f"{(e['t'] - t).total_seconds() / 3600:.1f} h antes" if t else "no"  # noqa: E731
        out.append(
            f"| {names.get(e['id'], e['id'])} | {(e['t'] - timedelta(hours=5)):%d/%m %H:%M} | "
            f"{e['uncertainty'].total_seconds() / 3600:.0f} h | {fmt(yellow)} | {fmt(red)} | "
            f"{max(in_window) if in_window else '—'} | [enlace]({e['source']}) |"
        )
    out.append("")

    out.append("## Barrido de umbrales de lluvia, por perfil")
    out.append("")
    out.append(
        "Para cada umbral: cuántos eventos tuvieron lluvia ≥ umbral en su ventana, la anticipación mediana, "
        "y cuántas horas sonaría fuera de cualquier evento (\"falsa alarma\"; con pocos eventos cargados, "
        "esta columna exagera). Cada perfil se calibra por separado."
    )
    for profile in config.THRESHOLD_PROFILES:
        profile_series = {qid: rows for qid, rows in series.items() if profile_of.get(qid) == profile}
        if not profile_series:
            continue
        profile_events = [e for e in events if profile_of.get(e["id"]) == profile]
        out.append("")
        out.append(f"### {profile}")
        out.append("")
        out.append("| Umbral (mm/h) | Eventos detectados | Anticipación mediana | Horas de falsa alarma |")
        out.append("| ---: | :--- | :--- | ---: |")
        for threshold in SWEEP_THRESHOLDS:
            test = lambda r, th=threshold: r["rain"] is not None and r["rain"] >= th  # noqa: E731
            leads = []
            for e in profile_events:
                hit = first_alert(profile_series.get(e["id"], []), event_window_start(e), e["t"], test)
                if hit:
                    leads.append((e["t"] - hit).total_seconds() / 3600)
            false_scans = sum(
                1
                for qid, rows in profile_series.items()
                for r in rows
                if test(r) and not near_event(r["t"], by_area.get(qid, []))
            )
            lead = f"{median(leads):.1f} h" if leads else "—"
            detected = f"{len(leads)} de {len(profile_events)}" if profile_events else "sin eventos"
            out.append(f"| {threshold} | {detected} | {lead} | {false_scans * step_minutes / 60:.1f} |")
    out.append("")
    return "\n".join(out) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--start", default="2023-03-01", help="Fecha UTC de inicio (incluida)")
    parser.add_argument("--end", default="2023-04-01", help="Fecha UTC de fin (excluida)")
    parser.add_argument("--step-minutes", type=int, default=30, choices=[10, 20, 30, 60])
    parser.add_argument("--lightning", action="store_true", help="Cuenta rayos GLM (≈ 45 archivos extra por barrido)")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--events", type=Path, default=HERE / "events.csv")
    parser.add_argument("--out", type=Path, default=HERE / "out")
    args = parser.parse_args()

    start, end = utc(args.start), utc(args.end)
    areas = load_areas(config.QUEBRADAS_GEOJSON)
    fs = s3fs.S3FileSystem(anon=True)
    series_csv = args.out / "rain_timeseries.csv"

    collect_scans(fs, areas, start, end, args.step_minutes, args.lightning, args.workers, series_csv)
    if not series_csv.exists():
        print("No se obtuvo ningún barrido.", file=sys.stderr)
        return 1

    archive = fetch_archive(areas, start, end)
    series = load_series(series_csv, archive, areas)
    series = {qid: [r for r in rows if start <= r["t"] < end] for qid, rows in series.items()}
    events = [e for e in load_events(args.events) if start <= event_window_start(e) and e["t"] < end]
    report = evaluate(series, events, areas, args.step_minutes)

    report_path = args.out / "report.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\nReporte: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
