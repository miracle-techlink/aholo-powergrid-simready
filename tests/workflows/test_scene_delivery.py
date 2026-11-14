"""A reviewed source cannot silently become another scene or changed asset."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.execution.processes import ProcessCancelledError, ProcessTimeoutError
from simready.simulation.asset_bundle import verify_bundle
from simready.workflows import scene_delivery, scene_execution
from simready.workflows.scene_delivery import run_scene_delivery


def make_bundle(root):
    root.mkdir()
    for name in ("input.mp4", "scene.usda", "robot.usda"):
        (root / name).write_bytes(name.encode())
    bundle = {
        "schema_version": 1,
        "scene": "starbucks",
        "source_video": "input.mp4",
        "source_video_sha256": digest(root / "input.mp4"),
        "scene_usd": "scene.usda",
        "robot_usd": "robot.usda",
        "files": [
            {"path": p.name, "bytes": p.stat().st_size, "sha256": digest(p)}
            for p in sorted(root.iterdir())
        ],
    }
    write_json(root / "bundle.json", bundle)


@pytest.fixture
def source(tmp_path):
    assets = tmp_path / "bundle"
    make_bundle(assets)
    config = tmp_path / "config.json"
    write_json(
        config,
        {
            "schema_version": 1,
            "scene": "starbucks",
            "render": {"width": 640, "height": 360, "frames": 8, "fps": 30},
        },
    )
    return assets, config


def test_plan_has_no_paid_cloud_or_external_worker_and_preserves_venv(tmp_path):
    bundle = tmp_path / "bundle"
    make_bundle(bundle)
    config = Path(__file__).resolve().parents[2] / "examples/scenes/starbucks.json"
    output = tmp_path / "output"
    plan = run_scene_delivery(config, bundle, output, "/venv/bin/python", True)
    assert not output.exists()
    assert not plan["cloud_submission"]
    assert all(s["argv"][0] == "/venv/bin/python" for s in plan["stages"])
    assert [s["name"] for s in plan["stages"]] == ["observations", "render"]
    (bundle / "scene.usda").write_bytes(b"changed")
    with pytest.raises(ValueError, match="changed"):
        run_scene_delivery(config, bundle, output, dry_run=True)


def test_escape_from_bundle_and_unlisted_entry_are_rejected(tmp_path):
    root = tmp_path / "bundle"
    make_bundle(root)
    bundle = read_json(root / "bundle.json")
    bundle["files"][0]["path"] = "../outside"
    write_json(root / "bundle.json", bundle)
    with pytest.raises(ValueError, match="path"):
        verify_bundle(root)
    bundle["files"] = bundle["files"][1:]
    write_json(root / "bundle.json", bundle)
    with pytest.raises(ValueError, match="entry point"):
        verify_bundle(root)


def test_direct_scene_entry_runs_delivery_validation_before_success(
    tmp_path, monkeypatch
):
    assets = tmp_path / "bundle"
    make_bundle(assets)
    config_path = tmp_path / "config.json"
    config = {
        "schema_version": 1,
        "scene": "starbucks",
        "render": {"width": 640, "height": 360, "frames": 8, "fps": 30},
    }
    write_json(config_path, config)
    output = tmp_path / "output"

    def fake_worker(command, **kwargs):
        if "cafe-video" in command:
            render = output / "render"
            render.mkdir()
            (render / "g2_starbucks_4k.mp4").write_bytes(b"synthetic encoded bytes")
            write_json(
                render / "delivery.json",
                {"frames": 1, "fps": 30, "resolution": [640, 360]},
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(scene_delivery, "run_process", fake_worker)
    verify = scene_delivery.verify_scene_delivery

    def before_publication(*args, **kwargs):
        assert read_json(output / "timing.json")["status"] == "RUNNING"
        return verify(*args, **kwargs)

    monkeypatch.setattr(scene_delivery, "verify_scene_delivery", before_publication)
    with pytest.raises(ValueError, match="does not match"):
        run_scene_delivery(config_path, assets, output)
    assert read_json(output / "timing.json")["status"] == "FAILED"


@pytest.mark.parametrize(
    "error,status",
    [
        (ProcessTimeoutError(0.1, 0.1), "TIMEOUT"),
        (ProcessCancelledError(0.1), "CANCELLED"),
        (KeyboardInterrupt(), "INTERRUPTED"),
    ],
)
def test_delivery_terminal_status_keeps_failed_stage_timing(
    source, tmp_path, monkeypatch, error, status
):
    assets, config = source
    output = tmp_path / "out"

    def fail(*args, **kwargs):
        assert 0 < kwargs["timeout"] <= 0.25
        raise error

    monkeypatch.setattr(scene_delivery, "run_process", fail)
    with pytest.raises(type(error)):
        run_scene_delivery(config, assets, output, timeout_s=0.25)
    timing = read_json(output / "timing.json")
    assert timing["status"] == status
    assert timing["stages"][0]["status"] == status
    assert timing["stages"][0]["returncode"] is None
    assert timing["stages"][0]["wall_s"] >= 0


@pytest.mark.parametrize(
    "error,status",
    [
        (ProcessTimeoutError(0.1, 0.1), "TIMEOUT"),
        (ProcessCancelledError(0.1), "CANCELLED"),
        (KeyboardInterrupt(), "INTERRUPTED"),
    ],
)
def test_internal_scene_config_entry_preserves_terminal_state(
    source, tmp_path, monkeypatch, error, status
):
    assets, config = source
    output = tmp_path / "execution"

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(scene_execution, "run_process", fail)
    command = ["--config", str(config), "--assets", str(assets), "--out", str(output)]
    if isinstance(error, KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            scene_execution.main(command)
    else:
        assert scene_execution.main(command) == 2
    assert (output / "scene.json").read_bytes() == config.read_bytes()
    state = read_json(output / "pipeline-status.json")
    assert state["status"] == state["stage_status"] == status
    assert state["stages"][-1]["name"] == "environment"
    assert state["stages"][-1]["status"] == status
    assert state["stages"][-1]["wall_s"] >= 0


def test_public_dry_run_never_acquires_a_device(source, tmp_path, monkeypatch):
    assets, config = source
    monkeypatch.setattr(
        scene_delivery, "device_lease", lambda **k: pytest.fail("dry run leased GPU")
    )
    output = tmp_path / "out"
    assert (
        scene_delivery.main(
            [
                "--config",
                str(config),
                "--assets",
                str(assets),
                "--out",
                str(output),
                "--dry-run",
            ]
        )
        == 0
    )
    assert not output.exists()


def test_public_entry_passes_lease_to_processes_and_releases_on_failure(
    source, tmp_path, monkeypatch
):
    assets, config = source
    closed = []

    @contextmanager
    def lease(**kwargs):
        assert kwargs["resource_kind"] == "rtx"
        try:
            yield SimpleNamespace(
                environment={"CUDA_VISIBLE_DEVICES": "GPU-fixture"}, pass_fds=(41,)
            )
        finally:
            closed.append(True)

    def fail(*args, **kwargs):
        assert kwargs["env"]["CUDA_VISIBLE_DEVICES"] == "GPU-fixture"
        assert kwargs["pass_fds"] == (41,)
        raise ProcessCancelledError(0.1)

    monkeypatch.setattr(scene_delivery, "device_lease", lease)
    monkeypatch.setattr(scene_delivery, "run_process", fail)
    with pytest.raises(ProcessCancelledError):
        scene_delivery.main(
            [
                "--config",
                str(config),
                "--assets",
                str(assets),
                "--out",
                str(tmp_path / "out"),
            ]
        )
    assert closed == [True]


def test_delivery_workers_share_one_total_budget(source, tmp_path, monkeypatch):
    assets, config = source
    output = tmp_path / "out"
    budgets = []

    def worker(command, **kwargs):
        budgets.append(kwargs["timeout"])
        if "cafe-video" in command:
            (output / "render").mkdir()
            (output / "render/g2_starbucks_4k.mp4").write_bytes(b"fixture")
            write_json(
                output / "render/delivery.json",
                {"frames": 8, "fps": 30, "resolution": [640, 360]},
            )
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(scene_delivery, "run_process", worker)
    assert (
        run_scene_delivery(config, assets, output, timeout_s=10)["status"] == "COMPLETE"
    )
    assert len(budgets) == 2 and 0 < budgets[1] < budgets[0] <= 10
