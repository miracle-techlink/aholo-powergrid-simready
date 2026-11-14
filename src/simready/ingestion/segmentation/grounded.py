"""Text-grounded asset candidates from Grounding DINO and box-prompted SAM.

Raw masks are preserved even when overlapping. Disjoint downstream masks use
explicit robot/person priority, then detector score; clipping is never labeled
ground truth. Model proposals require independent semantic and geometric review.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from simready.core.artifacts import digest, write_json
from simready.ingestion.segmentation.semantic import (
    IGNORE_LABEL,
    _minimum_area,
    _read_image,
    _save_artifacts,
)

DETECTOR_ID = "IDEA-Research/grounding-dino-tiny"
DETECTOR_REVISION = "a2bb814dd30d776dcf7e30523b00659f4f141c71"
MASK_MODEL_ID = "facebook/sam-vit-base"
MASK_MODEL_REVISION = "70c1a07f894ebb5b307fd9eaaee97b9dfc16068f"
DEFAULT_QUERIES = {
    "cabinet": "cabinet",
    "door": "door",
    "table": "table",
    "chair": "chair",
    "floor": "floor",
    "robot": "robot",
}
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,95}\Z")
_PHRASE = re.compile(r"[A-Za-z][A-Za-z0-9 /_-]{0,95}\Z")


def _query_mapping(queries: Mapping[str, str] | None) -> dict[str, str]:
    if queries is None:
        return dict(DEFAULT_QUERIES)
    if not isinstance(queries, Mapping) or not 1 <= len(queries) <= 32:
        raise ValueError(
            "queries must map between 1 and 32 categories to English phrases"
        )
    output = {}
    for category, phrase in queries.items():
        if not isinstance(category, str) or not _IDENTIFIER.fullmatch(category):
            raise ValueError("Query categories must be safe identifiers")
        if category == "unknown":
            raise ValueError("unknown is reserved for unresolved model labels")
        if not isinstance(phrase, str) or not _PHRASE.fullmatch(phrase.strip()):
            raise ValueError(
                "Query phrases must be plain English without period delimiters"
            )
        phrase = " ".join(phrase.lower().split())
        if phrase in output.values():
            raise ValueError("Each query phrase must identify exactly one category")
        output[category] = phrase
    return output


def select_detections(
    boxes: Sequence[Sequence[float]],
    scores: Sequence[float],
    text_labels: Sequence[str],
    queries: Mapping[str, str],
    *,
    width: int,
    height: int,
    nms_iou: float = 0.5,
    max_detections: int = 48,
) -> list[dict[str, Any]]:
    """Clip image boxes and suppress same-label duplicates without merging classes.

    Text that does not exactly match a requested phrase remains unknown.
    Pixel boxes are continuous xyxy coordinates. Invalid model arrays raise.
    """
    mapping = _query_mapping(queries)
    if type(width) is not int or type(height) is not int or min(width, height) < 1:
        raise ValueError("Image dimensions must be positive integers")
    if not math.isfinite(nms_iou) or not 0 <= nms_iou <= 1:
        raise ValueError("nms_iou must be in [0, 1]")
    if type(max_detections) is not int or not 1 <= max_detections <= 128:
        raise ValueError("max_detections must be between 1 and 128")
    if not len(boxes) == len(scores) == len(text_labels):
        raise ValueError("Detection boxes, scores and labels must have equal lengths")
    inverse = {phrase: category for category, phrase in mapping.items()}
    candidates = []
    for index, (box, score, text) in enumerate(
        zip(boxes, scores, text_labels, strict=True)
    ):
        values = np.array(box, dtype=float, copy=True)
        if (
            values.shape != (4,)
            or not np.isfinite(values).all()
            or values[2] <= values[0]
            or values[3] <= values[1]
        ):
            raise ValueError(
                "Detection boxes must be finite, nondegenerate xyxy values"
            )
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("Detection scores must be finite probabilities in [0, 1]")
        if not isinstance(text, str):
            raise ValueError("Detection text labels must be strings")
        raw = " ".join(text.strip().lower().split())
        values[[0, 2]] = np.clip(values[[0, 2]], 0, width)
        values[[1, 3]] = np.clip(values[[1, 3]], 0, height)
        if values[2] <= values[0] or values[3] <= values[1]:
            continue
        category = inverse.get(raw, "unknown")
        candidates.append(
            {
                "asset_id": f"{category}_{index:04d}",
                "category": category,
                "raw_label": raw or "unresolved_prompt",
                "detection_index": index,
                "detection_bbox_xyxy": values.tolist(),
                "score": float(score),
            }
        )
    accepted = []
    for candidate in sorted(
        candidates, key=lambda item: (-item["score"], item["detection_index"])
    ):
        box = candidate["detection_bbox_xyxy"]
        duplicate = False
        for previous in accepted:
            if previous["raw_label"] != candidate["raw_label"]:
                continue
            other = previous["detection_bbox_xyxy"]
            intersection = max(0, min(box[2], other[2]) - max(box[0], other[0])) * max(
                0, min(box[3], other[3]) - max(box[1], other[1])
            )
            area = (box[2] - box[0]) * (box[3] - box[1])
            other_area = (other[2] - other[0]) * (other[3] - other[1])
            if intersection / (area + other_area - intersection) > nms_iou:
                duplicate = True
                break
        if not duplicate:
            accepted.append(candidate)
        if len(accepted) >= max_detections:
            break
    return accepted


def resolve_mask_overlap(
    candidates: Sequence[dict[str, Any]],
    masks: np.ndarray,
    *,
    min_area_px: int = 256,
    min_retained_fraction: float = 0.2,
) -> tuple[list[tuple[dict[str, Any], np.ndarray]], list[dict[str, Any]]]:
    """Produce mutually exclusive derived masks while recording every removal.

    Robot/person regions claim pixels first, then descending detector score.
    All original masks must be persisted by the caller. A removed region is a
    review item, not evidence that an object is absent. Masks are never mutated.
    """
    _minimum_area(min_area_px)
    if not math.isfinite(min_retained_fraction) or not 0 <= min_retained_fraction <= 1:
        raise ValueError("min_retained_fraction must be in [0, 1]")
    values = np.asarray(masks)
    if (
        values.dtype != np.bool_
        or values.ndim != 3
        or values.shape[0] != len(candidates)
    ):
        raise ValueError(
            "masks must be a bool array with shape (candidates, height, width)"
        )
    if min(values.shape[1:]) < 1:
        raise ValueError("Mask dimensions must be positive")
    occupied = np.zeros(values.shape[1:], dtype=bool)
    ordered = sorted(
        enumerate(candidates),
        key=lambda entry: (
            entry[1]["category"] not in {"robot", "person"},
            -entry[1]["score"],
            entry[0],
        ),
    )
    accepted, rejected = [], []
    for index, candidate in ordered:
        original = values[index]
        raw_area = int(original.sum())
        mask = original & ~occupied
        area = int(mask.sum())
        removed = raw_area - area
        row = {
            **candidate,
            "raw_area_px": raw_area,
            "removed_pixel_count": removed,
            "removed_fraction": removed / raw_area if raw_area else 0.0,
            "ambiguous": removed > 0 or candidate["category"] == "unknown",
            "score_type": "grounding_dino_text_match_uncalibrated",
            "instance_method": "text_grounded_box_prompted_sam",
            "mask_status": "derived_nonoverlapping_proposal",
            "disambiguation": "robot_person_first_then_descending_detector_score",
        }
        retained_fraction = area / raw_area if raw_area else 0.0
        if area < min_area_px or retained_fraction < min_retained_fraction:
            rejected.append(
                {
                    **row,
                    "area_px": area,
                    "reason": (
                        "insufficient_retained_area"
                        if area >= min_area_px
                        else "overlap_removed_or_too_small"
                        if removed
                        else "empty_or_too_small"
                    ),
                }
            )
            # A rejected small region must not reserve pixels for later candidates.
            continue
        occupied |= mask
        ys, xs = np.nonzero(mask)
        row.update(
            {
                "area_px": area,
                "bbox_xyxy": [
                    int(xs.min()),
                    int(ys.min()),
                    int(xs.max()) + 1,
                    int(ys.max()) + 1,
                ],
            }
        )
        accepted.append((row, mask))
    return accepted, rejected


def grounded_segment_image(
    image_path: str | Path,
    output_dir: str | Path,
    *,
    queries: Mapping[str, str] | None = None,
    device: str = "cpu",
    box_threshold: float = 0.25,
    text_threshold: float = 0.25,
    min_area_px: int = 256,
    min_retained_fraction: float = 0.2,
    nms_iou: float = 0.5,
    max_detections: int = 48,
    cpu_threads: int = 4,
    local_files_only: bool = False,
) -> dict[str, Any]:
    """Run pinned Grounding DINO + SAM models and save reviewable pixel evidence.

    Returns the common segmentation manifest. Downloads/loads are timed together;
    neural text tokenization is local and is not an LLM/API token charge.
    No physics, calibration, material assignment or semantic accuracy is claimed.
    """
    started = time.perf_counter()
    mapping = _query_mapping(queries)
    _minimum_area(min_area_px)
    for name, value in (
        ("box_threshold", box_threshold),
        ("text_threshold", text_threshold),
        ("nms_iou", nms_iou),
        ("min_retained_fraction", min_retained_fraction),
    ):
        if isinstance(value, bool) or not math.isfinite(value) or not 0 <= value <= 1:
            raise ValueError(f"{name} must be a finite number in [0, 1]")
    if type(cpu_threads) is not int or cpu_threads < 1:
        raise ValueError("cpu_threads must be a positive integer")
    if type(max_detections) is not int or not 1 <= max_detections <= 128:
        raise ValueError("max_detections must be between 1 and 128")
    out = Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Segmentation output already exists: {out}")
    image, source = _read_image(image_path)
    try:
        import torch
        import transformers
        from transformers import (
            AutoModelForZeroShotObjectDetection,
            AutoProcessor,
            SamModel,
            SamProcessor,
        )
        from transformers.utils.hub import cached_file
    except ImportError as error:
        raise RuntimeError(
            "Install simready-pipeline[segmentation] for grounded inference"
        ) from error
    target = torch.device(device)
    if target.type not in {"cpu", "cuda"}:
        raise ValueError("device must be cpu, cuda or cuda:N")
    if target.type == "cuda" and not torch.cuda.is_available():
        raise ValueError(
            "CUDA requested but no CUDA device is available in this PyTorch installation"
        )
    old_threads = torch.get_num_threads()
    load_start = time.perf_counter()
    try:
        if target.type == "cpu":
            torch.set_num_threads(cpu_threads)
        detector_processor = AutoProcessor.from_pretrained(
            DETECTOR_ID,
            revision=DETECTOR_REVISION,
            local_files_only=local_files_only,
            trust_remote_code=False,
        )
        detector = (
            AutoModelForZeroShotObjectDetection.from_pretrained(
                DETECTOR_ID,
                revision=DETECTOR_REVISION,
                local_files_only=local_files_only,
                use_safetensors=True,
                trust_remote_code=False,
            )
            .eval()
            .to(target)
        )
        mask_processor = SamProcessor.from_pretrained(
            MASK_MODEL_ID,
            revision=MASK_MODEL_REVISION,
            local_files_only=local_files_only,
        )
        mask_model = (
            SamModel.from_pretrained(
                MASK_MODEL_ID,
                revision=MASK_MODEL_REVISION,
                local_files_only=local_files_only,
                use_safetensors=True,
            )
            .eval()
            .to(target)
        )
        components = []
        for model_id, revision in (
            (DETECTOR_ID, DETECTOR_REVISION),
            (MASK_MODEL_ID, MASK_MODEL_REVISION),
        ):
            checkpoint = cached_file(
                model_id, "model.safetensors", revision=revision, local_files_only=True
            )
            components.append(
                {
                    "model_id": model_id,
                    "revision": revision,
                    "checkpoint_sha256": digest(checkpoint),
                    "license": "Apache-2.0",
                    "model_card_url": f"https://huggingface.co/{model_id}/tree/{revision}",
                }
            )
        load_s = time.perf_counter() - load_start
        prompt = " . ".join(mapping.values()) + " ."
        inference_start = time.perf_counter()
        detector_inputs = detector_processor(
            images=image, text=prompt, return_tensors="pt"
        ).to(target)
        token_count = int(detector_inputs.input_ids.shape[-1])
        if token_count > detector.config.max_text_len:
            raise ValueError(
                "Query prompt exceeds the detector text limit; use fewer phrases"
            )
        with torch.inference_mode():
            detected = detector(**detector_inputs)
        result = detector_processor.post_process_grounded_object_detection(
            detected,
            detector_inputs.input_ids,
            threshold=box_threshold,
            text_threshold=text_threshold,
            target_sizes=[(image.height, image.width)],
        )[0]
        if target.type == "cuda":
            torch.cuda.synchronize(target)
        detection_s = time.perf_counter() - inference_start
        candidates = select_detections(
            result["boxes"].cpu().tolist(),
            result["scores"].cpu().tolist(),
            result["text_labels"],
            mapping,
            width=image.width,
            height=image.height,
            nms_iou=nms_iou,
            max_detections=max_detections,
        )
        sam_start = time.perf_counter()
        if candidates:
            mask_inputs = mask_processor(
                images=image,
                input_boxes=[[item["detection_bbox_xyxy"] for item in candidates]],
                return_tensors="pt",
            ).to(target)
            with torch.inference_mode():
                predicted = mask_model(**mask_inputs, multimask_output=False)
            raw_masks = mask_processor.image_processor.post_process_masks(
                predicted.pred_masks.cpu(),
                mask_inputs.original_sizes.cpu(),
                mask_inputs.reshaped_input_sizes.cpu(),
            )[0][:, 0].numpy()
            for candidate, predicted_iou in zip(
                candidates, predicted.iou_scores.flatten().cpu().tolist(), strict=True
            ):
                candidate["sam_predicted_iou"] = predicted_iou
        else:
            raw_masks = np.zeros((0, image.height, image.width), dtype=bool)
        segmentation_s = time.perf_counter() - sam_start
    finally:
        if target.type == "cpu":
            torch.set_num_threads(old_threads)
    post_start = time.perf_counter()
    rows, rejected = resolve_mask_overlap(
        candidates,
        raw_masks,
        min_area_px=min_area_px,
        min_retained_fraction=min_retained_fraction,
    )
    label_ids = {
        category: index for index, category in enumerate((*mapping, "unknown"))
    }
    labels = {
        str(index): {
            "category": category,
            "raw_label": mapping.get(category, "unknown"),
        }
        for category, index in label_ids.items()
    }
    dense = np.full((image.height, image.width), IGNORE_LABEL, dtype=np.uint16)
    for row, mask in rows:
        row["raw_label_id"] = label_ids[row["category"]]
        dense[mask] = row["raw_label_id"]
    if digest(source["path"]) != source["sha256"]:
        raise ValueError("Source image changed during inference")
    artifacts = _save_artifacts(out, image, dense, rows)
    (out / "raw_masks").mkdir()
    raw_index = {}
    for candidate, mask in zip(candidates, raw_masks, strict=True):
        name = f"raw_masks/{candidate['asset_id']}.png"
        Image.fromarray(mask.astype(np.uint8) * 255).save(out / name)
        raw_index[candidate["asset_id"]] = {
            "path": name,
            "sha256": digest(out / name),
            "encoding": "binary_0_255",
        }
    for row in [*artifacts["instances"], *rejected]:
        row["raw_mask"] = raw_index[row["asset_id"]]
    manifest = {
        "schema_version": 1,
        "source_image": source,
        "model": {
            "provider": "huggingface",
            "model_id": "grounding_dino_tiny+sam_vit_base",
            "components": components,
            "device": str(target),
            "torch_version": torch.__version__,
            "transformers_version": transformers.__version__,
            "license": "Apache-2.0 model card declarations",
            "commercial_use_verified": False,
        },
        "provenance": {
            "source_type": "model_inference",
            "worker_sha256": digest(__file__),
            "artifact_writer_sha256": digest(Path(__file__).with_name("semantic.py")),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "instance_method": "text_grounded_box_prompted_sam",
            "metric_geometry_available": False,
            "material_inference_performed": False,
            "semantic_accuracy_validated": False,
            "button_detection_validated": False,
        },
        "parameters": {
            "queries": mapping,
            "prompt": prompt,
            "box_threshold": box_threshold,
            "text_threshold": text_threshold,
            "min_area_px": min_area_px,
            "min_retained_fraction": min_retained_fraction,
            "nms_iou": nms_iou,
            "max_detections": max_detections,
            "cpu_threads": cpu_threads if target.type == "cpu" else None,
        },
        "labels": labels,
        **artifacts,
        "rejected_candidates": rejected,
        "detector_proposals": [
            {
                "detection_index": index,
                "bbox_xyxy": box,
                "score": score,
                "text_label": label,
            }
            for index, (box, score, label) in enumerate(
                zip(
                    result["boxes"].cpu().tolist(),
                    result["scores"].cpu().tolist(),
                    result["text_labels"],
                    strict=True,
                )
            )
        ],
        "disambiguation": {
            "policy": "robot_person_first_then_descending_detector_score",
            "raw_masks_preserved": True,
            "derived_masks_nonoverlapping": True,
            "ambiguous_candidates": sum(
                row["ambiguous"] for row in [*artifacts["instances"], *rejected]
            ),
            "original_detection_count": len(result["boxes"]),
            "after_class_aware_nms_and_cap": len(candidates),
            "semantic_review_required": True,
        },
        "timing": {
            "model_load_s": load_s,
            "detection_s": detection_s,
            "segmentation_s": segmentation_s,
            "postprocess_s": time.perf_counter() - post_start,
            "total_s": time.perf_counter() - started,
        },
        "usage": {
            "llm_calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "local_detector_text_tokens": token_count,
        },
        "limitations": [
            "Text-grounded proposals and SAM masks are not verified asset identities.",
            "Raw masks overlap; derived masks are clipped by an explicit heuristic, not ground truth.",
            "A cabinet and its door may overlap legitimately; clipping can remove a real part.",
            "Detector score and SAM predicted IoU are uncalibrated model outputs.",
            "Metric geometry, material properties and physical behavior require independent evidence.",
        ],
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    """Run the optional grounded segmentation worker with pinned models."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument(
        "--categories",
        help="Comma-separated categories; underscores become phrase spaces",
    )
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--min-area-px", type=int, default=256)
    parser.add_argument("--min-retained-fraction", type=float, default=0.2)
    parser.add_argument("--box-threshold", type=float, default=0.25)
    parser.add_argument("--text-threshold", type=float, default=0.25)
    parser.add_argument("--nms-iou", type=float, default=0.5)
    parser.add_argument("--max-detections", type=int, default=48)
    parser.add_argument("--local-files-only", action="store_true")
    args = parser.parse_args(argv)
    queries = (
        {
            category: category.replace("_", " ")
            for category in args.categories.split(",")
        }
        if args.categories
        else None
    )
    result = grounded_segment_image(
        args.image,
        args.output_dir,
        queries=queries,
        device=args.device,
        cpu_threads=args.cpu_threads,
        min_area_px=args.min_area_px,
        min_retained_fraction=args.min_retained_fraction,
        box_threshold=args.box_threshold,
        text_threshold=args.text_threshold,
        nms_iou=args.nms_iou,
        max_detections=args.max_detections,
        local_files_only=args.local_files_only,
    )
    print(
        json.dumps(
            {
                "manifest": str(Path(args.output_dir).resolve() / "manifest.json"),
                "instances": len(result["instances"]),
                "timing": result["timing"],
                "semantic_review_required": True,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
