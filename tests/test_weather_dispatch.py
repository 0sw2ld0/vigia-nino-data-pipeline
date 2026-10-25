import json
from types import SimpleNamespace
from unittest.mock import Mock
from collect_senamhi import request_weather_update

def test_existing_weather_run_does_not_create_a_queue():
    run=Mock(return_value=SimpleNamespace(stdout=json.dumps([{'status':'in_progress'}])))
    assert request_weather_update('/test/gh',run)=={'state':'already_running'}
    assert run.call_count==1

def test_finished_weather_runs_allow_one_dispatch():
    run=Mock(return_value=SimpleNamespace(stdout=json.dumps([{'status':'completed'}])))
    assert request_weather_update('/test/gh',run)=={'state':'requested'}
    assert run.call_count==2
    assert run.call_args.args[0]==['/test/gh','workflow','run','nowcast.yml','--repo','0sw2ld0/vigia-nino-data-pipeline']

def test_dispatch_failure_does_not_erase_a_successful_senamhi_collection():
    run=Mock(side_effect=TimeoutError('offline'))
    assert request_weather_update('/test/gh',run)=={'state':'failed','error':'offline'}
