"""Conservative name crosswalk: coincident lines can name; hazard polygons cannot."""
import gzip
import hashlib
import json
import re
import unicodedata
from pathlib import Path

import shapely
from shapely.geometry import MultiLineString, shape
from pyproj import Transformer

UNKNOWN = 'Cauce · nombre por identificar'


def normalized(value):
    value = unicodedata.normalize('NFKD', value or '')
    value = ''.join(c for c in value if not unicodedata.combining(c)).upper().strip()
    value = re.sub(r'^(QUEBRADA|QDA\.?|Q\.)\s+', '', value)
    return re.sub(r'\s+', ' ', value)


def display_name(value):
    name = re.sub(r'^(Quebrada|Qda\.?|Q\.)\s+', '', value.strip(), flags=re.I)
    return 'Quebrada ' + (name.title() if name.isupper() else name)


def crosswalk(channels, sources, tolerance_m=2, minimum_coverage=.98, minimum_length_m=100):
    """No nearest assignment, topology propagation, polygon-to-name or original overwrite.

    The tolerance absorbs coordinate rounding, NOT nominal 1:500k map accuracy.
    An exact-looking alignment still indicates cartographic association, not field verification.
    """
    if not 0 < tolerance_m <= 2 or not .98 <= minimum_coverage <= 1 or minimum_length_m < 100:
        raise ValueError('Unsafe matching parameters')
    transform = Transformer.from_crs(4326, 32718, always_xy=True)
    project = lambda g: shapely.transform(g, transform.transform, interleaved=False)
    records = []
    for source in sources:
        for f in source['features']:
            name = (f['properties'].get('nombre') or '').strip()
            if not name:
                continue
            g = shape(f['geometry'])
            if g.geom_type not in ('LineString', 'MultiLineString') or not g.is_valid or g.is_empty:
                raise ValueError('Only valid named lines can assign names')
            records.append((source['id'], str(f['properties'].get('OBJECTID_1', f.get('id', ''))), name, project(g).buffer(tolerance_m)))
    buffers = [r[3] for r in records]
    tree = shapely.STRtree(buffers)
    result, rejected = {}, []
    for oid, original, paths in channels:
        line = project(MultiLineString(paths))
        hits = []
        if line.length >= minimum_length_m:
            for i in tree.query(line, predicate='intersects'):
                coverage = line.intersection(buffers[i]).length / line.length
                if coverage >= minimum_coverage:
                    source, source_id, name, _ = records[i]
                    hits.append(dict(source=source, source_id=source_id, name=name, coverage=round(min(coverage,1),6)))
        names = {normalized(h['name']) for h in hits}
        if len(names) > 1 or (original.strip() and any(n != normalized(original) for n in names)):
            rejected.append(dict(id=f'IGN-{oid}', original=original, reason='CONFLICTING_NAMES', candidates=hits))
            hits = []
        if original.strip():
            result[f'IGN-{oid}'] = dict(name=display_name(original), state='SOURCE_IGN', source='ign100', evidence=hits)
        elif len(names) == 1:
            best = max(hits, key=lambda h:h['coverage'])
            result[f'IGN-{oid}'] = dict(name=display_name(best['name']), state='CARTOGRAPHIC_MATCH', source=best['source'], evidence=hits)
    return result, rejected


def load_catalog(root):
    folder = Path(root)/'data_static/names'
    payload = (folder/'lima-name-crosswalk.json').read_bytes()
    manifest = json.loads((folder/'manifest.json').read_text())
    if hashlib.sha256(payload).hexdigest() != manifest['crosswalk_sha256']:
        raise ValueError('Name catalog checksum mismatch')
    data = json.loads(payload)
    if len(data['names']) != manifest['named_segments']:
        raise ValueError('Incomplete name crosswalk')
    return data
