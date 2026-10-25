"""Read-only connectivity diagnosis; bounded retries, no changes to the published feed."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import tempfile
import time
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

TARGETS = [
    ('GitHub control', 'https://api.github.com/meta', 'json'),
    ('SENAMHI www', 'https://www.senamhi.gob.pe/?p=aviso-activacion-quebrada', 'html'),
    ('SENAMHI apex', 'https://senamhi.gob.pe/?p=aviso-activacion-quebrada', 'html'),
    ('IDESEP capabilities', 'https://idesep.senamhi.gob.pe/geoserver/g_acti_quebrada/ows?service=WFS&version=1.0.0&request=GetCapabilities', 'xml'),
    ('IDESEP Lima sample', "https://idesep.senamhi.gob.pe/geoserver/g_acti_quebrada/ows?service=WFS&version=1.0.0&request=GetFeature&typeName=g_acti_quebrada%3Aview_av_activ_qdra&outputFormat=application%2Fjson&srsName=EPSG%3A4326&CQL_FILTER=nombdep%3D%27LIMA%27&maxFeatures=1", 'geojson'),
]

def probe(target):
    name,url,kind = target
    host = urlsplit(url).hostname
    try: addresses = sorted({v[4][0] for v in socket.getaddrinfo(host,443,type=socket.SOCK_STREAM)})
    except OSError as error: addresses = [str(error)]
    attempts=[]
    for attempt in range(2):
        with tempfile.TemporaryDirectory() as tmp:
            body=Path(tmp)/'response'
            cmd=['curl','--silent','--show-error','--location','--connect-timeout','8','--max-time','20',
                 '--output',str(body),'--write-out','%{json}']
            if attempt: cmd.append('--ipv4')
            cmd.append(url)
            result=subprocess.run(cmd,capture_output=True,text=True,timeout=25)
            try: stats=json.loads(result.stdout)
            except ValueError: stats={}
            row={'attempt':attempt+1,'ipv4_only':bool(attempt),'curl_exit':result.returncode,
                 **{k:stats.get(k) for k in ['http_code','remote_ip','time_connect','time_appconnect','time_total','size_download','url_effective']},
                 'error':result.stderr[:500]}
            valid=False
            if result.returncode==0 and 200<=stats.get('http_code',0)<300 and body.exists():
                try:
                    data=body.read_bytes()
                    if kind=='xml': valid=ET.fromstring(data).tag.endswith('WFS_Capabilities')
                    elif kind=='html': valid=b'aviso-vigente' in data
                    else:
                        payload=json.loads(data)
                        valid=isinstance(payload,dict) and (kind=='json' or payload.get('type')=='FeatureCollection')
                        if kind=='geojson':
                            row['returned_features']=len(payload.get('features',[]))
                            row['feature_dates']=sorted({f['properties'].get('fecha','') for f in payload.get('features',[])})
                except (ValueError,ET.ParseError): pass
            row['expected_content_valid']=valid;attempts.append(row)
            if valid: break
        if attempt==0: time.sleep(2)
    return {'name':name,'url':url,'dns_addresses':addresses,'success':any(a['expected_content_valid'] for a in attempts),'attempts':attempts}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--out',type=Path,default=Path('diagnostics/connectivity.json'));args=parser.parse_args()
    with ThreadPoolExecutor(max_workers=5) as pool: results=list(pool.map(probe,TARGETS))
    report={'checked_at_utc':datetime.now(timezone.utc).isoformat(),'os':platform.platform(),'runner_os':os.getenv('RUNNER_OS','local'),'results':results}
    args.out.parent.mkdir(parents=True,exist_ok=True);args.out.write_text(json.dumps(report,ensure_ascii=False,indent=2))
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if os.getenv('GITHUB_STEP_SUMMARY'):
        with open(os.environ['GITHUB_STEP_SUMMARY'],'a',encoding='utf-8') as out:
            out.write('# SENAMHI connectivity diagnosis\n\n| Source | Valid response | Attempts | HTTP | Curl exit |\n|---|---|---|---|---|\n')
            for r in results:
                a=r['attempts'][-1];out.write(f"| {r['name']} | {r['success']} | {len(r['attempts'])} | {a['http_code']} | {a['curl_exit']} |\n")
