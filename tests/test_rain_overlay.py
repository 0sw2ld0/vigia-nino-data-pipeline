import struct
import zlib
import numpy as np
import pytest
from rain_overlay import colorize, nearest_indices, png_rgba


def test_missing_dry_and_real_rain_have_distinct_pixels():
    rain=np.array([[0.,.05,.1,2.,10.,30.,99.,np.nan,-1.]])
    quality=np.zeros_like(rain);quality[0,6]=1
    rgba,good=colorize(rain,quality)
    assert list(rgba[0,:2,3])==[0,0]
    assert rgba[0,2,3]>0 and rgba[0,3,3]>0
    assert not np.array_equal(rgba[0,3],rgba[0,4])
    assert not good[0,6] and not good[0,7] and not good[0,8]
    assert np.array_equal(rgba[0,6],rgba[0,7]) and rgba[0,7,3]>0


def test_sampling_handles_north_to_south_axis_and_missing_bounds():
    indices,valid=nearest_indices(np.array([3.,2.,1.]),np.array([2.9,1.1,4.,np.nan]))
    assert indices[:2].tolist()==[0,2]
    assert valid.tolist()==[True,True,False,False]


def test_png_preserves_row_orientation_and_transparency():
    rgba=np.array([[[0,0,255,0]],[[255,0,0,200]]],dtype=np.uint8)
    png=png_rgba(rgba)
    assert png.startswith(b'\x89PNG\r\n\x1a\n')
    offset=8;decoded=b''
    while offset<len(png):
        length=struct.unpack('!I',png[offset:offset+4])[0];kind=png[offset+4:offset+8];data=png[offset+8:offset+8+length]
        if kind==b'IDAT':decoded+=zlib.decompress(data)
        offset+=length+12
    assert decoded==b'\0'+rgba[0].tobytes()+b'\0'+rgba[1].tobytes()


def test_geographic_export_keeps_north_south_and_original_quality():
    import hashlib
    import xarray as xr
    from pyproj import Transformer
    from rain_overlay import from_dataset
    projection=Transformer.from_crs(4326,3857,always_xy=True)
    west,south,east,north=-77.,-12.,-76.9,-11.9
    x0,y0=projection.transform(west,south);x1,y1=projection.transform(east,north)
    ds=xr.Dataset({'RRQPE':(('y','x'),np.array([[0.,2.],[10.,30.]])), 'DQF':(('y','x'),np.array([[0,0],[1,0]]))},coords={'x':[x0,x1],'y':[y1,y0]},attrs={'time_coverage_end':'2026-10-25T00:00:00Z'})
    manifest,png=from_dataset(ds,projection,1.,'real-source-key',[west,south,east,north])
    assert manifest['projection']=='EPSG:3857' and manifest['source_key']=='real-source-key'
    assert manifest['sha256']==hashlib.sha256(png).hexdigest()
    offset=8;raw=b''
    while offset<len(png):
        n=struct.unpack('!I',png[offset:offset+4])[0]
        if png[offset+4:offset+8]==b'IDAT':raw+=zlib.decompress(png[offset+8:offset+8+n])
        offset+=n+12
    h,w=manifest['height'],manifest['width']
    pixels=np.frombuffer(raw,dtype=np.uint8).reshape(h,1+w*4)[:,1:].reshape(h,w,4)
    assert pixels[0,0,3]==0  # Northwest dry, not flipped vertically.
    assert pixels[-1,0].tolist()==[116,125,139,90]  # Southwest degraded data, never dry.
    assert pixels[-1,-1].tolist()==[202,42,151,195]
    with pytest.raises(ValueError):from_dataset(ds,projection,1.,'key',[west,south,east,north],resolution_m=100)


def test_default_raster_scope_contains_all_three_pilot_departments_without_finer_pixels():
    from rain_overlay import BOUNDS
    import gzip,json
    from pathlib import Path
    root=Path(__file__).resolve().parents[1]/'data_static'
    for name in ['lima_channels.json.gz','north_channels.json.gz']:
        rows=json.loads(gzip.decompress((root/name).read_bytes()))
        for _,_,lines in rows:
            for line in lines:
                for lon,lat in line:
                    assert BOUNDS[0] <= lon <= BOUNDS[2]
                    assert BOUNDS[1] <= lat <= BOUNDS[3]
    # Same minimum native sampling (2 km); expanded scope stays under app limits.
    from pyproj import Transformer
    import math
    projection=Transformer.from_crs(4326,3857,always_xy=True)
    x0,y0=projection.transform(*BOUNDS[:2]);x1,y1=projection.transform(*BOUNDS[2:])
    assert max(math.ceil((x1-x0)/2000),math.ceil((y1-y0)/2000)) <= 1024


def test_regional_rain_counts_do_not_mistake_rain_elsewhere_for_northern_rain():
    import xarray as xr
    from pyproj import Transformer
    from rain_overlay import from_dataset, BOUNDS
    projection=Transformer.from_crs(4326,3857,always_xy=True)
    x0,y0=projection.transform(*BOUNDS[:2]);x1,y1=projection.transform(*BOUNDS[2:])
    ds=xr.Dataset({'RRQPE':(('y','x'),np.array([[0.,2.],[0.,2.]])), 'DQF':(('y','x'),np.zeros((2,2)))},coords={'x':[x0,x1],'y':[y1,y0]},attrs={'time_coverage_end':'2026-10-25T00:00:00Z'})
    manifest,_=from_dataset(ds,projection,1.,'synthetic-regional-test')
    counts=manifest['regional_rainy_pixel_counts']
    assert counts['Piura']==counts['Tumbes']==0
    assert counts['Lima']>0
    assert manifest['rainy_pixel_count']>counts['Lima']
