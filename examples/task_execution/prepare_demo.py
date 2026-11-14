"""Generate a portable four-scene CPU fixture plan, not a GPU performance test."""

import argparse
import json
import sys
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
args.out.mkdir(parents=True, exist_ok=False)
(args.out / "source.txt").write_text("Synthetic scene fixture; no customer video.\n")
worker = str(Path(__file__).with_name("synthetic_task.py").resolve())
tasks = []
for scene in range(4):
    previous = None
    for stage, delay in [
        ("ingest", 0.15),
        ("reconstruct", 0.3),
        ("render", 0.25),
        ("observe", 0.1),
    ]:
        task_id = f"scene{scene}_{stage}"
        task = dict(
            id=task_id,
            scene_id=f"fixture-{scene}",
            stage=stage,
            deps=[previous] if previous else [],
            argv=[
                "{python}",
                "{input:worker}",
                "--out",
                "{out}",
                "--stage",
                stage,
                "--delay",
                str(delay),
                "--input",
                f"{{dep:{previous}:artifact.json}}" if previous else "{input:source}",
            ],
            inputs={"worker": worker, "source": "source.txt"},
            outputs=["artifact.json"],
            resources={"kind": "cpu", "cpu": 1, "ram_gb": 0.1},
            timeout_s=10,
            runtime_identity=f"cpu-fixture-python-{sys.version.split()[0]}",
            seed=12,
            quality_profile="synthetic-scheduling-only",
            cache=True,
            environment={"inherit": []},
        )
        tasks.append(task)
        previous = task_id
for name, value in [
    ("plan.json", {"schema_version": 1, "tasks": tasks}),
    (
        "inventory.json",
        {
            "schema_version": 1,
            "cpu_slots": 4,
            "ram_gb": 1,
            "cloud_slots": 0,
            "gpus": [],
        },
    ),
]:
    (args.out / name).write_text(json.dumps(value, indent=2) + "\n")
