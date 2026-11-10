#!/usr/bin/env python3
"""s1: 提交配电房视频到 Aholo 云端重建 + 轮询 + 下载产物.
复用已验证客户端 ~/graduation_material/hackathon_pipeline/scripts/scene_recon.py
产物落 runs/aholo_panel/, 带成本回执(cost ledger)."""
import sys, os, json, time
from pathlib import Path
sys.path.insert(0, '/home/nvidia/graduation_material/hackathon_pipeline/scripts')
from scene_recon import Client

VIDEO = os.path.expanduser('~/Desktop/场景素材/mv_panel_room_full.mp4')
OUT = Path(__file__).resolve().parents[2] / 'runs' / 'aholo_panel'
OUT.mkdir(parents=True, exist_ok=True)
LEDGER = OUT / 'cost_ledger.jsonl'

def record(ev):
    ev['ts'] = time.time()
    with open(LEDGER, 'a') as f:
        f.write(json.dumps(ev, ensure_ascii=False) + '\n')
    print('[ledger]', ev)

c = Client()
print('key prefix:', c.key[:3] + '***')

# resume-safe: 如果已有 worldId 就不重复提交
wid_file = OUT / 'worldId.txt'
if wid_file.exists():
    wid = wid_file.read_text().strip()
    print('resume worldId:', wid)
else:
    t0 = time.time()
    url = c.upload(VIDEO)
    record({'event': 'upload', 'file': VIDEO, 'url': url, 'secs': round(time.time()-t0, 1)})
    t0 = time.time()
    import requests as _rq
    _r = c.s.post(f"{c.base}/world/v1/reconstructions", json={"name":"mv_panel_room","resources":[{"url":url,"type":"video"}],"taskQuality":"normal","scene":"space"}, timeout=60)
    _r.raise_for_status(); wid = _r.json().get("worldId")
    record({'event': 'submit', 'worldId': wid, 'quality': 'normal', 'secs': round(time.time()-t0, 1)})
    wid_file.write_text(str(wid))
    print('worldId:', wid)

t0 = time.time()
while True:
    j = c.get(wid)
    status = j.get('status'); progress = j.get('progress')
    assets = j.get('assets') or {}
    ply = ((assets.get('splats') or {}).get('urls') or {}).get('plyPath')
    print(f'status={status} progress={progress} ply={"Y" if ply else "-"} elapsed={time.time()-t0:.0f}s', flush=True)
    json.dump(j, open(OUT / 'remote.json', 'w'), ensure_ascii=False, indent=1)
    if str(status).upper() in ('SUCCEEDED', 'SUCCESS', 'DONE', 'FAILED', 'FAIL') or ply:
        break
    time.sleep(20)

urls = ((j.get('assets') or {}).get('splats') or {}).get('urls') or {}
for key, fn in [('plyPath', 'splats.ply'), ('spzPath', 'splats.spz')]:
    u = urls.get(key)
    if u:
        r = c.s.get(u, timeout=600)
        (OUT / fn).write_bytes(r.content)
        print('downloaded', fn, len(r.content), 'bytes')
record({'event': 'done', 'worldId': wid, 'status': status, 'assets': list(urls.keys())})
print('DONE ->', OUT)
