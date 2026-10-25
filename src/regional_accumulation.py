"""Bounded regional GFS samples: model precipitation, never watershed rainfall."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import math
import requests

API_URL = 'https://api.open-meteo.com/v1/forecast'
MODEL = 'gfs_global'
GRID_DEGREES = 0.25
MAX_SAMPLES = 80
BATCH_SIZE = 20
CACHE_PATH = Path(__file__).resolve().parent.parent / 'data_cache/lima_accumulation.json'
CONTEXT = ('Precipitación modelada GFS vía Open-Meteo, 72 horas completas; '
           'muestras cada 0,25° (~28 km), modelo ~13 km. Muestra próxima al centroide '
           'del tramo; no es lluvia medida ni promedio de su cuenca alta.')

@dataclass
class RegionalAccumulation:
    values: dict
    report: dict


def utc_string(value):
    return value.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def sample_key(area):
    return (round(area.centroid_lat / GRID_DEGREES) * GRID_DEGREES,
            round(area.centroid_lon / GRID_DEGREES) * GRID_DEGREES)


def _sum_window(item, end):
    """Require every one of 72 hourly end-times, finite and nonnegative."""
    if not isinstance(item, dict) or not isinstance(item.get('hourly_units'), dict) or item['hourly_units'].get('precipitation') != 'mm':
        return None
    hourly = item.get('hourly', {})
    if not isinstance(hourly, dict):
        return None
    times, values = hourly.get('time', []), hourly.get('precipitation', [])
    if len(times) != len(values):
        return None
    expected = {utc_string(end - timedelta(hours=i)) for i in range(72)}
    seen = {}
    try:
        for stamp, value in zip(times, values):
            ts = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            key = utc_string(ts)
            if key in expected:
                if key in seen or isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < 0:
                    return None
                seen[key] = value
    except (ValueError, TypeError, AttributeError):
        return None
    return round(sum(seen.values()), 1) if seen.keys() == expected else None


def fetch_regional_accumulation(areas, now, cache_path=CACHE_PATH, session=None):
    """Return values by area ID plus provenance; failed cells remain None.

    Cache has an exact UTC window key. It is never reused for a later hour,
    and failed batches are not cached. No implicit retries or stale fallback.
    """
    if now.tzinfo is None:
        raise ValueError('now must have timezone')
    end = now.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
    start = end - timedelta(hours=72)
    samples = sorted({sample_key(a) for a in areas})
    report = {'source':'NOAA GFS via Open-Meteo', 'model':MODEL,
              'state':'unavailable', 'context':CONTEXT,
              'window_start_utc':utc_string(start), 'window_end_utc':utc_string(end),
              'window_hours':72, 'required_hours':72,
              'model_resolution_km_approx':13, 'sampling_grid_degrees':GRID_DEGREES,
              'sampling_spacing_km_approx':28, 'sample_count':len(samples),
              'http_requests':0, 'locations_requested':0, 'cache_hit':False,
              'retrieved_at_utc':None, 'segments_with_data':0,
              'source_url':'https://open-meteo.com/en/docs/gfs-api'}
    unknown = {a.id:None for a in areas}
    if not samples or len(samples) > MAX_SAMPLES:
        report['error'] = 'sample_budget_exceeded' if samples else 'no_areas'
        return RegionalAccumulation(unknown, report)
    signature = {'window_end_utc':utc_string(end), 'model':MODEL, 'grid':GRID_DEGREES, 'samples':samples}
    # JSON normalizes tuple samples to lists.
    signature = json.loads(json.dumps(signature))
    payload = None
    if cache_path is not None:
        try:
            cached = json.loads(Path(cache_path).read_text())
            receipt = datetime.fromisoformat(cached['retrieved_at_utc'].replace('Z','+00:00'))
            if cached['signature'] == signature and end <= receipt <= now.astimezone(timezone.utc):
                payload = cached
                report['cache_hit'] = True
        except (OSError, ValueError, TypeError, KeyError):
            pass
    if payload is None:
        responses = []
        client = session or requests
        for offset in range(0,len(samples),BATCH_SIZE):
            batch = samples[offset:offset+BATCH_SIZE]
            params = {'latitude':','.join(str(s[0]) for s in batch),
                      'longitude':','.join(str(s[1]) for s in batch),
                      'hourly':'precipitation', 'start_hour':(start+timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M'),
                      'end_hour':end.strftime('%Y-%m-%dT%H:%M'),
                      'timezone':'GMT', 'models':MODEL, 'cell_selection':'nearest'}
            report['http_requests'] += 1
            report['locations_requested'] += len(batch)
            try:
                response = client.get(API_URL,params=params,timeout=(5,20))
                response.raise_for_status()
                items = response.json()
                items = items if isinstance(items,list) else [items]
                if len(items) != len(batch) or not all(isinstance(i,dict) for i in items):
                    raise ValueError('Incomplete location response')
                responses.extend(items)
            except (requests.RequestException, ValueError, TypeError):
                responses.extend([{} for _ in batch])
        payload = {'signature':signature, 'retrieved_at_utc':utc_string(now), 'items':responses}
        if cache_path is not None and all(_sum_window(i,end) is not None for i in responses):
            target = Path(cache_path)
            try:
                target.parent.mkdir(parents=True,exist_ok=True)
                temporary = target.with_suffix('.tmp')
                temporary.write_text(json.dumps(payload,separators=(',',':')))
                temporary.replace(target)
            except OSError:
                report['cache_write_failed'] = True
    items = payload.get('items', [])
    by_sample = {}
    if len(items) == len(samples):
        for sample,item in zip(samples,items):
            by_sample[sample] = _sum_window(item,end)
    values = {a.id:by_sample.get(sample_key(a)) for a in areas}
    report['segments_with_data'] = sum(v is not None for v in values.values())
    report['samples_with_data'] = sum(v is not None for v in by_sample.values())
    report['state'] = ('available' if all(v is not None for v in values.values()) else
                       'partial' if any(v is not None for v in values.values()) else 'unavailable')
    report['retrieved_at_utc'] = payload['retrieved_at_utc'] if report['samples_with_data'] else None
    return RegionalAccumulation(values,report)
