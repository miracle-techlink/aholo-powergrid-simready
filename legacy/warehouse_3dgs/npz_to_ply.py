#!/usr/bin/env python3
"""Convert model.npz (our 3DGS format) -> standard 3DGS PLY for usd-convert-gsplat.

Output PLY fields: x y z nx ny nz f_dc_0 f_dc_1 f_dc_2 opacity scale_0 scale_1 scale_2 rot_0 rot_1 rot_2 rot_3
No SH rest coeffs (f_rest) — DC-only spherical harmonics (degree 0).
"""

import os
import sys
import numpy as np
import argparse

_SH_C0 = 0.28209479177387814


def rgb_to_fdc(rgb: np.ndarray) -> np.ndarray:
    """RGB [0,1] -> SH DC coefficient. f_dc = (rgb - 0.5) / C0

    Preserves black and white endpoints without introducing a color bias.
    """
    fdc = (rgb - 0.5) / _SH_C0
    return fdc.astype(np.float32)


def write_ply(
    path: str,
    positions: np.ndarray,
    f_dc: np.ndarray,
    opacities: np.ndarray,
    scales: np.ndarray,
    quats_wxyz: np.ndarray,
):
    """Write binary little-endian PLY with 3DGS standard fields."""
    n = len(positions)
    header = f"""ply
format binary_little_endian 1.0
element vertex {n}
property float x
property float y
property float z
property float nx
property float ny
property float nz
property float f_dc_0
property float f_dc_1
property float f_dc_2
property float opacity
property float scale_0
property float scale_1
property float scale_2
property float rot_0
property float rot_1
property float rot_2
property float rot_3
end_header
"""
    normals = np.zeros((n, 3), dtype=np.float32)
    # Pack: x y z nx ny nz fdc0 fdc1 fdc2 opacity s0 s1 s2 r0 r1 r2 r3
    data = np.column_stack(
        [
            positions.astype(np.float32),
            normals,
            f_dc,
            opacities.astype(np.float32).reshape(-1, 1),
            scales.astype(np.float32),
            quats_wxyz.astype(np.float32),
        ]
    ).astype("<f4")

    with open(path, "wb") as f:
        f.write(header.encode("ascii"))
        f.write(data.tobytes())
    print(f"[PLY] wrote {n} splats -> {path} ({os.path.getsize(path) / 1e6:.1f} MB)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("input", help="model.npz path")
    ap.add_argument("output", help="output .ply path")
    ap.add_argument(
        "--quat-order",
        choices=["xyzw", "wxyz"],
        default="xyzw",
        help="Explicit input quaternion order; current model format is xyzw",
    )
    args = ap.parse_args()

    from legacy.rsi_3dgs.model_io import GaussianModel
    from simready_bridge.geometry import reorder_quaternion

    with np.load(args.input, allow_pickle=False) as d:
        model = GaussianModel(
            **{k: d[k] for k in ("means", "scales", "quats", "rgb", "opacity")}
        )
    means, scales, rgb, opacity = model.means, model.scales, model.rgb, model.opacity
    quats = reorder_quaternion(model.quats, args.quat_order, "wxyz")
    quats /= np.linalg.norm(quats, axis=1, keepdims=True)
    if model.n == 0:
        raise ValueError("Cannot export an empty Gaussian model")

    n = len(means)
    print(f"[PLY] loaded {n} splats from {args.input}")
    print(f"  means range: [{means.min():.3f}, {means.max():.3f}]")
    print(f"  scales range: [{scales.min():.6f}, {scales.max():.6f}]")
    print(f"  opacity range: [{opacity.min():.4f}, {opacity.max():.4f}]")

    # usd-convert-gsplat expects log-space scales (it applies exp() internally)
    log_scales = np.log(np.clip(scales, 1e-8, None))

    # usd-convert-gsplat expects logit-space opacity (it applies sigmoid internally)
    opacity_clipped = np.clip(opacity, 1e-6, 1.0 - 1e-6)
    logit_opacity = np.log(opacity_clipped / (1.0 - opacity_clipped))

    # PLY rot_0..3 are wxyz; input order was explicitly converted above.
    f_dc = rgb_to_fdc(rgb)

    write_ply(args.output, means, f_dc, logit_opacity, log_scales, quats)


if __name__ == "__main__":
    main()
