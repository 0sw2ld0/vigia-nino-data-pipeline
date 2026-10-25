"""Collect and validate public SENAMHI Lima sectors on a machine with source access."""
import argparse
import fcntl
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
from datetime import datetime, timezone

import requests
import official_warnings as official

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
    response = requests.get(official.PAGE_URL, timeout=25)
    response.raise_for_status()
    metadata = official.parse_metadata(response.content.decode('utf-8'))
    if not metadata['from'] <= now < metadata['until']:
        raise ValueError('No current advisory with verifiable validity; previous copy is not refreshed')
    response = requests.get(official.WFS_URL, params={
        'service':'WFS', 'version':'1.0.0', 'request':'GetFeature',
        'typeName':'g_acti_quebrada:view_av_activ_qdra', 'outputFormat':'application/json',
        'srsName':'EPSG:4326', 'CQL_FILTER':"nombdep='LIMA'"}, timeout=45)
    response.raise_for_status()
    collection = response.json()
    if not collection.get('features'):
        raise ValueError('Empty sector collection; refusing to replace last verified copy')
    snapshot = {'retrieved_at_utc':official.iso(now), 'metadata':{
        **metadata, 'from':official.iso(metadata['from']), 'until':official.iso(metadata['until'])},
        'collection':collection}
    official.verify_snapshot(snapshot, channels_path, now)
    return snapshot


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
                    features=len(snapshot['collection']['features']), published=args.publish)
        print(json.dumps({'retrieved_at_utc':snapshot['retrieved_at_utc'],
            'advisory':snapshot['metadata']['id'], 'features':len(snapshot['collection']['features']),
            'bytes':target.stat().st_size, 'published':args.publish}), flush=True)


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
