"""Reproducible fixed-view comparison for subsequent isolated RSI rounds.

Never changes the baseline or promotes a scene. New views belong in a separate
reviewed appendix, not in the frozen development acceptance aggregate.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from legacy.scene_bench.bench import candidate_digest, evaluate, evidence_file, sha256
from legacy.scene_bench.rsi_loop import checked_render, decide, metric, pixels, read


def index_views(path):
    views = read(path)["views"]
    result = {v["view_id"]: v for v in views}
    if len(result) != len(views):
        raise ValueError("Duplicate view ids")
    return result


def verify_pair(candidate, repeat, baseline_assets):
    if candidate["renderer_script_sha256"] != repeat["renderer_script_sha256"]:
        raise ValueError("Candidate and repeat use different renderer code")
    if candidate["raw_capture_index"] != repeat["raw_capture_index"]:
        raise ValueError("Candidate and repeat use different view ordering")
    expected = candidate_digest({"assets": baseline_assets})
    if repeat["candidate_digest"] != expected:
        raise ValueError("Repeat is not the original baseline asset")
    common = {(x["path"], x["sha256"]) for x in candidate["assets"]}
    if not all((x["path"], x["sha256"]) in common for x in baseline_assets):
        raise ValueError("Candidate does not preserve original source layers")


def run(plan_path, repeat_path, candidates, out):
    plan_path = Path(plan_path).resolve()
    root = plan_path.parent
    plan = read(plan_path)
    config_path = evidence_file(
        {"path": plan["baseline_config"], "sha256": plan["baseline_config_sha256"]},
        root,
    )
    config = read(config_path)
    baseline = config["candidates"][0]
    validation = evaluate(config_path)["candidates"][0]
    if validation["errors"]:
        raise ValueError(validation["errors"])
    repeats = index_views(repeat_path)
    results = []
    for name, manifest in candidates:
        views = index_views(manifest)
        rows = []
        for pair in baseline["matched_views"]:
            if pair["split"] != "dev":
                raise ValueError(
                    "Do not consume holdout images in the development loop"
                )
            key = pair["reference_id"] + "_trainonly"
            current, control = views[key], repeats[key]
            verify_pair(current, control, baseline["assets"])
            original = read(pair["render_provenance"]["path"])
            before_image = checked_render(control, original, root)
            after_image = checked_render(current, original, root)
            reference = pixels(pair["reference"]["path"])
            mask = (
                np.asarray(Image.open(pair["mask"]["path"]).convert("L")) > 0
                if pair.get("mask")
                else np.ones(reference.shape[:2], bool)
            )
            before, after = (
                metric(reference, before_image, mask),
                metric(reference, after_image, mask),
            )
            historical = metric(reference, pixels(pair["render"]["path"]), mask)
            rows.append(
                {
                    "id": pair["id"],
                    "baseline": historical,
                    "repeat": before,
                    "candidate": after,
                    "repeat_psnr_drift_abs": abs(
                        before["psnr_db"] - historical["psnr_db"]
                    ),
                    "repeat_mae_drift_abs": abs(
                        before["rgb_mae"] - historical["rgb_mae"]
                    ),
                    "psnr_gain_vs_repeat": after["psnr_db"] - before["psnr_db"],
                    "reference_sha256": pair["reference"]["sha256"],
                    "mask_sha256": (pair.get("mask") or {}).get("sha256"),
                    "candidate_render_sha256": current["render_sha256"],
                    "repeat_render_sha256": control["render_sha256"],
                }
            )
        results.append(
            {
                "candidate_id": name,
                "render_manifest": str(Path(manifest).resolve()),
                "render_manifest_sha256": sha256(manifest),
                "metrics": rows,
                "photometric_decision": decide(
                    rows, plan["predeclared_engineering_gates"]
                ),
                "geometric_validation": "See separate independent asset audit",
                "promotion": False,
            }
        )
    report = {
        "plan_sha256": sha256(plan_path),
        "baseline_config_sha256": sha256(config_path),
        "repeat_manifest_sha256": sha256(repeat_path),
        "candidate_trials": results,
        "scope": "Frozen conditional estimated-pose dev regions only; not full-scene quality",
        "automatic_promotion": False,
        "cloud_calls": 0,
        "limitations": [
            "One repeat measures drift, not a calibrated noise distribution.",
            "Geometry and holdout must be independently reviewed before promotion.",
        ],
    }
    Path(out).write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            [{"id": r["candidate_id"], **r["photometric_decision"]} for r in results],
            indent=2,
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--plan", type=Path, required=True)
    parser.add_argument("--repeat", type=Path, required=True)
    parser.add_argument(
        "--candidate", action="append", required=True, help="name=render_manifest.json"
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    run(args.plan, args.repeat, [s.split("=", 1) for s in args.candidate], args.out)
