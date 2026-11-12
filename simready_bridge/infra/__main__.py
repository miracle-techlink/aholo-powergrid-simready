import argparse
import json
from pathlib import Path

from .runner import run


def main():
    parser = argparse.ArgumentParser(description='Execute a bounded, resource-aware scene DAG (Linux).')
    parser.add_argument('--plan', required=True, type=Path)
    parser.add_argument('--inventory', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    parser.add_argument('--cache', required=True, type=Path)
    parser.add_argument('--lock-dir', default='/tmp/simready-device-leases', type=Path)
    parser.add_argument('--max-workers', default=8, type=int)
    args = parser.parse_args()
    try:
        result = run(json.loads(args.plan.read_text()), json.loads(args.inventory.read_text()),
                     args.plan.resolve().parent, args.out, args.cache, args.lock_dir, args.max_workers)
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({'status': 'INVALID', 'error': str(error)}))
        return 3
    print(json.dumps({k: result[k] for k in ('status', 'wall_seconds', 'cache_hits', 'release_authorized')}))
    return 0 if result['status'] == 'SUCCESS' else 2


if __name__ == '__main__':
    raise SystemExit(main())
