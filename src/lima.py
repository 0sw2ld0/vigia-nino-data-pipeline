"""Official-advisory coverage for all mapped Lima segments; no inferred rainfall."""
import gzip
import json
from collections import Counter


def build_feed(root, base_feed, notices, notice_report):
    rows = json.loads(gzip.decompress((root/'data_static/lima_channels.json.gz').read_bytes()))
    zones = []
    for oid, name, paths in rows:
        points = [p for line in paths for p in line]
        lon, lat = points[len(points)//2]
        zone_id = f'IGN-{oid}'
        notice = notices.get(zone_id)
        zones.append({'id':zone_id, 'name':f'{name or "Tramo sin nombre"} · {oid}',
            'region':'Lima', 'district':notice['geographic_scope'] if notice else 'Catálogo IGN · Lima',
            'centroid':{'lat':lat,'lon':lon},
            'observation':{'scan_time_utc':None, 'source':'Sin área de lluvia validada para este tramo',
                'rain_rate_max_mmh':None,'rain_rate_mean_mmh':None,'lightning_flashes_15m':None,'rain_accum_72h_mm':None},
            'status':{'level':'UNKNOWN','headline':'Sin lectura de lluvia asociada',
                'advice':'Consulta el aviso oficial. La falta de asociación no indica ausencia de peligro.'},
            'official_notice':notice})
    zones.sort(key=lambda q:({'RED':0,'ORANGE':1,'YELLOW':2}.get((q['official_notice'] or {}).get('level'),3),q['name']))
    retrieved = notice_report.get('source_retrieved_at_utc')
    if notice_report['state']=='verified':
        retrieved = base_feed['processed_at_utc']
    result = {**base_feed,'quebradas':zones,'official_source_retrieved_at_utc':retrieved,
        'global_notice':{'severity':'INFO','title':'Avisos oficiales · toda Lima',
            'body':'Cobertura del departamento de Lima, incluida Lima Metropolitana. Cada registro es un tramo IGN, no una quebrada única. Sin lluvia asociada ni áreas de afectación validadas. Un tramo sin aviso asociado no equivale a zona segura.','url':None}}
    counts = Counter(q['official_notice']['level'] for q in zones if q['official_notice'])
    return result, {'segments':len(zones),'assigned_segments':sum(counts.values()),'levels':dict(counts),
        'rain_coverage':'Only the two separately configured pilot areas have rainfall observations',
        'official_source_retrieved_at_utc':retrieved}
