import copy

import pytest
from pyproj import Transformer

from ana_names import review_banks


def fixture():
    reverse = Transformer.from_crs(32718, 4326, always_xy=True)
    features = []
    for side, x, code in [('Derecha', 306000, 'HD'), ('Izquierda', 306020, 'HI')]:
        for number, y in [(1, 8674000), (2, 8674500)]:
            features.append(dict(type='Feature', geometry=dict(type='Point', coordinates=reverse.transform(x, y)),
                properties=dict(TIPCUA='Quebrada', ZONUTM=18, ESTE=x, NORTE=y,
                    NOMCUA='Example 01', RESAPROB='RD example', NOMDIST='Chaclacayo',
                    MARGEN=side, CODIGO=f'{code}-{number}', OBJECTID=len(features)+1)))
    paths = [[reverse.transform(306010, y) for y in (8674050, 8674450)]]
    rows = [[1, '', paths]]
    reviews = [dict(ana_name='Example 01', resolution='RD example', id='IGN-1',
                    marker_ids=[1, 2, 3, 4], display_name='Quebrada Example', source='reviewed',
                    district='Chaclacayo', marker_coordinates={str(f['properties']['OBJECTID']): [f['properties']['ESTE'], f['properties']['NORTE']] for f in features})]
    return rows, features, reviews


def test_bank_containment_alone_cannot_name_even_a_perfect_match():
    rows, features, _ = fixture()
    names, report = review_banks(rows, features, [], {})
    assert not names
    assert report[0]['candidates'][0]['coverage'] == 1
    assert report[0]['state'] == 'DOCUMENTED_NOT_ASSIGNED'


def test_reviewed_pair_has_provenance_and_never_propagates_to_neighbour():
    rows, features, reviews = fixture()
    rows.append([2, '', copy.deepcopy(rows[0][2])])
    names, _ = review_banks(rows, features, reviews, {})
    assert set(names) == {'IGN-1'}
    assert names['IGN-1']['state'] == 'DOCUMENTED_BANK_REVIEW'
    assert names['IGN-1']['evidence'][0]['source_ids'] == [1, 2, 3, 4]
    with pytest.raises(ValueError, match='conflicts'):
        review_banks(rows, features, reviews, {'IGN-1': dict(name='Different')})


def test_changed_markers_geometry_and_partial_overlap_invalidate_review():
    rows, features, reviews = fixture()
    changed = copy.deepcopy(features)
    changed[0]['properties']['OBJECTID'] = 999
    with pytest.raises(ValueError, match='markers changed'):
        review_banks(rows, changed, reviews, {})
    changed = copy.deepcopy(features)
    changed[0]['properties']['ESTE'] += 1000
    with pytest.raises(ValueError, match='coordinates disagree'):
        review_banks(rows, changed, reviews, {})
    reverse = Transformer.from_crs(32718, 4326, always_xy=True)
    rows[0][2] = [[reverse.transform(306010, y) for y in (8674050, 8675500)]]
    with pytest.raises(ValueError, match='spatial checks'):
        review_banks(rows, features, reviews, {})


def test_ambiguous_bank_order_is_not_repaired_into_a_naming_area():
    rows, features, reviews = fixture()
    features[1]['properties']['CODIGO'] = 'HD-1'
    names, report = review_banks(rows, features, [], {})
    assert not names and report[0]['bank_geometry_state'] == 'AMBIGUOUS_MARKERS_NOT_USED'
    with pytest.raises(ValueError, match='missing'):
        review_banks(rows, features, reviews, {})
