"""Read a single 2 m temperature GRIB message from public NOAA GFS byte ranges."""
from datetime import datetime,timedelta,timezone
import numpy as np
import requests

BASE='https://noaa-gfs-bdp-pds.s3.amazonaws.com'
SOURCE_URL='https://registry.opendata.aws/noaa-gfs-bdp-pds/'

def temperature_range(index):
    rows=[line.split(':') for line in index.splitlines() if line.strip()]
    matches=[i for i,row in enumerate(rows) if len(row)>5 and row[3:5]==['TMP','2 m above ground']]
    if len(matches)!=1 or matches[0]+1>=len(rows):raise ValueError('Unverifiable 2 m temperature index')
    i=matches[0];start=int(rows[i][1]);end=int(rows[i+1][1])-1
    if not 0<=start<end or end-start>20_000_000:raise ValueError('Unexpected GFS message size')
    return start,end

def decode_message(payload,valid,cells):
    import eccodes as ec
    handle=ec.codes_new_from_message(payload)
    try:
        get=lambda key:ec.codes_get(handle,key)
        if not (get('shortName')=='2t' and get('typeOfLevel')=='heightAboveGround' and get('level')==2 and get('units')=='K' and get('gridType')=='regular_ll' and get('jScansPositively')==0 and get('iScansNegatively')==0 and get('jPointsAreConsecutive')==0):raise ValueError('Unexpected GFS temperature field or grid')
        stamp=f"{get('validityDate'):08d}{get('validityTime'):04d}"
        if stamp!=valid.strftime('%Y%m%d%H%M'):raise ValueError('GFS validity does not match requested hour')
        ni,nj=int(get('Ni')),int(get('Nj'))
        dx,dy=float(get('iDirectionIncrementInDegrees')),float(get('jDirectionIncrementInDegrees'))
        if (ni,nj)!=(1440,721) or dx!=.25 or dy!=.25:raise ValueError('Unexpected GFS resolution')
        array=np.asarray(ec.codes_get_values(handle)).reshape(nj,ni)
        north=float(get('latitudeOfFirstGridPointInDegrees'));west=float(get('longitudeOfFirstGridPointInDegrees'))
        for c in cells:
            row=int(round((north-c['lat'])/dy));col=int(round(((c['lon']-west)%360)/dx))%ni
            kelvin=float(array[row,col]);value=kelvin-273.15
            c['celsius']=round(value,1) if np.isfinite(value) and -80<=value<=60 else None
        return dict(model_resolution_degrees=.25,model_cycle_utc=f"{get('dataDate'):08d}{get('dataTime'):04d}")
    finally:ec.codes_release(handle)

def fetch(cells,valid,session=None):
    client=session or requests
    cycle=valid.replace(hour=(valid.hour//6)*6)
    errors=[]
    for offset in [0,6,12]:
        run=cycle-timedelta(hours=offset);lead=int((valid-run).total_seconds()/3600)
        key=f'gfs.{run:%Y%m%d}/{run:%H}/atmos/gfs.t{run:%H}z.pgrb2.0p25.f{lead:03}'
        try:
            index=client.get(f'{BASE}/{key}.idx',timeout=25);index.raise_for_status()
            start,end=temperature_range(index.text)
            r=client.get(f'{BASE}/{key}',headers={'Range':f'bytes={start}-{end}'},timeout=40);r.raise_for_status()
            if r.status_code!=206 or len(r.content)!=end-start+1 or len(r.content)>20_000_000:raise ValueError('Server did not honor GFS byte range')
            report=decode_message(r.content,valid,cells)
            return {**report,'source_key':key,'download_bytes':len(r.content),'forecast_lead_hours':lead,'model_run_utc':run.strftime('%Y-%m-%dT%H:%M:%SZ')}
        except (requests.RequestException,ValueError,RuntimeError) as exc:errors.append(type(exc).__name__)
    raise ValueError('No verifiable GFS field for current hour: '+','.join(errors))
