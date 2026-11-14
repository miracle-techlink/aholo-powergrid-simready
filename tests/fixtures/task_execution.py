"""Shared synthetic inputs; no test collection or real device initialization."""

from simready.execution.task_graph import run_task_graph
from tests.paths import REPOSITORY_ROOT

WORKER = REPOSITORY_ROOT / "examples/task_execution/synthetic_task.py"


INVENTORY = {
    "schema_version": 1,
    "cpu_slots": 4,
    "ram_gb": 4,
    "gpus": [],
    "cloud_slots": 1,
}


def task(name="one", deps=(), delay=0.05):
    return dict(
        id=name,
        scene_id="fixture",
        stage="fixture",
        deps=list(deps),
        argv=[
            "{python}",
            "{input:worker}",
            "--out",
            "{out}",
            "--stage",
            name,
            "--delay",
            str(delay),
        ],
        inputs={"worker": str(WORKER)},
        outputs=["artifact.json"],
        resources={"kind": "cpu", "ram_gb": 0.1},
        timeout_s=3,
        queue_timeout_s=3,
        runtime_identity="fixture-v1",
        environment={"inherit": []},
        seed=12,
    )


def execute(tmp_path, tasks, name="run", **kwargs):
    return run_task_graph(
        {"schema_version": 1, "tasks": tasks},
        INVENTORY,
        tmp_path,
        tmp_path / name,
        tmp_path / "cache",
        tmp_path / "locks",
        **kwargs,
    )
