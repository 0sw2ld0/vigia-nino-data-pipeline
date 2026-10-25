"""Tests de la evaluación del backtest con series sintéticas (sin red)."""

import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import config
from areas import load_areas

spec = importlib.util.spec_from_file_location("backtest_yaku", Path(__file__).resolve().parent.parent / "backtest" / "backtest_yaku.py")
bt = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bt)

T0 = datetime(2023, 3, 16, 12, 0, tzinfo=timezone.utc)


def series_with_peak(peak_hours_before_event: float, rain: float, event: datetime):
    rows = []
    for i in range(48):
        t = event - timedelta(hours=24) + timedelta(minutes=30 * i)
        value = rain if abs((event - t).total_seconds() / 3600 - peak_hours_before_event) < 0.25 else 0.0
        rows.append({"t": t, "rain": value, "lightning": None, "accum": None, "level": "RED" if value >= 20 else "GREEN"})
    return rows


def test_events_csv_parses_peru_time_and_skips_comments(tmp_path):
    path = tmp_path / "events.csv"
    path.write_text(
        "# comentario\nquebrada_id,event_time,uncertainty_hours,source_url,note\n"
        "LIM-CHO-QUIRIO,2023-03-16T18:00-05:00,6,https://example.org,x\n# LIM-CHO-QUIRIO,2023-03-17T18:00-05:00,0,u,y\n",
        encoding="utf-8",
    )
    events = bt.load_events(path)
    assert len(events) == 1
    assert events[0]["t"] == datetime(2023, 3, 16, 23, 0, tzinfo=timezone.utc)
    assert events[0]["uncertainty"] == timedelta(hours=6)


def test_lead_time_and_window():
    event = T0
    rows = series_with_peak(3, 25.0, event)
    found = bt.first_alert(rows, event - bt.LEAD_WINDOW, event, lambda r: r["level"] == "RED")
    assert found == event - timedelta(hours=3)
    # Un pico 10 h antes queda fuera de la ventana de 6 h.
    rows = series_with_peak(10, 25.0, event)
    assert bt.first_alert(rows, event - bt.LEAD_WINDOW, event, lambda r: r["level"] == "RED") is None


def test_report_counts_detection_and_false_alarms():
    areas = load_areas(config.QUEBRADAS_GEOJSON)
    qid = areas[0].id
    event = {"id": qid, "t": T0, "uncertainty": timedelta(0), "source": "https://example.org", "note": ""}
    rows = series_with_peak(2, 25.0, T0)
    rows.append({"t": T0 + timedelta(hours=12), "rain": 30.0, "lightning": None, "accum": None, "level": "RED"})
    report = bt.evaluate({qid: rows}, [event], areas, 30)
    assert "2.0 h antes" in report
    assert "| 20 | 1 de 1 | 2.0 h | 0.5 |" in report
