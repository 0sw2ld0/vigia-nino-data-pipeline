"""Tests offline: no tocan S3 ni Open-Meteo."""

import json
from datetime import datetime, timedelta, timezone

import jsonschema
import numpy as np
import pytest
from shapely.geometry import Polygon, box

import config
import goes
import openmeteo
import risk
from areas import Area, load_areas
from main_nowcast import build_feed, select_areas

NOW = datetime(2026, 10, 23, 20, 52, tzinfo=timezone.utc)


def make_area(geometry, area_id="TST-TST-AREA", lat=-10.0, lon=-77.0) -> Area:
    return Area(area_id, "Prueba", "Lima", "Distrito", "SIERRA_LIMA", lat, lon, geometry)


def grid(n=5):
    """Rejilla de n x n píxeles entre lon -77..-76.6 y lat -10..-9.6 (centros cada 0.1°)."""
    lons, lats = np.meshgrid(np.linspace(-77.0, -76.6, n), np.linspace(-9.6, -10.0, n))
    return lons, lats


# --- GOES -------------------------------------------------------------------


def test_start_time_from_key():
    key = "noaa-goes19/GLM-L2-LCFA/2026/296/20/OR_GLM-L2-LCFA_G19_s20262962048200_e20262962048400_c20262962048418.nc"
    assert goes.start_time_from_key(key) == datetime(2026, 10, 23, 20, 48, 20, tzinfo=timezone.utc)


def test_dqf_bad_pixels_are_ignored():
    lons, lats = grid()
    rain = np.full(lons.shape, 1.0)
    dqf = np.zeros(lons.shape, dtype=np.uint8)
    rain[2, 2], dqf[2, 2] = 250.0, 1  # píxel malo con un valor absurdo
    stats = goes.area_rain_stats(rain, dqf, lons, lats, make_area(box(-77.05, -10.05, -76.55, -9.55)))
    assert stats.max_mmh == 1.0


def test_uses_real_polygon_not_bounding_box():
    lons, lats = grid()
    rain = np.zeros(lons.shape)
    rain[0, 4] = 40.0  # esquina noreste: dentro del rectángulo envolvente, fuera del triángulo
    triangle = Polygon([(-77.05, -10.05), (-76.55, -10.05), (-77.05, -9.55)])
    stats = goes.area_rain_stats(rain, np.zeros(lons.shape, dtype=np.uint8), lons, lats, make_area(triangle))
    assert stats.max_mmh == 0.0


def test_mostly_invalid_pixels_give_none_not_zero():
    lons, lats = grid()
    rain = np.zeros(lons.shape)
    dqf = np.ones(lons.shape, dtype=np.uint8)
    dqf[0, 0] = 0
    assert goes.area_rain_stats(rain, dqf, lons, lats, make_area(box(-77.05, -10.05, -76.55, -9.55))) is None


def test_nan_pixels_give_none():
    lons, lats = grid()
    rain = np.full(lons.shape, np.nan)
    dqf = np.zeros(lons.shape, dtype=np.uint8)
    assert goes.area_rain_stats(rain, dqf, lons, lats, make_area(box(-77.05, -10.05, -76.55, -9.55))) is None


def test_polygon_smaller_than_pixel_uses_nearest_pixel():
    lons, lats = grid()
    rain = np.zeros(lons.shape)
    rain[1, 1] = 7.5  # centro en (-76.9, -9.7)
    tiny = make_area(box(-76.91, -9.71, -76.905, -9.705), lat=-9.708, lon=-76.908)
    stats = goes.area_rain_stats(rain, np.zeros(lons.shape, dtype=np.uint8), lons, lats, tiny)
    assert stats.max_mmh == 7.5


def test_count_flashes_per_polygon():
    a = make_area(box(-77, -10, -76, -9), area_id="AAA-AAA-A")
    b = make_area(box(-80, -6, -79, -5), area_id="BBB-BBB-B")
    lats = np.array([-9.5, -9.4, -5.5, -12.0])
    lons = np.array([-76.5, -76.4, -79.5, -75.0])
    assert goes.count_flashes(lats, lons, [a, b]) == {"AAA-AAA-A": 2, "BBB-BBB-B": 1}


# --- Open-Meteo -------------------------------------------------------------


def hourly_series(hours: int, value: float | None = 1.0):
    times = [(NOW - timedelta(hours=h)).strftime("%Y-%m-%dT%H:00") for h in range(hours, -1, -1)]
    return times, [value] * len(times)


def test_accumulates_only_last_72_hours():
    times, values = hourly_series(80, 1.0)
    assert openmeteo.accumulate_hourly(times, values, NOW) == 72.0


def test_missing_hours_give_none():
    times, values = hourly_series(72, None)
    values[:10] = [2.0] * 10
    assert openmeteo.accumulate_hourly(times, values, NOW) is None


# --- Semáforo ---------------------------------------------------------------

T = config.THRESHOLD_PROFILES["SIERRA_LIMA"]


def test_no_rain_reading_is_unknown_even_with_other_data():
    assert risk.classify(None, 30, 80.0, T).level == "UNKNOWN"


def test_levels():
    assert risk.classify(0.0, 0, 0.0, T).level == "GREEN"
    assert risk.classify(6.0, 0, 0.0, T).level == "YELLOW"
    assert risk.classify(25.0, 0, 0.0, T).level == "RED"
    assert risk.classify(12.0, 6, 0.0, T).level == "RED"
    assert risk.classify(0.5, 1, 0.0, T).level == "YELLOW"
    assert risk.classify(3.0, 0, 35.0, T).level == "YELLOW"


def test_missing_secondary_data_never_lowers_level():
    assert risk.classify(25.0, None, None, T).level == "RED"
    assert risk.classify(6.0, None, None, T).level == "YELLOW"


# --- Contrato ---------------------------------------------------------------


def test_feed_matches_contract_and_marks_missing_data():
    areas = load_areas(config.QUEBRADAS_GEOJSON)
    first, *others = areas
    rain = goes.RainResult(
        scan_time=NOW - timedelta(minutes=3),
        s3_key="x",
        per_area={first.id: None, **{a.id: goes.RainStats(6.4, 1.8) for a in others}},
    )
    feed = build_feed(areas, NOW, rain, {a.id: None for a in areas}, {a.id: 12.0 for a in areas})

    schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    jsonschema.Draft202012Validator(schema).validate(feed)

    missing = feed["quebradas"][0]
    assert missing["status"]["level"] == "UNKNOWN"
    assert missing["observation"]["scan_time_utc"] is None
    assert missing["observation"]["rain_rate_max_mmh"] is None
    assert feed["quebradas"][1]["status"]["level"] == "YELLOW"
    assert feed["quebradas"][1]["observation"]["lightning_flashes_15m"] is None


def test_geojson_ids_match_contract_pattern():
    schema = json.loads(config.SCHEMA_PATH.read_text(encoding="utf-8"))
    pattern = schema["properties"]["quebradas"]["items"]["properties"]["id"]["pattern"]
    import re

    for area in load_areas(config.QUEBRADAS_GEOJSON):
        assert re.match(pattern, area.id)
        assert area.geometry.contains(area.geometry.centroid)


def test_classify_uses_the_given_profile():
    strict = dict(T, red_rain_mmh=8.0)
    assert risk.classify(9.0, None, None, T).level == "YELLOW"
    assert risk.classify(9.0, None, None, strict).level == "RED"


def test_every_area_has_a_known_profile(tmp_path):
    areas = load_areas(config.QUEBRADAS_GEOJSON)
    assert {a.threshold_profile for a in areas} <= set(config.THRESHOLD_PROFILES)
    assert next(a for a in areas if a.id == "LIM-CHO-QUIRIO").threshold_profile == "SIERRA_LIMA"
    assert next(a for a in areas if a.id == "PIU-PIU-CUENCAMEDIA").threshold_profile == "COSTA_NORTE"

    bad = json.loads(config.QUEBRADAS_GEOJSON.read_text(encoding="utf-8"))
    bad["features"][0]["properties"]["threshold_profile"] = "NO_EXISTE"
    path = tmp_path / "bad.geojson"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="threshold_profile"):
        load_areas(path)


def test_lima_pilot_only_uses_configured_lima_areas():
    areas = load_areas(config.QUEBRADAS_GEOJSON)
    selected = select_areas(areas, "lima")
    assert selected and all(a.region == "Lima" for a in selected)
    assert {a.id for a in selected} == {"LIM-CHO-QUIRIO"}
    assert select_areas(areas, None) == areas


def test_unknown_region_does_not_silently_publish_an_empty_feed():
    with pytest.raises(ValueError):
        select_areas(load_areas(config.QUEBRADAS_GEOJSON), "NO_CONFIGURADA")
