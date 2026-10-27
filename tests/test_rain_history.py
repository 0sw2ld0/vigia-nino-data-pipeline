import hashlib,json
from datetime import datetime,timezone,timedelta
from rain_history import publish

def test_bounded_observed_history_and_expiry(tmp_path):
 out=tmp_path/'out';out.mkdir();cache=tmp_path/'cache';now=datetime(2026,10,27,tzinfo=timezone.utc)
 for i in range(15):
  png=f'png{i}'.encode();h=hashlib.sha256(png).hexdigest()
  m={'state':'AVAILABLE','scan_time_utc':(now+timedelta(minutes=i*10)).isoformat(),'image':f'rain-{h[:16]}.png','sha256':h}
  result=publish(out,cache,now+timedelta(minutes=i*10),(m,png))
 assert len(result['frames'])==12 and result['mode']=='OBSERVED'
 assert len(list(cache.iterdir()))==24
 result=publish(out,cache,now+timedelta(days=1))
 assert not result['frames'] and not list(cache.iterdir())

def test_invalid_future_or_corrupt_images_not_published(tmp_path):
 out=tmp_path/'out';out.mkdir();cache=tmp_path/'cache';now=datetime.now(timezone.utc);h=hashlib.sha256(b'png').hexdigest()
 m={'state':'AVAILABLE','scan_time_utc':(now+timedelta(hours=1)).isoformat(),'image':f'rain-{h[:16]}.png','sha256':h}
 assert not publish(out,cache,now,(m,b'png'))['frames']
 m['scan_time_utc']=now.isoformat();m['image']='../x.png'
 assert not publish(out,cache,now,(m,b'png'))['frames']

def test_bootstrap_keeps_original_scan_time(tmp_path,monkeypatch):
 import base64,gzip,requests
 from types import SimpleNamespace
 from rain_history import bootstrap
 now=datetime(2026,10,27,tzinfo=timezone.utc);stamp=(now-timedelta(minutes=20)).isoformat();png=b'png';h=hashlib.sha256(png).hexdigest()
 m={'state':'AVAILABLE','scan_time_utc':stamp,'image':f'rain-{h[:16]}.png','sha256':h}
 payload=gzip.compress(json.dumps([dict(metadata=m,png=base64.b64encode(png).decode())]).encode())
 monkeypatch.setattr(requests,'get',lambda *a,**kw:SimpleNamespace(content=payload,raise_for_status=lambda:None))
 cache=tmp_path/'cache';out=tmp_path/'out';out.mkdir();bootstrap(cache,'https://example.org/seed')
 assert publish(out,cache,now)['frames'][0]['scan_time_utc']==stamp
