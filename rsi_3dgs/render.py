"""冻结机位渲染 + 图像指标 + 对比帧合成.

渲染入口统一把 xyzw→wxyz, scales 已是线性值; 全 float32(GB10 统一内存, double 会崩).
"""
from __future__ import annotations

import math
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from .model_io import GaussianModel

_HFZOOM = 4  # 对比帧放大倍率


def _to_torch(model: GaussianModel, device="cuda"):
    import torch
    q = torch.tensor(model.quats, dtype=torch.float32, device=device)
    quats = torch.cat([q[:, 3:], q[:, :3]], 1)  # xyzw -> wxyz
    return dict(
        means=torch.tensor(model.means, dtype=torch.float32, device=device),
        quats=quats,
        scales=torch.tensor(model.scales, dtype=torch.float32, device=device).clamp(1e-6, 1.0),
        opacities=torch.tensor(model.opacity, dtype=torch.float32, device=device),
        colors=torch.tensor(model.rgb, dtype=torch.float32, device=device).clamp(0, 1),
    )


def render_view(model: GaussianModel, w2c: np.ndarray,
                K: Optional[np.ndarray] = None, device="cuda") -> np.ndarray:
    """单视角渲染 → (H,W,3) float32 [0,1]."""
    import torch
    import gsplat
    K = model.K if K is None else K
    if K is None:
        raise ValueError("模型无内参 K, 且未显式传入")
    t = _to_torch(model, device)
    vm = torch.tensor(w2c, dtype=torch.float32, device=device).unsqueeze(0)
    Kd = torch.tensor(K, dtype=torch.float32, device=device).unsqueeze(0)
    out, _, _ = gsplat.rasterization(**t, viewmats=vm, Ks=Kd,
                                     width=model.W, height=model.H, render_mode="RGB")
    return out[0].clamp(0, 1).detach().cpu().numpy()


def render_views(model: GaussianModel, w2cs: Optional[np.ndarray] = None,
                 indices: Optional[Sequence[int]] = None, device="cuda") -> List[np.ndarray]:
    """批量冻结机位渲染. w2cs 缺省用模型自带."""
    w2cs = model.w2cs if w2cs is None else w2cs
    if w2cs is None:
        raise ValueError("无 w2cs 可渲染")
    idx = list(indices) if indices is not None else list(range(len(w2cs)))
    return [render_view(model, w2cs[i], device=device) for i in idx]


def psnr(a: np.ndarray, b: np.ndarray, eps=1e-10) -> float:
    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    return 100.0 if mse < eps else -10 * math.log10(mse)


def ssim(a: np.ndarray, b: np.ndarray) -> float:
    """SSIM (gaussian 11x11, 标准 3DGS 口径), 三通道均值."""
    a8 = (np.clip(a, 0, 1) * 255).astype(np.uint8)
    b8 = (np.clip(b, 0, 1) * 255).astype(np.uint8)
    return float(np.mean([_ssim_ch(a8[..., c], b8[..., c]) for c in range(3)]))


def _ssim_ch(a, b) -> float:
    import cv2
    C1, C2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    a, b = a.astype(np.float64), b.astype(np.float64)
    mu_a, mu_b = cv2.GaussianBlur(a, (11, 11), 1.5), cv2.GaussianBlur(b, (11, 11), 1.5)
    va = cv2.GaussianBlur(a * a, (11, 11), 1.5) - mu_a ** 2
    vb = cv2.GaussianBlur(b * b, (11, 11), 1.5) - mu_b ** 2
    cab = cv2.GaussianBlur(a * b, (11, 11), 1.5) - mu_a * mu_b
    ssim_map = ((2 * mu_a * mu_b + C1) * (2 * cab + C2)) / ((mu_a ** 2 + mu_b ** 2 + C1) * (va + vb + C2))
    return float(ssim_map.mean())


def side_by_side(img_a: np.ndarray, img_b: np.ndarray,
                 label_a="before", label_b="after",
                 diff_amplify: float = 4.0) -> np.ndarray:
    """[A | B | 放大diff] 三联对比帧 → uint8 (H, 3W+2b, 3)."""
    import cv2
    a = (np.clip(img_a, 0, 1) * 255).astype(np.uint8)
    b = (np.clip(img_b, 0, 1) * 255).astype(np.uint8)
    diff = np.clip(np.abs(a.astype(np.float32) - b.astype(np.float32)) * diff_amplify, 0, 255).astype(np.uint8)
    bar = np.full((a.shape[0], 4, 3), 255, np.uint8)
    row = np.concatenate([a, bar, b, bar, diff], 1)
    font = cv2.FONT_HERSHEY_SIMPLEX
    for x, t in [(8, label_a), (a.shape[1] + 12, label_b), (2 * a.shape[1] + 16, f"|diff|x{diff_amplify:g}")]:
        cv2.putText(row, t, (x, 28), font, 0.8, (255, 255, 255), 2, cv2.LINE_AA)
        cv2.putText(row, t, (x, 28), font, 0.8, (0, 0, 0), 1, cv2.LINE_AA)
    return row


def write_video(frames: List[np.ndarray], path, fps=2) -> None:
    import imageio
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    imageio.mimwrite(str(path), frames, fps=fps, quality=9)
