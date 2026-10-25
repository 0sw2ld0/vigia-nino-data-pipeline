"""Documentary event inventory. Candidates never become calibration labels by name."""
from collections import Counter
from datetime import date, datetime, timedelta
import json
from pathlib import Path
from urllib.parse import urlparse


def load_inventory(root):
    data = json.loads((Path(root)/'backtest/lima_events.json').read_text())
    ids = set()
    for source in data['sources'].values():
        url = urlparse(source['url'])
        if url.scheme != 'https' or not url.hostname or not source.get('locator'):
            raise ValueError('Event evidence needs HTTPS source and locator')
        date.fromisoformat(source['published_on'])
    for event in data['events']:
        ident = event['event_id']
        if ident in ids:
            raise ValueError('Duplicate historical event')
        ids.add(ident)
        if event['source_id'] not in data['sources']:
            raise ValueError('Unknown event source')
        if event['observed_event'] is not True or event['hazard'] != 'DEBRIS_FLOW' or event['region'] != 'Lima':
            raise ValueError('Inventory only admits documented Lima debris-flow positives')
        day = date.fromisoformat(event['event_date_local'])
        stamp = event['event_time_local']
        if stamp is not None:
            parsed = datetime.fromisoformat(stamp)
            if parsed.utcoffset() != timedelta(hours=-5) or parsed.date() != day:
                raise ValueError('Event time must preserve Peru date and timezone')
            if event['time_semantics'] not in ('OCCURRENCE_REPORTED','RAIN_CAUSING_ACTIVATION_REPORTED'):
                raise ValueError('Report/publication time is not occurrence time')
        elif event['time_semantics'] != 'DATE_ONLY':
            raise ValueError('Date-only event must not invent an onset')
        if day > date.fromisoformat(data['sources'][event['source_id']]['published_on']):
            raise ValueError('Historical event cannot postdate its evidence')
        # Promotion requires a separate reviewed geospatial/rainfall dataset.
        # This documentary inventory cannot quietly become a training dataset.
        if event['verified_channel_ids'] or event['evaluation_split'] != 'UNASSIGNED':
            raise ValueError('Documentary candidates cannot be promoted automatically')
    return data


def summarize(data):
    events = data['events']
    return {'state':'DOCUMENTED_EVENTS_PENDING_RAINFALL_PAIRING',
        'confirmed_event_records':len(events),
        'reported_occurrence_time_records':sum(e['time_semantics']=='OCCURRENCE_REPORTED' for e in events),
        'date_only_records':sum(e['time_semantics']=='DATE_ONLY' for e in events),
        'rain_activation_time_records':sum(e['time_semantics']=='RAIN_CAUSING_ACTIVATION_REPORTED' for e in events),
        'records_by_year':dict(sorted(Counter(e['event_date_local'][:4] for e in events).items())),
        'conservative_episode_groups':len({e['episode_group'] for e in events}),
        'reviewed_evaluation_windows':0, 'reviewed_negative_windows':0,
        'reviewed_on_peru':data['reviewed_on_peru'],
        'limitation':'Non-exhaustive documentary positives, not independent storms or calibrated labels. Precise channel geometry, same-product rainfall, uncertainty windows and reviewed negatives remain pending.'}
