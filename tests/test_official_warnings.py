from datetime import datetime, timezone
import copy
import pytest
import official_warnings as o

PAGE='N&deg;297 - 2026 Fecha de inicio: Sábado 24 octubre 2026 - 13:00 horas Duraci&oacute;n: 24 hrs'
NOW=datetime(2026,10,25,0,tzinfo=timezone.utc)

def fixture():
    polygon={'type':'Polygon','coordinates':[[[-77.1,-12.1],[-76.9,-12.1],[-76.9,-11.9],[-77.1,-11.9],[-77.1,-12.1]]]}
    channel={'type':'Feature','properties':{'zone_id':'TEST'},'geometry':{'type':'LineString','coordinates':[[-77,-12],[-76.95,-12]]}}
    collection={'type':'FeatureCollection','features':[{'geometry':polygon,'properties':{'fecha':'2026-10-24Z','nombdep':'LIMA','nombprov':'TEST','nombdist':'TEST','nivel':'Nivel 3'}}],'totalFeatures':1}
    return collection,{'features':[channel]}

def test_validity_uses_lima_timezone_and_expiry_is_exclusive():
    m=o.parse_metadata(PAGE)
    assert o.iso(m['from'])=='2026-10-24T18:00:00Z'
    assert o.iso(m['until'])=='2026-10-25T18:00:00Z'
    d,c=fixture()
    assert o.assign_notices(m,d,c,NOW)['TEST']['level']=='ORANGE'
    assert not o.assign_notices(m,d,c,m['until'])
    assert not o.assign_notices(m,d,c,datetime(2026,10,24,17,tzinfo=timezone.utc))

def test_old_geometry_or_incomplete_download_is_rejected():
    m=o.parse_metadata(PAGE); d,c=fixture()
    d['features'][0]['properties']['fecha']='2026-10-23Z'
    with pytest.raises(ValueError):o.assign_notices(m,d,c,NOW)
    d,c=fixture();d['totalFeatures']=2
    with pytest.raises(ValueError):o.assign_notices(m,d,c,NOW)

def test_same_district_does_not_assign_notice_to_distant_or_tiny_overlap():
    m=o.parse_metadata(PAGE);d,c=fixture()
    c['features'][0]['geometry']['coordinates']=[[-76,-12],[-75.9,-12]]
    assert not o.assign_notices(m,d,c,NOW)
    c['features'][0]['geometry']['coordinates']=[[-77,-12],[-76,-12]]
    assert not o.assign_notices(m,d,c,NOW)

def test_missing_or_ambiguous_dates_are_not_guessed():
    with pytest.raises(ValueError):o.parse_metadata('N°297 - 2026')
    with pytest.raises(ValueError):o.parse_metadata(PAGE.replace('24 hrs','240 hrs'))


def test_archived_numbers_in_page_header_cannot_override_current_advisory():
    page='N°044-2020 <article><h1 class="aviso-vigente">Aviso actual</h1>'+PAGE+'</article>'
    assert o.parse_metadata(page)['id']=='SENAMHI-AQ-2026-297'


def test_verified_snapshot_does_not_extend_validity_or_verification_time(tmp_path):
    import gzip, json
    d,c=fixture(); channels=tmp_path/'channels.json';channels.write_text(json.dumps(c))
    cache=tmp_path/'cache.json.gz'
    payload={'retrieved_at_utc':'2026-10-25T00:00:00Z','metadata':{'id':'TEST','number':'297','from':'2026-10-24T18:00:00Z','until':'2026-10-25T18:00:00Z'},'collection':d}
    cache.write_bytes(gzip.compress(json.dumps(payload).encode()))
    notices,report=o.read_verified_snapshot(cache,channels,NOW)
    assert notices['TEST']['level']=='ORANGE' and report['state']=='verified_snapshot'
    assert report['source_retrieved_at_utc']=='2026-10-25T00:00:00Z'
    for later in [datetime(2026,10,25,7,tzinfo=timezone.utc),datetime(2026,10,25,18,tzinfo=timezone.utc)]:
        with pytest.raises(ValueError):o.read_verified_snapshot(cache,channels,later)
