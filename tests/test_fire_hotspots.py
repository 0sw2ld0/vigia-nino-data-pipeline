from datetime import datetime,timezone,timedelta
import numpy as np
import xarray as xr
import pytest
from pyproj import Transformer
from shapely.geometry import box
import fire_hotspots as fire

NOW=datetime(2026,10,25,20,tzinfo=timezone.utc)

def fixture(monkeypatch):
    projection=Transformer.from_crs(4326,3857,always_xy=True)
    x=[projection.transform(lon,-5)[0] for lon in [-81,-80.9,-80.8,-80.7,-80.6,-80.5]]
    y=[projection.transform(-81,lat)[1] for lat in [-4.9,-5.1]]
    masks=np.array([[10,13,14,12,33,100],[15,30,31,11,35,100]])
    dqf=np.array([[0,2,0,0,0,1],[0,0,0,3,0,1]])
    ds=xr.Dataset({'Mask':(('y','x'),masks),'DQF':(('y','x'),dqf)},coords={'x':x,'y':y},attrs={'time_coverage_end':'2026-10-25T19:59:00Z'})
    ds['Mask'].attrs.update(flag_values=list(fire.ACCEPTED),flag_meanings=' '.join(fire.ACCEPTED.values()))
    ds['DQF'].attrs.update(flag_values=[0,1],flag_meanings='good_quality_fire_pixel_qf good_quality_fire_free_land_pixel_qf')
    monkeypatch.setattr(fire.goes,'_geos_transformer',lambda ds:(projection,1.))
    monkeypatch.setattr(fire.goes,'_window',lambda *args:(slice(0,2),slice(0,6)))
    return ds,{'Piura':box(-81.1,-5.2,-80.4,-4.8)}

def test_only_high_quality_high_probability_categories_are_exported(monkeypatch):
    ds,regions=fixture(monkeypatch)
    feed=fire.from_dataset(ds,'source-key',NOW,regions)
    assert feed['state']=='AVAILABLE'
    assert {d['mask'] for d in feed['detections']}=={10,30,31,33}
    assert len({d['id'] for d in feed['detections']})==4
    assert all(d['quality']==0 and d['confirmation']=='UNCONFIRMED' for d in feed['detections'])
    assert feed['regions']['Piura']['cloud_pixel_fraction']>0
    assert all('area' not in d for d in feed['detections'])

def test_clouds_and_empty_scan_never_claim_no_fires(monkeypatch):
    ds,regions=fixture(monkeypatch);ds['DQF'].values[:]=2
    feed=fire.from_dataset(ds,'key',NOW,regions)
    assert feed['detections']==[] and feed['regions']['Piura']['cloud_pixel_fraction']==1
    assert feed['regions']['Piura']['usable_pixel_fraction']==0
    assert 'no significa ausencia' in feed['context']

@pytest.mark.parametrize('offset',[timedelta(hours=2),timedelta(hours=-1)])
def test_old_or_future_scan_is_rejected(monkeypatch,offset):
    ds,regions=fixture(monkeypatch)
    with pytest.raises(ValueError,match='stale or'):
        fire.from_dataset(ds,'key',NOW+offset,regions)

def test_changed_product_semantics_and_uncovered_region_are_rejected(monkeypatch):
    ds,regions=fixture(monkeypatch);ds['Mask'].attrs['flag_meanings']='wrong'
    with pytest.raises(ValueError,match='semantics'):fire.from_dataset(ds,'key',NOW,regions)
    ds,regions=fixture(monkeypatch)
    with pytest.raises(ValueError,match='No thermal pixels'):fire.from_dataset(ds,'key',NOW,{'Tumbes':box(-80,-4,-79,-3)})

def test_pixel_footprints_follow_native_grid_edges(monkeypatch):
    from shapely.geometry import Polygon,Point
    ds,regions=fixture(monkeypatch)
    rows=fire.from_dataset(ds,'key',NOW,regions)['detections']
    for d in rows:
        ring=d['pixel_footprint']
        assert len(ring)==5 and ring[0]==ring[-1]
        assert Polygon(ring).contains(Point(d['lon'],d['lat']))
    first=rows[0]['pixel_footprint']
    assert first[0]==pytest.approx([-81.05,-4.799992],abs=0.0001)
    assert first[2]==pytest.approx([-80.95,-5.000008],abs=0.0001)
