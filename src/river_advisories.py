"""Official station-scoped hydrological advisories; never infer a river-wide extent."""
import gzip,json,re,unicodedata
from datetime import datetime,timedelta,timezone
from html.parser import HTMLParser
from urllib.parse import parse_qs,urlparse
import requests
PAGE='https://www.senamhi.gob.pe/?p=avisos-hidrologicos'
POINTS='https://www.senamhi.gob.pe/mapas/mapa-aviso-hidro/include/ajaxIdesepWFSAvisos.php'
TZ=timezone(timedelta(hours=-5))
LEVELS={'AMARILLO':'YELLOW','NARANJA':'ORANGE','ROJO':'RED'}
def iso(value):return value.astimezone(timezone.utc).isoformat().replace('+00:00','Z')
def key(value):return ''.join(c for c in unicodedata.normalize('NFD',value).lower() if not unicodedata.combining(c)).strip()
class Table(HTMLParser):
 def __init__(self):super().__init__();self.row=None;self.cell=None;self.href=None;self.rows=[]
 def handle_starttag(self,t,attrs):
  if t=='tr':self.row=[];self.href=None
  if t=='td' and self.row is not None:self.cell=[]
  if t=='a' and self.row is not None:
   h=dict(attrs).get('href','')
   if 'avisos-detalle-hidrologicos' in h:self.href=h
 def handle_data(self,text):
  if self.cell is not None:self.cell.append(text)
 def handle_endtag(self,t):
  if t=='td' and self.cell is not None:
   if self.row is not None:self.row.append(' '.join(' '.join(self.cell).split()))
   self.cell=None
  if t=='tr' and self.row is not None:
   if self.href:self.rows.append((self.href,self.row))
   self.row=None

def parse(page,collection,now):
 if collection.get('type')!='FeatureCollection' or collection.get('totalFeatures')!=len(collection.get('features',[])):raise ValueError('Incomplete station coverage')
 table=Table();table.feed(page);active={}
 for href,row in table.rows:
  if len(row)!=6 or '(vigente)' not in row[1]:continue
  q=parse_qs(urlparse(href).query)
  ident=q.get('ca',[''])[0];station=q.get('ce',[''])[0]
  if not ident.isdigit() or not re.fullmatch('[A-Za-z0-9]+',station):raise ValueError('Unverified notice link')
  start=datetime.strptime(row[2],'%Y-%m-%d').replace(tzinfo=TZ);end=datetime.strptime(row[3],'%Y-%m-%d').replace(tzinfo=TZ)
  if end<=start or row[5] not in LEVELS:raise ValueError('Invalid advisory dates or level')
  if ident in active:raise ValueError('Duplicate active table notice')
  active[ident]=(row,station,start,end)
 notices=[];seen=set()
 for f in collection['features']:
  p=f['properties'];ident=str(p['id_aviso'])
  if ident not in active:raise ValueError('Station notice not verified by active table')
  row,station,start,end=active[ident]
  if key(p['titulo'])!=key(row[0]) or station!=f['id'].split('.')[-1]:raise ValueError('Station identity mismatch')
  if p['color_text']!=row[5] or p['nivel']!={'AMARILLO':1,'NARANJA':2,'ROJO':3}[row[5]]:raise ValueError('Conflicting source level')
  if f['geometry']['type']!='Point':raise ValueError('Expected station point, not a river extent')
  lon,lat=f['geometry']['coordinates']
  if not (-82<=lon<=-68 and -19<=lat<=1) or abs(lon-float(p['longitud']))>0.001 or abs(lat-float(p['latitud']))>0.001:raise ValueError('Conflicting station coordinates')
  if ident in seen:raise ValueError('Duplicate notice')
  seen.add(ident)
  match=re.search(r'R[IÍ]O\s+(.+?)\s*[-–]\s*ESTACI[OÓ]N',p['titulo'],re.I)
  if not match:raise ValueError('Unverifiable river name')
  river=match[1].strip();description=p['peligro_nivel'];semantic=key(p['titulo']+' '+description)
  hazard='LOW_WATER' if any(x in semantic for x in ['descenso','descendente','vaciante']) else 'HIGH_WATER' if any(x in semantic for x in ['incremento','crecida','desborde','inundacion']) else 'UNSPECIFIED'
  notices.append(dict(id='SENAMHI-H-'+ident,river=river,river_key=key(river),station=p['nom_estacion'],station_code=station,department=p['nom_departamento'].title(),lat=lat,lon=lon,level=LEVELS[row[5]],hazard=hazard,title=p['titulo'],description=description,advice=p['recomendacion_nivel'],issued_on=row[2],valid_until_date=row[3],valid_from_utc=iso(start),valid_until_utc=iso(end),source_url=f'https://www.senamhi.gob.pe/?p=avisos-detalle-hidrologicos&ca={ident}&ce={station}',spatial_scope='STATION_ONLY'))
 if seen!=set(active):raise ValueError('Missing active station advisories')
 if not table.rows:raise ValueError('Advisory table unavailable')
 return dict(version=1,state='VERIFIED',source_retrieved_at_utc=iso(now),source_url=PAGE,notices=notices,context='Avisos verificados por estación. La fecha final se aplica de forma conservadora desde las 00:00 de Perú; el listado no publica hora de vencimiento. No son lecturas actuales de caudal ni áreas de desborde.')
def collect(now,get=requests.get):
 a=get(PAGE,timeout=25);a.raise_for_status();b=get(POINTS,timeout=25);b.raise_for_status()
 if len(a.content)>2_000_000 or len(b.content)>1_000_000:raise ValueError('Source too large')
 return parse(a.text,b.json(),now)
def empty():return dict(version=1,state='UNAVAILABLE',source_retrieved_at_utc=None,source_url=PAGE,notices=[],context='Avisos hidrológicos sin verificar. No indica ausencia de peligro.')
def fetch(now,collector_url=None):
 if collector_url:
  try:
   r=requests.get(collector_url,timeout=15);r.raise_for_status();data=json.loads(gzip.decompress(r.content))
   stamp=datetime.fromisoformat(data['source_retrieved_at_utc'].replace('Z','+00:00'))
   if data['version']==1 and data['state']=='VERIFIED' and 0<=(now-stamp).total_seconds()<=21600:return data
  except Exception:pass
 try:return collect(now)
 except Exception:return empty()
