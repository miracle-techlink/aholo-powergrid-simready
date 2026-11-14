"""Record supported plane fits and insufficient evidence in the cafe left wing."""

import argparse
import json
from pathlib import Path

import numpy as np
from pxr import Usd

from simready.core.artifacts import digest, write_json
from simready.repair.structural_completion import robust_plane


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference-assets", required=True, type=Path)
    parser.add_argument("--sfm-root", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    if args.out.exists():
        parser.error("Refusing to replace existing layout evidence")
    source = args.reference_assets / "scene/original_video_splats.usdc"
    alignment = args.sfm_root / "integration/full_alignment.json"
    matrix = np.asarray(json.loads(alignment.read_text())["new_to_scene_sim3_column"])
    stage = Usd.Stage.Open(str(source))
    field = next(
        p for p in stage.Traverse() if p.GetTypeName() == "ParticleField3DGaussianSplat"
    )
    points = np.asarray(field.GetAttribute("positions").Get())
    alpha = np.asarray(field.GetAttribute("opacities").Get())
    scales = np.asarray(field.GetAttribute("scales").Get()).max(1)
    points = points[(alpha > 0.25) & (scales < 0.07)]
    points = points @ matrix[:3, :3].T + matrix[:3, 3]
    record = {
        "inputs": {str(source): digest(source), str(alignment): digest(alignment)}
    }
    for name, low, high, axis in [
        ("stair_wood_wall", [-1.85, -4.4, -0.3], [-1.5, -3.02, 2], 0),
        ("railing_front", [-6.2, -3.16, 0.45], [-3, -2.82, 0.75], 1),
        ("railing_back", [-3, -4.5, 0.35], [-1.55, -4.08, 0.8], 1),
        ("corridor_round_table", [0.5, -2.65, 0.12], [1.1, -2.15, 0.38], 2),
        ("stair_round_table", [-5.5, -2.75, 0.05], [-4.6, -2.0, 0.35], 2),
    ]:
        subset = points[((points > low) & (points < high)).all(1)]
        result = {"points": len(subset), "roi": [low, high]}
        if len(subset) < 30:
            result["status"] = "INSUFFICIENT_SUPPORT"
        else:
            coefficients, keep = robust_plane(subset, axis, 0.025)
            design = np.c_[
                subset[keep][:, [a for a in range(3) if a != axis]], np.ones(keep.sum())
            ]
            result.update(
                coefficients=coefficients.tolist(),
                axis=axis,
                inliers=int(keep.sum()),
                inlier_q05_q95=np.quantile(subset[keep], [0.05, 0.95], axis=0).tolist(),
                median_abs_residual=float(
                    np.median(abs(subset[keep, axis] - design @ coefficients))
                ),
            )
        record[name] = result
    write_json(args.out, record)


if __name__ == "__main__":
    main()
