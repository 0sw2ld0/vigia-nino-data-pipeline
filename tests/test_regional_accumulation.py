from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import json
import requests
import regional_accumulation as regional

NOW = datetime(2026,10,25,3,20,tzinfo=timezone.utc)
END = NOW.replace(minute=0)

def area(ident='a',lat=-12.01,lon=-76.01):
    return SimpleNamespace(id=ident,centroid_lat=lat,centroid_lon=lon)

def item(value=1):
    return {'hourly_units':{'precipitation':'mm'},'hourly':{
        'time':[(END-timedelta(hours=i)).strftime('%Y-%m-%dT%H:%M') for i in reversed(range(72))],
        'precipitation':[value]*72}}

class Client:
    def __init__(self, data=None,fail=False):
        self.data=data
        self.fail=fail
        self.calls=[]
    def get(self,url,params,timeout):
        self.calls.append(params)
        if self.fail:
            raise requests.Timeout('test')
        return SimpleNamespace(raise_for_status=lambda:None,json=lambda:self.data)

def test_complete_window_and_reject_missing_duplicates_bad_values():
    assert regional._sum_window(item(),END)==72
    for bad in [None,-1,float('nan'),True]:
        data=item(); data['hourly']['precipitation'][0]=bad
        assert regional._sum_window(data,END) is None
    data=item(); data['hourly']['time'][0]=data['hourly']['time'][1]
    assert regional._sum_window(data,END) is None
    data=item(); data['hourly']['time'].pop()
    assert regional._sum_window(data,END) is None

def test_shared_sample_not_one_request_per_channel(tmp_path):
    client=Client(item(0))
    result=regional.fetch_regional_accumulation([area(),area('b')],NOW,tmp_path/'c.json',client)
    assert result.values=={'a':0,'b':0}
    assert result.report['sample_count']==1
    assert result.report['locations_requested']==1
    assert client.calls[0]['models']=='gfs_global'
    assert client.calls[0]['start_hour']=='2026-10-22T04:00'
    assert client.calls[0]['end_hour']=='2026-10-25T03:00'

def test_hour_cache_exact_window_not_age(tmp_path):
    path=tmp_path/'c.json'
    regional.fetch_regional_accumulation([area()],NOW,path,Client(item()))
    client=Client(fail=True)
    result=regional.fetch_regional_accumulation([area()],NOW+timedelta(minutes=10),path,client)
    assert result.report['cache_hit']
    assert result.report['retrieved_at_utc']=='2026-10-25T03:20:00Z'
    assert not client.calls
    result=regional.fetch_regional_accumulation([area()],NOW+timedelta(minutes=45),path,client)
    assert not result.report['cache_hit']
    assert result.values['a'] is None
    assert len(client.calls)==1

def test_bad_cache_and_network_failure_are_unknown(tmp_path):
    path=tmp_path/'c.json'; path.write_text('bad json')
    result=regional.fetch_regional_accumulation([area()],NOW,path,Client(fail=True))
    assert result.values=={'a':None}
    assert result.report['state']=='unavailable'
    assert result.report['retrieved_at_utc'] is None

def test_budget_and_partial_response(tmp_path):
    areas=[area(str(i),i*.25,0) for i in range(81)]
    client=Client(item())
    result=regional.fetch_regional_accumulation(areas,NOW,None,client)
    assert result.report['error']=='sample_budget_exceeded'
    assert not client.calls
    client=Client([item()])
    result=regional.fetch_regional_accumulation([area(),area('b',-12.5)],NOW,None,client)
    assert result.values=={'a':None,'b':None}

def test_past_or_future_receipt_not_reused(tmp_path):
    path=tmp_path/'c.json'
    regional.fetch_regional_accumulation([area()],NOW,path,Client(item()))
    cache=json.loads(path.read_text()); cache['retrieved_at_utc']='2026-10-25T04:00:00Z'
    path.write_text(json.dumps(cache))
    result=regional.fetch_regional_accumulation([area()],NOW,path,Client(fail=True))
    assert not result.report['cache_hit']
    assert result.values['a'] is None
