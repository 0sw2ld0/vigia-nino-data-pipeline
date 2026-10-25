"""High-quality GOES-19 thermal detections; not confirmed incidents or burn perimeters."""
import gzip
import io
import json
from datetime import datetime, timezone
import numpy as np
import xarray as xr
from pyproj import Transformer
from shapely.geometry import shape, box
from shapely import contains_xy
import goes
from areas import Area

PRODUCT = 'ABI-L2-FDCF'
SOURCE_URL = 'https://www.star.nesdis.noaa.gov/goesr/product_land_fire.php'
ACCEPTED = {10:'good_fire_pixel',11:'saturated_fire_pixel',13:'high_probability_fire_pixel',30:'temporally_filtered_good_fire_pixel',31:'temporally_filtered_saturated_fire_pixel',33:'temporally_filtered_high_probability_fire_pixel'}


def stamp(dt):
    return dt.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def empty(now, state='UNAVAILABLE', reason='No se pudo verificar el producto satelital'):
    return dict(version=1,state=state,processed_at_utc=stamp(now),scan_time_utc=None,
        stale_after_minutes=60,source='NOAA GOES-19 · ABI FDCF',source_url=SOURCE_URL,
        source_key=None,regions={},detections=[],context=reason+'. Sin detecciones no significa ausencia de incendios; nubes y resolución pueden ocultarlos. No es una delimitación de área quemada.')


def boundaries(root):
    result={}
    files=['peru_boundary.geojson.gz'] if (root/'data_static/peru_boundary.geojson.gz').exists() else ['lima_boundary.geojson.gz','north_boundary.geojson.gz']
    for filename in files:
        for f in json.loads(gzip.decompress((root/'data_static'/filename).read_bytes()))['features']:
            name=(f['properties'].get('nombdpto') or f['properties'].get('NM_DEPA')).title()
            result[name]=shape(f['geometry'])
    return result


def from_dataset(ds, key, now, regions):
    flag_names=dict(zip(map(int,ds['Mask'].attrs['flag_values']),ds['Mask'].attrs['flag_meanings'].split()))
    if any(flag_names.get(code)!=meaning for code,meaning in ACCEPTED.items()):
        raise ValueError('Unexpected NOAA thermal mask semantics')
    dqf_names=dict(zip(map(int,ds['DQF'].attrs['flag_values']),ds['DQF'].attrs['flag_meanings'].split()))
    if dqf_names.get(0)!='good_quality_fire_pixel_qf' or dqf_names.get(1)!='good_quality_fire_free_land_pixel_qf':
        raise ValueError('Unexpected NOAA thermal quality semantics')
    scan=goes._parse_iso(ds.attrs['time_coverage_end'])
    if not -300 <= (now-scan).total_seconds() <= 3600:
        raise ValueError('Thermal satellite scan is stale or in the future')
    transform,height=goes._geos_transformer(ds)
    envelope=Area('FIRE-WINDOW','Thermal coverage','','','LIMA_OBSERVATION',0,0,box(-82,-19,-68,1))
    rows,cols=goes._window(ds,transform,height,envelope)
    masks=ds['Mask'][rows,cols].values
    quality=ds['DQF'][rows,cols].values
    xx,yy=np.meshgrid(ds['x'].values[cols]*height,ds['y'].values[rows]*height)
    lon,lat=Transformer.from_crs(transform.target_crs,4326,always_xy=True).transform(xx,yy)
    good=np.isin(masks,list(ACCEPTED)) & (quality==0) & np.isfinite(lon) & np.isfinite(lat)
    inverse=Transformer.from_crs(transform.target_crs,4326,always_xy=True)
    def edges(values):
        values=np.asarray(values,dtype=float)*height
        if len(values)<2 or not np.all(np.isfinite(values)): raise ValueError('Invalid native satellite grid')
        return np.r_[values[0]-(values[1]-values[0])/2,(values[:-1]+values[1:])/2,values[-1]+(values[-1]-values[-2])/2]
    xedges=edges(ds['x'].values);yedges=edges(ds['y'].values)
    coverage={};detections=[]
    row_offset=rows.start or 0;col_offset=cols.start or 0
    for region,boundary in regions.items():
        inside=contains_xy(boundary,lon,lat)
        total=int(inside.sum())
        if total==0: raise ValueError('No thermal pixels cover '+region)
        usable=inside & np.isin(quality,[0,1])
        chosen=inside & good
        coverage[region]=dict(detection_count=int(chosen.sum()),native_pixel_count=total,
            usable_pixel_fraction=round(float(usable.sum()/total),6),
            cloud_pixel_fraction=round(float((inside & (quality==2)).sum()/total),6))
        for row,col in zip(*np.where(chosen)):
            code=int(masks[row,col]);source_row=int(row+row_offset);source_col=int(col+col_offset)
            corners=[inverse.transform(xedges[c],yedges[r]) for r,c in [(source_row,source_col),(source_row,source_col+1),(source_row+1,source_col+1),(source_row+1,source_col),(source_row,source_col)]]
            if not np.all(np.isfinite(corners)): raise ValueError('Invalid native pixel footprint')
            footprint=[[round(float(lo),6),round(float(la),6)] for lo,la in corners]
            detections.append(dict(pixel_footprint=footprint,id=f'G19-{scan:%Y%m%dT%H%M%S}-{source_row}-{source_col}',
                lat=round(float(lat[row,col]),6),lon=round(float(lon[row,col]),6),region=region,
                observed_at_utc=stamp(scan),mask=code,quality=0,
                category=ACCEPTED[code],confirmation='UNCONFIRMED',
                location_context='Centro de píxel satelital; no ubicación exacta del incendio'))
    if len(detections)>10000: raise ValueError('Unexpected regional thermal detection count')
    feed=empty(now)
    feed.update(state='AVAILABLE',scan_time_utc=stamp(scan),source_key=key,regions=coverage,
        detections=detections,nominal_resolution_m_at_nadir=2000,
        context='Detecciones térmicas de alta calidad y alta probabilidad en el último barrido; no incendios confirmados. Se excluyen baja/media probabilidad, contaminación por nubes y calidad inválida. La posición es el centro del píxel, de resolución nominal 2 km en el nadir y mayor fuera de él. Sin detecciones no significa ausencia de incendios; las nubes y el tamaño del fuego pueden ocultarlos. Las manchas representan los píxeles nativos con detección de calor, no la superficie quemada ni riesgo para una vivienda.')
    return feed


def fetch(fs,root,now):
    keys=[k for k in goes.list_recent_keys(fs,PRODUCT,now,2) if goes.start_time_from_key(k)<=now]
    if not keys: return empty(now,reason='No hay barrido térmico reciente disponible')
    key=keys[-1]
    with xr.open_dataset(io.BytesIO(fs.cat(key)),engine='h5netcdf') as ds:
        return from_dataset(ds,key,now,boundaries(root))
