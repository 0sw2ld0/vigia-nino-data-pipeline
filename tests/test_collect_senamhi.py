import gzip
import json

import pytest
import collect_senamhi as collector
import official_warnings as official
from test_official_warnings import NOW, PAGE, fixture


class Response:
    def __init__(self, data):
        self.content = data.encode() if isinstance(data, str) else gzip.compress(json.dumps(data).encode())
        self.data = data

    def raise_for_status(self):
        pass

    def json(self):
        return self.data


def test_collector_checks_dates_before_publishing(monkeypatch, tmp_path):
    data, channels = fixture()
    path = tmp_path/'channels.json'
    path.write_text(json.dumps(channels))
    responses = iter([Response(PAGE), Response(data)])
    monkeypatch.setattr(collector.requests, 'get', lambda *a, **kw: next(responses))
    snapshot = collector.collect(NOW, path)
    assert snapshot['retrieved_at_utc'] == official.iso(NOW)
    assert official.verify_snapshot(snapshot, path, NOW)[0]['TEST']['level'] == 'ORANGE'


@pytest.mark.parametrize('invalid', ['old_date', 'truncated', 'empty'])
def test_collector_rejects_invalid_download(monkeypatch, tmp_path, invalid):
    data, channels = fixture()
    if invalid == 'old_date':
        data['features'][0]['properties']['fecha'] = '2026-10-23Z'
    elif invalid == 'truncated':
        data['totalFeatures'] = 2
    else:
        data['features'] = []
    path = tmp_path/'channels.json'
    path.write_text(json.dumps(channels))
    responses = iter([Response(PAGE), Response(data)])
    monkeypatch.setattr(collector.requests, 'get', lambda *a, **kw: next(responses))
    with pytest.raises(ValueError):
        collector.collect(NOW, path)


def test_remote_snapshot_cannot_refresh_original_timestamp(monkeypatch, tmp_path):
    from datetime import timedelta
    data, channels = fixture()
    path = tmp_path/'channels.json'
    path.write_text(json.dumps(channels))
    metadata = official.parse_metadata(PAGE)
    snapshot = {'retrieved_at_utc':official.iso(NOW), 'metadata':{
        **metadata, 'from':official.iso(metadata['from']), 'until':official.iso(metadata['until'])}, 'collection':data}
    monkeypatch.setattr(official.requests, 'get', lambda *a, **kw: Response(snapshot))
    notices, report = official.fetch_collector_snapshot('https://example.test/snapshot',path,NOW)
    assert report['state'] == 'verified_collector'
    assert report['source_retrieved_at_utc'] == official.iso(NOW)
    with pytest.raises(ValueError):
        official.fetch_collector_snapshot('https://example.test/snapshot',path,NOW+timedelta(hours=7))
