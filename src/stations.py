"""Public SENAMHI chart readings; never infer a timezone or solve table CAPTCHAs."""
import concurrent.futures
from datetime import datetime, timezone
import gzip
import json
import math
from pathlib import Path
import re
from urllib.parse import urlencode

import requests

CATALOG_URL='https://www.senamhi.gob.pe/mapas/mapa-estaciones-2/'
CHART_URL=CATALOG_URL+'map_red_graf.php'
CONTEXT='Public chart values, not quality-controlled observations. Published hour has no declared timezone; do not treat as current or compare directly with instantaneous satellite rain.'


def stamp(now):return now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def parse_catalog(text):
    match=re.search(r'var\s+PruebaTest\s*=\s*(\[.*?\])\s*;',text,re.S)
    if not match:raise ValueError('Station catalog missing')
    rows=[]
    for raw in re.findall(r'\{[^{}]*\}',match[1]):
        try:rows.append(json.loads(raw))
        except json.JSONDecodeError:continue  # Some public entries have empty numeric fields.
    result=[];seen=set()
    for r in rows:
        if r.get('ico')!='M':continue
        try:code=str(r['cod']);lat=float(r['lat']);lon=float(r['lon'])
        except (KeyError,TypeError,ValueError):continue
        if code in seen or not re.fullmatch(r'[A-Za-z0-9]{4,12}',code):continue
        if not (-19<=lat<=1 and -82<=lon<=-68):continue
        seen.add(code)
        result.append(dict(id=code,name=r['nom'].strip().title(),lat=lat,lon=lon,kind=r['estado'],category=r['cate'],source_url=CHART_URL+'?'+urlencode(dict(cod=code,estado=r['estado'],tipo_esta='M',cate=r['cate'],cod_old=r.get('cod_old','')))))
    if len(result)<100:raise ValueError('Incomplete station catalog')
    return result


def parse_chart(text):
    cats=re.search(r'categories:\s*\[(.*?)\]',text,re.S)
    series=re.search(r"name:\s*'Precipitaci.*?data:\s*\[(.*?)\]",text,re.S)
    if not cats or not series:raise ValueError('No public hourly rainfall chart')
    labels=re.findall(r"'(\d{4}/\d{2}/\d{2} - \d{2})'",cats[1])
    values=json.loads('['+series[1].strip().rstrip(',')+']')
    if not labels or len(labels)!=len(values):raise ValueError('Unaligned chart')
    for value in values:
        if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=500):raise ValueError('Invalid rain reading')
    # Keep the final published slot, including null. Never silently replace missing with zero.
    return dict(published_hour=labels[-1],rain_hour_mm=values[-1],observed_at_utc=None,time_basis='NOT_DECLARED',series=[dict(published_hour=t,rain_hour_mm=v) for t,v in zip(labels[-48:],values[-48:])])


def select_charts(rows,limit=32):
    candidates=[r for r in rows if r['kind']=='AUTOMATICA']
    chosen=[r for r in candidates if r['name'].upper() in ['VON HUMBOLDT','CAMPO DE MARTE']]
    # Spread the bounded pilot through Peru, independent of any user's location.
    remaining=[r for r in candidates if r not in chosen]
    while remaining and len(chosen)<limit:
        row=max(remaining,key=lambda r:min(((r['lat']-c['lat'])**2+(r['lon']-c['lon'])**2 for c in chosen),default=1))
        chosen.append(row);remaining.remove(row)
    return chosen


def collect(now,cache=None,get=None):
    get=get or requests.get
    if cache and Path(cache).exists():
        try:
            saved=json.loads(Path(cache).read_text());age=(now-datetime.fromisoformat(saved['retrieved_at_utc'].replace('Z','+00:00'))).total_seconds()
            if 0<=age<3600:return saved
        except (ValueError,KeyError,TypeError):pass
    response=get(CATALOG_URL,timeout=15);response.raise_for_status();rows=parse_catalog(response.text)
    def read(row):
        try:
            response=get(row['source_url'],timeout=8);response.raise_for_status()
            return row['id'],dict(state='PUBLISHED',**parse_chart(response.text))
        except Exception:return row['id'],dict(state='UNAVAILABLE')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        readings=dict(pool.map(read,select_charts(rows)))
    result=dict(version=1,state='AVAILABLE',retrieved_at_utc=stamp(now),source='SENAMHI · gráficos públicos',source_url=CATALOG_URL,context=CONTEXT,chart_limit=32,stations=[dict(**r,reading=readings.get(r['id'],dict(state='NOT_SAMPLED'))) for r in rows])
    if cache:
        path=Path(cache);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp');tmp.write_text(json.dumps(result,ensure_ascii=False));tmp.replace(path)
    return result


def validate(feed):
    if feed.get('version')!=1 or feed.get('source_url')!=CATALOG_URL or len(feed.get('stations',[]))>3000:raise ValueError('Invalid station feed')
    datetime.fromisoformat(feed['retrieved_at_utc'].replace('Z','+00:00'))
    for r in feed['stations']:
        if not (-19<=r['lat']<=1 and -82<=r['lon']<=-68) or not r['source_url'].startswith(CHART_URL+'?'):raise ValueError('Invalid station')
        reading=r['reading']
        if reading.get('state')=='PUBLISHED':
            if reading.get('time_basis')!='NOT_DECLARED' or reading.get('observed_at_utc') is not None:raise ValueError('Unverified station timezone')
            for v in [reading.get('rain_hour_mm')]+[x.get('rain_hour_mm') for x in reading.get('series',[])]:
                if v is not None and (isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=500):raise ValueError('Invalid rain')
    return feed


def fetch(now,collector_url=None,get=None):
    get=get or requests.get
    if collector_url:
        try:
            response=get(collector_url,timeout=20);response.raise_for_status()
            if len(response.content)>2_000_000:raise ValueError('Oversized station copy')
            raw=gzip.decompress(response.content)
            if len(raw)>5_000_000:raise ValueError('Oversized station content')
            return validate(json.loads(raw))
        except Exception:pass
    try:return validate(collect(now,get=get))
    except Exception:return dict(version=1,state='UNAVAILABLE',retrieved_at_utc=None,source='SENAMHI',source_url=CATALOG_URL,context=CONTEXT,stations=[])
