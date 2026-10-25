"""Lluvia acumulada de las últimas 72 h por quebrada (Open-Meteo, sin API key)."""

from datetime import datetime, timedelta, timezone

import requests

import config
from areas import Area

API_URL = "https://api.open-meteo.com/v1/forecast"


def accumulate_hourly(times: list[str], values: list[float | None], now: datetime) -> float | None:
    """Suma la lluvia horaria de (now - 72 h, now]. Devuelve `None` si faltan demasiadas horas."""
    start = now - timedelta(hours=config.RAIN_ACCUM_HOURS)
    window = []
    for t, v in zip(times, values):
        ts = datetime.fromisoformat(t).replace(tzinfo=timezone.utc)
        if start < ts <= now:
            window.append(v)
    present = [v for v in window if v is not None]
    if len(present) < config.RAIN_ACCUM_MIN_HOURS:
        return None
    return round(float(sum(present)), 1)


def fetch_rain_72h(areas: list[Area], now: datetime) -> dict[str, float | None]:
    unknown: dict[str, float | None] = {a.id: None for a in areas}
    params = {
        "latitude": ",".join(str(a.centroid_lat) for a in areas),
        "longitude": ",".join(str(a.centroid_lon) for a in areas),
        "hourly": "precipitation",
        "past_hours": config.RAIN_ACCUM_HOURS + 1,
        "forecast_hours": 1,
        "timezone": "GMT",
    }
    try:
        response = requests.get(API_URL, params=params, timeout=20)
        response.raise_for_status()
        payload = response.json()
    except (requests.RequestException, ValueError):
        return unknown

    items = payload if isinstance(payload, list) else [payload]
    if len(items) != len(areas):
        return unknown

    result: dict[str, float | None] = {}
    for area, item in zip(areas, items):
        hourly = item.get("hourly") or {}
        result[area.id] = accumulate_hourly(hourly.get("time", []), hourly.get("precipitation", []), now)
    return result
