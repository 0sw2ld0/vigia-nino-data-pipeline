import copy
from pathlib import Path
import pytest
from exposure import get_export, validate_feature, context_for_channel

ROOT=Path(__file__).resolve().parents[1]

def test_real_historical_source_keeps_event_and_parts():
    data=get_export(ROOT)
    assert len(data['features'])==6
    assert data['coverage']['state']=='PARTIAL_HISTORICAL'
    ids={i for f in data['features'] for i in f['properties']['channel_ids']}
    assert {'IGN-13127','IGN-13129'} <= ids
    assert all(f['properties']['event_date']=='2015-03-23' for f in data['features'])
    assert all(f['properties']['evidence_kind']=='HISTORICAL_FOOTPRINT' for f in data['features'])

@pytest.mark.parametrize('field,value',[
    ('source_url','http://example.com'),('scenario',''),
    ('evidence_kind','SENAMHI_SECTOR'),('review_scope','SCIENTIFICALLY_VALIDATED'),
    ('event_date','not-a-date')])
def test_rejects_unsubstantiated_exposure(field,value):
    feature=copy.deepcopy(get_export(ROOT)['features'][0])
    feature['properties'][field]=value
    with pytest.raises(ValueError): validate_feature(feature)

def test_no_coverage_is_not_safe_and_no_name_matching():
    result=context_for_channel({'type':'LineString','coordinates':[[-75,-12],[-75.01,-12.01]]},get_export(ROOT))
    assert result['state']=='NO_DOCUMENTED_COVERAGE'
    assert result['predicts_current_extent'] is False

def test_rejects_invalid_ring():
    feature=copy.deepcopy(get_export(ROOT)['features'][0])
    feature['geometry']['coordinates'][0]=[[-76,-12],[-76.1,-12],[-76,-12.1]]
    with pytest.raises(ValueError):validate_feature(feature)
