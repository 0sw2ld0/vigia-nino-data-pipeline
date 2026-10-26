"""Collect and replace one verified national SENAMHI snapshot."""
import argparse
import fcntl
import gzip
import json
import river_advisories
import os
from pathlib import Path
import shutil
import subprocess
import time
from datetime import datetime, timezone

import requests
import official_warnings as official
import stations
import layer_health

ROOT = Path(__file__).resolve().parents[1]
REPO = '0sw2ld0/vigia-nino-data-pipeline'
TAG = 'senamhi-live'


def write_state(directory, **updates):
    path = directory/'status.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    previous.update(updates)
    temporary = directory/'status.tmp'
    temporary.write_text(json.dumps(previous, ensure_ascii=False))
    os.replace(temporary, path)


def collect(now, channels_path):
    started = time.perf_counter()
    response = requests.get(official.PAGE_URL, timeout=25)
    response.raise_for_status()
    metadata = official.parse_metadata(response.content.decode('utf-8'))
    if not metadata['from'] <= now < metadata['until']:
        raise ValueError('No current advisory with verifiable validity; previous copy is not refreshed')
    response = requests.get(official.WFS_URL, params={
        'service':'WFS', 'version':'1.0.0', 'request':'GetFeature',
        'typeName':'g_acti_quebrada:view_av_activ_qdra', 'outputFormat':'application/json',
        'srsName':'EPSG:4326', 'CQL_FILTER':official.DEPARTMENT_FILTER}, timeout=45)
    response.raise_for_status()
    collection = response.json()
    if collection.get('type') != 'FeatureCollection' or 'features' not in collection or 'totalFeatures' not in collection:
        raise ValueError('Unverifiable sector collection; preserving previous copy')
    snapshot = {'retrieved_at_utc':official.iso(now), 'department_scope':list(official.DEPARTMENTS), 'metadata':{
        **metadata, 'from':official.iso(metadata['from']), 'until':official.iso(metadata['until'])},
        'collection':collection}
    matching_started = time.perf_counter()
    notices, verification_report = official.verify_snapshot(snapshot, channels_path, now)
    snapshot['collector_metrics'] = {
        'download_seconds':round(matching_started-started,3),
        'spatial_matching_seconds':round(time.perf_counter()-matching_started,3),
        'total_fetch_validation_seconds':round(time.perf_counter()-started,3),
        'pilot_segments':verification_report['reference_segment_count'],
        'excluded_invalid_reference_segments':len(verification_report.get('excluded_invalid_reference_ids',[])),
        'assigned_pilot_segments':sum(key.startswith('IGN-') for key in notices)}
    return snapshot


def request_weather_update(gh=None, runner=None):
    """Use the existing collector cadence; do not queue duplicate weather builds."""
    runner=runner or subprocess.run
    gh=gh or shutil.which('gh')
    if not gh:return {'state':'failed','error':'GitHub CLI not available'}
    try:
        response=runner([gh,'run','list','--repo',REPO,'--workflow','nowcast.yml','--limit','5','--json','status'],check=True,capture_output=True,text=True,timeout=30)
        runs=json.loads(response.stdout)
        if any(run.get('status') in ('queued','in_progress','waiting','pending') for run in runs):
            return {'state':'already_running'}
        runner([gh,'workflow','run','nowcast.yml','--repo',REPO],check=True,capture_output=True,text=True,timeout=30)
        return {'state':'requested'}
    except Exception as error:
        return {'state':'failed','error':str(error)}


def audit_publication(now, get=None):
    get=get or requests.get
    response=get('https://0sw2ld0.github.io/vigia-nino-data-pipeline/v1/layer_health.json',timeout=10)
    response.raise_for_status()
    layers=response.json()['layers']
    return {name:layer_health.status(row.get('source_at_utc'),now,int(row['stale_after_minutes'])) for name,row in layers.items()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--publish', action='store_true', help='Upload only public official data to the existing GitHub release')
    args = parser.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    with (args.out/'collector.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print('Another collection is running; skipped', flush=True)
            return
        now = datetime.now(timezone.utc).replace(microsecond=0)
        write_state(args.out, last_attempt_utc=official.iso(now), running=True)
        try:
            try:
                station_feed=stations.collect(now,args.out/'stations-cache.json')
                station_path=args.out/'stations_latest.json.gz'
                station_path.write_bytes(gzip.compress(json.dumps(station_feed,ensure_ascii=False).encode(),mtime=0))
                if args.publish:
                    subprocess.run([shutil.which('gh') or 'gh','release','upload',TAG,str(station_path),'--repo',REPO,'--clobber'],check=True,timeout=90)
                write_state(args.out,stations_retrieved_at_utc=station_feed['retrieved_at_utc'],stations_count=len(station_feed['stations']),stations_error=None)
            except Exception as error:
                write_state(args.out,stations_error=str(error))
            try:
                river_feed=river_advisories.collect(now)
                river_path=args.out/'river_advisories_latest.json.gz'
                river_path.write_bytes(gzip.compress(json.dumps(river_feed,ensure_ascii=False).encode(),mtime=0))
                if args.publish:
                    subprocess.run([shutil.which('gh') or 'gh','release','upload',TAG,str(river_path),'--repo',REPO,'--clobber'],check=True,timeout=90)
                write_state(args.out,rivers_retrieved_at_utc=river_feed['source_retrieved_at_utc'],rivers_count=len(river_feed['notices']),rivers_error=None)
            except Exception as error:write_state(args.out,rivers_error=str(error))
            snapshot = collect(now, ROOT/'data_static/monitored_channels.geojson')
            target = args.out/'senamhi_verified_snapshot.json.gz'
            temporary = target.with_suffix('.tmp')
            temporary.write_bytes(gzip.compress(json.dumps(snapshot, ensure_ascii=False).encode(), mtime=0))
            os.replace(temporary, target)
            if args.publish:
                gh = shutil.which('gh')
                if not gh:
                    raise RuntimeError('GitHub CLI not found in PATH')
                subprocess.run([gh,'release','upload',TAG,str(target),'--repo',REPO,'--clobber'],check=True,timeout=90)
            write_state(args.out, running=False, last_error=None,
                        last_success_utc=official.iso(datetime.now(timezone.utc)),
                        source_retrieved_at_utc=snapshot['retrieved_at_utc'], advisory=snapshot['metadata']['id'],
                        features=len(snapshot['collection']['features']), published=args.publish,
                        collector_metrics=snapshot['collector_metrics'])
            print(json.dumps({'retrieved_at_utc':snapshot['retrieved_at_utc'],
                'advisory':snapshot['metadata']['id'], 'features':len(snapshot['collection']['features']),
                'bytes':target.stat().st_size, 'published':args.publish}), flush=True)
        finally:
            # Weather must still update if the SENAMHI request fails.
            if args.publish:
                try:write_state(args.out,publication_health=audit_publication(datetime.now(timezone.utc)),publication_checked_at_utc=official.iso(datetime.now(timezone.utc)),publication_error=None)
                except Exception as error:write_state(args.out,publication_error=str(error))
                dispatch=request_weather_update()
                write_state(args.out,weather_dispatch=dispatch,weather_dispatch_at_utc=official.iso(datetime.now(timezone.utc)))



if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        # Preserve the previous successful timestamp when an attempt fails.
        import sys
        if '--out' in sys.argv:
            output = Path(sys.argv[sys.argv.index('--out')+1])
            if output.exists():
                write_state(output, running=False, last_error=str(error))
        raise
