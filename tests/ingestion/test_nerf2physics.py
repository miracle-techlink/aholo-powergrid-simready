import copy
import runpy

import numpy as np
import pytest

from simready.core.artifacts import read_json, write_json
from simready.core.evidence import reference
from simready.ingestion.nerf2physics import regress_features, run_estimation
from tests.paths import REPOSITORY_ROOT


@pytest.fixture
def request_path(tmp_path):
    create = runpy.run_path(
        str(REPOSITORY_ROOT / "examples/assets/nerf2physics_fixture.py")
    )["create_request"]
    return create(tmp_path / "inputs")


def test_regression_matches_visibility_normalization_and_softmax_equations():
    patches = np.array([[[1.0, 0.0], [50.0, 50.0]], [[0.0, 2.0], [30.0, 30.0]]])
    visible = np.array([[True, False], [True, False]])
    intervals, weights, valid = regress_features(
        patches, visible, np.eye(2), [[100.0, 200.0], [900.0, 1000.0]]
    )
    vector = np.array([0.5, 1.0]) / np.linalg.norm([0.5, 1.0])
    expected = np.exp(vector / 0.1 - max(vector / 0.1))
    expected /= expected.sum()
    np.testing.assert_allclose(weights[0], expected)
    np.testing.assert_allclose(
        intervals[0], expected @ np.array([[100.0, 200.0], [900.0, 1000.0]])
    )
    assert valid.tolist() == [True, False]


@pytest.mark.parametrize("corruption", ["unobserved", "zero", "nan", "visibility_type"])
def test_invalid_features_are_not_silently_assigned_properties(corruption):
    patches = np.ones((1, 1, 2))
    visible = np.ones((1, 1), dtype=bool)
    if corruption == "unobserved":
        visible[:] = False
    elif corruption == "zero":
        patches[:] = 0
    elif corruption == "nan":
        patches[:] = np.nan
    else:
        visible = visible.astype(int)
    with pytest.raises(ValueError):
        regress_features(patches, visible, [[1.0, 0.0]], [[100.0, 200.0]])


def test_synthetic_mass_candidate_records_unknowns_and_all_sources(
    request_path, tmp_path
):
    candidate = read_json(run_estimation(request_path, tmp_path / "out"))
    assert candidate["object_id"] == "fixture-cup"
    assert candidate["method"]["execution_mode"] == "synthetic_fixture"
    assert candidate["method"]["new_visual_inference_performed"] is False
    assert candidate["method"]["upstream_commit"] is None
    assert candidate["properties"]["mass_kg"]["interval"] == pytest.approx(
        [0.04692, 0.04824]
    )
    assert "dynamic_friction" in candidate["unknown_fields"]
    assert candidate["method"]["parameters"]["scale_evidence"] in candidate["evidence"]
    assert reference(request_path) in candidate["evidence"]
    with pytest.raises(ValueError, match="already exists"):
        run_estimation(request_path, tmp_path / "out")


@pytest.mark.parametrize("field", ["payload", "association", "scale_evidence"])
def test_changed_source_is_rejected_before_publishing_candidate(
    request_path, tmp_path, field
):
    config = read_json(request_path)
    ref = config["association"]["evidence"] if field == "association" else config[field]
    from pathlib import Path

    Path(ref["path"]).write_text("changed source")
    with pytest.raises(ValueError, match="SHA256 mismatch"):
        run_estimation(request_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("mode", ["feature_regression", "official_prediction_replay"])
def test_real_modes_cannot_use_unpinned_fixture_provenance(
    request_path, tmp_path, mode
):
    config = read_json(request_path)
    config["execution_mode"] = mode
    write_json(request_path, config)
    with pytest.raises(ValueError, match="pinned upstream commit"):
        run_estimation(request_path, tmp_path / "out")


def test_material_order_and_units_are_part_of_feature_contract(request_path, tmp_path):
    config = read_json(request_path)
    payload = read_json(config["payload"]["path"])
    for index, mutation in enumerate(("order", "unit")):
        changed = copy.deepcopy(payload)
        if mutation == "order":
            changed["materials"].reverse()
        else:
            changed["materials"][0]["unit"] = "g/cm^3"
        write_json(config["payload"]["path"], changed)
        config["payload"] = reference(config["payload"]["path"])
        write_json(request_path, config)
        with pytest.raises(ValueError, match="order|units"):
            run_estimation(request_path, tmp_path / f"out-{index}")


def test_friction_retains_counterface_and_does_not_infer_mass(request_path, tmp_path):
    config = read_json(request_path)
    config["property_name"] = "friction"
    payload = read_json(config["payload"]["path"])
    for row in payload["materials"]:
        row.update(interval=[0.2, 0.4], unit="1")
    write_json(config["payload"]["path"], payload)
    config["payload"] = reference(config["payload"]["path"])
    write_json(request_path, config)
    candidate = read_json(run_estimation(request_path, tmp_path / "out"))
    assert candidate["properties"]["dynamic_friction"]["context"] == {
        "counterface": "fabric",
        "regime": "kinetic",
    }
    assert "mass_kg" not in candidate["properties"]


def test_official_prediction_replay_is_distinct_from_new_regression(
    request_path, tmp_path
):
    config = read_json(request_path)
    # These are fabricated provenance fixtures; no official sample is claimed.
    source = tmp_path / "upstream.py"
    source.write_text("# fixture source, not downloaded upstream\n")
    license_file = tmp_path / "LICENSE"
    license_file.write_text("fixture MIT license marker")
    config["upstream"].update(
        commit="a" * 40,
        source_files=[reference(source)],
        license_file=reference(license_file),
    )
    config["execution_mode"] = "official_prediction_replay"
    predictions = tmp_path / "predictions.json"
    write_json(predictions, {"synthetic-object": [1.2, 1.8]})
    config["payload"] = reference(predictions)
    write_json(request_path, config)
    candidate = read_json(run_estimation(request_path, tmp_path / "out"))
    assert candidate["method"]["execution_mode"] == "official_prediction_replay"
    assert candidate["properties"]["mass_kg"]["value"] == 1.5
    assert reference(source) in candidate["evidence"]


def test_mass_requires_explicit_scale_evidence(request_path, tmp_path):
    config = read_json(request_path)
    del config["scale_evidence"]
    write_json(request_path, config)
    with pytest.raises(KeyError, match="scale_evidence"):
        run_estimation(request_path, tmp_path / "out")


def test_source_change_between_resolution_and_read_cannot_rebind_hash(
    request_path, tmp_path, monkeypatch
):
    from simready.ingestion import nerf2physics

    config = read_json(request_path)
    resolve = nerf2physics.resolve_ref

    def mutate_after_resolution(ref, root):
        path = resolve(ref, root)
        if str(path) == config["payload"]["path"]:
            payload = read_json(path)
            payload["temperature"] = 0.2
            write_json(path, payload)
        return path

    monkeypatch.setattr(nerf2physics, "resolve_ref", mutate_after_resolution)
    with pytest.raises(ValueError, match="Frozen campaign evidence changed"):
        run_estimation(request_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
