import gzip
import json
from pathlib import Path
from datetime import datetime, timezone

import jsonschema
import lima
import official_warnings as official
from test_official_warnings import fixture, PAGE, NOW

ROOT = Path(__file__).resolve().parents[1]


def test_regional_model_is_context_and_preserves_zero_and_provenance():
    base = {'processed_at_utc':official.iso(NOW),'quebradas':[]}
    feed, report = lima.build_feed(ROOT,base,{}, {'state':'unavailable'},
        accumulation={'IGN-343130':0.0}, accumulation_context='GFS modeled, not observed',
        accumulation_retrieved_at='2026-10-24T23:00:00Z')
    zone = next(q for q in feed['quebradas'] if q['id']=='IGN-343130')
    assert zone['observation']['rain_accum_72h_mm']==0.0
    assert zone['observation']['rain_accum_72h_retrieved_at_utc']=='2026-10-24T23:00:00Z'
    assert zone['status']['level']=='UNKNOWN'
    missing = next(q for q in feed['quebradas'] if q['id']!='IGN-343130')
    assert missing['observation']['rain_accum_72h_retrieved_at_utc'] is None
    assert report['accumulation_segments_with_data']==1


def test_every_lima_segment_is_in_feed_without_invented_rain_or_safe_status():
    data, _ = fixture()
    channels = official.load_channels(ROOT/'data_static/monitored_channels.geojson')
    rows = json.loads(gzip.decompress((ROOT/'data_static/lima_channels.json.gz').read_bytes()))
    assert len(rows) == len({r[0] for r in rows}) == 12462
    assert {r[0] for r in rows} == set(json.loads((ROOT/'data_static/lima-index.json').read_text())['ids'])
    assert any(f['properties']['zone_id']=='IGN-343130' for f in channels['features'])
    base = {'schema_version':'1.0','processed_at_utc':official.iso(NOW),'stale_after_minutes':60,'official_links':[]}
    feed, report = lima.build_feed(ROOT,base,{}, {'state':'unavailable'})
    assert report['segments'] == 12462 and report['assigned_segments'] == 0
    assert feed['official_source_retrieved_at_utc'] is None
    assert all(q['status']['level']=='UNKNOWN' and q['observation']['rain_rate_max_mmh'] is None for q in feed['quebradas'])
    jsonschema.Draft202012Validator(json.loads((ROOT/'contract/status_quebradas.schema.json').read_text())).validate(feed)


def test_regional_feed_preserves_source_age_and_prioritizes_official_notice():
    data, channels = fixture()
    notice = official.assign_notices(official.parse_metadata(PAGE), data, channels, NOW)['TEST']
    base = {'processed_at_utc':official.iso(NOW),'quebradas':[]}
    feed, report = lima.build_feed(ROOT,base,{'IGN-343130':notice},
        {'state':'verified_collector','source_retrieved_at_utc':'2026-10-24T23:00:00Z'})
    assert feed['quebradas'][0]['id']=='IGN-343130'
    assert feed['quebradas'][0]['official_notice']['level']=='ORANGE'
    assert feed['quebradas'][0]['status']['level']=='UNKNOWN'
    assert feed['official_source_retrieved_at_utc']=='2026-10-24T23:00:00Z'
    assert report['levels']=={'ORANGE':1}


def test_regional_selection_respects_boundary_holes_and_keeps_cross_border_segments(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location('export_lima',ROOT.parent/'tools/cartography/export_lima.py')
    # Public export intentionally excludes private repository tooling.
    if not spec.origin or not Path(spec.origin).exists():
        import pytest
        pytest.skip('Private cartography exporter is tested in the source workspace')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    from shapely.geometry import Polygon
    boundary = Polygon([(0,0),(1,0),(1,1),(0,1)], holes=[[(.4,.4),(.6,.4),(.6,.6),(.4,.6)]])
    (tmp_path/'national-index.json').write_text(json.dumps({'tiles':[{'id':'test','x':0,'y':0}]}))
    rows = [[1,'inside',[[[.1,.1],[.2,.2]]]], [2,'hole',[[[.45,.45],[.55,.55]]]],
        [3,'outside',[[[2,2],[3,3]]]], [4,'cross-border',[[[-1,.1],[.2,.1]]]]]
    (tmp_path/'test.json.gz').write_bytes(gzip.compress(json.dumps(rows).encode()))
    selected,_,_ = module.select_rows(tmp_path,boundary)
    assert [r[0] for r in selected]==[1,4]
