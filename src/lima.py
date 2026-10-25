"""Official advisories and experimental satellite observations near Lima channels."""
import channel_names
import gzip
import json
from collections import Counter
from datetime import timezone
import shapely
from shapely.geometry import MultiLineString
from pyproj import Transformer
from areas import Area
import config
import risk

RADIUS_M = 1000


def observation_areas(root):
    rows = json.loads(gzip.decompress((root/'data_static/lima_channels.json.gz').read_bytes()))
    lines = [MultiLineString(row[2]) for row in rows]
    forward = Transformer.from_crs(4326,32718,always_xy=True)
    inverse = Transformer.from_crs(32718,4326,always_xy=True)
    projected = shapely.transform(lines,forward.transform,interleaved=False)
    corridors = shapely.buffer(projected,RADIUS_M,quad_segs=4)
    corridors = shapely.transform(corridors,inverse.transform,interleaved=False)
    areas = []
    for row,geometry in zip(rows,corridors):
        if geometry.is_empty or not geometry.is_valid:
            raise ValueError(f'Invalid observation corridor for IGN-{row[0]}')
        centroid = geometry.centroid
        areas.append(Area(f'IGN-{row[0]}',row[1] or 'Tramo sin nombre','Lima','Catálogo IGN',
            'LIMA_OBSERVATION',centroid.y,centroid.x,geometry))
    return areas


def build_feed(root, base_feed, notices, notice_report, rain=None, lightning=None,
               accumulation=None, accumulation_context=None, accumulation_retrieved_at=None,
               watershed_context=None):
    rows = json.loads(gzip.decompress((root/'data_static/lima_channels.json.gz').read_bytes()))
    names = channel_names.load_catalog(root)["names"]
    zones = []
    for oid, name, paths in rows:
        points = [p for line in paths for p in line]
        lon, lat = points[len(points)//2]
        zone_id = f'IGN-{oid}'
        notice = notices.get(zone_id)
        stats = rain.per_area.get(zone_id) if rain else None
        flashes = (lightning or {}).get(zone_id)
        status = risk.classify(stats.max_mmh if stats else None,flashes,None,
            config.THRESHOLD_PROFILES['LIMA_OBSERVATION']).as_dict()
        for key in ['headline','advice']:
            status[key] = status[key].replace('en la parte alta','en el entorno observado del cauce')
        zones.append({'id':zone_id, 'name':names.get(zone_id,{}).get('name',channel_names.UNKNOWN),
            'region':'Lima', 'district':notice['geographic_scope'] if notice else 'Catálogo IGN · Lima',
            'centroid':{'lat':lat,'lon':lon},
            'observation':{'scan_time_utc':rain.scan_time.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if stats and rain.scan_time else None,
                'source':'NOAA GOES-19 · observación experimental en franja de 1 km del tramo IGN',
                'rain_rate_max_mmh':stats.max_mmh if stats else None,'rain_rate_mean_mmh':stats.mean_mmh if stats else None,
                'lightning_flashes_15m':flashes,'rain_accum_72h_mm':(accumulation or {}).get(zone_id),
                'rain_accum_72h_context':accumulation_context,
                'rain_accum_72h_retrieved_at_utc':accumulation_retrieved_at if (accumulation or {}).get(zone_id) is not None else None,
                'sampling_method':'REFERENCE_CHANNEL_CORRIDOR','sampling_radius_m':RADIUS_M},
            'status':status,
            'official_notice':notice,
            'watershed':(watershed_context or {}).get(zone_id)})
    zones.sort(key=lambda q:({'RED':0,'ORANGE':1,'YELLOW':2}.get((q['official_notice'] or {}).get('level'),3),q['name']))
    retrieved = notice_report.get('source_retrieved_at_utc')
    if notice_report['state']=='verified':
        retrieved = base_feed['processed_at_utc']
    result = {**base_feed,'quebradas':zones,'official_source_retrieved_at_utc':retrieved,
        'global_notice':{'severity':'INFO','title':'Piloto de lluvia y avisos · toda Lima',
            'body':'Observación experimental de lluvia y rayos en franjas de 1 km alrededor de 12.462 tramos IGN. No representan cuencas altas ni áreas de afectación. Umbrales sin calibración operativa suficiente para Lima. '
                +(accumulation_context or 'Acumulado modelado regional no disponible en esta consulta.')
                +' La pertenencia a una unidad hidrográfica publicada no identifica por sí sola la microcuenca aportante del tramo. Los avisos oficiales son independientes.','url':None}}
    counts = Counter(q['official_notice']['level'] for q in zones if q['official_notice'])
    return result, {'segments':len(zones),'assigned_segments':sum(counts.values()),'levels':dict(counts),
        'rain_coverage':'Experimental 1000 m observation corridors around every selected IGN segment; not watersheds or flood extents',
        'sampling_radius_m':RADIUS_M,
        'rain_segments_with_data':sum(q['observation']['rain_rate_max_mmh'] is not None for q in zones),
        'lightning_segments_with_data':sum(q['observation']['lightning_flashes_15m'] is not None for q in zones),
        'rain_levels':dict(Counter(q['status']['level'] for q in zones)),
        'accumulation_segments_with_data':sum(q['observation']['rain_accum_72h_mm'] is not None for q in zones),
        'watershed_assigned_segments':sum(q['watershed'] is not None for q in zones),
        'official_source_retrieved_at_utc':retrieved}
