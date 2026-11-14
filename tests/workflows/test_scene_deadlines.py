"""Public plans propagate finite budgets and retain timeout evidence."""

import json
import shlex
import sys
import time

import pytest

from simready.core.artifacts import read_json
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.workflows import scene_pipeline
from tests.fixtures.scene_delivery import arguments, make_bundle, prepared_spec


@pytest.mark.parametrize("flag", ["--worker-timeout", "--bundle-timeout"])
@pytest.mark.parametrize("value", ["nan", "inf", "0", "-1"])
def test_invalid_budget_is_rejected_before_output(tmp_path, flag, value):
    assets = make_bundle(tmp_path / "assets")
    output = tmp_path / "out"
    assert scene_pipeline.main(arguments(assets, output) + [flag, value]) == 3
    assert not output.exists()


@pytest.mark.parametrize(
    ("error", "status"),
    [
        (ProcessTimeoutError(0.25, 0.25), "TIMEOUT"),
        (ProcessCancelledError(0.1), "CANCELLED"),
    ],
)
def test_worker_budget_and_terminal_state_reach_public_summary(
    tmp_path, monkeypatch, error, status
):
    assets = make_bundle(tmp_path / "assets")
    output = tmp_path / "out"

    def launch(*args, **kwargs):
        assert kwargs["timeout_s"] == 0.25
        raise error

    monkeypatch.setattr(scene_pipeline, "launch_worker", launch)
    assert (
        scene_pipeline.main(arguments(assets, output) + ["--worker-timeout", "0.25"])
        == 2
    )
    assert read_json(output / "run-plan.json")["worker_timeout_s"] == 0.25
    summary = read_json(output / "summary.json")
    assert summary["status"] == status and summary["stage"] == "launch"
    assert not (output / "execution/delivery").exists()


def test_remote_plan_forwards_budgets_without_local_paths(tmp_path, capsys):
    assert (
        scene_pipeline.main(
            [
                "--scene",
                "starbucks",
                "--backend",
                "ssh",
                "--ssh-host",
                "test-node",
                "--repo",
                "/remote/repo",
                "--out",
                "/remote/output",
                "--dry-run",
                "--worker-timeout",
                "37",
                "--bundle-timeout",
                "11",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    command = shlex.split(plan["argv"][-1])
    assert float(command[command.index("--worker-timeout") + 1]) == 37
    assert float(command[command.index("--bundle-timeout") + 1]) == 11
    assert plan["worker_timeout_s"] == 37


def test_bundle_export_timeout_cleans_owned_child_and_prevents_launch(
    tmp_path, monkeypatch
):
    spec = prepared_spec(tmp_path)
    output = tmp_path / "out"
    marker = tmp_path / "escaped-child"
    real_run = scene_pipeline.run_process
    child = (
        f"import pathlib,time; time.sleep(.5); pathlib.Path({str(marker)!r}).touch()"
    )

    def export(command, **kwargs):
        return real_run(
            [
                sys.executable,
                "-c",
                "import subprocess,sys,time; subprocess.Popen([sys.executable,'-c',sys.argv[1]]); time.sleep(10)",
                child,
            ],
            **kwargs,
        )

    monkeypatch.setattr(scene_pipeline, "run_process", export)
    monkeypatch.setattr(
        scene_pipeline,
        "launch_worker",
        lambda *a, **k: pytest.fail("timed-out export launched worker"),
    )
    assert (
        scene_pipeline.main(
            [
                "--scene",
                "starbucks",
                "--backend",
                "local",
                "--bundle-spec",
                str(spec),
                "--out",
                str(output),
                "--bundle-timeout",
                "0.15",
                "--accept-eula",
            ]
        )
        == 2
    )
    summary = read_json(output / "summary.json")
    assert summary["status"] == "TIMEOUT" and summary["stage"] == "bundle_export"
    assert (output / "bundle-export.stderr.log").is_file()
    time.sleep(0.6)
    assert not marker.exists()
