"""Northern pilot: verified SENAMHI advisories, without invented satellite observations."""
import gzip
import json
from collections import Counter
from channel_names import display_name, UNKNOWN


def build_feed(root, base, notices, notice_report):
    rows = json.loads(gzip.decompress((root/'data_static/north_channels.json.gz').read_bytes()))
    memberships = {}
    for region in ('Piura', 'Tumbes'):
        index = json.loads((root/f'data_static/{region.lower()}-index.json').read_text())
        for oid in index['ids']:
            memberships.setdefault(oid, []).append(region)
    verified = notice_report.get('state') in ('verified','verified_snapshot','verified_collector') and {'PIURA','TUMBES'} <= set(notice_report.get('department_scope', []))
    retrieved = notice_report.get('source_retrieved_at_utc') if verified else None
    if verified and notice_report.get('state') == 'verified':
        retrieved = base['processed_at_utc']
    zones = []
    for oid, name, paths in rows:
        ident = f'IGN-{oid}'
        points = [p for line in paths for p in line]
        lon, lat = points[len(points)//2]
        zones.append(dict(id=ident, name=display_name(name) if name.strip() else UNKNOWN,
            region=' / '.join(memberships[oid]), district='Catálogo IGN · distrito por verificar',
            centroid=dict(lat=lat, lon=lon),
            observation=dict(scan_time_utc=None, source='Piloto norte · lluvia por tramo no incorporada',
                rain_rate_max_mmh=None, rain_rate_mean_mmh=None,
                lightning_flashes_15m=None, rain_accum_72h_mm=None),
            status=dict(level='UNKNOWN', headline='Lluvia por tramo no incorporada en este piloto',
                advice='Consulta el aviso oficial del tramo. La falta de un aviso asociado no indica ausencia de peligro.'),
            official_notice=notices.get(ident) if verified else None, watershed=None))
    zones.sort(key=lambda q: ({'RED':0,'ORANGE':1,'YELLOW':2}.get((q['official_notice'] or {}).get('level'),3),q['name']))
    feed = {**base, 'quebradas':zones, 'official_source_retrieved_at_utc':retrieved,
        'global_notice':dict(severity='INFO',title='Piloto norte · Piura y Tumbes',
            body='Cartografía local y avisos oficiales por asociación espacial. El boletín describe sectores, no activaciones confirmadas de cada quebrada. La capa de lluvia satelital regional está disponible en el mapa. Las métricas de lluvia por tramo, rayos, acumulados, cuencas y áreas de afectación todavía no están incorporadas para el norte. Sin aviso asociado no significa zona segura.',url=None)}
    return feed, dict(segments=len(zones), assigned_segments=sum(q['official_notice'] is not None for q in zones),
        levels=dict(Counter(q['official_notice']['level'] for q in zones if q['official_notice'])),
        department_scope=notice_report.get('department_scope', []), source_scope_verified=verified,
        official_source_retrieved_at_utc=retrieved)
