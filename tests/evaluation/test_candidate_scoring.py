"""Known faults and bypass attempts against the unified benchmark boundary."""

import subprocess
import sys

import numpy as np
import pytest
from PIL import Image
from scipy.ndimage import gaussian_filter

from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference
from simready.evaluation.observations import (
    depth_metrics,
    image_metrics,
    instance_metrics,
)
from simready.evaluation.scoring import evaluate_candidate
from tests.fixtures.scene_candidate import make_scene_candidate
from tests.paths import REPOSITORY_ROOT

ROOT = REPOSITORY_ROOT


def save_protocol(p, c, protocol, candidate):
    write_json(p, protocol)
    candidate["protocol_sha256"] = digest(p)
    write_json(c, candidate)


def test_clean_pixels_pass_checks_but_never_authorize_publication(tmp_path):
    p, c, *_ = make_scene_candidate(tmp_path)
    result = evaluate_candidate(p, c, tmp_path / "out")
    assert result["status"] == "PASS" and result["release_authorized"] is False
    assert result["wall_seconds"] > 0
    assert read_json(tmp_path / "out/defects.json")["defects"] == []


def test_verifier_snapshot_preserves_modules_with_the_same_filename(tmp_path):
    protocol, candidate, *_ = make_scene_candidate(tmp_path)
    output = tmp_path / "out"
    result = evaluate_candidate(protocol, candidate, output)
    files = result["evaluator_files"]
    assert {"core/__init__.py", "evaluation/__init__.py"} <= files.keys()
    for name, expected_digest in files.items():
        assert digest(output / "verifier_code" / name) == expected_digest


def test_ssim_matches_independent_library_definition():
    from skimage.metrics import structural_similarity

    rng = np.random.default_rng(8)
    a = rng.random((32, 32, 3))
    b = np.clip(a + 0.02, 0, 1)
    ours = image_metrics(a, b)["ssim"]
    expected = structural_similarity(
        a,
        b,
        channel_axis=2,
        data_range=1,
        gaussian_weights=True,
        sigma=1.5,
        use_sample_covariance=False,
    )
    assert abs(ours - expected) < 1e-10


@pytest.mark.parametrize("fault", ["blur", "hole", "shift", "color"])
def test_observer_detects_visible_faults(tmp_path, fault):
    p, c, protocol, candidate, image = make_scene_candidate(tmp_path)
    altered = {
        "blur": lambda: gaussian_filter(image.astype(float), sigma=(3, 3, 0)).astype(
            "uint8"
        ),
        "hole": lambda: np.zeros_like(image),
        "shift": lambda: np.roll(image, 6, axis=1),
        "color": lambda: 255 - image,
    }[fault]()
    Image.fromarray(altered).save(tmp_path / "render.png")
    candidate["views"][0]["rgb"] = reference(tmp_path / "render.png")
    write_json(c, candidate)
    result = evaluate_candidate(p, c, tmp_path / "out")
    assert result["status"] == "FAIL"
    defects = read_json(tmp_path / "out/defects.json")["defects"]
    assert defects and any(d["evidence"] for d in defects)
    assert all(d["root_cause"] == "UNCONFIRMED" for d in defects)


def test_small_critical_error_cannot_hide_in_good_global_average(tmp_path):
    p, c, protocol, candidate, image = make_scene_candidate(tmp_path)
    image[16:32, 16:32] = 0
    Image.fromarray(image).save(tmp_path / "render.png")
    candidate["views"][0]["rgb"] = reference(tmp_path / "render.png")
    protocol["checks"][1]["limit"] = 10  # full frame passes, critical crop still fails
    save_protocol(p, c, protocol, candidate)
    result = evaluate_candidate(p, c, tmp_path / "out")
    checks = {x["check_id"]: x for x in result["checks"]}
    assert (
        checks["visual"]["status"] == "PASS"
        and checks["critical_roi"]["status"] == "FAIL"
    )
    assert result["status"] == "FAIL"


def test_self_reported_pass_and_physics_flags_are_ignored(tmp_path):
    p, c, protocol, candidate, _ = make_scene_candidate(tmp_path)
    candidate.update(
        passed=True, physics={"passed": True}, metrics={"physics.pass_rate": 1}
    )
    protocol["checks"].append(
        dict(
            check_id="physical",
            track="physics",
            metric="physics.pass_rate",
            op=">=",
            limit=1,
            required=True,
            severity="critical",
            calibration="structural",
        )
    )
    save_protocol(p, c, protocol, candidate)
    result = evaluate_candidate(p, c, tmp_path / "out")
    assert result["status"] == "INCOMPLETE" and "physical" in result["missing_checks"]


def test_missing_views_are_not_silently_dropped(tmp_path):
    p, c, _, candidate, _ = make_scene_candidate(tmp_path)
    candidate["views"] = []
    write_json(c, candidate)
    result = evaluate_candidate(p, c, tmp_path / "out")
    assert result["status"] == "FAIL"
    assert result["checks"][0]["value"] == 0 and result["missing_checks"]


@pytest.mark.parametrize(
    "fault",
    [
        "empty_views",
        "duplicate_views",
        "fit_leak",
        "unknown_split",
        "empty_checks",
        "missing_tracks",
    ],
)
def test_invalid_protocol_is_rejected(tmp_path, fault):
    p, c, protocol, candidate, _ = make_scene_candidate(tmp_path)
    if fault == "empty_views":
        protocol["views"] = []
    elif fault == "duplicate_views":
        protocol["views"] *= 2
    elif fault == "fit_leak":
        protocol["fit_frame_ids"] = ["test-frame"]
    elif fault == "unknown_split":
        protocol["views"][0]["split"] = "train"
    elif fault == "empty_checks":
        protocol["checks"] = []
    elif fault == "missing_tracks":
        protocol["profile"] = "simready"
    save_protocol(p, c, protocol, candidate)
    with pytest.raises(ValueError):
        evaluate_candidate(p, c, tmp_path / "out")
    assert not (tmp_path / "out/scorecard.json").exists()


def test_candidate_training_access_to_test_frame_rejected(tmp_path):
    p, c, _, candidate, _ = make_scene_candidate(tmp_path)
    candidate["fit_frame_ids"] = ["test-frame"]
    write_json(c, candidate)
    with pytest.raises(ValueError, match="overlap"):
        evaluate_candidate(p, c, tmp_path / "out")


@pytest.mark.parametrize("file", ["source.png", "render.png", "model.bin"])
def test_stale_input_hash_rejected(tmp_path, file):
    p, c, *_ = make_scene_candidate(tmp_path)
    with (tmp_path / file).open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="SHA256"):
        evaluate_candidate(p, c, tmp_path / "out")


def test_pinned_protocol_cannot_change_between_parent_and_worker(tmp_path):
    p, c, protocol, candidate, _ = make_scene_candidate(tmp_path)
    expected = digest(p)
    protocol["checks"][1]["limit"] = 0
    save_protocol(p, c, protocol, candidate)
    with pytest.raises(ValueError, match="Protocol changed"):
        evaluate_candidate(p, c, tmp_path / "out", expected_protocol=expected)


def test_missing_threshold_never_passes(tmp_path):
    p, c, protocol, candidate, _ = make_scene_candidate(tmp_path)
    protocol["checks"][1].update(limit=None, calibration="pending")
    save_protocol(p, c, protocol, candidate)
    result = evaluate_candidate(p, c, tmp_path / "out")
    assert result["status"] == "INCOMPLETE"


def test_sealed_evaluation_does_not_emit_repair_list(tmp_path):
    p, c, protocol, candidate, _ = make_scene_candidate(tmp_path)
    protocol["phase"] = "sealed_release"
    protocol["views"][0]["split"] = "sealed_release"
    save_protocol(p, c, protocol, candidate)
    result = evaluate_candidate(p, c, tmp_path / "out")
    assert not (tmp_path / "out/defects.json").exists()
    assert result["release_authorized"] is False


def test_invalid_depth_is_penalized_in_coverage():
    ref = np.ones((8, 8))
    pred = ref.copy()
    pred[:4] = 0
    d = depth_metrics(ref, pred)
    assert d["valid_fraction"] == 0.5 and d["mae_m"] == 0
    d = depth_metrics(ref, np.zeros_like(ref))
    assert d["valid_fraction"] == 0 and d["mae_m"] is None
    with pytest.raises(ValueError):
        depth_metrics(np.zeros_like(ref), pred)


def test_missing_extra_and_wrong_controls_are_separate():
    ref = [
        dict(instance_id="stop", center_px=[10, 10], category="button", critical=True),
        dict(instance_id="lamp", center_px=[20, 20], category="lamp"),
    ]
    pred = [
        dict(instance_id="lamp", center_px=[23, 24], category="button"),
        dict(instance_id="invented", center_px=[40, 40], category="button"),
    ]
    d = instance_metrics(ref, pred)
    assert (
        d["missing_critical_count"]
        == d["extra_count"]
        == d["wrong_category_count"]
        == 1
    )
    assert d["center_error_p95_px"] == 5
    with pytest.raises(ValueError):
        instance_metrics([], [])
    with pytest.raises(ValueError):
        instance_metrics(ref, ref + ref)


@pytest.mark.parametrize("text", ['{"x":NaN}', '{"x":1,"x":2}'])
def test_ambiguous_json_rejected(tmp_path, text):
    p = tmp_path / "bad.json"
    p.write_text(text)
    with pytest.raises(ValueError):
        read_json(p)


def test_real_subprocess_recomputes_results(tmp_path):
    p, c, *_ = make_scene_candidate(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simready.evaluation",
            "run",
            "--protocol",
            str(p),
            "--candidate",
            str(c),
            "--out",
            str(tmp_path / "out"),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert read_json(tmp_path / "out/scorecard.json")["release_authorized"] is False
