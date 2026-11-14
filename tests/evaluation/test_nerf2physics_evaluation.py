import math

import pytest

from simready.core.artifacts import write_json
from simready.evaluation.nerf2physics import evaluate_mass_predictions


def test_independent_metrics_match_official_definitions(tmp_path):
    predictions, truths = tmp_path / "predictions.json", tmp_path / "truth.json"
    write_json(predictions, {"A_view": [1.0, 3.0], "B_view": [3.0, 5.0]})
    write_json(truths, {"A": 1.0, "B": 8.0})
    report = evaluate_mass_predictions(
        predictions, truths, ["A_view", "B_view"], tmp_path / "score.json"
    )
    assert report["status"] == "SCORED" and report["prediction_coverage"] == 1
    assert report["metrics"] == pytest.approx(
        {
            "ADE_kg": 2.5,
            "ALDE": math.log(2),
            "APE": 0.75,
            "MnRE": 0.5,
            "proposal_range_coverage": 0.5,
        }
    )
    assert report["physical_validation_passed"] is False


def test_missing_and_invalid_predictions_remain_in_coverage_denominator(tmp_path):
    predictions, truths = tmp_path / "predictions.json", tmp_path / "truth.json"
    write_json(
        predictions, {"A_view": [1.0, 1.0], "B_view": [5.0, 2.0], "EXTRA": [1.0, 1.0]}
    )
    write_json(truths, {"A": 1.0, "B": 2.0, "C": 3.0})
    report = evaluate_mass_predictions(
        predictions, truths, ["A_view", "B_view", "C_view"], tmp_path / "score.json"
    )
    assert report["status"] == "INCOMPLETE"
    assert report["prediction_coverage"] == pytest.approx(1 / 3)
    assert len(report["errors"]) == 2
    assert report["extra_prediction_ids"] == ["EXTRA"]


def test_no_valid_prediction_does_not_report_zero_error(tmp_path):
    predictions, truths = tmp_path / "predictions.json", tmp_path / "truth.json"
    write_json(predictions, {})
    write_json(truths, {"A": 1.0})
    report = evaluate_mass_predictions(
        predictions, truths, ["A_view"], tmp_path / "score.json"
    )
    assert report["prediction_coverage"] == 0
    assert all(value is None for value in report["metrics"].values())


def test_evaluation_refuses_to_overwrite_ground_truth(tmp_path):
    predictions, truths = tmp_path / "predictions.json", tmp_path / "truth.json"
    write_json(predictions, {"A": [1.0, 1.0]})
    write_json(truths, {"A": 1.0})
    before = truths.read_bytes()
    with pytest.raises(ValueError, match="already exists"):
        evaluate_mass_predictions(predictions, truths, ["A"], truths)
    assert truths.read_bytes() == before
