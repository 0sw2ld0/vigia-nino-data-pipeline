from copy import deepcopy
from pathlib import Path
import json
import pytest
from historical_events import load_inventory, summarize

ROOT = Path(__file__).resolve().parents[1]


def test_verified_documentary_inventory_preserves_uncertainty_and_independence():
    data = load_inventory(ROOT)
    report = summarize(data)
    assert report['confirmed_event_records']==11
    assert report['reported_occurrence_time_records']==8
    assert report['date_only_records']==2
    assert report['rain_activation_time_records']==1
    assert report['conservative_episode_groups']==5
    assert report['reviewed_evaluation_windows']==report['reviewed_negative_windows']==0
    assert all(e['evaluation_split']=='UNASSIGNED' and not e['verified_channel_ids'] for e in data['events'])
    jointly = next(e for e in data['events'] if 'JITA-CONDORAY' in e['event_id'])
    assert len(jointly['watercourse_names'])==2  # one record, not independent positives


@pytest.mark.parametrize('mutation', ['duplicate','timezone','future','promoted','report_time'])
def test_bad_evidence_cannot_enter_calibration(tmp_path, mutation):
    data = deepcopy(load_inventory(ROOT))
    event = data['events'][2]
    if mutation=='duplicate': data['events'].append(deepcopy(event))
    if mutation=='timezone': event['event_time_local']='2023-03-12T16:30:00'
    if mutation=='future': event['event_date_local']='2029-03-12'; event['event_time_local']='2029-03-12T16:30:00-05:00'
    if mutation=='promoted': event['verified_channel_ids']=['IGN-11684']
    if mutation=='report_time': event['time_semantics']='REPORT_TIME'
    (tmp_path/'backtest').mkdir()
    (tmp_path/'backtest/lima_events.json').write_text(json.dumps(data))
    with pytest.raises(ValueError): load_inventory(tmp_path)
