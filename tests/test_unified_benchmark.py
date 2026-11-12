"""Known faults and bypass attempts against the unified benchmark boundary."""

import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from scipy.ndimage import gaussian_filter

from simready_bridge.artifacts import digest, write_json
from simready_bridge.benchmark.contracts import read_json, reference
from simready_bridge.benchmark.evaluator import evaluate
from simready_bridge.benchmark.observer import (
    depth_metrics,
    image_metrics,
    instance_metrics,
)
from simready_bridge.benchmark.scheduler import plan

ROOT = Path(__file__).resolve().parents[1]


def fixture(tmp_path):
    rng = np.random.default_rng(12)
    source = rng.integers(0, 256, (64, 64, 3), dtype=np.uint8)
    for name in ("source", "render"):
        Image.fromarray(source).save(tmp_path / f"{name}.png")
    (tmp_path / "model.bin").write_bytes(b"candidate-model")

    def check(name, track, metric, limit):
        return dict(
            check_id=name,
            track=track,
            metric=metric,
            op=">=",
            limit=limit,
            required=True,
            severity="critical",
            calibration="structural",
            defect_kind="appearance_mismatch",
        )

    protocol = dict(
        schema_version=1,
        benchmark_id="fault-fixture",
        profile="observer_calibration",
        phase="development",
        fit_frame_ids=["train-frame"],
        fit_image_sha256=[],
        views=[
            dict(
                view_id="front",
                asset_id="cabinet",
                frame_id="test-frame",
                split="development",
                reference_kind="synthetic_gt",
                rgb=reference(tmp_path / "source.png"),
                regions=[dict(asset_id="button", bbox_xyxy=[16, 16, 32, 32])],
            )
        ],
        checks=[
            check("coverage", "coverage", "coverage.observation_fraction", 1),
            check("visual", "appearance", "appearance.psnr_db.mean", 22),
            check("critical_roi", "appearance", "appearance.psnr_db.region_min", 22),
        ],
    )
    p = tmp_path / "protocol.json"
    write_json(p, protocol)
    candidate = dict(
        schema_version=1,
        candidate_id="fixture",
        protocol_sha256=digest(p),
        artifacts=[dict(artifact_id="model", **reference(tmp_path / "model.bin"))],
        views=[dict(view_id="front", rgb=reference(tmp_path / "render.png"))],
        fit_frame_ids=[],
        fit_image_sha256=[],
    )
    c = tmp_path / "candidate.json"
    write_json(c, candidate)
    return p, c, protocol, candidate, source


def save_protocol(p, c, protocol, candidate):
    write_json(p, protocol)
    candidate["protocol_sha256"] = digest(p)
    write_json(c, candidate)


def test_clean_pixels_pass_checks_but_never_authorize_publication(tmp_path):
    p, c, *_ = fixture(tmp_path)
    result = evaluate(p, c, tmp_path / "out")
    assert result["status"] == "PASS" and result["release_authorized"] is False
    assert result["wall_seconds"] > 0
    assert read_json(tmp_path / "out/defects.json")["defects"] == []


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
    p, c, protocol, candidate, image = fixture(tmp_path)
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
    result = evaluate(p, c, tmp_path / "out")
    assert result["status"] == "FAIL"
    defects = read_json(tmp_path / "out/defects.json")["defects"]
    assert defects and any(d["evidence"] for d in defects)
    assert all(d["root_cause"] == "UNCONFIRMED" for d in defects)


def test_small_critical_error_cannot_hide_in_good_global_average(tmp_path):
    p, c, protocol, candidate, image = fixture(tmp_path)
    image[16:32, 16:32] = 0
    Image.fromarray(image).save(tmp_path / "render.png")
    candidate["views"][0]["rgb"] = reference(tmp_path / "render.png")
    protocol["checks"][1]["limit"] = 10  # full frame passes, critical crop still fails
    save_protocol(p, c, protocol, candidate)
    result = evaluate(p, c, tmp_path / "out")
    checks = {x["check_id"]: x for x in result["checks"]}
    assert (
        checks["visual"]["status"] == "PASS"
        and checks["critical_roi"]["status"] == "FAIL"
    )
    assert result["status"] == "FAIL"


def test_self_reported_pass_and_physics_flags_are_ignored(tmp_path):
    p, c, protocol, candidate, _ = fixture(tmp_path)
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
    result = evaluate(p, c, tmp_path / "out")
    assert result["status"] == "INCOMPLETE" and "physical" in result["missing_checks"]


def test_missing_views_are_not_silently_dropped(tmp_path):
    p, c, _, candidate, _ = fixture(tmp_path)
    candidate["views"] = []
    write_json(c, candidate)
    result = evaluate(p, c, tmp_path / "out")
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
    p, c, protocol, candidate, _ = fixture(tmp_path)
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
        evaluate(p, c, tmp_path / "out")
    assert not (tmp_path / "out/scorecard.json").exists()


def test_candidate_training_access_to_test_frame_rejected(tmp_path):
    p, c, _, candidate, _ = fixture(tmp_path)
    candidate["fit_frame_ids"] = ["test-frame"]
    write_json(c, candidate)
    with pytest.raises(ValueError, match="overlap"):
        evaluate(p, c, tmp_path / "out")


@pytest.mark.parametrize("file", ["source.png", "render.png", "model.bin"])
def test_stale_input_hash_rejected(tmp_path, file):
    p, c, *_ = fixture(tmp_path)
    with (tmp_path / file).open("ab") as f:
        f.write(b"changed")
    with pytest.raises(ValueError, match="SHA256"):
        evaluate(p, c, tmp_path / "out")


def test_pinned_protocol_cannot_change_between_parent_and_worker(tmp_path):
    p, c, protocol, candidate, _ = fixture(tmp_path)
    expected = digest(p)
    protocol["checks"][1]["limit"] = 0
    save_protocol(p, c, protocol, candidate)
    with pytest.raises(ValueError, match="Protocol changed"):
        evaluate(p, c, tmp_path / "out", expected_protocol=expected)


def test_missing_threshold_never_passes(tmp_path):
    p, c, protocol, candidate, _ = fixture(tmp_path)
    protocol["checks"][1].update(limit=None, calibration="pending")
    save_protocol(p, c, protocol, candidate)
    result = evaluate(p, c, tmp_path / "out")
    assert result["status"] == "INCOMPLETE"


def test_sealed_evaluation_does_not_emit_repair_list(tmp_path):
    p, c, protocol, candidate, _ = fixture(tmp_path)
    protocol["phase"] = "sealed_release"
    protocol["views"][0]["split"] = "sealed_release"
    save_protocol(p, c, protocol, candidate)
    result = evaluate(p, c, tmp_path / "out")
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


def test_scheduler_groups_errors_and_never_executes_legacy_commands(tmp_path):
    p, c, _, candidate, _ = fixture(tmp_path)
    candidate["views"] = []
    write_json(c, candidate)
    evaluate(p, c, tmp_path / "out")
    bundle = read_json(tmp_path / "out/defects.json")
    registry = read_json(ROOT / "configs/benchmark/plugin_registry.v1.json")
    proposed = plan(bundle, registry)
    assert proposed["executed"] is False and proposed["blocked"]
    assert all(a["execution_status"] == "ADAPTER_REQUIRED" for a in proposed["actions"])
    assert plan(bundle, registry, stagnant_rounds=2)["status"] == "NEEDS_NEW_STRATEGY"
    assert plan(bundle, registry, round_index=4)["status"] == "BUDGET_EXHAUSTED"
    bundle["phase"] = "sealed_release"
    with pytest.raises(ValueError):
        plan(bundle, registry)


def test_scheduler_serializes_conflicting_geometry_writes():
    registry = read_json(ROOT / "configs/benchmark/plugin_registry.v1.json")
    bundle = dict(
        phase="development",
        protocol_sha256="p",
        candidate_sha256="c",
        defects=[
            dict(
                defect_id="one",
                kind="surface_hole",
                repairable_now=True,
                asset_ids=["cabinet"],
            ),
            dict(
                defect_id="two",
                kind="missing_instance",
                repairable_now=True,
                asset_ids=["button"],
            ),
        ],
    )
    result = plan(bundle, registry)
    assert (
        len(result["actions"]) == 1
        and result["deferred"][0]["reason"] == "WRITE_CONFLICT"
    )
    assert "physics" in result["actions"][0]["invalidates"]


def test_real_subprocess_recomputes_results(tmp_path):
    p, c, *_ = fixture(tmp_path)
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "simready_bridge.benchmark",
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
