"""Grounded mask ownership remains explicit and preserves original evidence."""

import numpy as np
import pytest

from simready.ingestion.segmentation.grounded import (
    _query_mapping,
    grounded_segment_image,
    resolve_mask_overlap,
    select_detections,
)


def test_nms_only_removes_same_class_duplicates_and_clips_to_image():
    rows = select_detections(
        [[-2, -2, 8, 8], [0, 0, 8, 8], [0, 0, 8, 8], [1, 1, 4, 4]],
        [0.9, 0.8, 0.7, 0.6],
        ["cabinet", "cabinet", "door", "door cabinet"],
        {"cabinet": "cabinet", "door": "door"},
        width=10,
        height=10,
    )
    assert len(rows) == 3
    assert rows[0]["detection_bbox_xyxy"] == [0, 0, 8, 8]
    assert [row["category"] for row in rows] == ["cabinet", "door", "unknown"]
    assert rows[-1]["raw_label"] == "door cabinet"


def test_prompt_alias_uses_exact_phrase_and_does_not_guess_material():
    rows = select_detections(
        [[0, 0, 4, 4]],
        [0.8],
        ["electrical cabinet"],
        {"cabinet": "electrical cabinet"},
        width=10,
        height=10,
    )
    assert rows[0]["category"] == "cabinet"
    assert "material" not in rows[0]


def test_outside_frame_detections_are_discarded_and_cap_is_deterministic():
    rows = select_detections(
        [[-8, 0, -1, 2], [0, 0, 2, 2], [5, 5, 8, 8]],
        [0.9, 0.7, 0.7],
        ["chair"] * 3,
        {"chair": "chair"},
        width=10,
        height=10,
        max_detections=1,
    )
    assert len(rows) == 1
    assert rows[0]["detection_index"] == 1


@pytest.mark.parametrize(
    "queries",
    [
        {},
        {"../escape": "chair"},
        {"chair": ""},
        {"chair": "chair . button"},
        {"chair": "seat", "stool": "seat"},
    ],
)
def test_ambiguous_or_invalid_queries_fail(queries):
    with pytest.raises(ValueError):
        _query_mapping(queries)


@pytest.mark.parametrize(
    "boxes,scores,labels",
    [
        ([[0, 0, 2, 2]], [], ["chair"]),
        ([[0, 0, 0, 2]], [0.9], ["chair"]),
        ([[0, 0, np.inf, 2]], [0.9], ["chair"]),
        ([[0, 0, 2, 2]], [float("nan")], ["chair"]),
        ([[0, 0, 2, 2]], [True], ["chair"]),
        ([[0, 0, 2, 2]], [0.7], [5]),
    ],
)
def test_bad_model_outputs_fail(boxes, scores, labels):
    with pytest.raises(ValueError):
        select_detections(
            boxes, scores, labels, {"chair": "chair"}, width=10, height=10
        )


def test_robot_priority_preserves_original_masks_and_records_removed_object():
    candidates = [
        {"asset_id": "table_1", "category": "table", "score": 0.95},
        {"asset_id": "robot_1", "category": "robot", "score": 0.4},
        {"asset_id": "floor_1", "category": "floor", "score": 0.8},
    ]
    masks = np.zeros((3, 6, 8), dtype=bool)
    masks[0, 2:4, 3:5] = True
    masks[1, 1:5, 2:6] = True
    masks[2] = True
    original = masks.copy()
    accepted, rejected = resolve_mask_overlap(candidates, masks, min_area_px=1)
    assert np.array_equal(masks, original)
    assert [row[0]["category"] for row in accepted] == ["robot", "floor"]
    assert not (accepted[0][1] & accepted[1][1]).any()
    assert np.array_equal(accepted[0][1] | accepted[1][1], masks[2])
    assert rejected[0]["category"] == "table"
    assert rejected[0]["removed_pixel_count"] == 4
    assert rejected[0]["removed_fraction"] == 1.0
    assert rejected[0]["ambiguous"] is True
    assert accepted[1][0]["removed_pixel_count"] == 16
    assert accepted[1][0]["mask_status"] == "derived_nonoverlapping_proposal"


def test_nested_door_is_explicitly_ambiguous_not_silently_certified_absent():
    candidates = [
        {"asset_id": "cabinet", "category": "cabinet", "score": 0.7},
        {"asset_id": "door", "category": "door", "score": 0.5},
    ]
    masks = np.ones((2, 3, 3), dtype=bool)
    masks[1, 0, :] = False
    accepted, rejected = resolve_mask_overlap(candidates, masks, min_area_px=1)
    assert len(accepted) == len(rejected) == 1
    assert rejected[0]["raw_area_px"] == 6
    assert rejected[0]["reason"] == "overlap_removed_or_too_small"
    assert rejected[0]["ambiguous"] is True


def test_too_small_rejected_region_does_not_reserve_pixels():
    candidates = [
        {"asset_id": "robot", "category": "robot", "score": 0.8},
        {"asset_id": "floor", "category": "floor", "score": 0.6},
    ]
    masks = np.ones((2, 3, 3), dtype=bool)
    masks[0] = False
    masks[0, 1, 1] = True
    accepted, rejected = resolve_mask_overlap(candidates, masks, min_area_px=2)
    assert accepted[0][0]["area_px"] == 9
    assert rejected[0]["raw_area_px"] == 1


def test_empty_detector_outputs_are_representable():
    accepted, rejected = resolve_mask_overlap([], np.zeros((0, 5, 5), bool))
    assert accepted == rejected == []


def test_large_pixel_remnants_are_rejected_when_most_of_object_was_clipped():
    candidates = [
        {"asset_id": "cabinet", "category": "cabinet", "score": 0.9},
        {"asset_id": "door", "category": "door", "score": 0.8},
    ]
    masks = np.ones((2, 10, 10), dtype=bool)
    masks[0, 0, :] = False
    accepted, rejected = resolve_mask_overlap(candidates, masks, min_area_px=5)
    assert len(accepted) == len(rejected) == 1
    assert rejected[0]["area_px"] == 10
    assert rejected[0]["reason"] == "insufficient_retained_area"
    assert rejected[0]["removed_fraction"] == 0.9


def test_bad_mask_values_are_not_silently_cast_to_foreground():
    with pytest.raises(ValueError, match="bool array"):
        resolve_mask_overlap([], np.zeros((0, 5, 5), np.uint8))


def test_bad_worker_arguments_fail_before_loading_models(tmp_path):
    with pytest.raises(ValueError, match="box_threshold"):
        grounded_segment_image(
            tmp_path / "missing", tmp_path / "out", box_threshold=float("nan")
        )
    with pytest.raises(ValueError, match="max_detections"):
        grounded_segment_image(tmp_path / "missing", tmp_path / "out", max_detections=0)
    with pytest.raises(FileExistsError):
        grounded_segment_image(tmp_path / "missing", tmp_path)
