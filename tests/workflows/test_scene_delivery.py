"""A reviewed source cannot silently become another scene or changed asset."""

from pathlib import Path

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.simulation.asset_bundle import verify_bundle
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
