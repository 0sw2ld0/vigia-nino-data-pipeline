"""Published ANA units are context, never inferred upstream catchments or exposure."""
import gzip
import hashlib
import json
import math
from pathlib import Path

import shapely
from shapely.geometry import shape, MultiLineString, mapping
from pyproj import Transformer

LIMITATION = 'Unidad hidrográfica publicada por ANA; no es la microcuenca aportante de cada tramo ni un área de inundación. La pertenencia espacial no demuestra conexión aguas arriba.'
SOURCE = 'https://geosnirh.ana.gob.pe/server/rest/services/ws_UnidadesHidro/MapServer/1'


def assign_units(channels, features, minimum_coverage=0.9):
    """Require metric length coverage, retain ambiguity instead of nearest/name guesses."""
    if not 0 < minimum_coverage <= 1:
        raise ValueError('Invalid coverage threshold')
    transform = Transformer.from_crs(4326,32718,always_xy=True)
    geometries = [shape(f['geometry']) for f in features]
    if any(g.is_empty or not g.is_valid or g.geom_type not in ('Polygon','MultiPolygon') for g in geometries):
        raise ValueError('Invalid watershed geometry')
    metric = shapely.transform(geometries,transform.transform,interleaved=False)
    tree = shapely.STRtree(metric)
    result = {}
    for oid, _, paths in channels:
        line = shapely.transform(MultiLineString(paths),transform.transform,interleaved=False)
        if line.length <= 0:
            continue
        candidates = []
        for index in tree.query(line,predicate='intersects'):
            fraction = min(1.0,line.intersection(metric[index]).length/line.length)
            if fraction >= minimum_coverage:
                candidates.append((int(index),fraction))
        if len(candidates) != 1:
            continue
        index,fraction = candidates[0]
        props = features[index]['properties']
        area = props.get('AREA_KM2')
        result[f'IGN-{oid}'] = {'unit_id':f"ANA-{props['CODIGO']}", 'name':props['NOMBRE'],
            'source_name':'Autoridad Nacional del Agua (ANA)', 'source_url':SOURCE,
            'area_km2':area if isinstance(area,(float,int)) and math.isfinite(area) and area > 0 else None,
            'channel_coverage_fraction':round(fraction,6), 'assignment':'SPATIAL_LENGTH_COVERAGE',
            'limitation':LIMITATION}
    return result


def load_catalog(root):
    folder = Path(root)/'data_static/watersheds'
    manifest = json.loads((folder/'manifest.json').read_text())
    payload = (folder/'ana_lima_units.geojson.gz').read_bytes()
    if hashlib.sha256(payload).hexdigest() != manifest['sha256']:
        raise ValueError('Watershed checksum mismatch')
    features = json.loads(gzip.decompress(payload))['features']
    if len(features) != manifest['units'] or len({f['properties']['CODIGO'] for f in features}) != len(features):
        raise ValueError('Duplicate or missing ANA units')
    channels = json.loads(gzip.decompress((Path(root)/'data_static/lima_channels.json.gz').read_bytes()))
    assignments = assign_units(channels,features)
    report = {**manifest,'assigned_segments':len(assignments),'unassigned_segments':len(channels)-len(assignments),
              'assignment':'At least 90% of projected segment length in exactly one official unit; unassigned includes ambiguous and coastal mismatches',
              'calibration_state':'INSUFFICIENT_EVIDENCE'}
    return features,assignments,report


def map_features(features, retrieved_at):
    output = []
    for feature in features:
        props = feature['properties']
        geometry = shape(feature['geometry'])
        parts = list(geometry.geoms) if geometry.geom_type == 'MultiPolygon' else [geometry]
        for index,part in enumerate(parts):
            output.append({'type':'Feature','id':f"ANA-{props['CODIGO']}-{index}",'geometry':mapping(part),
                'properties':{'id':f"ANA-{props['CODIGO']}-{index}",'kind':'BASIN','zone_id':f"ANA-{props['CODIGO']}",
                'name':props['NOMBRE'],'source_name':'Autoridad Nacional del Agua (ANA)', 'source_url':SOURCE,
                'retrieved_at':retrieved_at,'review_status':'REFERENCE','description':LIMITATION}})
    return output
