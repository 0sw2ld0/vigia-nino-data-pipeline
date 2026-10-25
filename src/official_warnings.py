"""SENAMHI advisory sectors, kept separate from observed rain and flood extents."""
import html
import gzip
import json
import re
import time
from datetime import datetime, timedelta, timezone

import requests
from shapely.geometry import shape
from shapely.ops import unary_union
from shapely.strtree import STRtree

PAGE_URL = 'https://www.senamhi.gob.pe/?p=aviso-activacion-quebrada'
WFS_URL = 'https://idesep.senamhi.gob.pe/geoserver/g_acti_quebrada/ows'
LIMA_TZ = timezone(timedelta(hours=-5))
MONTHS = {name: i+1 for i, name in enumerate(['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'])}
LEVELS = {'Nivel 2': ('YELLOW','amarillo',2), 'Nivel 3': ('ORANGE','naranja',3), 'Nivel 4': ('RED','rojo',4)}
DEPARTMENTS = ('LIMA', 'PIURA', 'TUMBES')
DEPARTMENT_FILTER = "nombdep IN ('LIMA','PIURA','TUMBES')"


def load_channels(path):
    channels = json.loads(path.read_text())
    seen = {f['properties']['zone_id'] for f in channels['features']}
    for filename in ('lima_channels.json.gz', 'north_channels.json.gz'):
        regional = path.parent/filename
        if regional.exists():
            for row in json.loads(gzip.decompress(regional.read_bytes())):
                ident = f'IGN-{row[0]}'
                if ident not in seen:
                    channels['features'].append({'type':'Feature','properties':{'zone_id':ident},
                        'geometry':{'type':'MultiLineString','coordinates':row[2]}})
                    seen.add(ident)
    return channels

def iso(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')

def parse_metadata(page):
    articles = re.findall(r'<article\b[^>]*>(.*?)</article>', page, re.S | re.I)
    if articles:
        active = [a for a in articles if 'aviso-vigente' in a]
        if len(active) != 1:
            raise ValueError('Cannot isolate current advisory from archived page content')
        page = active[0]
    text = ' '.join(html.unescape(re.sub(r'<[^>]+>', ' ', page)).split())
    number = re.search(r'N[°º]\s*(\d+)\s*-\s*(\d{4})', text)
    start = re.search(r'Fecha de inicio:\s*(?:\w+\s+)?(\d{1,2})\s+(\w+)\s+(\d{4})\s*-\s*(\d{2}):(\d{2})', text)
    duration = re.search(r'Duración:\s*(\d+)\s*hrs', text)
    if not (number and start and duration):
        raise ValueError('Cannot verify advisory number and validity from SENAMHI page')
    day,month,year,hour,minute = start.groups()
    valid_from = datetime(int(year), MONTHS[month.lower()], int(day), int(hour), int(minute), tzinfo=LIMA_TZ)
    hours = int(duration[1])
    if not (1 <= hours <= 48) or year != number[2]:
        raise ValueError('Unexpected advisory validity')
    return {'id':f'SENAMHI-AQ-{number[2]}-{number[1]}', 'number':number[1],
            'from':valid_from, 'until':valid_from+timedelta(hours=hours)}

def assign_notices(metadata, collection, channels, now, departments=('LIMA',)):
    if not metadata['from'] <= now < metadata['until']:
        return {}
    if collection.get('type') != 'FeatureCollection' or not isinstance(collection.get('features'), list) or 'totalFeatures' not in collection:
        raise ValueError('Unverifiable official sector collection')
    rows = collection['features']
    if int(collection['totalFeatures']) != len(rows):
        raise ValueError('Truncated official warning dataset')
    date = metadata['from'].date().isoformat()
    if any(f['properties'].get('fecha','')[:10] != date for f in rows):
        raise ValueError('Official geometry date does not match current advisory')
    polygons, properties = [], []
    for feature in rows:
        p = feature['properties']
        if p.get('nombdep') not in departments or p.get('nivel') not in LEVELS:
            continue
        polygon = shape(feature['geometry'])
        if polygon.is_empty or not polygon.is_valid:
            raise ValueError('Invalid SENAMHI advisory geometry')
        polygons.append(polygon)
        properties.append(p)
    tree = STRtree(polygons)
    notices = {}
    for channel in channels['features']:
        line = shape(channel['geometry'])
        if line.is_empty or not line.is_valid or line.length <= 0:
            raise ValueError('Invalid reference channel')
        grouped = {}
        for index in tree.query(line, predicate='intersects'):
            p, polygon = properties[index], polygons[index]
            grouped.setdefault(p['nivel'],[]).append((polygon,p))
        # >=90% coverage of this reference segment is required. Never infer from a shared name/district.
        matches=[]
        for raw, group in grouped.items():
            covered = unary_union([g for g,p in group]).intersection(line).length/line.length
            if covered >= .9: matches.append((LEVELS[raw],group,min(1.0,covered)))
        if not matches: continue
        (level,label,rank),group,fraction = max(matches,key=lambda m:m[0][2])
        districts = sorted({p['nombdist']+' · '+p['nombprov']+' · '+p['nombdep'] for g,p in group})
        notices[channel['properties']['zone_id']] = {
            'id':metadata['id'],'level':level,
            'title':f"SENAMHI · aviso {metadata['number']} · nivel {label}",
            'body':'El tramo cartografiado está dentro de un sector del aviso ante posible activación de quebradas. La asociación es espacial; el boletín no identifica esta quebrada por nombre. No es un desborde confirmado ni un área de afectación.',
            'url':PAGE_URL,'valid_from_utc':iso(metadata['from']),'valid_until_utc':iso(metadata['until']),
            'geographic_scope':'; '.join(districts),'channel_coverage_fraction':round(fraction,6),
        }
    return notices

def fetch_notices(channels_path, now):
    page = requests.get(PAGE_URL, timeout=25)
    page.raise_for_status()
    # SENAMHI serves UTF-8 pages; do not let a missing HTTP charset produce mojibake.
    metadata = parse_metadata(page.content.decode('utf-8'))
    report = {'source':PAGE_URL,'id':metadata['id'],'valid_from_utc':iso(metadata['from']), 'valid_until_utc':iso(metadata['until'])}
    if not metadata['from'] <= now < metadata['until']:
        return {}, {**report,'state':'outside_validity','assigned_zones':[]}
    response = requests.get(WFS_URL, params={'service':'WFS','version':'1.0.0','request':'GetFeature',
        'typeName':'g_acti_quebrada:view_av_activ_qdra','outputFormat':'application/json','srsName':'EPSG:4326',
        'CQL_FILTER':DEPARTMENT_FILTER},timeout=45)
    response.raise_for_status()
    collection = response.json()
    channels = load_channels(channels_path)
    notices = assign_notices(metadata,collection,channels,now,departments=DEPARTMENTS)
    return notices, {**report,'state':'verified','department_scope':list(DEPARTMENTS),'source_features':len(collection['features']),'assigned_zones':sorted(notices)}


def read_verified_snapshot(path, channels_path, now):
    """Never refresh a snapshot's original retrieval time on read."""
    return verify_snapshot(json.loads(gzip.decompress(path.read_bytes())), channels_path, now)


def verify_snapshot(snapshot, channels_path, now):
    retrieved = datetime.fromisoformat(snapshot['retrieved_at_utc'].replace('Z','+00:00'))
    if not 0 <= (now - retrieved).total_seconds() <= 6 * 3600:
        raise ValueError('Official backup is older than the six-hour verification limit')
    m = snapshot['metadata']
    metadata = {**m,'from':datetime.fromisoformat(m['from'].replace('Z','+00:00')).astimezone(LIMA_TZ),
        'until':datetime.fromisoformat(m['until'].replace('Z','+00:00')).astimezone(LIMA_TZ)}
    if not metadata['from'] <= now < metadata['until']:
        raise ValueError('Official backup is outside advisory validity')
    scope = snapshot.get('department_scope', ['LIMA'])
    if not isinstance(scope, list) or not scope or not set(scope) <= set(DEPARTMENTS):
        raise ValueError('Unknown official snapshot department scope')
    if any(f['properties'].get('nombdep') not in scope for f in snapshot['collection']['features']):
        raise ValueError('Official geometry outside declared department scope')
    notices = assign_notices(metadata, snapshot['collection'], load_channels(channels_path), now, departments=scope)
    stamp = retrieved.astimezone(LIMA_TZ).strftime('%d/%m/%Y %H:%M')
    for notice in notices.values():
        notice['body'] += f' Copia oficial verificada el {stamp} (hora de Perú); consulta directa temporalmente no disponible.'
    return notices, {'state':'verified_snapshot','id':metadata['id'],'source':PAGE_URL,
        'source_retrieved_at_utc':iso(retrieved),'valid_from_utc':iso(metadata['from']),'valid_until_utc':iso(metadata['until']),
        'snapshot_max_age_hours':6,'department_scope':scope,'assigned_zones':sorted(notices),'source_features':len(snapshot['collection']['features'])}


def fetch_collector_snapshot(url, channels_path, now):
    started = time.perf_counter()
    response = requests.get(url, params={'checked_at':iso(now)}, timeout=30)
    response.raise_for_status()
    if len(response.content) > 5_000_000:
        raise ValueError('Collector snapshot exceeds size limit')
    downloaded = time.perf_counter()
    notices, report = verify_snapshot(json.loads(gzip.decompress(response.content)), channels_path, now)
    return notices, {**report, 'state':'verified_collector', 'collector_url':url,
        'download_seconds':round(downloaded-started,3),
        'spatial_matching_seconds':round(time.perf_counter()-downloaded,3)}
