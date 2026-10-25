import gzip,json
from datetime import datetime,timezone
from pathlib import Path
import national
import official_warnings as warnings
from gfs_temperature import temperature_range
import pytest

def test_gfs_download_selects_only_two_metre_temperature():
    idx='1:0:d=2026102518:TMP:surface:2 hour fcst:\n2:1234:d=2026102518:TMP:2 m above ground:2 hour fcst:\n3:5678:d=2026102518:RH:2 m above ground:2 hour fcst:\n'
    assert temperature_range(idx)==(1234,5677)
    with pytest.raises(ValueError):temperature_range(idx.replace('2 m above ground','surface'))
    with pytest.raises(ValueError):temperature_range(idx.replace('5678','999999999'))

def test_every_department_has_official_boundary_and_local_catalogue():
    root=Path(__file__).resolve().parents[1]/'data_static'
    catalog=json.loads((root/'departments.json').read_text())['departments']
    assert len(catalog)==25 and {c['code'] for c in catalog}=={f'{i:02}' for i in range(1,26)}
    assert set(c['name'].upper() for c in catalog)==set(warnings.DEPARTMENTS)
    for c in catalog:
        index=json.loads((root/(c['key']+'-index.json')).read_text())
        assert len(set(index['ids']))==index['segmentCount']==c['segmentCount']

def test_old_partial_snapshot_cannot_verify_another_department(tmp_path):
    static=tmp_path/'data_static';static.mkdir()
    (static/'departments.json').write_text(json.dumps({'departments':[{'name':'Cusco','key':'cusco','segmentCount':1}]}))
    (static/'cusco-index.json').write_text(json.dumps({'ids':[1]}))
    (static/'peru_channels.json.gz').write_bytes(gzip.compress(json.dumps([[1,'TEST',[[[-72,-13],[-72.01,-13.01]]]]]).encode()))
    feeds,report=national.build_feeds(tmp_path,{'processed_at_utc':'2026-10-25T20:00:00Z'},{'IGN-1':{'level':'RED'}},{'state':'verified_snapshot','department_scope':['LIMA']})
    assert feeds['cusco']['official_source_retrieved_at_utc'] is None
    assert feeds['cusco']['quebradas']==[] and not report['cusco']['source_scope_verified']

def test_gfs_decoder_checks_height_validity_units_and_grid_orientation():
    import eccodes as ec
    import numpy as np
    from gfs_temperature import decode_message
    h=ec.codes_grib_new_from_samples('regular_ll_sfc_grib2')
    try:
        for key,value in {'shortName':'2t','Ni':1440,'Nj':721,'latitudeOfFirstGridPointInDegrees':90,'longitudeOfFirstGridPointInDegrees':0,'latitudeOfLastGridPointInDegrees':-90,'longitudeOfLastGridPointInDegrees':359.75,'iDirectionIncrementInDegrees':.25,'jDirectionIncrementInDegrees':.25,'dataDate':20261025,'dataTime':1200,'forecastTime':8}.items():ec.codes_set(h,key,value)
        ec.codes_set_values(h,np.repeat(np.arange(721)*.01+273.15,1440))
        payload=ec.codes_get_message(h)
        cells=[{'lat':-13.0,'lon':-72.0}]
        decode_message(payload,datetime(2026,10,25,20,tzinfo=timezone.utc),cells)
        assert cells[0]['celsius']==4.1
        with pytest.raises(ValueError,match='validity'):decode_message(payload,datetime(2026,10,25,21,tzinfo=timezone.utc),cells)
        ec.codes_set(h,'level',10)
        with pytest.raises(ValueError,match='field or grid'):decode_message(ec.codes_get_message(h),datetime(2026,10,25,20,tzinfo=timezone.utc),cells)
    finally:ec.codes_release(h)

def test_country_overview_deduplicates_segments_without_claiming_incidents(tmp_path):
    static=tmp_path/'data_static';static.mkdir()
    (static/'departments.json').write_text(json.dumps({'departments':[{'name':'Lima','key':'lima'},{'name':'Cusco','key':'cusco'}]}))
    notice={'id':'A1','level':'ORANGE','geographic_scope':'SECTOR','valid_from_utc':'2026-10-25T00:00:00Z','valid_until_utc':'2026-10-26T00:00:00Z','url':'https://www.senamhi.gob.pe/'}
    feed={'processed_at_utc':'2026-10-25T20:00:00Z','official_source_retrieved_at_utc':'2026-10-25T19:00:00Z','quebradas':[{'official_notice':notice}]*100}
    result=national.build_overview(tmp_path,{'lima':feed,'cusco':feed},{'lima':{'source_scope_verified':True},'cusco':{'source_scope_verified':False}})
    assert result['regions'][0]['associated_segments']==100
    assert result['regions'][0]['notices']==[notice]
    assert result['regions'][1]['notices']==[] and result['regions'][1]['source_retrieved_at_utc'] is None
