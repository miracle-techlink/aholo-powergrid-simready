"""Frozen-view, offline RSI acceptance loop. Never promotes without holdout review.

This compares genuine opacity-only USD candidates, not arbitrary enhanced images.
Original camera review admits a diagnostic scope; it is not reused as a new review.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image

from legacy.scene_bench.bench import candidate_digest, evaluate, evidence_file, sha256


def read(path):
    return json.loads(Path(path).read_text())


def pixels(path):
    return np.asarray(Image.open(path).convert("RGB"), dtype=np.float64) / 255


def metric(ref, image, mask):
    if ref.shape != image.shape or mask.shape != ref.shape[:2] or not mask.any():
        raise ValueError("Image/ROI shape mismatch or empty mask")
    delta = ref[mask] - image[mask]
    mse = float(np.mean(delta**2))
    return {
        "psnr_db": float(-10 * np.log10(mse)) if mse else 100.0,
        "rgb_mae": float(np.mean(np.abs(delta))),
    }


def decide(rows, gates):
    gain = float(np.mean([r["psnr_gain_vs_repeat"] for r in rows]))
    noise = float(np.mean([r["repeat_psnr_drift_abs"] for r in rows]))
    old = float(np.mean([r["repeat"]["rgb_mae"] for r in rows]))
    new = float(np.mean([r["candidate"]["rgb_mae"] for r in rows]))
    reduction = (old - new) / old if old else 0.0
    noise_mae = float(np.mean([r["repeat_mae_drift_abs"] for r in rows]))
    checks = {
        "mean_psnr_gain": gain
        >= max(
            gates["minimum_mean_psnr_gain_db"],
            gates["baseline_repeat_noise_multiplier"] * noise,
        ),
        "mean_mae_reduction": reduction >= gates["minimum_mean_mae_reduction_fraction"],
        "mae_gain_exceeds_repeat_drift": old - new
        > gates["baseline_repeat_noise_multiplier"] * noise_mae,
        "no_roi_regression": all(
            r["candidate"]["rgb_mae"] - r["repeat"]["rgb_mae"]
            <= gates["maximum_any_roi_mae_increase"]
            for r in rows
        ),
    }
    return {
        "status": "DEV_IMPROVEMENT_PENDING_HOLDOUT"
        if all(checks.values())
        else "REJECT_KEEP_BASELINE",
        "checks": checks,
        "mean_psnr_gain_db": gain,
        "mean_mae_reduction_fraction": reduction,
        "mean_repeat_psnr_drift_abs_db": noise,
        "mean_repeat_mae_drift_abs": noise_mae,
        "aggregation": "equal weight per predeclared ROI; 3 ROIs from 2 correlated views",
        "promoted": False,
        "holdout_reviewed": False,
    }


def checked_render(entry, original, root):
    evidence_file({"path": entry["render"], "sha256": entry["render_sha256"]}, root)
    for asset in entry["assets"]:
        evidence_file(asset, root)
    if entry["candidate_digest"] != candidate_digest(entry):
        raise ValueError("Candidate digest mismatch")
    evidence_file(
        {"path": entry["resolved_camera"], "sha256": entry["resolved_camera_sha256"]},
        root,
    )
    current = read(entry["resolved_camera"])
    old = read(original["resolved_camera"])
    for field in ("resolution", "K", "camera_to_world_cv", "near_far", "projection"):
        if current[field] != old[field]:
            raise ValueError(f"Camera changed: {field}")
    if entry["render_settings"] != original["render_settings"]:
        raise ValueError("Renderer settings changed")
    if entry["camera_evidence_sha256"] != original["camera_evidence_sha256"]:
        raise ValueError("Camera evidence changed")
    settle = original["static_settle_captures"]
    if (
        entry["static_settle_captures"] != settle
        or entry["raw_capture_index"] % settle != original["raw_capture_index"] % settle
    ):
        raise ValueError("Capture settling changed")
    return pixels(entry["render"])


def run(campaign):
    campaign = Path(campaign).resolve()
    plan = read(campaign / "plan.json")
    config_path = evidence_file(
        {"path": plan["baseline_config"], "sha256": plan["baseline_config_sha256"]},
        campaign,
    )
    config = read(config_path)
    baseline_audit = evaluate(config_path)
    if baseline_audit["candidates"][0]["errors"]:
        raise ValueError(baseline_audit["candidates"][0]["errors"])
    audit = {
        a["candidate_id"]: a
        for a in read(campaign / "evaluation/patch_independent_audit.json")
    }
    repeats = {
        v["view_id"]: v
        for v in read(campaign / "repairs/baseline_repeat/render_manifest.json")[
            "views"
        ]
    }
    ledger = []
    for name in ("opacity_lt_0p005", "opacity_lt_0p01"):
        if not audit[name]["only_opacity_override"]:
            raise ValueError("Candidate lacks independent opacity-only audit")
        manifest_path = campaign / "repairs" / name / "candidate_manifest.json"
        manifest = read(manifest_path)
        if audit[name]["manifest_sha256"] != sha256(manifest_path) or audit[name][
            "patch_sha256"
        ] != sha256(manifest["patch"]):
            raise ValueError("Independent patch audit is stale")
        views = {
            v["view_id"]: v
            for v in read(manifest_path.parent / "render/render_manifest.json")["views"]
        }
        rows = []
        for pair in config["candidates"][0]["matched_views"]:
            if pair["split"] != "dev":
                raise ValueError("This development round must not read heldout images")
            original = read(pair["render_provenance"]["path"])
            view_id = pair["reference_id"] + "_trainonly"
            if (
                views[view_id]["renderer_script_sha256"]
                != repeats[view_id]["renderer_script_sha256"]
            ):
                raise ValueError(
                    "Candidate renderer differs from paired repeat control"
                )
            repeat = checked_render(repeats[view_id], original, campaign)
            candidate = checked_render(views[view_id], original, campaign)
            ref = pixels(pair["reference"]["path"])
            mask = (
                np.asarray(Image.open(pair["mask"]["path"]).convert("L")) > 0
                if pair.get("mask")
                else np.ones(ref.shape[:2], bool)
            )
            before = metric(ref, pixels(pair["render"]["path"]), mask)
            control = metric(ref, repeat, mask)
            after = metric(ref, candidate, mask)
            rows.append(
                {
                    "id": pair["id"],
                    "baseline": before,
                    "repeat": control,
                    "candidate": after,
                    "candidate_render_sha256": views[view_id]["render_sha256"],
                    "repeat_render_sha256": repeats[view_id]["render_sha256"],
                    "mask_sha256": (pair.get("mask") or {}).get("sha256"),
                    "psnr_gain_vs_repeat": after["psnr_db"] - control["psnr_db"],
                    "repeat_psnr_drift_abs": abs(
                        control["psnr_db"] - before["psnr_db"]
                    ),
                    "repeat_mae_drift_abs": abs(control["rgb_mae"] - before["rgb_mae"]),
                }
            )
        ledger.append(
            {
                "candidate_id": name,
                "manifest_sha256": sha256(manifest_path),
                "kind": "3d_opacity_patch",
                "scope": "conditional estimated-pose appearance comparison, not fresh independent review",
                "metrics": rows,
                "decision": decide(rows, plan["predeclared_engineering_gates"]),
                "rollback": "Keep original scene; candidate layer is never applied to original",
                "source_unchanged": "All pinned baseline assets rehashed successfully",
            }
        )
    output = campaign / "evaluation"
    report = {
        "plan_sha256": sha256(campaign / "plan.json"),
        "baseline_config_sha256": sha256(config_path),
        "candidate_trials": ledger,
        "cloud_calls": 0,
        "limitations": [
            "Only 2 estimated-pose dev views; original camera review scopes are frozen.",
            "One baseline repeat measures drift, not a statistical noise distribution.",
            "Original baseline renderer predates provenance fields; paired repeat and candidates use identical renderer script SHA.",
            "No heldout review or geometry/collision claim; no original asset modified.",
        ],
    }
    (output / "round_001.json").write_text(json.dumps(report, indent=2) + "\n")
    rejected = [
        x["candidate_id"]
        for x in ledger
        if x["decision"]["status"] == "REJECT_KEEP_BASELINE"
    ]
    skill = {
        "id": "starbucks_low_opacity_pruning_v1",
        "evidence": str(output / "round_001.json"),
        "evidence_sha256": sha256(output / "round_001.json"),
        "validated_improvement": False,
        "avoid_repeating_unchanged": rejected,
        "next_action": "Use localized visibility/multiview evidence before further opacity pruning; compare no-ref model image enhancement separately.",
        "scope": "These thresholds and this asset only; not a universal pruning conclusion.",
    }
    (output / "skill_store.json").write_text(json.dumps([skill], indent=2) + "\n")
    print(
        json.dumps(
            [{"id": x["candidate_id"], **x["decision"]} for x in ledger], indent=2
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("campaign", type=Path)
    run(parser.parse_args().campaign)
