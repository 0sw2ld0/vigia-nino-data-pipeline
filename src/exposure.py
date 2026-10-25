"""Publish documented historical footprints, independently of current warning levels.

REVIEWED means source/geometry metadata review, never scientific certification.
No geometry is inferred from a channel, a warning polygon or a place name.
"""
import gzip
import hashlib
import json
from datetime import date
from urllib.parse import urlparse
from shapely.geometry import shape, MultiLineString, mapping

SOURCE_LAYER = 'https://sig.cenepred.gob.pe/sigrid/rest/services/CARTOGRAFIA_DE_PELIGROS/MapServer/5020200'


def validate_feature(feature):
    props = feature['properties']
    required = ('zone_id','name','source_name','source_url','retrieved_at',
                'study_date','scenario','reviewed_by','reviewed_at','description')
    if any(not isinstance(props.get(k), str) or not props[k].strip() for k in required):
        raise ValueError('Missing exposure provenance or scenario')
    if props.get('kind') != 'EXPOSURE' or props.get('review_status') != 'REVIEWED':
        raise ValueError('Exposure must have documented metadata review')
    if props.get('evidence_kind') not in ('HISTORICAL_FOOTPRINT','STUDY_SCENARIO'):
        raise ValueError('Warning sectors, buffers and susceptibility are not footprints')
    if props.get('review_scope') != 'SOURCE_AND_GEOMETRY_METADATA':
        raise ValueError('Explicit review scope required')
    for key in ('study_date','retrieved_at','reviewed_at'):
        date.fromisoformat(props[key])
    url = urlparse(props['source_url'])
    if url.scheme != 'https' or not url.hostname:
        raise ValueError('HTTPS source required')
    if props['evidence_kind'] == 'HISTORICAL_FOOTPRINT':
        date.fromisoformat(props['event_date'])
        if 'históric' not in props['description'].lower():
            raise ValueError('Historical footprint needs visible historical label')
    geometry = shape(feature['geometry'])
    if geometry.geom_type != 'Polygon' or geometry.is_empty or not geometry.is_valid:
        raise ValueError('Exposure must be a valid nonempty Polygon')
    left,bottom,right,top = geometry.bounds
    if not (-82 <= left <= right <= -68 and -19 <= bottom <= top <= 1):
        raise ValueError('Exposure outside Peru')
    for ring in feature['geometry']['coordinates']:
        if len(ring) < 4 or ring[0] != ring[-1]:
            raise ValueError('Unclosed exposure ring')
    if not isinstance(props.get('channel_ids'),list) or any(not isinstance(x,str) for x in props['channel_ids']):
        raise ValueError('channel_ids must be explicit identifiers')
    return feature


def get_export(root):
    """Return reviewed historical Quirio footprint, split without simplifying rings."""
    source = root/'data_static/exposure/quirio_2015_source.geojson'
    raw = source.read_bytes()
    provenance = json.loads((source.parent/'provenance.json').read_text())
    if hashlib.sha256(raw).hexdigest() != provenance['source_sha256']:
        raise ValueError('Exposure source hash differs from reviewed extraction')
    features = json.loads(raw)['features']
    if len(features) != 1:
        raise ValueError('Unexpected source feature count')
    original = features[0]
    p = original['properties']
    if p.get('objectid_1') != 1818 or p.get('descrip') != 'Flujo del 23-03-15' or p.get('nom_lugar') != 'Quebrada Quirio':
        raise ValueError('Source does not match reviewed event')
    geometry = shape(original['geometry'])
    if not geometry.is_valid or geometry.geom_type not in ('Polygon','MultiPolygon'):
        raise ValueError('Invalid source footprint; do not repair silently')
    rows = json.loads(gzip.decompress((root/'data_static/lima_channels.json.gz').read_bytes()))
    channels = [(f'IGN-{r[0]}', MultiLineString(r[2])) for r in rows]
    curated = json.loads((root/'data_static/monitored_channels.geojson').read_text())['features']
    channels += [(f['properties']['zone_id'],shape(f['geometry'])) for f in curated if f['properties'].get('kind')=='CHANNEL']
    parts = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
    output=[]
    # Splitting a multipart source must not hide its disconnected components.
    # IDs relate to the entire original historical footprint, not each fragment.
    ids=sorted({key for key,line in channels if geometry.intersects(line)})
    for index,part in enumerate(parts):
        feature={'type':'Feature','id':f'SIGRID-5020200-1818-{index+1}',
          'geometry':mapping(part),'properties':{
            'zone_id':'STUDY-INGEMMET-A6680-QUIRIO','channel_ids':ids,
            'association_method':'SOURCE_FOOTPRINT_INTERSECTION','kind':'EXPOSURE','name':'Quirio · huella histórica de huaico (23/03/2015)',
            'source_name':'INGEMMET · Informe técnico A6680, vía SIGRID',
            'source_url':p['url'].replace('http://','https://',1),
            'source_layer_url':SOURCE_LAYER,'source_object_id':1818,
            'source_sha256':hashlib.sha256(raw).hexdigest(),
            'retrieved_at':'2026-10-25','study_date':'2015-04-02',
            'event_date':'2015-03-23','evidence_kind':'HISTORICAL_FOOTPRINT',
            'scenario':'Flujo de detritos observado el 23/03/2015; no predice un desborde actual.',
            'review_status':'REVIEWED','review_scope':'SOURCE_AND_GEOMETRY_METADATA',
            'reviewed_by':'Vigía · revisión de procedencia y geometría, sin validación científica',
            'reviewed_at':'2026-10-25','scale':None,
            'description':'Huella histórica cartografiada de flujo de detritos de marzo de 2015. '
            'Cobertura parcial de Quirio; no delimita afectación futura, zonas seguras, ni viviendas afectadas hoy. '
            'Se revisaron fuente, evento y geometría; no constituye validación científica. Escala no declarada en la capa.'}}
        # Convert tuples from shapely.mapping to canonical JSON lists before validation.
        output.append(validate_feature(json.loads(json.dumps(feature))))
    return {'type':'FeatureCollection','features':output,
            'coverage':{'state':'PARTIAL_HISTORICAL','study_count':1,'source_feature_count':1,
              'message':'Solo huella histórica documentada de Quirio (23/03/2015). Sin estudio publicado en esta app no significa zona segura.'}}


def context_for_channel(geometry, collection):
    """Spatial association is contact, not probability, upstream routing or risk."""
    channel = shape(geometry) if isinstance(geometry,dict) else geometry
    matches=[f['id'] for f in collection['features'] if channel.intersects(shape(f['geometry']))]
    return {'state':'HISTORICAL_CONTEXT' if matches else 'NO_DOCUMENTED_COVERAGE',
            'feature_ids':matches,'association_method':'GEOMETRIC_INTERSECTION',
            'predicts_current_extent':False}
