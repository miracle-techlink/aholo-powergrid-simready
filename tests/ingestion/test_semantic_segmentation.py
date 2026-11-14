"""Pixel identity and provenance are independent of model/network availability."""

import json

import numpy as np
import pytest
from PIL import Image

from simready.core.artifacts import digest
from simready.ingestion.segmentation.semantic import (
    IGNORE_LABEL,
    canonical_category,
    extract_semantic_regions,
    import_segmentation,
    segment_image,
)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("cabinet", "cabinet"),
        (" Coffee table ", "table"),
        ("swivel chair", "chair"),
        ("bed ", "bed"),
        ("machine", "unknown"),
        ("button", "unknown"),
        ("cabinet door", "unknown"),
        ("door", "door"),
    ],
)
def test_category_mapping_is_conservative(raw, expected):
    assert canonical_category(raw) == expected


def test_components_keep_identity_area_box_and_scores():
    labels = np.array([[1, 1, 0, 1], [1, 0, 0, 1], [0, 0, 0, 1]], dtype=np.uint16)
    confidence = np.full(labels.shape, 0.8)
    confidence[0, 0] = 0.2
    rows = extract_semantic_regions(
        labels,
        {0: "wall", 1: "cabinet"},
        min_area_px=3,
        categories=["cabinet"],
        confidence=confidence,
    )
    assert len(rows) == 2
    left, right = rows
    assert left[0]["bbox_xyxy"] == [0, 0, 2, 2]
    assert left[0]["area_px"] == 3
    assert left[0]["score"] == pytest.approx(0.6)
    assert right[0]["bbox_xyxy"] == [3, 0, 4, 3]
    assert not (left[1] & right[1]).any()
    assert np.array_equal(left[1] | right[1], labels == 1)
    assert left[0]["instance_method"] == "semantic_connected_components_4"


def test_diagonally_touching_pixels_are_not_merged_and_small_regions_removed():
    labels = np.array([[1, 0, 0], [0, 1, 1], [0, 1, 1]], dtype=np.int32)
    rows = extract_semantic_regions(
        labels, {0: "wall", 1: "chair"}, categories=["chair"], min_area_px=2
    )
    assert len(rows) == 1
    assert rows[0][0]["area_px"] == 4
    assert rows[0][0]["bbox_xyxy"] == [1, 1, 3, 3]


def test_unknown_ids_preserve_evidence_and_no_fake_confidence():
    rows = extract_semantic_regions(np.full((2, 3), 487), {}, min_area_px=1)
    metadata, _ = rows[0]
    assert metadata["category"] == "unknown"
    assert metadata["raw_label_id"] == 487
    assert metadata["raw_label"] == "unmapped_label_487"
    assert metadata["score"] is None


@pytest.mark.parametrize(
    "labels", [np.array([]), np.array([[0.5]]), np.array([[-1]]), np.array([[65535]])]
)
def test_bad_dense_maps_fail(labels):
    with pytest.raises(ValueError):
        extract_semantic_regions(labels, {}, min_area_px=1)


@pytest.mark.parametrize(
    "confidence",
    [np.zeros((1, 2)), np.array([[np.nan]]), np.array([[1.1]]), np.array([[-0.1]])],
)
def test_invalid_confidence_fails(confidence):
    with pytest.raises(ValueError, match="confidence"):
        extract_semantic_regions(
            np.zeros((1, 1), dtype=np.uint8),
            {0: "wall"},
            confidence=confidence,
            min_area_px=1,
        )


@pytest.mark.parametrize("minimum", [0, -1, 1.5, True])
def test_invalid_min_area_fails(minimum):
    with pytest.raises(ValueError, match="min_area_px"):
        extract_semantic_regions(
            np.zeros((1, 1), dtype=np.uint8), {}, min_area_px=minimum
        )


@pytest.mark.parametrize("categories", [[], "cabinet", ["button"]])
def test_invalid_or_unsupported_class_filter_fails(categories):
    with pytest.raises(ValueError, match="categor"):
        extract_semantic_regions(
            np.zeros((1, 1), dtype=np.uint8), {}, categories=categories
        )


def _import_source(tmp_path):
    image = tmp_path / "image.png"
    Image.new("RGB", (8, 6), (70, 80, 90)).save(image)
    mask = np.zeros((6, 8), dtype=np.uint8)
    mask[2:5, 3:7] = 255
    mask_path = tmp_path / "mask.png"
    Image.fromarray(mask).save(mask_path)
    data = {
        "schema_version": 1,
        "source_image": {"sha256": digest(image), "width": 8, "height": 6},
        "instances": [
            {
                "asset_id": "button_1",
                "category": "button",
                "score": 0.99,
                "mask": {"path": "mask.png", "sha256": digest(mask_path)},
            }
        ],
        "model": {"model_id": "claimed_model"},
        "provenance": {"source_type": "model_inference"},
    }
    manifest = tmp_path / "input.json"
    manifest.write_text(json.dumps(data))
    return image, manifest, data


def test_import_is_hashed_operator_evidence_with_exact_pixel_geometry(tmp_path):
    image, manifest, _ = _import_source(tmp_path)
    out = tmp_path / "out"
    result = import_segmentation(image, manifest, out)
    assert result["provenance"]["source_type"] == "operator_provided"
    assert result["model"] is None
    row = result["instances"][0]
    assert row["category"] == "button"
    assert row["bbox_xyxy"] == [3, 2, 7, 5]
    assert row["area_px"] == 12
    assert row["score"] is None
    assert row["upstream_score_unverified"] == 0.99
    assert digest(out / row["mask"]["path"]) == row["mask"]["sha256"]
    saved_labels = np.asarray(Image.open(out / result["label_map"]["path"]))
    assert int(saved_labels[0, 0]) == IGNORE_LABEL
    assert int(saved_labels[2, 3]) == 0
    assert (out / "manifest.json").is_file()
    assert result["usage"]["input_tokens"] == 0


@pytest.mark.parametrize(
    "mutation",
    [
        "source_hash",
        "source_size",
        "mask_hash",
        "identifier",
        "duplicate",
        "overlap",
        "score",
    ],
)
def test_invalid_import_rejected_before_writing(tmp_path, mutation):
    image, manifest, data = _import_source(tmp_path)
    row = data["instances"][0]
    if mutation == "source_hash":
        data["source_image"]["sha256"] = "0" * 64
    elif mutation == "source_size":
        data["source_image"]["width"] = 99
    elif mutation == "mask_hash":
        row["mask"]["sha256"] = "0" * 64
    elif mutation == "identifier":
        row["asset_id"] = "../escape"
    elif mutation == "duplicate":
        data["instances"].append(dict(row))
    elif mutation == "overlap":
        data["instances"].append({**row, "asset_id": "button_2"})
    elif mutation == "score":
        row["score"] = 12.0
    manifest.write_text(json.dumps(data))
    out = tmp_path / "out"
    with pytest.raises(ValueError):
        import_segmentation(image, manifest, out)
    assert not out.exists()


@pytest.mark.parametrize(
    "pixels",
    [
        np.full((6, 8), 127, np.uint8),
        np.zeros((6, 8), np.uint8),
        np.full((3, 4), 255, np.uint8),
        np.full((6, 8, 3), 255, np.uint8),
    ],
)
def test_invalid_import_mask_shape_values_or_empty(tmp_path, pixels):
    image, manifest, data = _import_source(tmp_path)
    mask = tmp_path / "mask.png"
    Image.fromarray(pixels).save(mask)
    data["instances"][0]["mask"]["sha256"] = digest(mask)
    manifest.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        import_segmentation(image, manifest, tmp_path / "out")


def test_existing_output_and_invalid_config_fail_without_model_download(tmp_path):
    image, _, _ = _import_source(tmp_path)
    with pytest.raises(FileExistsError):
        segment_image(image, tmp_path)
    with pytest.raises(ValueError, match="min_area_px"):
        segment_image(image, tmp_path / "new", min_area_px=0)
    with pytest.raises(ValueError, match="cpu_threads"):
        segment_image(image, tmp_path / "new", cpu_threads=0)
