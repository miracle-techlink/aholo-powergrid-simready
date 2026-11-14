import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

pytest.importorskip("pxr")

from examples.starbucks_grasp.align_original_scene import plane, table_fit


def test_source_table_fit_recovers_noisy_support():
    rng = np.random.default_rng(25)
    angles = rng.uniform(-np.pi, np.pi, 6000)
    radius = 0.35 * np.sqrt(rng.uniform(0, 1, len(angles)))
    x = 3.4 + radius * np.cos(angles)
    y = 1.16 + radius * np.sin(angles)
    z = 0.02 * x - 0.01 * y + 0.03 + rng.normal(0, 0.002, len(x))
    result = table_fit(np.c_[x, y, z])
    np.testing.assert_allclose(result["center_old"][:2], [3.4, 1.16], atol=0.008)
    assert abs(result["radius_old"] - 0.35) < 0.015


def test_floor_plane_fit_rejects_vertical_outliers():
    rng = np.random.default_rng(26)
    xy = rng.uniform(-2, 2, (2000, 2))
    z = 0.02 * xy[:, 0] - 0.03 * xy[:, 1] - 0.7 + rng.normal(0, 0.002, len(xy))
    z[:100] += 0.15
    np.testing.assert_allclose(plane(np.c_[xy, z]), [0.02, -0.03, -0.7], atol=0.001)


def test_original_scene_requirement_rejects_rebuilt_appearance(tmp_path):
    scene = tmp_path / "assets/scene"
    scene.mkdir(parents=True)
    (scene / "scene.usda").write_text("#usda 1.0\n")
    data = b"placeholder source"
    (scene / "original_video_splats.usdc").write_bytes(data)
    (scene / "scene_contract.json").write_text(
        json.dumps(
            {
                "appearance": "parametric_rebuilt_corner",
                "render_table_proxies": False,
                "visual_gaussians_removed": 0,
                "source_usd_sha256": hashlib.sha256(data).hexdigest(),
            }
        )
    )
    root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            str(root / "examples/starbucks_grasp/run.py"),
            "--assets",
            str(scene.parent),
            "--scene",
            "scene/scene.usda",
            "--out",
            str(tmp_path / "out"),
            "--require-original-video",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert "Original reconstruction scene contract failed" in result.stderr
    assert not (tmp_path / "out").exists()
