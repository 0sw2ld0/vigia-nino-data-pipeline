"""Hourly 2 m GFS air temperature samples; not station measurements or hazard levels."""
import json
import math
from datetime import datetime,timezone
from pathlib import Path
import requests
from shapely.geometry import box,Polygon,MultiPolygon
from fire_hotspots import boundaries,stamp

URL='https://api.open-meteo.com/v1/forecast'
STEP=0.25

def empty(now):
    return dict(version=1,state='UNAVAILABLE',processed_at_utc=stamp(now),valid_time_utc=None,source='GFS vía Open-Meteo',source_url='https://open-meteo.com/en/docs/gfs-api',units='°C',height_m=2,sampling_degrees=STEP,cells=[],context='Temperatura del aire estimada por modelo a 2 m; no medición de estación. Muestras cada 0,25° (~28 km); sin datos no implica temperatura cero.')

def grid(root):
    cells=[]
    for region,boundary in boundaries(root).items():
        west,south,east,north=boundary.bounds
        for y in range(math.floor(south/STEP),math.ceil(north/STEP)):
            for x in range(math.floor(west/STEP),math.ceil(east/STEP)):
                clipped=boundary.intersection(box(x*STEP,y*STEP,(x+1)*STEP,(y+1)*STEP)).simplify(0.002,preserve_topology=True)
                if clipped.is_empty or clipped.area<1e-8:continue
                parts=list(clipped.geoms) if isinstance(clipped,MultiPolygon) else [clipped]
                parts=[p for p in parts if isinstance(p,Polygon) and p.area>1e-8]
                if not parts:continue
                center=clipped.representative_point()
                cells.append(dict(id=f'{region}-{x}-{y}',region=region,lat=round(center.y,5),lon=round(center.x,5),polygons=[[[[round(lo,5),round(la,5)] for lo,la in ring.coords] for ring in [p.exterior,*p.interiors]] for p in parts]))
    if len(cells)>400:raise ValueError('Temperature sampling budget exceeded')
    return cells

def parse_item(item,valid):
    if item.get('hourly_units',{}).get('temperature_2m')!='°C':return None
    hourly=item.get('hourly',{});times=hourly.get('time',[]);values=hourly.get('temperature_2m',[])
    if len(times)!=len(values):return None
    target=valid.strftime('%Y-%m-%dT%H:%M')
    if times.count(target)!=1:return None
    v=values[times.index(target)]
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not -80<=v<=60:return None
    return round(v,1)

def fetch(root,now,cache_path,session=None):
    valid=now.astimezone(timezone.utc).replace(minute=0,second=0,microsecond=0)
    feed=empty(now);feed['valid_time_utc']=stamp(valid)
    cache=Path(cache_path)
    try:
        old=json.loads(cache.read_text())
        if old['valid_time_utc']==stamp(valid) and old['state']=='AVAILABLE':return old
    except (OSError,ValueError,KeyError):pass
    cells=grid(root);client=session or requests;complete=True
    for start in range(0,len(cells),30):
        batch=cells[start:start+30]
        try:
            r=client.get(URL,params=dict(latitude=','.join(str(c['lat']) for c in batch),longitude=','.join(str(c['lon']) for c in batch),hourly='temperature_2m',models='gfs_global',timezone='GMT',forecast_hours=1,temperature_unit='celsius',elevation=','.join('nan' for _ in batch)),timeout=25)
            r.raise_for_status();items=r.json();items=items if isinstance(items,list) else [items]
            if len(items)!=len(batch):raise ValueError('Temperature response size mismatch')
            for c,item in zip(batch,items):c['celsius']=parse_item(item,valid)
        except (requests.RequestException,ValueError,TypeError,KeyError):
            complete=False
            for c in batch:c['celsius']=None
    feed['cells']=cells;feed['state']='AVAILABLE' if any(c['celsius'] is not None for c in cells) else 'UNAVAILABLE'
    if feed['state']=='UNAVAILABLE':feed['valid_time_utc']=None
    if complete and all(c['celsius'] is not None for c in cells):
        cache.parent.mkdir(parents=True,exist_ok=True);tmp=cache.with_suffix('.tmp');tmp.write_text(json.dumps(feed,ensure_ascii=False));tmp.replace(cache)
    return feed
