"""CPU integration of the public entry, isolation, preflight and evidence gates."""

import json
import shlex
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.workflows import scene_execution, scene_pipeline


def make_bundle(root, scene="starbucks"):
    root.mkdir(parents=True)
    filenames = (
        ["input.mp4", "scene.usda", "robot.usda"]
        if scene == "starbucks"
        else ["input.mp4", "mission.json"]
    )
    for name in filenames:
        (root / name).write_bytes(name.encode())
    bundle = {
        "schema_version": 1,
        "scene": scene,
        "source_video": "input.mp4",
        "source_video_sha256": digest(root / "input.mp4"),
        "files": [
            {
                "path": name,
                "bytes": (root / name).stat().st_size,
                "sha256": digest(root / name),
            }
            for name in filenames
        ],
    }
    bundle.update(
        {"scene_usd": "scene.usda", "robot_usd": "robot.usda"}
        if scene == "starbucks"
        else {"mission_config": "mission.json"}
    )
    write_json(root / "bundle.json", bundle)
    return root


def complete_receipts(execution):
    """Fake only GPU outputs; production completion checks consume real files."""
    execution = Path(execution)
    config = read_json(execution / "scene.json")
    scene, render = config["scene"], config["render"]
    delivery = execution / "delivery"
    (delivery / "render").mkdir(parents=True)
    name = (
        "g2_starbucks_4k.mp4"
        if scene == "starbucks"
        else "navigation_vision_z1_stop.mp4"
    )
    video = delivery / "render" / name
    video.write_bytes(b"simulated video receipt fixture, not GPU evidence")
    stages = (
        ["observations", "render"]
        if scene == "starbucks"
        else ["observations", "physics", "audit", "render"]
    )
    write_json(
        delivery / "timing.json",
        {
            "status": "COMPLETE",
            "stages": [{"name": name, "returncode": 0} for name in stages],
            "video_sha256": digest(video),
        },
    )
    if scene == "starbucks":
        write_json(
            delivery / "render/delivery.json",
            {
                "frames": render["frames"],
                "fps": render["fps"],
                "resolution": [render["width"], render["height"]],
            },
        )
    else:
        mission = delivery / "mission"
        write_json(mission / "result.json", {"passed": True})
        (mission / "sensors.jsonl").write_text("{}\n")
        (mission / "measured_replay.npz").write_bytes(b"simulated replay fixture")
        audit = {
            "passed": True,
            "checks": {"fixture": True},
            "result_sha256": digest(mission / "result.json"),
            "sensor_sha256": digest(mission / "sensors.jsonl"),
            "replay_sha256": digest(mission / "measured_replay.npz"),
        }
        write_json(mission / "audit.json", audit)
        write_json(
            delivery / "render/render.json",
            {
                "source_result_sha256": audit["result_sha256"],
                "source_replay_sha256": audit["replay_sha256"],
                "video_sha256": digest(video),
                "frames": 5,
                "fps": 25,
                "width": render["width"],
                "height": render["height"],
            },
        )
    write_json(
        execution / "pipeline-status.json",
        {"status": "COMPLETE", "stage": "delivery_verification"},
    )


def arguments(assets, output, scene="starbucks", backend="local"):
    return [
        "--scene",
        scene,
        "--backend",
        backend,
        "--assets",
        str(assets),
        "--out",
        str(output),
        "--accept-eula",
    ]


def test_dry_run_is_offline_read_only_and_preserves_venv_path(
    tmp_path, monkeypatch, capsys
):
    assets = make_bundle(tmp_path / "assets")
    output = tmp_path / "run"
    monkeypatch.setattr(
        scene_pipeline.subprocess,
        "run",
        lambda *a, **k: pytest.fail("dry-run started a process"),
    )
    assert (
        scene_pipeline.main(
            arguments(assets, output) + ["--dry-run", "--python", "/venv/bin/python"]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["mode"] == "reviewed_scene_reproduction"
    assert plan["python"] == "/venv/bin/python"
    assert plan["covered_stages"] == [
        "bundle_verification",
        "environment",
        "observations",
        "render",
        "delivery_verification",
        "summary",
    ]
    assert plan["cloud_submission"] is False and plan["new_reconstruction"] is False
    assert not output.exists()


def test_defaults_choose_bundle_and_unique_run_without_creating_it(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    make_bundle(tmp_path / "data/starbucks")
    command = ["--scene", "starbucks", "--backend", "local", "--dry-run"]
    assert scene_pipeline.main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert scene_pipeline.main(command) == 0
    second = json.loads(capsys.readouterr().out)
    assert first["assets"] == str(tmp_path / "data/starbucks")
    assert first["output"] != second["output"]
    assert not (tmp_path / "runs").exists()


@pytest.mark.parametrize(
    "failure",
    [
        "existing",
        "overlap",
        "symlink",
        "scene_mismatch",
        "changed_bundle",
        "bad_config",
        "no_eula",
    ],
)
def test_invalid_inputs_do_not_start_processes_or_overwrite_output(
    tmp_path, monkeypatch, failure
):
    assets = make_bundle(tmp_path / "assets")
    output = tmp_path / "run"
    command = arguments(assets, output)
    if failure == "existing":
        output.mkdir()
        (output / "evidence.txt").write_text("keep")
    elif failure == "overlap":
        output = assets / "run"
        command = arguments(assets, output)
    elif failure == "symlink":
        output.symlink_to(assets, target_is_directory=True)
    elif failure == "scene_mismatch":
        command[1] = "substation"
    elif failure == "changed_bundle":
        (assets / "scene.usda").write_bytes(b"changed")
    elif failure == "bad_config":
        config = dict(
            scene_pipeline.SCENE_PRESETS["starbucks"],
            render={"width": 641, "height": 360, "frames": 2, "fps": 30},
        )
        write_json(tmp_path / "bad.json", config)
        command += ["--config", str(tmp_path / "bad.json")]
    elif failure == "no_eula":
        command.remove("--accept-eula")
    monkeypatch.setattr(
        scene_pipeline,
        "launch_worker",
        lambda *a, **k: pytest.fail("invalid input launched a worker"),
    )
    assert scene_pipeline.main(command) == 3
    if failure == "existing":
        assert (output / "evidence.txt").read_text() == "keep"
        assert sorted(p.name for p in output.iterdir()) == ["evidence.txt"]
    elif failure == "symlink":
        assert output.is_symlink() and not (assets / "summary.json").exists()
    else:
        assert not output.exists()


def test_wheel_style_local_import_path_and_docker_checkout_requirement(
    tmp_path, monkeypatch
):
    from simready.execution.isaac_launcher import (
        package_source_root,
        worker_environment,
    )

    empty = tmp_path / "not-a-checkout"
    empty.mkdir()
    assert worker_environment(empty)["PYTHONPATH"].split(":")[0] == str(
        package_source_root()
    )
    assets = make_bundle(tmp_path / "assets")
    output = tmp_path / "out"
    assert (
        scene_pipeline.main(
            arguments(assets, output, backend="docker")
            + ["--repo", str(empty), "--dry-run"]
        )
        == 3
    )
    assert not output.exists()


def test_wheel_package_is_exposed_without_host_binary_site_packages(
    tmp_path, monkeypatch
):
    from simready.execution import isaac_launcher

    host_packages = tmp_path / "python3.14/site-packages"
    (host_packages / "simready").mkdir(parents=True)
    (host_packages / "simready/__init__.py").write_text('VERSION = "fixture"\n')
    (host_packages / "numpy").mkdir()
    (host_packages / "numpy/__init__.py").write_text(
        'raise AssertionError("host ABI leaked")\n'
    )
    output = tmp_path / "owned-output"
    output.mkdir()
    monkeypatch.setattr(isaac_launcher, "package_source_root", lambda: host_packages)
    monkeypatch.delenv("PYTHONPATH", raising=False)
    environment = isaac_launcher.worker_environment(tmp_path, output)
    exposed = Path(environment["PYTHONPATH"].split(":")[0])
    assert exposed == output / ".python-source"
    assert sorted(p.name for p in exposed.iterdir()) == ["simready"]
    assert (exposed / "simready").resolve() == host_packages / "simready"
    assert str(host_packages) not in environment["PYTHONPATH"]


def test_ssh_dry_run_preserves_remote_paths_and_quoted_arguments(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        scene_pipeline.subprocess,
        "run",
        lambda *a, **k: pytest.fail("dry-run connected to SSH"),
    )
    assert (
        scene_pipeline.main(
            [
                "--scene",
                "substation",
                "--backend",
                "ssh",
                "--ssh-host",
                "user@host",
                "--repo",
                "/remote/repo with spaces",
                "--assets",
                "data/$(literal)",
                "--out",
                "runs/run 1",
                "--dry-run",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    remote = shlex.split(plan["argv"][-1])
    assert remote[0] == "/remote/repo with spaces/.venv/bin/python"
    assert (
        remote[remote.index("--assets") + 1]
        == "/remote/repo with spaces/data/$(literal)"
    )
    assert remote[remote.index("--out") + 1] == "/remote/repo with spaces/runs/run 1"
    assert plan["remote_backend"] == "docker"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("scene", ["starbucks", "substation"])
def test_success_requires_and_reports_bound_delivery_receipts(
    tmp_path, monkeypatch, scene
):
    assets = make_bundle(tmp_path / "assets", scene)
    output = tmp_path / "out"

    def launch(worker, **kwargs):
        assert kwargs["python"] == "/chosen/venv/bin/python"
        complete_receipts(kwargs["output"])
        return 0

    monkeypatch.setattr(scene_pipeline, "launch_worker", launch)
    assert (
        scene_pipeline.main(
            arguments(assets, output, scene) + ["--python", "/chosen/venv/bin/python"]
        )
        == 0
    )
    result = read_json(output / "summary.json")
    assert result["status"] == "COMPLETE"
    assert result["physics_validated"] == (scene == "substation")
    assert Path(result["video"]).is_relative_to(output / "execution/delivery")
    assert result["bundle_sha256"] == digest(assets / "bundle.json")


@pytest.mark.parametrize(
    "failure",
    [
        "missing_completion",
        "failed_completion",
        "changed_video",
        "audit_failed",
        "audit_changed",
        "wrong_render_binding",
    ],
)
def test_zero_exit_alone_never_marks_delivery_successful(
    tmp_path, monkeypatch, failure
):
    assets = make_bundle(tmp_path / "assets", "substation")
    output = tmp_path / "out"

    def launch(worker, **kwargs):
        execution = Path(kwargs["output"])
        complete_receipts(execution)
        if failure == "missing_completion":
            (execution / "pipeline-status.json").unlink()
        elif failure == "failed_completion":
            write_json(
                execution / "pipeline-status.json",
                {
                    "status": "FAILED",
                    "stage": "physics",
                    "error": "contact check failed",
                },
            )
        elif failure == "changed_video":
            (execution / "delivery/render/navigation_vision_z1_stop.mp4").write_bytes(
                b"tampered"
            )
        elif failure == "audit_failed":
            audit_path = execution / "delivery/mission/audit.json"
            audit = read_json(audit_path)
            audit["passed"] = False
            write_json(audit_path, audit)
        elif failure == "audit_changed":
            (execution / "delivery/mission/sensors.jsonl").write_text("changed")
        else:
            path = execution / "delivery/render/render.json"
            render = read_json(path)
            render["source_replay_sha256"] = "wrong"
            write_json(path, render)
        return 0

    monkeypatch.setattr(scene_pipeline, "launch_worker", launch)
    assert scene_pipeline.main(arguments(assets, output, "substation")) == 2
    summary = read_json(output / "summary.json")
    assert summary["status"] == "FAILED" and summary["error"]
    if failure == "failed_completion":
        assert summary["stage"] == "physics"


def test_environment_failure_prevents_observations_and_physics(tmp_path, monkeypatch):
    assets = make_bundle(tmp_path / "assets", "substation")
    output = tmp_path / "out"
    calls = []

    def process(command, **kwargs):
        calls.append(command)
        json.dump({"passed": True, "ffmpeg": False}, kwargs["stdout"])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(scene_execution.subprocess, "run", process)
    monkeypatch.setattr(
        scene_pipeline,
        "launch_worker",
        lambda worker, **kwargs: scene_execution.execute_scene(
            kwargs["assets"], kwargs["output"]
        ),
    )
    assert scene_pipeline.main(arguments(assets, output, "substation")) == 2
    assert len(calls) == 1 and "--require-lab" in calls[0]
    assert read_json(output / "summary.json")["stage"] == "environment"
    assert not (output / "execution/delivery").exists()


def test_physics_failure_stops_audit_and_render_with_stage_evidence(
    tmp_path, monkeypatch
):
    assets = make_bundle(tmp_path / "assets", "substation")
    output = tmp_path / "out"
    called = []

    def process(command, **kwargs):
        called.append(command)
        if command[2] == "simready.simulation.environment_check":
            json.dump({"passed": True, "ffmpeg": True}, kwargs["stdout"])
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(returncode=2 if "mission" in command else 0)

    monkeypatch.setattr(scene_execution.subprocess, "run", process)
    monkeypatch.setattr(
        scene_pipeline,
        "launch_worker",
        lambda worker, **kwargs: scene_execution.execute_scene(
            kwargs["assets"], kwargs["output"]
        ),
    )
    assert scene_pipeline.main(arguments(assets, output, "substation")) == 2
    assert len(called) == 3
    summary = read_json(output / "summary.json")
    assert summary["stage"] == "physics" and summary["status"] == "FAILED"
    timing = read_json(output / "execution/delivery/timing.json")
    assert [row["name"] for row in timing["stages"]] == ["observations", "physics"]
    assert not (output / "execution/delivery/render").exists()


def test_docker_command_keeps_input_readonly_and_spaces_literal(tmp_path, monkeypatch):
    from simready.execution import isaac_container

    assets = make_bundle(tmp_path / "assets with spaces")
    output = tmp_path / "run $(literal); spaces"

    def execute(command):
        mounts = [
            command[i + 1] for i, value in enumerate(command) if value == "--mount"
        ]
        assert f"type=bind,src={assets},dst=/assets,readonly" in mounts
        assert f"type=bind,src={output / 'execution'},dst=/output" in mounts
        assert command[-6:] == [
            "-m",
            "simready.workflows.scene_execution",
            "--assets",
            "/assets",
            "--out",
            "/output",
        ]
        complete_receipts(output / "execution")
        return 0

    monkeypatch.setattr(isaac_container, "execute_container", execute)
    assert scene_pipeline.main(arguments(assets, output, backend="docker")) == 0
    assert read_json(output / "execution/launch.json")["status"] == "COMPLETE"


def prepared_spec(tmp_path):
    source = tmp_path / "prepared"
    source.mkdir()
    (source / "video.mp4").write_bytes(b"video fixture")
    (source / "scene.usda").write_text('#usda 1.0\ndef Xform "World" {}\n')
    write_json(
        source / "spec.json",
        {
            "scene": "starbucks",
            "source_video": "video.mp4",
            "scene_usd": "scene.usda",
            "robot_usd": "scene.usda",
        },
    )
    return source / "spec.json"


def test_bundle_spec_dry_run_does_not_export_or_create_output(
    tmp_path, monkeypatch, capsys
):
    spec = prepared_spec(tmp_path)
    output = tmp_path / "run"
    monkeypatch.setattr(
        scene_pipeline.subprocess,
        "run",
        lambda *a, **k: pytest.fail("dry-run exported"),
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
                "--dry-run",
            ]
        )
        == 0
    )
    plan = json.loads(capsys.readouterr().out)
    assert plan["validation"]["bundle"] == "export_pending"
    assert plan["covered_stages"][0] == "bundle_export"
    assert not output.exists()


def test_one_command_exports_real_usd_before_mocked_gpu_execution(
    tmp_path, monkeypatch
):
    pytest.importorskip("pxr")
    spec = prepared_spec(tmp_path)
    output = tmp_path / "run"

    def launch(worker, **kwargs):
        from simready.execution.isaac_container import validate_paths
        from simready.simulation.asset_bundle import verify_bundle

        assert verify_bundle(kwargs["assets"])["scene"] == "starbucks"
        validate_paths(kwargs["repo"], kwargs["assets"], kwargs["output"])
        complete_receipts(kwargs["output"])
        return 0

    monkeypatch.setattr(scene_pipeline, "launch_worker", launch)
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
                "--bundle-python",
                sys.executable,
                "--accept-eula",
            ]
        )
        == 0
    )
    assert (output / "bundle/bundle.json").is_file()
    assert (output / "bundle-export.log").is_file()
    assert read_json(output / "summary.json")["status"] == "COMPLETE"


def test_bundle_export_failure_is_reported_without_worker_start(tmp_path, monkeypatch):
    spec = prepared_spec(tmp_path)
    output = tmp_path / "run"
    monkeypatch.setattr(
        scene_pipeline.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=2)
    )
    monkeypatch.setattr(
        scene_pipeline,
        "launch_worker",
        lambda *a, **k: pytest.fail("failed export launched"),
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
                "--accept-eula",
            ]
        )
        == 2
    )
    summary = read_json(output / "summary.json")
    assert summary["status"] == "FAILED" and summary["stage"] == "bundle_export"
    assert not (output / "execution").exists()


@pytest.mark.parametrize(
    "arguments",
    [
        ["run", "--help"],
        ["run", "--scene", "starbucks", "--backend", "local", "--dry-run"],
    ],
)
def test_public_entry_help_and_dry_run_do_not_import_gpu_or_numeric_modules(
    tmp_path, arguments
):
    make_bundle(tmp_path / "data/starbucks")
    program = """
import importlib.abc,sys
class BlockHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname.split('.')[0] in {'numpy','scipy','PIL','torch','isaacsim','isaaclab','pxr'}:
            raise AssertionError('Unexpected optional runtime: '+fullname)
sys.meta_path.insert(0,BlockHeavy())
from simready.cli import main
raise SystemExit(main(sys.argv[1:]))
"""
    result = subprocess.run(
        [sys.executable, "-c", program, *arguments],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    assert not (tmp_path / "runs").exists()
