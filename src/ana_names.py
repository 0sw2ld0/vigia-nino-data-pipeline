"""Auditable review of ANA bank markers against existing IGN segments.

Bank markers are not a hydrographic centreline or a flood prediction. Only an
explicitly reviewed segment/source pair can augment the name catalogue. All
other spatial relationships stay in the review report.
"""
import re
from collections import defaultdict
from math import dist

import shapely
from pyproj import Transformer
from shapely.geometry import MultiLineString, Polygon

from channel_names import normalized


def bank_groups(features):
    project = Transformer.from_crs(4326, 32718, always_xy=True)
    grouped = defaultdict(list)
    for f in features:
        p = f['properties']
        if p['TIPCUA'] != 'Quebrada' or p['ZONUTM'] != 18:
            raise ValueError('Expected ANA quebrada markers in UTM zone 18S')
        actual = project.transform(*f['geometry']['coordinates'])
        if dist(actual, (p['ESTE'], p['NORTE'])) > 2:
            raise ValueError('ANA geometry and published UTM coordinates disagree')
        grouped[(p['NOMCUA'], p['RESAPROB'])].append(f)
    result = []
    for (name, resolution), rows in sorted(grouped.items()):
        banks = []
        for side in ('Derecha', 'Izquierda'):
            points = [r['properties'] for r in rows if r['properties']['MARGEN'] == side]
            # Unclear numbering or repeated markers are retained for review.
            codes = [re.findall(r'\d+', p['CODIGO']) for p in points]
            if len(points) < 2 or any(len(c) != 1 for c in codes):
                break
            numbered = sorted(zip((int(c[0]) for c in codes), points), key=lambda x: x[0])
            if len({n for n, _ in numbered}) != len(points):
                break
            banks.append([(p['ESTE'], p['NORTE']) for _, p in numbered])
        polygon = None
        if len(banks) == 2:
            a, b = banks
            if dist(a[0], b[0]) + dist(a[-1], b[-1]) > dist(a[0], b[-1]) + dist(a[-1], b[0]):
                b = b[::-1]
            candidate = Polygon(a + b[::-1])
            # Never repair a crossed bank ring into a plausible naming area.
            if candidate.is_valid and not candidate.is_empty:
                polygon = candidate
        result.append(dict(name=name, resolution=resolution,
                           district=rows[0]['properties']['NOMDIST'],
                           marker_ids=sorted(r['properties']['OBJECTID'] for r in rows),
                           marker_coordinates={str(r['properties']['OBJECTID']): [r['properties']['ESTE'], r['properties']['NORTE']] for r in rows},
                           marker_count=len(rows), polygon=polygon))
    return result


def review_banks(channels, features, reviews, existing):
    project = Transformer.from_crs(4326, 32718, always_xy=True)
    lines = [shapely.transform(MultiLineString(r[2]), project.transform, interleaved=False) for r in channels]
    tree = shapely.STRtree(lines)
    additions, report = {}, []
    approved = {(r['ana_name'], r['resolution'], r['id']): r for r in reviews}
    used = set()
    for group in bank_groups(features):
        polygon = group.pop('polygon')
        candidates = []
        if polygon is not None:
            buffered = polygon.buffer(20)
            for i in tree.query(buffered, predicate='intersects'):
                line = lines[i]
                if line.length < 100:
                    continue
                raw = line.intersection(polygon).length / line.length
                coverage = line.intersection(buffered).length / line.length
                if coverage < .05:
                    continue
                ident = f'IGN-{channels[i][0]}'
                metrics = dict(id=ident, original_name=channels[i][1],
                               length_m=round(line.length, 1), coverage=round(coverage, 6),
                               bank_interior_coverage=round(raw, 6))
                candidates.append(metrics)
                key = (group['name'], group['resolution'], ident)
                if key not in approved:
                    continue
                review = approved[key]
                if raw < .90 or coverage < .95:
                    raise ValueError('Reviewed ANA association no longer meets its spatial checks')
                if group['marker_ids'] != review['marker_ids']:
                    raise ValueError('ANA markers changed since documentary review')
                if group['marker_coordinates'] != review['marker_coordinates'] or group['district'] != review['district']:
                    raise ValueError('ANA marker coordinates or district changed since documentary review')
                prior = existing.get(ident)
                if prior and normalized(prior['name']) != normalized(review['display_name']):
                    raise ValueError('Documentary review conflicts with an existing name')
                if ident in additions:
                    raise ValueError('More than one reviewed name for an IGN segment')
                if prior is None:
                    additions[ident] = dict(name=review['display_name'], state='DOCUMENTED_BANK_REVIEW',
                        source=review['source'], district=group['district'], evidence=[dict(source=review['source'],
                            source_ids=group['marker_ids'], ana_name=group['name'],
                            resolution=group['resolution'], **metrics,
                            tolerance_m=20, review='PLAN_AND_COORDINATE_TABLE_REVIEW')])
                used.add(key)
        group['bank_geometry_state'] = 'VALID_ORDERED_BANKS' if polygon is not None else 'AMBIGUOUS_MARKERS_NOT_USED'
        group['candidates'] = sorted(candidates, key=lambda c: -c['coverage'])
        group['state'] = 'REVIEWED_ASSOCIATION' if any((group['name'], group['resolution'], c['id']) in used for c in candidates) else 'DOCUMENTED_NOT_ASSIGNED'
        report.append(group)
    if used != set(approved):
        raise ValueError('A reviewed ANA pair is missing from the source snapshot')
    return additions, report
