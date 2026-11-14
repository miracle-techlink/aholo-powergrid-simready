"""Paired identities and freeze behavior, using synthetic USD without Isaac."""

import copy
import sys
from threading import Event
from types import SimpleNamespace

import pytest

from simready.core.artifacts import read_json, write_json
from simready.execution.processes import ProcessCancelledError
from simready.simulation.cup_control import VERSIONS, controller_spec
from simready.simulation.cup_recording import implementation_hashes
from simready.workflows.cup_robustness import (
    run_campaign,
    run_comparison,
    runtime_configuration,
)
from tests.fixtures.cup_assets import PROTOCOL, fixture_assets


def test_prepared_comparison_keeps_same_parameters_without_claiming_trials(tmp_path):
    assets = fixture_assets(tmp_path, False)
    protocol = copy.deepcopy(PROTOCOL)
    protocol["episodes"] = [{"id": "fresh", "split": "heldout", "seed": 11001}]
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    output = tmp_path / "paired"
    report = run_comparison(path, assets, output, prepare_only=True)
    assert report["status"] == "PREPARED"
    assert report["physical_validation"] == "NOT_RUN"
    left, right = [
        read_json(output / version / "manifest.json") for version in VERSIONS
    ]
    assert left["samples"] == right["samples"]
    assert left["source_assets_sha256"] == right["source_assets_sha256"]
    assert (
        left["controller"]["implementation_sha256"]
        == right["controller"]["implementation_sha256"]
    )
    assert (
        left["controller"]["spec"]["version"] != right["controller"]["spec"]["version"]
    )
    assert all(
        report["pairs"][0][version]["task_passed"] is None for version in VERSIONS
    )


def test_observed_old_heldout_is_rejected_before_creating_output(tmp_path):
    assets = fixture_assets(tmp_path, False)
    protocol = tmp_path / "protocol.json"
    write_json(protocol, PROTOCOL)
    output = tmp_path / "paired"
    with pytest.raises(ValueError, match="901/902"):
        run_comparison(protocol, assets, output, prepare_only=True)
    assert not output.exists()


def test_frozen_comparison_refuses_config_changes_before_output(tmp_path):
    assets = fixture_assets(tmp_path, False)
    protocol = copy.deepcopy(PROTOCOL)
    protocol["episodes"] = protocol["episodes"][:1]
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    prepared = tmp_path / "prepared"
    run_comparison(path, assets, prepared, prepare_only=True)
    output = tmp_path / "changed"
    with pytest.raises(ValueError, match="frozen comparison"):
        run_comparison(
            path,
            assets,
            output,
            prepare_only=True,
            physics_hz=480,
            frozen_manifest=prepared / "comparison_manifest.json",
        )
    assert not output.exists()


def test_mutation_between_versions_keeps_unrun_pairs_in_report(tmp_path, monkeypatch):
    from simready.workflows import cup_robustness

    assets = fixture_assets(tmp_path, False)
    protocol = copy.deepcopy(PROTOCOL)
    protocol["episodes"] = protocol["episodes"][:1]
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    output = tmp_path / "paired"
    original = cup_robustness.run_campaign

    def mutate(*args, **kwargs):
        report = original(*args, **kwargs)
        (assets / "plan.json").write_text('{"changed": true}')
        return report

    monkeypatch.setattr(cup_robustness, "run_campaign", mutate)
    with pytest.raises(ValueError, match="Frozen paired"):
        run_comparison(path, assets, output, prepare_only=True)
    report = read_json(output / "comparison.json")
    assert len(report["pairs"]) == 1
    assert report["status"] == "INCOMPLETE"
    assert report["pairs"][0]["baseline-v1"]["status"] == "PREPARED"
    assert report["pairs"][0]["stabilized-v2"]["status"] == "NOT_RUN"


@pytest.mark.parametrize(
    "changed",
    [
        {"backend": "local"},
        {"image": "simready-isaac:different"},
        {"gpu": "1"},
        {"worker_timeout_s": 600},
        {"cleanup_timeout_s": 10},
        {"audit_timeout_s": 120},
    ],
)
def test_frozen_comparison_rejects_runtime_changes(tmp_path, changed):
    assets = fixture_assets(tmp_path, False)
    protocol = copy.deepcopy(PROTOCOL)
    protocol["episodes"] = protocol["episodes"][:1]
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    prepared = tmp_path / "prepared"
    run_comparison(path, assets, prepared, prepare_only=True)
    with pytest.raises(ValueError, match="frozen comparison"):
        run_comparison(
            path,
            assets,
            tmp_path / "changed",
            prepare_only=True,
            frozen_manifest=prepared / "comparison_manifest.json",
            **changed,
        )
    assert not (tmp_path / "changed").exists()
    runtime = read_json(prepared / "comparison_manifest.json")["runtime"]
    assert runtime["image_content_addressed"] is False
    assert runtime["environment_immutable"] is False


def test_runtime_identity_preserves_venv_invocation_path(tmp_path):
    binary = tmp_path / "python-bin"
    binary.write_text("fixture interpreter")
    link = tmp_path / "venv/bin/python"
    link.parent.mkdir(parents=True)
    link.symlink_to(binary)
    runtime = runtime_configuration(backend="local", python=str(link))
    assert runtime["python"]["path"] == str(link)
    assert runtime["python"]["resolved_path"] == str(binary)
    assert (
        runtime_configuration(image="simready@sha256:" + "a" * 64)[
            "image_content_addressed"
        ]
        is True
    )


def test_frozen_local_comparison_rejects_interpreter_change(tmp_path):
    assets = fixture_assets(tmp_path, False)
    protocol = copy.deepcopy(PROTOCOL)
    protocol["episodes"] = protocol["episodes"][:1]
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    alternate = tmp_path / "different-python"
    alternate.symlink_to(sys.executable)
    prepared = tmp_path / "prepared"
    run_comparison(path, assets, prepared, prepare_only=True, backend="local")
    with pytest.raises(ValueError, match="frozen comparison"):
        run_comparison(
            path,
            assets,
            tmp_path / "changed",
            prepare_only=True,
            backend="local",
            python=str(alternate),
            frozen_manifest=prepared / "comparison_manifest.json",
        )
    assert not (tmp_path / "changed").exists()


def test_cancelled_pair_preserves_completed_and_unrun_episodes(tmp_path, monkeypatch):
    from pathlib import Path

    from simready.evaluation.cup_robustness import _TASK_CHECKS
    from simready.workflows import cup_robustness

    assets = fixture_assets(tmp_path, False)
    protocol = copy.deepcopy(PROTOCOL)
    protocol["episodes"] = [
        {"id": f"dev-{i}", "split": "development", "seed": 100 + i} for i in range(1, 4)
    ]
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    event, calls = Event(), []

    def worker(command, **kwargs):
        calls.append(command)
        assert kwargs["cancel_event"] is event
        if len(calls) == 2:
            raise ProcessCancelledError(0.1)
        parameters = read_json(kwargs["assets"] / "randomization.json")["sample"][
            "parameters"
        ]
        write_json(
            kwargs["output"] / "physics/result.json",
            {
                "status": "PASS",
                "frames": 5400,
                "checks": {"fixture": True},
                "controller": controller_spec(
                    command[command.index("--controller") + 1]
                ),
                "controller_code_sha256": implementation_hashes(),
                "randomization_runtime": {
                    "cup_mass_kg": parameters["cup_mass_kg"],
                    "materials": {
                        group: {
                            "combine": "average",
                            "static_friction": parameters[group + "_static_friction"],
                            "dynamic_friction": parameters[group + "_static_friction"]
                            * parameters["dynamic_static_ratio"],
                        }
                        for group in ("cup", "gripper", "table")
                    },
                },
            },
        )
        return 0

    def audit(command, **kwargs):
        assert kwargs["cancel_event"] is event
        write_json(
            Path(command[command.index("--out") + 1]),
            {
                "status": "PASS",
                "checks": dict.fromkeys(_TASK_CHECKS, True),
            },
        )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(cup_robustness, "launch_worker", worker)
    monkeypatch.setattr(cup_robustness, "run_process", audit)
    output = tmp_path / "pair"
    with pytest.raises(ProcessCancelledError):
        run_comparison(path, assets, output, cancel_event=event)
    assert len(calls) == 2
    report = read_json(output / "comparison.json")
    campaign = read_json(output / "baseline-v1/report.json")
    assert campaign["status"] == report["status"] == "CANCELLED"
    assert campaign["all"]["episodes"] == 3
    assert campaign["all"]["successes"] == 1
    assert [row["baseline-v1"]["status"] for row in report["pairs"]] == [
        "COMPLETE",
        "CANCELLED",
        "NOT_RUN",
    ]
    assert report["pairs"][0]["baseline-v1"]["task_passed"] is True
    assert all(row["stabilized-v2"]["status"] == "NOT_RUN" for row in report["pairs"])
    assert report["physical_validation"] == "INCOMPLETE"


def test_precancelled_campaign_never_launches_or_prepares_assets(tmp_path, monkeypatch):
    from simready.workflows import cup_robustness

    assets = fixture_assets(tmp_path, False)
    path = tmp_path / "protocol.json"
    write_json(path, PROTOCOL)
    event = Event()
    event.set()

    def unexpected(*args, **kwargs):
        raise AssertionError("Cancelled campaign must not launch")

    monkeypatch.setattr(cup_robustness, "launch_worker", unexpected)
    output = tmp_path / "run"
    with pytest.raises(ProcessCancelledError):
        run_campaign(path, assets, output, cancel_event=event)
    report = read_json(output / "report.json")
    assert report["status"] == "CANCELLED"
    assert [row["status"] for row in report["episodes"]] == [
        "CANCELLED",
        "NOT_RUN",
        "NOT_RUN",
        "NOT_RUN",
    ]
    assert not (output / "dev-01/assets").exists()
