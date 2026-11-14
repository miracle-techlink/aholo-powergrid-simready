"""Shared synthetic inputs; no test collection or real device initialization."""

from examples.repair_workflow.run_learning_demo import prepare
from simready.workflows import run_repair_workflow


def campaign(tmp_path, scene=0, **options):
    config, base = prepare(tmp_path, scene, **options)
    result = run_repair_workflow(
        config, base_dir=base, output_dir=tmp_path / f"run_{scene}"
    )
    return config, base, result
