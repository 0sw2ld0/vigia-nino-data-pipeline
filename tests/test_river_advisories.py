import copy,gzip,json
from pathlib import Path
from datetime import datetime,timedelta,timezone
import pytest
import river_advisories as r
FIX=Path(__file__).parent/'fixtures'
NOW=datetime(2026,10,26,17,tzinfo=timezone.utc)
def inputs():return (FIX/'river-advisories-table.html').read_text(),json.loads((FIX/'river-advisories-stations.json').read_text())
def test_verified_advisories_preserve_low_water_and_station_only_scope():
 page,stations=inputs();f=r.parse(page,stations,NOW)
 assert len(f['notices'])==3 and all(n['hazard']=='LOW_WATER' and n['spatial_scope']=='STATION_ONLY' for n in f['notices'])
 napo=next(n for n in f['notices'] if n['river_key']=='napo')
 assert napo['level']=='ORANGE' and napo['valid_until_utc']=='2026-10-28T05:00:00Z'
 assert f['source_retrieved_at_utc']=='2026-10-26T17:00:00Z'
@pytest.mark.parametrize('mutation',['level','identity','geometry','truncation','duplicate'])
def test_incomplete_or_conflicting_sources_do_not_become_verified(mutation):
 page,d=inputs()
 if mutation=='level':d['features'][0]['properties']['color_text']='ROJO'
 elif mutation=='identity':d['features'][0]['id']='aviso_hidro_pub.000'
 elif mutation=='geometry':d['features'][0]['geometry']['coordinates'][0]+=1
 elif mutation=='truncation':d['features'].pop()
 else:d['features'].append(copy.deepcopy(d['features'][0]));d['totalFeatures']+=1
 with pytest.raises(ValueError):r.parse(page,d,NOW)
def test_missing_table_or_conflicting_dates_fail_closed():
 page,d=inputs()
 with pytest.raises(ValueError):r.parse('',d,NOW)
 with pytest.raises(ValueError):r.parse(page.replace('2026-10-29','2026-10-25'),d,NOW)
def test_collector_does_not_refresh_original_retrieval_time(monkeypatch):
 page,d=inputs();snapshot=r.parse(page,d,NOW-timedelta(hours=1))
 class Response:
  content=gzip.compress(json.dumps(snapshot).encode())
  def raise_for_status(self):pass
 monkeypatch.setattr(r.requests,'get',lambda *a,**k:Response())
 assert r.fetch(NOW,'https://example.org/collector.gz')['source_retrieved_at_utc']==snapshot['source_retrieved_at_utc']
 monkeypatch.setattr(r,'collect',lambda *a:(_ for _ in ()).throw(ValueError('Unavailable')))
 assert r.fetch(NOW+timedelta(hours=7),'https://example.org/collector.gz')['state']=='UNAVAILABLE'
