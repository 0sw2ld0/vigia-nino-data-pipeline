"""Web-Mercator aligned GOES rainfall image. No interpolation, inferred risk or fake rain."""
import hashlib
import math
import struct
import zlib

import numpy as np
from pyproj import Transformer

BOUNDS = [-82.0, -13.6, -75.0, -2.8]  # Lima, Piura, Tumbes and surrounding context; not a basin boundary.
PALETTE = [(0.1, [65,182,230,160]), (2, [35,139,220,175]), (10, [113,74,197,185]), (30, [202,42,151,195])]


def png_rgba(rgba):
    height,width,_=rgba.shape
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    raw=b''.join(b'\0'+row.tobytes() for row in rgba)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,6,0,0,0))+chunk(b'IDAT',zlib.compress(raw,9))+chunk(b'IEND',b'')


def colorize(rain,quality):
    good=(quality==0)&np.isfinite(rain)&(rain>=0)
    rgba=np.zeros((*rain.shape,4),dtype=np.uint8)
    rgba[~good]=[116,125,139,90]  # Missing/degraded is gray, never transparent dry data.
    for threshold,color in PALETTE:rgba[good&(rain>=threshold)]=color
    return rgba,good


def nearest_indices(axis,values):
    ascending=axis[-1]>axis[0]
    ordered=axis if ascending else -axis
    query=values if ascending else -values
    right=np.clip(np.searchsorted(ordered,query),1,len(axis)-1)
    left=right-1
    indices=np.where(abs(ordered[left]-query)<=abs(ordered[right]-query),left,right)
    valid=np.isfinite(query)&(query>=ordered[0])&(query<=ordered[-1])
    return indices,valid


def from_dataset(ds,geos_transformer,height,s3_key,bounds=BOUNDS,resolution_m=2000):
    if resolution_m<2000:raise ValueError('Do not manufacture finer satellite detail')
    west,south,east,north=bounds
    merc=Transformer.from_crs(4326,3857,always_xy=True)
    inverse=Transformer.from_crs(3857,4326,always_xy=True)
    x0,y0=merc.transform(west,south);x1,y1=merc.transform(east,north)
    width=math.ceil((x1-x0)/resolution_m);rows=math.ceil((y1-y0)/resolution_m)
    if max(width,rows)>1024:raise ValueError('Overlay too large')
    xx,yy=np.meshgrid(x0+(np.arange(width)+.5)*(x1-x0)/width,y1-(np.arange(rows)+.5)*(y1-y0)/rows)
    lon,lat=inverse.transform(xx,yy)
    gx,gy=geos_transformer.transform(lon,lat)
    ci,cvalid=nearest_indices(ds['x'].values,np.asarray(gx)/height)
    ri,rvalid=nearest_indices(ds['y'].values,np.asarray(gy)/height)
    # Read one small native source window rather than the full disk.
    r0,r1=int(ri.min()),int(ri.max())+1;c0,c1=int(ci.min()),int(ci.max())+1
    rain=ds['RRQPE'][r0:r1,c0:c1].values[ri-r0,ci-c0].astype(float)
    quality=ds['DQF'][r0:r1,c0:c1].values[ri-r0,ci-c0].copy()
    quality[~(cvalid&rvalid)]=255
    rgba,good=colorize(rain,quality)
    payload=png_rgba(rgba);digest=hashlib.sha256(payload).hexdigest()
    manifest=dict(version=1,state='AVAILABLE',source='NOAA GOES-19 · ABI RRQPE',source_key=s3_key,scan_time_utc=ds.attrs['time_coverage_end'],stale_after_minutes=60,image=f'rain-{digest[:16]}.png',sha256=digest,bounds=bounds,projection='EPSG:3857',width=width,height=rows,resampling='NEAREST_NATIVE_PIXEL',nominal_source_resolution_m=2000,valid_pixel_fraction=round(float(good.mean()),6),rainy_pixel_count=int((good&(rain>=.1)).sum()),legend=[dict(min_mmh=t,rgba=c) for t,c in PALETTE],context='Instantaneous satellite rainfall estimate, not radar, accumulated rainfall, warning level or inundation extent. Transparent: <0.1 mm/h with valid data; gray: no valid data; outside image: not covered.')
    return manifest,payload
