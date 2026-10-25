import copy
import gzip
import json
from pathlib import Path
import jsonschema
import pytest
import north
import official_warnings as official
from test_official_warnings import NOW, PAGE, fixture

ROOT = Path(__file__).resolve().parents[1]


def test_northern_feed_has_unique_source_segments_and_no_invented_rain():
    base = {'schema_version':'1.0','processed_at_utc':official.iso(NOW),'stale_after_minutes':60,'official_links':[]}
    feed, report = north.build_feed(ROOT, base, {}, {'state':'verified','department_scope':list(official.DEPARTMENTS)})
    jsonschema.Draft202012Validator(json.loads((ROOT/'contract/status_quebradas.schema.json').read_text())).validate(feed)
    rows = feed['quebradas']
    assert len(rows) == len({q['id'] for q in rows}) == 8179
    assert sum('Piura' in q['region'] for q in rows) == 6807
    assert sum('Tumbes' in q['region'] for q in rows) == 1409
    assert sum(q['region']=='Piura / Tumbes' for q in rows) == 37
    assert report['source_scope_verified']
    for q in rows:
        assert q['status']['level'] == 'UNKNOWN'
        assert q['watershed'] is None
        assert all(v is None for k,v in q['observation'].items() if k != 'source')
    source = json.loads(gzip.decompress((ROOT/'data_static/north_channels.json.gz').read_bytes()))
    assert {q['id'] for q in rows} == {f'IGN-{r[0]}' for r in source}


@pytest.mark.parametrize('report', [
    {'state':'verified_snapshot','department_scope':['LIMA']},
    {'state':'failed','department_scope':list(official.DEPARTMENTS)}])
def test_old_lima_copy_or_failed_verification_cannot_mark_north_current(report):
    report['source_retrieved_at_utc']='2026-10-25T00:00:00Z'
    feed, result = north.build_feed(ROOT, {'processed_at_utc':'2026-10-25T00:00:00Z'}, {'IGN-175213':{'level':'YELLOW'}}, report)
    assert feed['official_source_retrieved_at_utc'] is None
    assert not any(q['official_notice'] for q in feed['quebradas'])
    assert not result['source_scope_verified']


def test_northern_sectors_require_explicit_verified_scope(tmp_path):
    data, channels = fixture()
    data['features'][0]['properties']['nombdep']='PIURA'
    metadata=official.parse_metadata(PAGE)
    assert official.assign_notices(metadata,data,channels,NOW)=={}
    assert official.assign_notices(metadata,data,channels,NOW,departments=official.DEPARTMENTS)['TEST']['level']=='ORANGE'
    path=tmp_path/'channels.json';path.write_text(json.dumps(channels))
    snapshot={'retrieved_at_utc':official.iso(NOW),'metadata':{**metadata,'from':official.iso(metadata['from']),'until':official.iso(metadata['until'])},'collection':data}
    with pytest.raises(ValueError,match='outside declared'):
        official.verify_snapshot(snapshot,path,NOW)
    snapshot['department_scope']=list(official.DEPARTMENTS)
    notices, report=official.verify_snapshot(snapshot,path,NOW)
    assert notices['TEST']['level']=='ORANGE'
    assert 'PIURA' in report['department_scope']
    data['features'][0]['properties']['fecha']='2026-10-23Z'
    with pytest.raises(ValueError,match='date'):
        official.verify_snapshot(snapshot,path,NOW)


def test_verified_empty_collection_is_distinct_from_truncated_download():
    metadata=official.parse_metadata(PAGE)
    _, channels=fixture()
    assert official.assign_notices(metadata,{'type':'FeatureCollection','features':[],'totalFeatures':0},channels,NOW,departments=official.DEPARTMENTS)=={}
    for data in [{'features':[],'totalFeatures':0},{'type':'FeatureCollection','features':[]},{'type':'FeatureCollection','features':[],'totalFeatures':1}]:
        with pytest.raises(ValueError):
            official.assign_notices(metadata,data,channels,NOW,departments=official.DEPARTMENTS)
