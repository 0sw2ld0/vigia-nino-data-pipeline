"""SENAMHI advisory sectors, kept separate from observed rain and flood extents."""
import html
import gzip
import json
import re
from datetime import datetime, timedelta, timezone

import requests
from shapely.geometry import shape
from shapely.ops import unary_union

PAGE_URL = 'https://www.senamhi.gob.pe/?p=aviso-activacion-quebrada'
WFS_URL = 'https://idesep.senamhi.gob.pe/geoserver/g_acti_quebrada/ows'
LIMA_TZ = timezone(timedelta(hours=-5))
MONTHS = {name: i+1 for i, name in enumerate(['enero','febrero','marzo','abril','mayo','junio','julio','agosto','septiembre','octubre','noviembre','diciembre'])}
LEVELS = {'Nivel 2': ('YELLOW','amarillo',2), 'Nivel 3': ('ORANGE','naranja',3), 'Nivel 4': ('RED','rojo',4)}

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

def assign_notices(metadata, collection, channels, now):
    if not metadata['from'] <= now < metadata['until']:
        return {}
    rows = collection['features']
    if int(collection.get('totalFeatures',len(rows))) != len(rows):
        raise ValueError('Truncated official warning dataset')
    date = metadata['from'].date().isoformat()
    if any(f['properties'].get('fecha','')[:10] != date for f in rows):
        raise ValueError('Official geometry date does not match current advisory')
    notices = {}
    for channel in channels['features']:
        line = shape(channel['geometry'])
        if line.is_empty or not line.is_valid or line.length <= 0:
            raise ValueError('Invalid reference channel')
        grouped = {}
        for f in rows:
            p = f['properties']
            if p.get('nombdep') != 'LIMA' or p.get('nivel') not in LEVELS:
                continue
            polygon = shape(f['geometry'])
            if polygon.is_empty or not polygon.is_valid:
                raise ValueError('Invalid SENAMHI advisory geometry')
            if polygon.intersects(line):
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
        'CQL_FILTER':"nombdep='LIMA'"},timeout=45)
    response.raise_for_status()
    collection = response.json()
    channels = json.loads(channels_path.read_text())
    notices = assign_notices(metadata,collection,channels,now)
    return notices, {**report,'state':'verified','source_features':len(collection['features']),'assigned_zones':sorted(notices)}


def read_verified_snapshot(path, channels_path, now):
    """A dated, manually checked official snapshot; never refresh its verification time on read."""
    snapshot = json.loads(gzip.decompress(path.read_bytes()))
    retrieved = datetime.fromisoformat(snapshot['retrieved_at_utc'].replace('Z','+00:00'))
    if not 0 <= (now - retrieved).total_seconds() <= 6 * 3600:
        raise ValueError('Official backup is older than the six-hour verification limit')
    m = snapshot['metadata']
    metadata = {**m,'from':datetime.fromisoformat(m['from'].replace('Z','+00:00')).astimezone(LIMA_TZ),
        'until':datetime.fromisoformat(m['until'].replace('Z','+00:00')).astimezone(LIMA_TZ)}
    if not metadata['from'] <= now < metadata['until']:
        raise ValueError('Official backup is outside advisory validity')
    notices = assign_notices(metadata, snapshot['collection'], json.loads(channels_path.read_text()), now)
    stamp = retrieved.astimezone(LIMA_TZ).strftime('%d/%m/%Y %H:%M')
    for notice in notices.values():
        notice['body'] += f' Copia oficial verificada el {stamp} (hora de Perú); consulta directa temporalmente no disponible.'
    return notices, {'state':'verified_snapshot','id':metadata['id'],'source':PAGE_URL,
        'source_retrieved_at_utc':iso(retrieved),'valid_from_utc':iso(metadata['from']),'valid_until_utc':iso(metadata['until']),
        'snapshot_max_age_hours':6,'assigned_zones':sorted(notices),'source_features':len(snapshot['collection']['features'])}
