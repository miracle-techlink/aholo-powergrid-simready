"""Image semantics with pixel masks, traceable evidence and no inferred 3D pose.

SegFormer ADE20K produces semantic classes, not object instances or materials.
Connected components are candidate regions: touching objects may merge and an
occluded object may split. Buttons are not in this model's class vocabulary.
The bundled model identifier refers to research/evaluation-only NVIDIA weights;
weights are fetched separately and are never redistributed by this package.
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

from simready.core.artifacts import digest, read_json, write_json

DEFAULT_MODEL_ID = "nvidia/segformer-b0-finetuned-ade-512-512"
DEFAULT_REVISION = "489d5cd81a0b59fab9b7ea758d3548ebe99677da"
_NVIDIA_ADE_REVISIONS = {
    DEFAULT_MODEL_ID: DEFAULT_REVISION,
    "nvidia/segformer-b5-finetuned-ade-640-640": "739f5d4692954e4a185eac280dec1ba5a7d52f1d",
}
MODEL_LICENSE_URL = "https://github.com/NVlabs/SegFormer/blob/master/LICENSE"
IGNORE_LABEL = 65535

# Deliberately conservative: a raw visual label is not a material observation.
_CATEGORY_LABELS = {
    "floor": ("floor",),
    "wall": ("wall",),
    "ceiling": ("ceiling",),
    "cabinet": ("cabinet",),
    "door": ("door", "screen door"),
    "window": ("windowpane", "window"),
    "table": ("table", "desk", "coffee table"),
    "chair": ("chair", "armchair", "swivel chair"),
    "sofa": ("sofa",),
    "shelf": ("shelf", "bookcase"),
    "bed": ("bed",),
    "countertop": ("countertop", "counter"),
    "appliance": ("refrigerator", "dishwasher", "microwave", "oven", "stove"),
    "sink": ("sink",),
    "toilet": ("toilet",),
    "lamp": ("lamp", "light", "chandelier", "sconce"),
    "plant": ("plant", "flower", "tree", "palm"),
    "person": ("person",),
    "vehicle": ("car", "bus", "truck", "van", "bicycle", "minibike", "boat"),
}
_NORMALIZED_LABELS = {
    label: category for category, labels in _CATEGORY_LABELS.items() for label in labels
}
SUPPORTED_CATEGORIES = frozenset((*_CATEGORY_LABELS, "unknown"))
_IDENTIFIER = re.compile(r"[A-Za-z][A-Za-z0-9_-]{0,95}\Z")


def canonical_category(raw_label: str) -> str:
    """Map a known semantic label conservatively; unknown labels stay unknown."""
    if not isinstance(raw_label, str) or not raw_label.strip():
        raise ValueError("raw_label must be a nonempty string")
    return _NORMALIZED_LABELS.get(raw_label.strip().lower(), "unknown")


def _minimum_area(value: int) -> None:
    if type(value) is not int or value < 1:
        raise ValueError("min_area_px must be a positive integer")


def _category_filter(categories: Sequence[str] | None) -> set[str] | None:
    if categories is None:
        return None
    if isinstance(categories, str) or not categories:
        raise ValueError("categories must be a nonempty sequence of category strings")
    if any(not isinstance(value, str) for value in categories):
        raise ValueError("categories must contain only strings")
    selected = set(categories)
    invalid = selected - SUPPORTED_CATEGORIES
    if invalid:
        raise ValueError(f"Unknown category filters: {sorted(invalid)}")
    return selected


def extract_semantic_regions(
    label_map: np.ndarray,
    id2label: Mapping[int, str],
    *,
    confidence: np.ndarray | None = None,
    min_area_px: int = 256,
    categories: Sequence[str] | None = None,
) -> list[tuple[dict[str, Any], np.ndarray]]:
    """Return 4-connected candidate regions and bool masks in input pixels.

    Bounding boxes use [x0, y0, x1, y1] with exclusive right/bottom edges.
    Scores are mean pixel confidence, not calibrated object probabilities.
    Raises ValueError for invalid labels, confidence or class filters.
    """
    from scipy import ndimage

    _minimum_area(min_area_px)
    selected = _category_filter(categories)
    labels = np.asarray(label_map)
    if labels.ndim != 2 or not labels.size or labels.dtype.kind not in "iu":
        raise ValueError("label_map must be a nonempty 2D integer array")
    if int(labels.min()) < 0 or int(labels.max()) >= IGNORE_LABEL:
        raise ValueError("label_map IDs must be between 0 and 65534")
    if confidence is not None:
        confidence = np.asarray(confidence)
        if (
            confidence.shape != labels.shape
            or not np.isfinite(confidence).all()
            or np.any((confidence < 0) | (confidence > 1))
        ):
            raise ValueError(
                "confidence must match the image and contain values in [0, 1]"
            )
    rows = []
    for label_id in sorted(int(value) for value in np.unique(labels)):
        raw = id2label.get(label_id, f"unmapped_label_{label_id}")
        category = canonical_category(raw)
        if selected is not None and category not in selected:
            continue
        components, count = ndimage.label(labels == label_id)
        slices = ndimage.find_objects(components)
        for component_id in range(1, count + 1):
            bounds = slices[component_id - 1]
            if bounds is None:
                continue
            region = components[bounds] == component_id
            area = int(region.sum())
            if area < min_area_px:
                continue
            mask = np.zeros(labels.shape, dtype=bool)
            mask[bounds] = region
            ys, xs = bounds
            row = {
                "asset_id": f"{category}_{label_id:03d}_{component_id:04d}",
                "category": category,
                "raw_label": raw,
                "raw_label_id": label_id,
                "bbox_xyxy": [xs.start, ys.start, xs.stop, ys.stop],
                "area_px": area,
                "score": float(confidence[mask].mean())
                if confidence is not None
                else None,
                "score_type": "mean_pixel_softmax"
                if confidence is not None
                else "unavailable",
                "instance_method": "semantic_connected_components_4",
            }
            rows.append((row, mask))
    return rows


def _read_image(image_path: str | Path) -> tuple[Image.Image, dict[str, Any]]:
    path = Path(image_path).resolve(strict=True)
    before = digest(path)
    with Image.open(path) as source:
        if source.width * source.height > 16_000_000:
            raise ValueError(
                "Image exceeds the 16 megapixel worker limit; resize explicitly"
            )
        image = source.convert("RGB")
    if digest(path) != before:
        raise ValueError("Source image changed while reading")
    return image, {
        "path": str(path),
        "sha256": before,
        "width": image.width,
        "height": image.height,
        "pixel_coordinates": "encoded_raster_no_exif_rotation",
    }


def _save_artifacts(
    out: Path,
    image: Image.Image,
    label_map: np.ndarray,
    rows: list[tuple[dict[str, Any], np.ndarray]],
) -> dict[str, Any]:
    out.mkdir(parents=True, exist_ok=False)
    (out / "masks").mkdir()
    Image.fromarray(label_map.astype(np.uint16)).save(out / "label_map.png")
    overlay = np.asarray(image).copy()
    instances = []
    for index, (row, mask) in enumerate(rows):
        name = f"masks/{row['asset_id']}.png"
        Image.fromarray(mask.astype(np.uint8) * 255).save(out / name)
        color = np.array(
            [
                50 + (index * 71) % 205,
                50 + (index * 137 + 60) % 205,
                50 + (index * 97 + 120) % 205,
            ],
            dtype=np.uint8,
        )
        overlay[mask] = (0.55 * overlay[mask] + 0.45 * color).astype(np.uint8)
        instances.append(
            {
                **row,
                "mask": {
                    "path": name,
                    "sha256": digest(out / name),
                    "encoding": "binary_0_255",
                },
                "overlay_color_rgb": color.tolist(),
            }
        )
    Image.fromarray(overlay).save(out / "overlay.png")
    return {
        "instances": instances,
        "label_map": {
            "path": "label_map.png",
            "sha256": digest(out / "label_map.png"),
            "encoding": "uint16_png",
            "unlabeled_id": IGNORE_LABEL,
        },
        "overlay": {"path": "overlay.png", "sha256": digest(out / "overlay.png")},
    }


def segment_image(
    image_path: str | Path,
    output_dir: str | Path,
    *,
    model_id: str = DEFAULT_MODEL_ID,
    revision: str | None = None,
    device: str = "cpu",
    min_area_px: int = 256,
    categories: Sequence[str] | None = None,
    local_files_only: bool = False,
    cpu_threads: int = 4,
) -> dict[str, Any]:
    """Run actual SegFormer inference and write a fresh evidence directory.

    The default weights are pinned to a known revision. CUDA is opt-in and
    fails explicitly if unavailable; it never silently falls back to CPU.
    Physical properties, metric geometry and button identity are not inferred.
    Requires the optional segmentation dependencies, fetched without remote code.
    """
    started = time.perf_counter()
    _minimum_area(min_area_px)
    _category_filter(categories)
    if type(cpu_threads) is not int or cpu_threads < 1:
        raise ValueError("cpu_threads must be a positive integer")
    out = Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Segmentation output already exists: {out}")
    image, source = _read_image(image_path)
    try:
        import torch
        import transformers
        from transformers import (
            SegformerForSemanticSegmentation,
            SegformerImageProcessor,
        )
        from transformers.utils.hub import cached_file
    except ImportError as error:
        raise RuntimeError(
            "Install simready-pipeline[segmentation] to run model inference"
        ) from error
    target = torch.device(device)
    if target.type not in {"cpu", "cuda"}:
        raise ValueError("device must be cpu, cuda or cuda:N")
    if target.type == "cuda" and not torch.cuda.is_available():
        raise ValueError(
            "CUDA was requested but this PyTorch installation has no available CUDA device"
        )
    requested_revision = revision or _NVIDIA_ADE_REVISIONS.get(model_id, "main")
    load_start = time.perf_counter()
    processor = SegformerImageProcessor.from_pretrained(
        model_id, revision=requested_revision, local_files_only=local_files_only
    )
    model = (
        SegformerForSemanticSegmentation.from_pretrained(
            model_id,
            revision=requested_revision,
            local_files_only=local_files_only,
            weights_only=True,
        )
        .eval()
        .to(target)
    )
    resolved_revision = getattr(model.config, "_commit_hash", None)
    checkpoint = cached_file(
        model_id,
        "model.safetensors",
        revision=resolved_revision or requested_revision,
        local_files_only=True,
        _raise_exceptions_for_missing_entries=False,
    )
    if checkpoint is None:
        checkpoint = cached_file(
            model_id,
            "pytorch_model.bin",
            revision=resolved_revision or requested_revision,
            local_files_only=True,
        )
    checkpoint_hash = digest(checkpoint)
    load_s = time.perf_counter() - load_start
    old_threads = torch.get_num_threads()
    inference_start = time.perf_counter()
    try:
        if target.type == "cpu":
            torch.set_num_threads(cpu_threads)
        inputs = processor(images=image, return_tensors="pt").to(target)
        with torch.inference_mode():
            logits = model(**inputs).logits
            if target.type == "cuda":
                torch.cuda.synchronize(target)
            inference_s = time.perf_counter() - inference_start
            post_start = time.perf_counter()
            resized = torch.nn.functional.interpolate(
                logits,
                size=(image.height, image.width),
                mode="bilinear",
                align_corners=False,
            )
            max_logits, label_tensor = resized.max(dim=1)
            confidence = (max_logits - resized.logsumexp(dim=1)).exp()[0].cpu().numpy()
            labels = label_tensor[0].cpu().numpy().astype(np.uint16)
        id2label = {int(key): value for key, value in model.config.id2label.items()}
        regions = extract_semantic_regions(
            labels,
            id2label,
            confidence=confidence,
            min_area_px=min_area_px,
            categories=categories,
        )
        if digest(source["path"]) != source["sha256"]:
            raise ValueError("Source image changed during model inference")
        artifacts = _save_artifacts(out, image, labels, regions)
        post_s = time.perf_counter() - post_start
    finally:
        if target.type == "cpu":
            torch.set_num_threads(old_threads)
    manifest = {
        "schema_version": 1,
        "source_image": source,
        "model": {
            "provider": "huggingface",
            "model_id": model_id,
            "requested_revision": requested_revision,
            "revision": resolved_revision,
            "checkpoint_sha256": checkpoint_hash,
            "checkpoint_format": Path(checkpoint).suffix,
            "weights_only_loading": True,
            "device": str(target),
            "torch_version": torch.__version__,
            "transformers_version": transformers.__version__,
            "license": "NVIDIA SegFormer non-commercial research/evaluation"
            if model_id in _NVIDIA_ADE_REVISIONS
            else "not_verified",
            "license_url": MODEL_LICENSE_URL
            if model_id in _NVIDIA_ADE_REVISIONS
            else None,
            "commercial_use_verified": False,
        },
        "provenance": {
            "source_type": "model_inference",
            "worker_sha256": digest(__file__),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "instance_method": "semantic_connected_components_4",
            "metric_geometry_available": False,
            "material_inference_performed": False,
            "button_class_supported": False
            if model_id in _NVIDIA_ADE_REVISIONS
            else None,
        },
        "parameters": {
            "min_area_px": min_area_px,
            "categories": list(categories) if categories else None,
            "cpu_threads": cpu_threads if target.type == "cpu" else None,
        },
        "labels": {
            str(key): {"raw_label": value, "category": canonical_category(value)}
            for key, value in sorted(id2label.items())
        },
        **artifacts,
        "timing": {
            "model_load_s": load_s,
            "inference_s": inference_s,
            "postprocess_s": post_s,
            "total_s": time.perf_counter() - started,
        },
        "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0},
        "limitations": [
            "Semantic regions are not verified physical object instances.",
            "Touching objects can merge; occluded objects can split.",
            "Material, mass, friction and metric dimensions require independent evidence.",
            "Mean pixel softmax scores are not calibrated object confidence.",
        ],
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def import_segmentation(
    image_path: str | Path,
    manifest_path: str | Path,
    output_dir: str | Path,
    *,
    min_area_px: int = 1,
) -> dict[str, Any]:
    """Import non-overlapping binary masks with explicit operator provenance.

    Requires schema_version=1, source_image.sha256/width/height and instances
    with asset_id, category and mask.path. Paths resolve against the input
    manifest; an optional mask.sha256 is checked. Custom category names are
    preserved. Claimed upstream model scores do not become measured scores.
    """
    started = time.perf_counter()
    _minimum_area(min_area_px)
    out = Path(output_dir).resolve()
    if out.exists():
        raise FileExistsError(f"Segmentation output already exists: {out}")
    image, source = _read_image(image_path)
    origin = Path(manifest_path).resolve(strict=True)
    origin_hash = digest(origin)
    data = read_json(origin)
    if (
        not isinstance(data, dict)
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != 1
    ):
        raise ValueError("Imported segmentation requires schema_version=1")
    observed = data.get("source_image")
    if not isinstance(observed, dict) or any(
        observed.get(key) != source[key] for key in ("sha256", "width", "height")
    ):
        raise ValueError(
            "Imported segmentation source image hash or size does not match"
        )
    instances = data.get("instances")
    if not isinstance(instances, list) or not instances:
        raise ValueError("Imported instances must be a nonempty list")
    label_map = np.full((image.height, image.width), IGNORE_LABEL, dtype=np.uint16)
    occupied = np.zeros(label_map.shape, dtype=bool)
    rows, label_ids, labels, ids = [], {}, {}, set()
    imported_masks = []
    for entry in instances:
        if not isinstance(entry, dict):
            raise ValueError("Each imported instance must be an object")
        asset_id, category = entry.get("asset_id"), entry.get("category")
        if (
            not isinstance(asset_id, str)
            or not _IDENTIFIER.fullmatch(asset_id)
            or asset_id in ids
        ):
            raise ValueError("asset_id must be a unique safe identifier")
        if not isinstance(category, str) or not _IDENTIFIER.fullmatch(category):
            raise ValueError("category must be a safe nonempty identifier")
        ids.add(asset_id)
        mask_info = entry.get("mask")
        if not isinstance(mask_info, dict) or not isinstance(
            mask_info.get("path"), str
        ):
            raise ValueError("Imported instance requires mask.path")
        mask_path = (origin.parent / mask_info["path"]).resolve(strict=True)
        mask_hash = digest(mask_path)
        if "sha256" in mask_info and mask_hash != mask_info["sha256"]:
            raise ValueError("Imported mask hash mismatch")
        with Image.open(mask_path) as opened:
            mask_pixels = np.asarray(opened)
        if (
            mask_pixels.ndim != 2
            or mask_pixels.shape != label_map.shape
            or not np.isin(mask_pixels, [0, 255]).all()
        ):
            raise ValueError(
                "Imported mask must be a same-size binary 0/255 grayscale image"
            )
        if digest(mask_path) != mask_hash:
            raise ValueError("Imported mask changed while reading")
        imported_masks.append({"path": str(mask_path), "sha256": mask_hash})
        mask = mask_pixels == 255
        if not mask.any():
            raise ValueError("Imported masks must not be empty")
        if (mask & occupied).any():
            raise ValueError(
                "Imported masks overlap; resolve ambiguous pixel ownership first"
            )
        occupied |= mask
        if int(mask.sum()) < min_area_px:
            continue
        raw = entry.get("raw_label", category)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError("raw_label must be nonempty")
        key = (category, raw)
        if key not in label_ids:
            label_ids[key] = len(label_ids)
            labels[str(label_ids[key])] = {"raw_label": raw, "category": category}
        label_id = label_ids[key]
        if label_id >= IGNORE_LABEL:
            raise ValueError("Too many imported semantic labels")
        label_map[mask] = label_id
        ys, xs = np.nonzero(mask)
        score = entry.get("score")
        if score is not None and (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(score)
            or not 0 <= score <= 1
        ):
            raise ValueError("Imported score must be null or a finite number in [0, 1]")
        rows.append(
            (
                {
                    "asset_id": asset_id,
                    "category": category,
                    "raw_label": raw,
                    "raw_label_id": label_id,
                    "bbox_xyxy": [
                        int(xs.min()),
                        int(ys.min()),
                        int(xs.max()) + 1,
                        int(ys.max()) + 1,
                    ],
                    "area_px": int(mask.sum()),
                    "score": None,
                    "upstream_score_unverified": score,
                    "score_type": "operator_provided_unverified",
                    "instance_method": "operator_provided",
                },
                mask,
            )
        )
    if digest(origin) != origin_hash or digest(source["path"]) != source["sha256"]:
        raise ValueError("Source evidence changed during segmentation import")
    artifacts = _save_artifacts(out, image, label_map, rows)
    manifest = {
        "schema_version": 1,
        "source_image": source,
        "model": None,
        "provenance": {
            "source_type": "operator_provided",
            "worker_sha256": digest(__file__),
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "import_manifest": {"path": str(origin), "sha256": origin_hash},
            "imported_masks": imported_masks,
            "metric_geometry_available": False,
            "material_inference_performed": False,
        },
        "parameters": {"min_area_px": min_area_px},
        "labels": labels,
        **artifacts,
        "timing": {"total_s": time.perf_counter() - started},
        "usage": {"llm_calls": 0, "input_tokens": 0, "output_tokens": 0},
        "limitations": [
            "Imported labels and instance identities are operator-provided, not model detections."
        ],
    }
    write_json(out / "manifest.json", manifest)
    return manifest


def main(argv: list[str] | None = None) -> int:
    """CLI for genuine image inference or explicitly attributed mask import."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--model-id", default=DEFAULT_MODEL_ID)
    parser.add_argument("--revision")
    parser.add_argument("--device", default="cpu")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--min-area-px", type=int, default=256)
    parser.add_argument(
        "--categories", help="Comma-separated canonical category filter"
    )
    parser.add_argument("--local-files-only", action="store_true")
    parser.add_argument("--import-manifest")
    args = parser.parse_args(argv)
    if args.import_manifest:
        if args.categories:
            parser.error("--categories is only supported for model inference")
        result = import_segmentation(
            args.image,
            args.import_manifest,
            args.output_dir,
            min_area_px=args.min_area_px,
        )
    else:
        result = segment_image(
            args.image,
            args.output_dir,
            model_id=args.model_id,
            revision=args.revision,
            device=args.device,
            cpu_threads=args.cpu_threads,
            min_area_px=args.min_area_px,
            categories=args.categories.split(",") if args.categories else None,
            local_files_only=args.local_files_only,
        )
    print(
        json.dumps(
            {
                "manifest": str(Path(args.output_dir).resolve() / "manifest.json"),
                "instances": len(result["instances"]),
                "timing": result["timing"],
                "source_type": result["provenance"]["source_type"],
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
