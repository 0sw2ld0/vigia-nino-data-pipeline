"""Hourly 2 m GFS air temperature samples; not station measurements or hazard levels."""
import json
import math
from datetime import datetime,timezone
from pathlib import Path
import requests
import gfs_temperature
from shapely.geometry import box,Polygon,MultiPolygon
from fire_hotspots import boundaries,stamp

STEP=0.25

def empty(now):
    return dict(version=1,state='UNAVAILABLE',processed_at_utc=stamp(now),valid_time_utc=None,source='NOAA GFS · temperatura del aire',source_url=gfs_temperature.SOURCE_URL,units='°C',height_m=2,sampling_scope='PERU_GFS_DIRECT_V1',sampling_degrees=STEP,cells=[],context='Temperatura del aire estimada por modelo a 2 m; no medición de estación. Muestras cada 0,25° (~28 km); sin datos no implica temperatura cero.')

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
    if len(cells)>4000:raise ValueError('Temperature sampling budget exceeded')
    return cells

def fetch(root,now,cache_path,session=None):
    valid=now.astimezone(timezone.utc).replace(minute=0,second=0,microsecond=0)
    feed=empty(now);feed['valid_time_utc']=stamp(valid)
    cache=Path(cache_path)
    try:
        old=json.loads(cache.read_text())
        if old['valid_time_utc']==stamp(valid) and old['state']=='AVAILABLE' and old.get('sampling_scope')=='PERU_GFS_DIRECT_V1':return old
    except (OSError,ValueError,KeyError):pass
    cells=grid(root)
    try:
        feed['model']=gfs_temperature.fetch(cells,valid,session)
    except (ValueError,requests.RequestException,RuntimeError):
        for c in cells:c['celsius']=None
    feed['cells']=cells;feed['state']='AVAILABLE' if any(c['celsius'] is not None for c in cells) else 'UNAVAILABLE'
    if feed['state']=='UNAVAILABLE':feed['valid_time_utc']=None
    if all(c['celsius'] is not None for c in cells):
        cache.parent.mkdir(parents=True,exist_ok=True);tmp=cache.with_suffix('.tmp');tmp.write_text(json.dumps(feed,ensure_ascii=False));tmp.replace(cache)
    return feed
