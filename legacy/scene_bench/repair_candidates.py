#!/usr/bin/env python3
"""Freeze and construct two reversible opacity ablations; CPU-only pxr."""

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path

os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
import numpy as np
from pxr import Usd, Sdf, Vt
from legacy.scene_bench.audit_usd import local_dependencies


def sha(p):
    h = hashlib.sha256()
    with Path(p).open("rb") as f:
        for b in iter(lambda: f.read(1048576), b""):
            h.update(b)
    return h.hexdigest()


def save(p, d):
    p.write_text(json.dumps(d, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--scene",
        type=Path,
        default=Path(
            "/home/nvidia/Desktop/星巴克重建资产_20261108/02_星巴克实拍重建/scene.usda"
        ),
    )
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    out, scene = args.out.resolve(), args.scene.resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any(p.name != "frozen_proposal.json" for p in out.iterdir()):
        raise FileExistsError("Output already contains candidate artifacts")
    proposal = {
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_scene": str(scene),
        "thresholds": [0.005, 0.01],
        "operation": "Set composed opacities strictly below threshold to zero; preserve point order/count and every other attribute.",
        "hypothesis": "Low-opacity contributions may include weak floating clutter, but may also encode valid thin/window structures. Both candidates may be rejected.",
        "parameter_policy": "Frozen before candidate construction or reading held-out views; no image-guided threshold tuning.",
        "evaluation": "Fixed ref3/ref4 cameras, same renderer/settings, baseline repeat to estimate rendering variability; independent review owns any holdout.",
    }
    if (out / "frozen_proposal.json").exists():
        proposal = json.loads((out / "frozen_proposal.json").read_text())
    else:
        save(out / "frozen_proposal.json", proposal)
    dependencies = local_dependencies(scene)
    deps, errors = dependencies["files"], dependencies["errors"]
    if errors:
        raise RuntimeError(errors)
    stage = Usd.Stage.Open(str(scene))
    prim_path = "/World/StarbucksSplats"
    prim = stage.GetPrimAtPath(prim_path)
    op = prim.GetAttribute("opacities")
    original = np.array(op.Get(), dtype=np.float32, copy=True)
    if not np.isfinite(original).all() or np.any(original < 0) or np.any(original > 1):
        raise ValueError("Expected finite activated linear opacities in [0,1]")
    if prim.GetAttribute("opacitiesh").HasAuthoredValueOpinion():
        raise ValueError("Half opacity precedence requires separate review")
    manifests = []
    for threshold in proposal["thresholds"]:
        ident = "opacity_lt_" + str(threshold).replace(".", "p")
        directory = out / ident
        directory.mkdir()
        selected = original < threshold
        changed = selected & (original != 0)
        edited = original.copy()
        edited[selected] = 0
        patch = directory / "opacity_patch.usdc"
        patch_stage = Usd.Stage.CreateNew(str(patch))
        attr = patch_stage.OverridePrim(prim_path).CreateAttribute(
            "opacities", Sdf.ValueTypeNames.FloatArray
        )
        attr.Set(Vt.FloatArray.FromNumpy(edited))
        patch_stage.GetRootLayer().Save()
        compose = directory / "scene.usda"
        composed = Usd.Stage.CreateNew(str(compose))
        composed.GetRootLayer().subLayerPaths = [str(patch), str(scene)]
        composed.SetMetadata("upAxis", "Z")
        composed.SetMetadata("metersPerUnit", 1.0)
        composed.SetDefaultPrim(composed.GetPrimAtPath("/World"))
        composed.GetRootLayer().Save()
        readback = np.asarray(
            composed.GetPrimAtPath(prim_path).GetAttribute("opacities").Get()
        )
        if not np.array_equal(readback, edited):
            raise AssertionError("Composed opacity readback differs")
        # Only opacity is authored by the candidate. Source composition supplies all geometry and SH.
        authored = list(patch_stage.GetPrimAtPath(prim_path).GetAuthoredAttributes())
        if [a.GetName() for a in authored] != ["opacities"]:
            raise AssertionError("Unexpected authored properties")
        manifest = {
            "candidate_id": ident,
            "status": "constructed_not_evaluated",
            "proposal": str(out / "frozen_proposal.json"),
            "proposal_sha256": sha(out / "frozen_proposal.json"),
            "scene": str(compose),
            "scene_sha256": sha(compose),
            "patch": str(patch),
            "patch_sha256": sha(patch),
            "source_assets": deps,
            "prim_path": prim_path,
            "attribute": "opacities",
            "threshold": threshold,
            "strict_comparison": "<",
            "replacement_value": 0,
            "point_count": len(original),
            "selected_count": int(selected.sum()),
            "changed_count": int(changed.sum()),
            "changed_fraction": float(changed.mean()),
            "removed_opacity_sum": float(original[selected].sum(dtype=np.float64)),
            "total_opacity_sum": float(original.sum(dtype=np.float64)),
            "unchanged": [
                "point count and order",
                "positions",
                "scales",
                "orientations",
                "SH coefficients",
                "scene transforms",
            ],
            "validation": {
                "exact_composed_opacity_readback": True,
                "patch_authors_only_opacities": True,
            },
            "interpretation": "Opacity ablation, not proven denoising or recovered geometry; zero opacity retains source point data.",
            "rollback": "Open the original scene or remove this patch sublayer.",
        }
        save(directory / "candidate_manifest.json", manifest)
        manifests.append(manifest)
    after = local_dependencies(scene)
    if after["errors"] or after["files"] != deps:
        raise AssertionError("Source dependencies changed during construction")
    save(
        out / "candidates.json",
        {"source_assets_unchanged": True, "candidates": manifests},
    )
    print(
        json.dumps(
            [
                {
                    "id": m["candidate_id"],
                    "changed": m["changed_count"],
                    "fraction": m["changed_fraction"],
                }
                for m in manifests
            ]
        )
    )


if __name__ == "__main__":
    main()
