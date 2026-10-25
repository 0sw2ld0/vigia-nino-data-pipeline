from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from shapely.geometry import Polygon,Point
import air_temperature as t
NOW=datetime(2026,10,25,19,45,tzinfo=timezone.utc)

def test_bounded_land_grid():
    cells=t.grid(Path(__file__).resolve().parents[1])
    assert 153<len(cells)<=4000
    assert len({c['region'] for c in cells})==25
    for c in cells:
        assert any(Polygon(p[0],p[1:]).covers(Point(c['lon'],c['lat'])) or Polygon(p[0],p[1:]).distance(Point(c['lon'],c['lat']))<0.00002 for p in c['polygons'])

def test_cache_same_hour_only_and_partial_failure_stays_missing(tmp_path,monkeypatch):
    cells=[dict(id='Lima-1',region='Lima',lat=-12,lon=-77,polygons=[])]
    monkeypatch.setattr(t,'grid',lambda root:[dict(c) for c in cells])
    calls=[]
    def fetch_model(rows,valid,session):
        calls.append(valid)
        if valid.hour!=19:raise ValueError('Missing model hour')
        for row in rows:row['celsius']=22
        return {'source_key':'test-model'}
    monkeypatch.setattr(t.gfs_temperature,'fetch',fetch_model)
    cache=tmp_path/'temperature.json'
    f=t.fetch(tmp_path,NOW,cache);assert f['cells'][0]['celsius']==22
    assert t.fetch(tmp_path,NOW,cache)==f and len(calls)==1
    f=t.fetch(tmp_path,NOW.replace(hour=20),cache)
    assert len(calls)==2 and f['state']=='UNAVAILABLE' and f['cells'][0]['celsius'] is None
