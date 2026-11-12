"""Measure only local scheduling overhead/concurrency/cache with synthetic workers."""

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

from simready_bridge.artifacts import write_json
from simready_bridge.infra import run

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="simready-scheduler-bench-") as temporary:
        root = Path(temporary)
        subprocess.run(
            [
                sys.executable,
                str(ROOT / "examples/infra/make_demo.py"),
                "--out",
                str(root / "input"),
            ],
            check=True,
        )
        plan = json.loads((root / "input/plan.json").read_text())
        inventory = json.loads((root / "input/inventory.json").read_text())
        records = {}
        for name, workers, cache in [
            ("serial", 1, "serial-cache"),
            ("parallel", 4, "parallel-cache"),
            ("warm_cache", 4, "parallel-cache"),
        ]:
            result = run(
                plan,
                inventory,
                root / "input",
                root / name,
                root / cache,
                root / "locks",
                workers,
            )
            if result["status"] != "SUCCESS":
                raise RuntimeError(result)
            records[name] = result
        outputs = [
            {k: v["outputs"] for k, v in result["tasks"].items()}
            for result in records.values()
        ]
        assert outputs[0] == outputs[1] == outputs[2], (
            "Scheduling changed fixture outputs"
        )
        summary = dict(
            workload="Four synthetic scene DAGs, 16 CPU subprocesses; no GPU or real reconstruction",
            python=sys.version.split()[0],
            declared_sleep_per_scene_s=0.8,
            outputs_identical=True,
            runs={
                name: {k: result[k] for k in ("wall_seconds", "cache_hits", "status")}
                for name, result in records.items()
            },
            synthetic_parallel_speedup=records["serial"]["wall_seconds"]
            / records["parallel"]["wall_seconds"],
            real_pipeline_speedup=None,
        )
        write_json(args.out, summary)
        print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
