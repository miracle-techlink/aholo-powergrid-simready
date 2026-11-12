import shutil
import subprocess
import sys

import numpy as np
import pytest
from PIL import Image
from scipy.spatial.transform import Rotation

from legacy.rsi_3dgs.detect import DetectionReport, _knn_counts
from legacy.rsi_3dgs.model_io import GaussianModel, load_model, model_digest, save_model
from legacy.rsi_3dgs.repair import color_blend, inpaint_from_views
from legacy.scene_bench.align_reconstruction import residuals, robust_fit
from legacy.warehouse_3dgs.npz_to_ply import rgb_to_fdc
from simready_bridge.geometry import (
    look_at_c2w,
    reorder_quaternion,
    rigid_inverse,
    usd_look_at_quaternion,
)
from simready_bridge.ingest.frames import extract_frames, hamming_distance


def model(n=4):
    return GaussianModel(
        means=np.arange(n * 3, dtype=np.float32).reshape(n, 3) * 0.01,
        scales=np.full((n, 3), 0.01, dtype=np.float32),
        quats=np.tile(np.array([0, 0, 0, 1], dtype=np.float32), (n, 1)),
        rgb=np.full((n, 3), 0.5, dtype=np.float32),
        opacity=np.full(n, 0.9, dtype=np.float32),
    )


@pytest.mark.parametrize(
    "eye,target",
    [([0, 0, 0], [1, 0, 0]), ([3, -2, 4], [1, 5, 1]), ([0, 0, 0], [-1, 0, 0])],
)
def test_camera_center_projection_and_usd_orientation(eye, target):
    c2w = look_at_c2w(eye, target)
    w2c = rigid_inverse(c2w)
    np.testing.assert_allclose(w2c @ np.r_[eye, 1], [0, 0, 0, 1], atol=1e-12)
    projected = w2c @ np.r_[target, 1]
    np.testing.assert_allclose(projected[:2], 0, atol=1e-12)
    assert projected[2] > 0
    np.testing.assert_allclose(c2w @ w2c, np.eye(4), atol=1e-12)
    quaternion = reorder_quaternion(usd_look_at_quaternion(eye, target), "wxyz", "xyzw")
    forward = Rotation.from_quat(quaternion).apply([0, 0, -1])
    wanted = np.subtract(target, eye)
    wanted = wanted / np.linalg.norm(wanted)
    np.testing.assert_allclose(forward, wanted, atol=1e-12)


def test_camera_rejects_degenerate_vectors():
    for target in ([0, 0, 0], [0, 0, 1], [float("nan"), 1, 1]):
        with pytest.raises(ValueError):
            look_at_c2w([0, 0, 0], target)
    with pytest.raises(ValueError):
        rigid_inverse(np.zeros((4, 4)))


def test_gaussian_roundtrip_no_fake_camera_and_pose_in_digest(tmp_path):
    value = model()
    target = tmp_path / "model.npz"
    save_model(value, target)
    loaded = load_model(target)
    assert loaded.K is None and loaded.w2cs is None
    assert model_digest(value) == model_digest(loaded)
    assert model_digest(value.with_arrays(w2cs=np.eye(4)[None])) != model_digest(value)
    with pytest.raises(ValueError):
        value.with_arrays(w2cs=np.zeros((1, 4, 4)))
    np.testing.assert_allclose(
        rgb_to_fdc(np.array([0, 1])) * 0.28209479177387814 + 0.5, [0, 1], atol=1e-7
    )


def test_knn_and_color_repair_match_small_exact_reference():
    value = model(12)
    distance = np.linalg.norm(value.means[:, None] - value.means[None, :], axis=2)
    np.testing.assert_array_equal(
        _knn_counts(value.means, 3, 0.1), (distance < 0.1).sum(1) - 1
    )
    colors = value.rgb.copy()
    colors[4] = [1, 0, 0]
    value = value.with_arrays(rgb=colors)
    # DetectionReport fields are intentionally explicit in this fixture.
    report = DetectionReport("color", np.array([4]), {})
    result = color_blend(value, report, k=3, strength=1)
    np.testing.assert_allclose(result.model.rgb[4], [0.5, 0.5, 0.5])
    np.testing.assert_array_equal(value.rgb[4], [1, 0, 0])


def test_fill_caps_count_and_preserves_float_colors(tmp_path):
    points = np.array([[i * 0.02, 0, 0] for i in range(10)], dtype=np.float32)
    np.savez(
        tmp_path / "pc_000.npz", pts=points, rgb=np.full((10, 3), 0.8, dtype=np.float32)
    )
    result = inpaint_from_views(
        model(0),
        center=[0, 0],
        radius=1,
        target_density=100,
        max_fill=3,
        views_dir=tmp_path,
        voxel=0.01,
    )
    assert result.n_changed == 3
    np.testing.assert_allclose(result.model.rgb, 0.8)


def test_large_camera_registration_does_not_materialize_combinations():
    source = np.random.default_rng(7).normal(size=(250, 3))
    target = source * 2 + [3, -2, 1]
    target[:10] += 8
    fit, keep = robust_fit(source, target, 0.001)
    assert keep.sum() == 240
    assert residuals(fit, source[keep], target[keep]).max() < 1e-8


def test_hash_distance_counts_bits():
    a = np.zeros(256, bool)
    b = a.copy()
    b[:7] = True
    assert hamming_distance(a, b) == 7


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_extraction_and_manifest(tmp_path):
    frames = tmp_path / "source"
    frames.mkdir()
    rng = np.random.default_rng(3)
    for index in range(6):
        Image.fromarray(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)).save(
            frames / f"{index:02d}.png"
        )
    video = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-framerate",
            "2",
            "-i",
            str(frames / "%02d.png"),
            "-c:v",
            "ffv1",
            str(video),
        ],
        check=True,
    )
    result = extract_frames(
        video, tmp_path / "out", fps=2, min_laplacian=0, dedup_bits=0, heldout_every=2
    )
    assert len(result["frames"]) == 6 and {r["split"] for r in result["frames"]} == {
        "train",
        "heldout",
    }
    assert all(len(r["sha256"]) == 64 for r in result["frames"])


def test_legacy_entries_import_without_isaac_cuda_or_network(tmp_path):
    names = [
        "train_3dgs",
        "train_3dgs_depth",
        "fuse_pc",
        "render_novel",
        "render_pc",
        "render_warehouse",
        "fast_render",
        "stage_scene",
        "profile_isaac",
        "benchmark_render_variants",
        "pipeline_3dgs_to_isaac",
    ]
    script = (
        "import importlib,sys; names="
        + repr(names)
        + '; [importlib.import_module("legacy.warehouse_3dgs."+name) for name in names]; importlib.import_module("legacy.s1_aholo.submit_panel"); assert "torch" not in sys.modules and "isaaclab" not in sys.modules and "scene_recon" not in sys.modules'
    )
    subprocess.run([sys.executable, "-c", script], check=True, timeout=10)
