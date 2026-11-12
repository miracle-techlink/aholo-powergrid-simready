"""局部修复算子. 纯函数: (受损 model, DetectionReport, 参考观测) -> RepairResult.

修复策略库(对应 skill_store 的"配方"):
- delete_indices:      删漂浮/离群高斯
- inpaint_from_views:  洞区从参考视图点云补点(密度加权, 颜色取观测)
- color_blend:         污染区颜色向邻域中位色回拉
- repose:              位姿扰动 → 还原干净 w2cs(需原始备份)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional

import numpy as np

from .detect import DetectionReport
from .model_io import GaussianModel


@dataclass
class RepairResult:
    model: GaussianModel
    recipe: str
    n_changed: int
    detail: dict = field(default_factory=dict)

    def summary(self) -> dict:
        return {"recipe": self.recipe, "n_changed": self.n_changed, **self.detail}


def delete_indices(model: GaussianModel, report: DetectionReport) -> RepairResult:
    """删除报告索引(漂浮噪声/离群点)."""
    idx = np.asarray(report.indices, dtype=np.int64)
    if len(idx) == 0:
        return RepairResult(model, "delete_indices", 0, {"note": "no indices"})
    return RepairResult(model.delete(idx), "delete_indices", int(len(idx)))


def color_blend(
    model: GaussianModel, report: DetectionReport, k=12, strength=0.85, max_n=200_000
) -> RepairResult:
    """污染点颜色向 kNN 中位色回拉(strength 控制回拉幅度)."""
    idx = np.asarray(report.indices, dtype=np.int64)
    if len(idx) == 0:
        return RepairResult(model, "color_blend", 0, {"note": "no indices"})
    pts, rgb = model.means, model.rgb.copy()
    if type(max_n) is not int or max_n < 1:
        raise ValueError("max_n must be positive")
    if len(pts) > max_n:  # 大模型下 kNN 全矩阵不可行 → 子采样参考集
        rng = np.random.default_rng(0)
        ref = rng.choice(len(pts), max_n, replace=False)
    else:
        ref = np.arange(len(pts))
    from scipy.spatial import cKDTree

    if type(k) is not int or k < 1 or not 0 <= strength <= 1:
        raise ValueError("Invalid color blend parameters")
    if (idx < 0).any() or (idx >= model.n).any():
        raise ValueError("Repair index out of range")
    tree = cKDTree(pts[ref])
    neighbors = min(k, len(ref))
    for start in range(0, len(idx), 16384):
        ci = idx[start : start + 16384]
        _, nearest = tree.query(pts[ci], k=neighbors)
        nearest = np.asarray(nearest).reshape(len(ci), neighbors)
        med = np.median(model.rgb[ref][nearest], axis=1)
        rgb[ci] = np.clip(model.rgb[ci] * (1 - strength) + med * strength, 0, 1)
    return RepairResult(
        model.with_arrays(rgb=rgb),
        "color_blend",
        int(len(idx)),
        {"k": k, "strength": strength},
    )


def inpaint_from_views(
    model: GaussianModel,
    center,
    radius,
    z_max=0.3,
    views_dir=None,
    target_density=25.0,
    voxel=0.02,
    max_fill=200_000,
    seed=0,
) -> RepairResult:
    """洞区补点: 从 views_dir 的点云(npz pts/rgb)取落入洞区的观测点.

    target_density: 每平米目标点数(对齐场景地面密度量级).
    """
    import glob

    if views_dir is None:
        return RepairResult(
            model, "inpaint_from_views", 0, {"note": "no source directory"}
        )
    if (
        voxel <= 0
        or radius <= 0
        or target_density < 0
        or type(max_fill) is not int
        or max_fill < 0
    ):
        raise ValueError("Invalid fill bounds")
    c = np.asarray(center[:2], np.float32)
    # 现有洞区点(几乎为空) → 由 target_density 决定补多少
    area = np.pi * radius**2
    n_target = int(target_density * area)
    # 汇聚所有视图点云, 取落在洞区圆柱内的
    pool_p, pool_c = [], []
    for f in sorted(glob.glob(str(Path(views_dir) / "pc_*.npz"))):
        with np.load(f, allow_pickle=False) as d:
            p, raw_rgb = d["pts"], d["rgb"]
            rgb = raw_rgb.astype(np.float32) / (
                255.0 if raw_rgb.dtype == np.uint8 else 1.0
            )
        if (
            p.shape != rgb.shape
            or p.ndim != 2
            or p.shape[1] != 3
            or not np.isfinite(p).all()
        ):
            raise ValueError("Invalid source point cloud")
        if not np.isfinite(rgb).all() or (rgb < 0).any() or (rgb > 1).any():
            raise ValueError("Source colors require uint8 [0,255] or float [0,1]")
        m = ((p[:, :2] - c) ** 2).sum(-1) < radius**2
        if z_max is not None:
            m &= p[:, 2] < z_max
        if m.any():
            pool_p.append(p[m])
            pool_c.append(rgb[m])
    if not pool_p:
        return RepairResult(
            model, "inpaint_from_views", 0, {"note": "no source points"}
        )
    P = np.concatenate(pool_p)
    C = np.concatenate(pool_c)
    rng = np.random.default_rng(seed)
    # 体素去重后重采样到目标密度
    q = np.floor(P / voxel).astype(np.int64)
    uk, inv = np.unique(q, axis=0, return_inverse=True)
    cnt = np.bincount(inv)
    sump = np.zeros((len(uk), 3))
    np.add.at(sump, inv, P)
    sumc = np.zeros((len(uk), 3))
    np.add.at(sumc, inv, C)
    P = (sump / cnt[:, None]).astype(np.float32)
    C = (sumc / cnt[:, None]).astype(np.float32)
    limit = min(n_target, max_fill)
    if len(P) > limit:
        sel = rng.choice(len(P), limit, replace=False)
        P, C = P[sel], C[sel]
    n = len(P)
    fill = GaussianModel(
        means=P,
        scales=np.full((n, 3), voxel * 1.1, np.float32),
        quats=np.tile(np.array([0.0, 0.0, 0.0, 1.0], np.float32), (n, 1)),
        rgb=C,
        opacity=np.full(n, 0.85, np.float32),
        K=model.K,
        w2cs=model.w2cs,
        H=model.H,
        W=model.W,
    )
    return RepairResult(
        model.append(fill),
        "inpaint_from_views",
        n,
        {"n_fill": n, "target_density": target_density},
    )


def repose(model: GaussianModel, record) -> RepairResult:
    """位姿扰动 → 按 DefectRecord 中保存的确定性扰动量逆变换还原."""
    d = record.params["deltas"]
    ang = np.asarray(d["ang"], np.float32)
    axis = np.asarray(d["axis"], np.float32)
    dt = np.asarray(d["dt"], np.float32)
    K_ = np.zeros((len(ang), 3, 3), np.float32)
    K_[:, 0, 1] = -axis[:, 2]
    K_[:, 0, 2] = axis[:, 1]
    K_[:, 1, 0] = axis[:, 2]
    K_[:, 1, 2] = -axis[:, 0]
    K_[:, 2, 0] = -axis[:, 1]
    K_[:, 2, 1] = axis[:, 0]
    ca, sa = np.cos(ang)[:, None, None], np.sin(ang)[:, None, None]
    dR = (
        ca * np.eye(3)[None]
        + sa * K_
        + (1 - ca) * (axis[:, :, None] @ axis[:, None, :])
    )
    w2cs = model.w2cs.copy()
    w2cs[:, :3, 3] -= dt
    w2cs[:, :3, :3] = np.transpose(dR, (0, 2, 1)) @ w2cs[:, :3, :3]
    return RepairResult(model.with_arrays(w2cs=w2cs), "repose", int(len(ang)), {})


# 缺陷类型 → 推荐配方
RECIPES = {
    "hole": "inpaint_from_views",
    "floater": "delete_indices",
    "color": "color_blend",
    "pose_jitter": "repose",
}
