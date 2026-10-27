"""Bounded observed satellite history: at most 12 scans / three hours, never forecasts."""
import hashlib,json,re
from datetime import datetime,timezone
from pathlib import Path
MAX_FRAMES=12
MAX_AGE=10800

def publish(out,cache,now,overlay=None):
 out=Path(out);cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
 if overlay:
  meta,png=overlay
  if meta.get('state')=='AVAILABLE' and len(png)<=2_000_000 and hashlib.sha256(png).hexdigest()==meta.get('sha256') and re.fullmatch(r'rain-[a-f0-9]{16}\.png',meta.get('image','')):
   (cache/meta['image']).write_bytes(png)
   (cache/(meta['sha256']+'.json')).write_text(json.dumps(meta))
 frames={}
 for p in cache.glob('*.json'):
  try:
   m=json.loads(p.read_text());stamp=datetime.fromisoformat(m['scan_time_utc'].replace('Z','+00:00'))
   assert m['state']=='AVAILABLE' and 0<=(now-stamp).total_seconds()<=MAX_AGE
   assert re.fullmatch(r'rain-[a-f0-9]{16}\.png',m['image'])
   png=(cache/m['image']).read_bytes();assert len(png)<=2_000_000 and hashlib.sha256(png).hexdigest()==m['sha256']
   frames[stamp]=(m,png)
  except (ValueError,KeyError,AssertionError,OSError,TypeError):continue
 keep=sorted(frames)[-MAX_FRAMES:];files=set()
 for t in keep:
  m,png=frames[t];(out/m['image']).write_bytes(png);files.update([m['image'],m['sha256']+'.json'])
 for p in cache.iterdir():
  if p.is_file() and p.name not in files:p.unlink()
 for p in out.glob('rain-*.png'):
  if p.name not in files and re.fullmatch(r'rain-[a-f0-9]{16}\.png',p.name):p.unlink()
 data={'version':1,'mode':'OBSERVED','state':'AVAILABLE' if keep else 'UNAVAILABLE','frames':[frames[t][0] for t in keep],'max_age_seconds':MAX_AGE,'context':'Secuencia de estimaciones satelitales observadas. No es pronóstico ni confirmación de inundaciones. Los intervalos pueden ser irregulares.'}
 (out/'rain_timeline.json').write_text(json.dumps(data,separators=(',',':')))
 return data
