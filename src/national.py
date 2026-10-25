"""Regional official-advisory feeds; local app cartography remains the channel catalogue."""
import gzip,json
from collections import Counter
from pathlib import Path

def build_feeds(root,base,notices,report):
    catalog=json.loads((root/'data_static/departments.json').read_text())
    rows=json.loads(gzip.decompress((root/'data_static/peru_channels.json.gz').read_bytes()))
    selected={row[0]:row for row in rows if f'IGN-{row[0]}' in notices}
    scope=set(report.get('department_scope',[]))
    verified=report.get('state') in ('verified','verified_snapshot','verified_collector')
    result={};coverage={}
    for d in catalog['departments']:
        name,key=d['name'],d['key']
        valid=verified and name.upper() in scope
        ids=set(json.loads((root/f'data_static/{key}-index.json').read_text())['ids'])
        zones=[]
        if valid:
            for oid in sorted(ids & selected.keys()):
                _,label,paths=selected[oid];coords=[p for line in paths for p in line];lon,lat=coords[len(coords)//2]
                if not (-18.6<=lat<=.1 and -81.6<=lon<=-68.4):continue
                zones.append(dict(id=f'IGN-{oid}',name='Quebrada '+label if label.strip() else 'Cauce · nombre por identificar',region=name,district='Distrito por verificar · catálogo IGN',centroid=dict(lat=lat,lon=lon),observation=dict(scan_time_utc=None,source='Sin métricas por tramo; consulte capas regionales',rain_rate_max_mmh=None,rain_rate_mean_mmh=None,lightning_flashes_15m=None,rain_accum_72h_mm=None),status=dict(level='UNKNOWN',headline='Consultar aviso oficial',advice='La asociación espacial no confirma activación ni desborde.'),official_notice=notices[f'IGN-{oid}'],watershed=None))
        retrieved=report.get('source_retrieved_at_utc') or (base['processed_at_utc'] if report.get('state')=='verified' else None)
        result[key]={**base,'quebradas':zones,'official_source_retrieved_at_utc':retrieved if valid else None,'global_notice':dict(severity='INFO',title=f'Cobertura nacional · {name}',body='Este archivo contiene solo los tramos con aviso oficial asociado; la cartografía completa se guarda localmente. Lluvia, temperatura y calor se consultan en capas separadas. Sin aviso asociado no significa zona segura. Acumulados por tramo, cuencas detalladas y áreas de afectación no incorporados fuera de los estudios disponibles.',url=None)}
        coverage[key]=dict(segments=d['segmentCount'],assigned_segments=len(zones),source_scope_verified=valid,levels=dict(Counter(q['official_notice']['level'] for q in zones)))
    return result,coverage
