"""CPU-only scheduling fixture. Does NOT reconstruct, render or evaluate a scene."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

parser = argparse.ArgumentParser()
parser.add_argument('--out', required=True, type=Path)
parser.add_argument('--input', action='append', default=[], type=Path)
parser.add_argument('--stage', required=True)
parser.add_argument('--delay', type=float, default=.1)
parser.add_argument('--fail', action='store_true')
args = parser.parse_args()
print(json.dumps({'start': time.monotonic()}), flush=True)
time.sleep(args.delay)
if args.fail:
    raise SystemExit(7)
value = dict(synthetic_fixture=True, stage=args.stage, seed=os.environ.get('SIMREADY_SEED'),
             inputs=[hashlib.sha256(p.read_bytes()).hexdigest() for p in args.input])
(args.out / 'artifact.json').write_text(json.dumps(value, sort_keys=True))
print(json.dumps({'end': time.monotonic()}), flush=True)
