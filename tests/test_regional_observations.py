from datetime import datetime, timezone
from pathlib import Path
import numpy as np
import shapely
from shapely.geometry import Polygon, box
import xarray as xr

import config
import goes
import lima
from test_pipeline import make_area, grid


def test_indexed_rain_matches_scalar_quality_holes_and_nearest_pixel_rules():
    lon, lat = grid()
    rain = np.arange(25,dtype=float).reshape(5,5)
    dqf = np.zeros((5,5),dtype=np.uint8)
    rain[0,0] = np.nan
    dqf[2,2] = 1
    geometries = [box(-77.05,-10.05,-76.55,-9.55),
        Polygon([(-77.05,-10.05),(-76.55,-10.05),(-77.05,-9.55)]),
        Polygon([(-77.05,-10.05),(-76.55,-10.05),(-76.55,-9.55),(-77.05,-9.55)],
            holes=[[(-76.95,-9.95),(-76.65,-9.95),(-76.65,-9.65),(-76.95,-9.65)]]),
        box(-76.91,-9.71,-76.905,-9.705)]
    areas = [make_area(geometry,str(index),lat=-9.708,lon=-76.908) for index,geometry in enumerate(geometries)]
    actual = goes.batch_rain_stats(rain,dqf,lon,lat,areas)
    assert actual == {area.id:goes.area_rain_stats(rain,dqf,lon,lat,area) for area in areas}
    assert all(value is None for value in goes.batch_rain_stats(rain,np.ones_like(dqf),lon,lat,areas).values())
    assert all(value is None for value in goes.batch_rain_stats(rain,dqf,lon*np.nan,lat,areas).values())


def test_indexed_lightning_counts_overlap_without_counting_invalid_coordinates():
    areas = [make_area(box(-77,-12,-76,-11),f'ID-{index}') for index in range(101)]
    lons = np.array([-76.5,-80,np.nan]); lats = np.array([-11.5,-11.5,-11.5])
    counts = goes.count_flashes(lats,lons,areas)
    assert len(counts)==101 and all(value==1 for value in counts.values())
    assert all(value==0 for value in goes.count_flashes(np.array([]),np.array([]),areas).values())


def test_large_regional_run_downloads_and_decodes_one_rrqpe_window(tmp_path,monkeypatch):
    ds = xr.Dataset({'RRQPE':(('y','x'),np.ones((12,12))), 'DQF':(('y','x'),np.zeros((12,12))),
        'goes_imager_projection':xr.DataArray(0,attrs={'perspective_point_height':35786023.,
            'longitude_of_projection_origin':-75.,'sweep_angle_axis':'x',
            'semi_major_axis':6378137.,'semi_minor_axis':6356752.31414})},
        coords={'x':np.linspace(-.012,-.001,12),'y':np.linspace(-.025,-.044,12)},
        attrs={'time_coverage_end':'2026-10-25T03:00:00Z'})
    path=tmp_path/'rain.nc'; ds.to_netcdf(path,engine='h5netcdf')
    class FS:
        calls=0
        def cat(self,key):
            self.calls+=1
            return path.read_bytes()
    fs=FS()
    areas=[make_area(box(-77.1,-12.1,-76.9,-11.9),f'ID-{index}',lat=-12,lon=-77) for index in range(101)]
    monkeypatch.setattr(goes,'area_rain_stats',lambda *args:(_ for _ in ()).throw(AssertionError('Scalar per-area raster reads must not run')))
    result=goes.rain_from_key(fs,'fixture',areas)
    assert fs.calls==1 and len(result.per_area)==101
    assert all(value==goes.RainStats(1.,1.) for value in result.per_area.values())


def test_corridors_cover_all_lima_ids_and_are_not_watersheds_or_flood_extents():
    root=Path(__file__).resolve().parents[1]
    areas=lima.observation_areas(root)
    assert len(areas)==len({a.id for a in areas})==12462
    assert all(a.geometry.is_valid and not a.geometry.is_empty for a in areas)
    assert all(a.threshold_profile=='LIMA_OBSERVATION' for a in areas)


def test_regional_feed_keeps_observation_and_official_advisory_independent():
    root=Path(__file__).resolve().parents[1]
    now=datetime(2026,10,25,3,tzinfo=timezone.utc)
    rain=goes.RainResult(now,'fixture',{'IGN-343130':goes.RainStats(6.,2.)})
    feed,report=lima.build_feed(root,{'processed_at_utc':'2026-10-25T03:00:00Z'}, {},
        {'state':'unavailable'},rain,{'IGN-343130':1})
    zone=next(q for q in feed['quebradas'] if q['id']=='IGN-343130')
    assert zone['status']['level']=='YELLOW' and zone['official_notice'] is None
    assert 'parte alta' not in zone['status']['headline']
    assert zone['observation']['sampling_radius_m']==1000
    assert zone['observation']['rain_accum_72h_mm'] is None
    assert report['rain_segments_with_data']==1
    assert report['rain_levels']['UNKNOWN']==12461
