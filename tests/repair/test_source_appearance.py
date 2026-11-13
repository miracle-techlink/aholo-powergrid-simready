import cv2
import numpy as np
import pytest

from simready.core.artifacts import scene_dependency_hashes
from simready.repair.source_texture import (
    SourceTextureBaker,
    compose_sources,
    sharpness_score,
)
from simready.repair.structural_completion import surface_grid


def test_coherent_sources_preserve_best_interior_and_unknown_pixels():
    fallback = np.full((40, 40, 3), 50, np.uint8)
    valid = np.zeros((40, 40), bool)
    valid[5:35, 5:35] = True
    image, owner, observed = compose_sources(
        [
            {
                "image": np.full_like(fallback, 80),
                "valid": valid,
                "score": 1,
                "camera_index": 2,
            },
            {
                "image": np.full_like(fallback, 180),
                "valid": valid,
                "score": 2,
                "camera_index": 3,
            },
        ],
        fallback,
    )
    assert (image[15:25, 15:25] == 180).all()
    assert (owner[15:25, 15:25] == 3).all()
    assert (image[0] == 50).all() and not observed[0].any()


def test_sharpness_prefers_supported_detail_over_blur():
    rng = np.random.default_rng(9)
    image = rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)
    valid = np.ones((128, 128), bool)
    assert (
        sharpness_score(image, valid)
        > sharpness_score(cv2.GaussianBlur(image, (15, 15), 3), valid) * 5
    )


def test_baker_never_selects_reserved_camera(tmp_path):
    rng = np.random.default_rng(10)
    image = rng.integers(0, 256, (128, 128, 3), dtype=np.uint8)
    cameras = []
    for i in range(8):
        name = f"frame{i}.png"
        cv2.imwrite(str(tmp_path / name), image)
        cameras.append(
            {
                "image_name": name,
                "world_to_camera": np.eye(4).tolist(),
                "camera_to_world": np.eye(4).tolist(),
                "K": [[80, 0, 64], [0, 80, 64], [0, 0, 1]],
                "camera_params": [80, 64, 64, 0],
                "resolution": [128, 128],
                "timestamp_s": i,
            }
        )
    points, _ = surface_grid(2, [0, 0, 2], [[-1, 1], [-1, 1]], 64, 64)
    baker = SourceTextureBaker(cameras, tmp_path, np.zeros((0, 3)))
    result = baker.bake(points, (64, 64), tmp_path / "out.png")
    selected = {r["camera_index"] for r in result["sources"]}
    assert selected and selected.isdisjoint({0, 7})
    assert result["observed_fraction"] > 0.9


def test_texture_change_invalidates_scene_dependency_evidence(tmp_path):
    (tmp_path / "scene.usda").write_text("#usda 1.0\n")
    (tmp_path / "textures").mkdir()
    path = tmp_path / "textures/a.png"
    path.write_bytes(b"first texture")
    first = scene_dependency_hashes(tmp_path)
    path.write_bytes(b"changed texture")
    second = scene_dependency_hashes(tmp_path)
    assert first["scene.usda"] == second["scene.usda"]
    assert first["textures/a.png"] != second["textures/a.png"]


def test_beveled_box_is_closed_and_normals_have_correct_interpolation():
    pytest.importorskip("pxr")
    from pxr import Usd

    from simready.repair.visible_geometry import VisibleGeometry

    stage = Usd.Stage.CreateInMemory()
    geometry = VisibleGeometry(stage, "/Scene")
    material = geometry.material("Wood", [0.5, 0.3, 0.1])
    mesh = geometry.box("Table", [0, 0, 1], [1, 1, 0.08], material, 0.01)
    counts = np.asarray(mesh.GetFaceVertexCountsAttr().Get())
    indices = np.asarray(mesh.GetFaceVertexIndicesAttr().Get()).reshape(-1, 3)
    edges = np.sort(
        np.vstack([indices[:, [0, 1]], indices[:, [1, 2]], indices[:, [2, 0]]]), axis=1
    )
    _, multiplicity = np.unique(edges, axis=0, return_counts=True)
    assert (multiplicity == 2).all()
    assert mesh.GetNormalsInterpolation() == "faceVarying"
    assert len(mesh.GetNormalsAttr().Get()) == counts.sum()
