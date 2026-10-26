from datetime import datetime,timezone,timedelta
import json
import pytest
import stations
import layer_health


def test_chart_never_turns_missing_into_zero_or_assumes_timezone():
    text="categories: ['2026/10/25 - 18','2026/10/25 - 19',],series: [{name: 'Precipitaci\\u00F3n horaria (mm/hora)',data:[0,null,]}]"
    r=stations.parse_chart(text)
    assert r['rain_hour_mm'] is None and r['observed_at_utc'] is None
    assert r['time_basis']=='NOT_DECLARED' and r['series'][0]['rain_hour_mm']==0
    with pytest.raises(ValueError):stations.parse_chart(text.replace('[0,null,]','[0,]'))
    with pytest.raises(ValueError):stations.parse_chart(text.replace('[0,null,]','[0,-999,]'))


def test_catalog_only_meteorological_public_links_and_bounded_spread():
    rows=[dict(nom=f'Station {i}',ico='M',cod=f'{i:06}',lat=-12+i/1000,lon=-77,estado='AUTOMATICA',cate='EMA') for i in range(120)]
    rows.append(dict(rows[0],ico='H',cod='999999'))
    rows.append(dict(rows[0]))
    parsed=stations.parse_catalog('var PruebaTest = '+json.dumps(rows)+';')
    assert len(parsed)==120
    selected=stations.select_charts(parsed)
    assert len(selected)==32 and len({r['id'] for r in selected})==32
    assert all(r['source_url'].startswith(stations.CHART_URL+'?') for r in selected)


def test_freshness_uses_source_time_not_download_time_and_handles_sleep_gap():
    now=datetime(2026,10,26,0,tzinfo=timezone.utc)
    assert layer_health.status(stations.stamp(now-timedelta(minutes=59)),now,60)['state']=='RECENT'
    assert layer_health.status(stations.stamp(now-timedelta(minutes=61)),now,60)['state']=='STALE'
    assert layer_health.status(stations.stamp(now+timedelta(minutes=6)),now,60)['state']=='UNAVAILABLE'
    assert layer_health.status(None,now,60)['state']=='UNAVAILABLE'
    health=layer_health.build(now,None,stations.stamp(now-timedelta(hours=1)),None,None,None)
    assert health['layers']['temperature']['state']=='RECENT'
    assert health['layers']['rain']['state']=='UNAVAILABLE'


def test_station_copy_does_not_rebase_cached_retrieval(tmp_path):
    now=datetime(2026,10,26,0,tzinfo=timezone.utc);cache=tmp_path/'latest.json'
    feed={'retrieved_at_utc':stations.stamp(now-timedelta(minutes=45)),'stations':[]}
    cache.write_text(json.dumps(feed))
    def forbidden(*a,**kw):raise AssertionError('cache still valid')
    assert stations.collect(now,cache,get=forbidden)==feed


def test_local_monitor_recomputes_published_age_after_sleep():
    import collect_senamhi
    now=datetime(2026,10,26,0,tzinfo=timezone.utc)
    class Response:
        def raise_for_status(self):pass
        def json(self):return {'layers':{'rain':{'state':'RECENT','source_at_utc':stations.stamp(now-timedelta(hours=3)),'stale_after_minutes':60}}}
    result=collect_senamhi.audit_publication(now,get=lambda *a,**k:Response())
    assert result['rain']['state']=='STALE'
    assert result['rain']['age_minutes']==180
