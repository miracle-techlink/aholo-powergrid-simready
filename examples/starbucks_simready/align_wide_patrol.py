"""Record local tabletop alignment between the two cafe reconstructions.

A trimmed nearest-point translation aligns supported planar tabletop samples.
It is a local scene estimate, not a measurement of real-world metric scale.
"""

import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Usd
from scipy.spatial import cKDTree

from simready.core.artifacts import digest, write_json
from simready.repair.structural_completion import robust_plane


def cloud(path):
    stage = Usd.Stage.Open(str(path))
    field = next(
        p for p in stage.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"
    )
    points = np.asarray(field.GetAttribute("positions").Get())
    alpha = np.asarray(field.GetAttribute("opacities").Get())
    scales = np.asarray(field.GetAttribute("scales").Get()).max(1)
    return points[(alpha > 0.25) & (scales < 0.06)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reference-assets", type=Path, required=True)
    ap.add_argument("--case-assets", type=Path, required=True)
    ap.add_argument("--sfm-alignment", type=Path, required=True)
    ap.add_argument("--reference-to-case", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    if args.out.exists():
        ap.error("Output already exists; preserve previous alignment evidence")
    reference_path = args.reference_assets / "scene/original_video_splats.usdc"
    case_path = args.case_assets / "scene/original_video_splats.usdc"
    reference, target = cloud(reference_path), cloud(case_path)
    alignment = np.asarray(
        json.loads(args.sfm_alignment.read_text())["new_to_scene_sim3_column"]
    )
    reference = reference @ alignment[:3, :3].T + alignment[:3, 3]
    transform = np.asarray(
        json.loads(args.reference_to_case.read_text())["reference_to_case"]
    )
    record = {
        "inputs": {
            str(p): digest(p)
            for p in [
                reference_path,
                case_path,
                args.sfm_alignment,
                args.reference_to_case,
            ]
        }
    }
    for name, ref_low, ref_high, low, high in [
        (
            "near",
            [-1.4, -1.1, 0.15],
            [2.4, 0.08, 0.6],
            [-0.9, -1.1, 0.15],
            [2.4, 0.08, 0.5],
        ),
        (
            "far",
            [-6.2, -1.2, 0.3],
            [-2.8, 0.02, 0.8],
            [-5.6, -1.2, 0.25],
            [-2.2, 0.06, 0.6],
        ),
    ]:
        source = reference[((reference > ref_low) & (reference < ref_high)).all(1)]
        dest = target[((target > low) & (target < high)).all(1)]
        _, source_mask = robust_plane(source, 2, 0.035)
        _, target_mask = robust_plane(dest, 2, 0.035)
        source = source[source_mask] @ transform[:3, :3].T + transform[:3, 3]
        dest = dest[target_mask]
        delta = np.quantile(dest, [0.04, 0.96], axis=0).mean(0) - np.quantile(
            source, [0.04, 0.96], axis=0
        ).mean(0)
        tree = cKDTree(dest)
        for _ in range(30):
            distances, indices = tree.query(source + delta)
            keep = distances < np.quantile(distances, 0.8)
            delta += np.median(dest[indices[keep]] - (source + delta)[keep], axis=0)
        record[name] = {
            "case_shift_m": delta.tolist(),
            "reference_shift_m": (delta @ transform[:3, :3]).tolist(),
            "median_nn_error_m": float(np.median(tree.query(source + delta)[0])),
            "source_points": len(source),
            "target_points": len(dest),
            "reference_roi": [ref_low, ref_high],
            "case_roi": [low, high],
            "scope": "trimmed local translation of independently reconstructed tabletop points; not metric calibration",
        }
    write_json(args.out, record)


if __name__ == "__main__":
    main()
