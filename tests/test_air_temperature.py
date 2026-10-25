from datetime import datetime,timezone
from pathlib import Path
from types import SimpleNamespace
import json
import pytest
from shapely.geometry import Polygon,Point
import air_temperature as t
NOW=datetime(2026,10,25,19,45,tzinfo=timezone.utc)

def item(value=22):return {'hourly_units':{'temperature_2m':'°C'},'hourly':{'time':['2026-10-25T19:00'],'temperature_2m':[value]}}

@pytest.mark.parametrize('value',[None,True,float('nan'),float('inf'),-100,100,'22'])
def test_invalid_values_never_become_temperature(value):
    assert t.parse_item(item(value),NOW.replace(minute=0)) is None

def test_time_units_and_zero():
    assert t.parse_item(item(0),NOW.replace(minute=0))==0
    assert t.parse_item(item(),NOW.replace(hour=20,minute=0)) is None
    bad=item();bad['hourly_units']['temperature_2m']='°F';assert t.parse_item(bad,NOW.replace(minute=0)) is None

def test_bounded_land_grid():
    cells=t.grid(Path(__file__).resolve().parents[1])
    assert len(cells)==153
    assert {c['region'] for c in cells}=={'Lima','Piura','Tumbes'}
    for c in cells:
        assert any(Polygon(p[0],p[1:]).covers(Point(c['lon'],c['lat'])) or Polygon(p[0],p[1:]).distance(Point(c['lon'],c['lat']))<0.00002 for p in c['polygons'])

def test_cache_same_hour_only_and_partial_failure_stays_missing(tmp_path,monkeypatch):
    cells=[dict(id='Lima-1',region='Lima',lat=-12,lon=-77,polygons=[])]
    monkeypatch.setattr(t,'grid',lambda root:[dict(c) for c in cells])
    calls=[]
    def get(*args,**kwargs):
        calls.append(kwargs);return SimpleNamespace(raise_for_status=lambda:None,json=lambda:[item()])
    cache=tmp_path/'temperature.json';client=SimpleNamespace(get=get)
    f=t.fetch(tmp_path,NOW,cache,client);assert f['cells'][0]['celsius']==22
    assert t.fetch(tmp_path,NOW,cache,client)==f and len(calls)==1
    f=t.fetch(tmp_path,NOW.replace(hour=20),cache,client)
    assert len(calls)==2 and f['state']=='UNAVAILABLE' and f['cells'][0]['celsius'] is None
