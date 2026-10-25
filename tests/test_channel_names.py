import gzip
import json
from pathlib import Path

import pytest
from channel_names import crosswalk, load_catalog, UNKNOWN

ROOT=Path(__file__).resolve().parents[1]
PATH=[[[-76.7,-11.92],[-76.7,-11.925]]]


def source(name='Quirio',path=PATH,ident='named500'):
    return dict(id=ident,features=[dict(type='Feature',properties=dict(nombre=name,OBJECTID_1=1),geometry=dict(type='MultiLineString',coordinates=path))])


def test_coincident_named_line_can_name_but_nearby_line_cannot():
    names,_=crosswalk([[1,'',PATH]],[source()])
    assert names['IGN-1']['name']=='Quebrada Quirio'
    assert names['IGN-1']['state']=='CARTOGRAPHIC_MATCH'
    near=[[[-76.6997,-11.92],[-76.6997,-11.925]]]
    names,_=crosswalk([[1,'',PATH]],[source(path=near)])
    assert not names


def test_conflicts_do_not_guess_and_original_names_are_preserved():
    names,conflicts=crosswalk([[1,'',PATH]],[source(),source('Libertad',ident='named100')])
    assert not names and conflicts[0]['reason']=='CONFLICTING_NAMES'
    names,conflicts=crosswalk([[1,'Original',PATH]],[source()])
    assert names['IGN-1']['name']=='Quebrada Original'
    assert names['IGN-1']['state']=='SOURCE_IGN' and not names['IGN-1']['evidence']
    assert len(conflicts)==1


def test_partial_overlap_short_segments_and_hazard_polygons_do_not_name():
    names,_=crosswalk([[1,'',PATH]],[source(path=[[[-76.7,-11.92],[-76.7,-11.921]]])])
    assert not names
    names,_=crosswalk([[1,'',[[[-76.7,-11.92],[-76.7,-11.9201]]]]],[source()])
    assert not names
    bad=source();bad['features'][0]['geometry']=dict(type='Polygon',coordinates=[[[-76.71,-11.91],[-76.69,-11.91],[-76.69,-11.93],[-76.71,-11.91]]])
    with pytest.raises(ValueError):crosswalk([[1,'',PATH]],[bad])
    with pytest.raises(ValueError):crosswalk([[1,'',PATH]],[source()],tolerance_m=100)


def test_bundled_crosswalk_preserves_ids_geometry_and_pending_case():
    data=load_catalog(ROOT);names=data['names']
    rows=json.loads(gzip.decompress((ROOT/'data_static/lima_channels.json.gz').read_bytes()))
    ids={f'IGN-{r[0]}' for r in rows}
    assert len(names)==2454 and set(names)<=ids
    assert sum(n['state']=='CARTOGRAPHIC_MATCH' for n in names.values())==251
    assert 'IGN-13131' not in names
    assert names['IGN-12186']['name']=='Quebrada Huaycoloro'
    assert names['IGN-11834']['name']=='Quebrada Quirio'
    assert all(n['evidence'] and all(e['coverage']>=.98 for e in n['evidence']) for n in names.values() if n['state']=='CARTOGRAPHIC_MATCH')
    # Hazard/ANA documentary sources cannot become name assignments.
    assert {n['source'] for n in names.values()} <= {'ign100','named100','named500'}
