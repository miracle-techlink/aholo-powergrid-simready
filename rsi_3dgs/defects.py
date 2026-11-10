"""可控缺陷注入. 每类缺陷: 纯函数 model -> (受损 model, DefectRecord).

缺陷类型(对齐作战文档终端2):
- hole:    地面/区域内删高斯成洞(模拟重建缺失)
- floater: 加漂浮噪声团(模拟 floater 伪影)
- color:   局部颜色污染(模拟光照/纹理错误)
- pose_jitter: 位姿扰动(作用于 w2cs, 检验机位敏感性) —— 元信息型, 不改高斯
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List

import numpy as np

from .model_io import GaussianModel

DEFECT_TYPES = ("hole", "floater", "color", "pose_jitter")


@dataclass
class DefectRecord:
    defect_type: str
    seed: int
    affected_indices: List[int]       # 受影响高斯索引(hole 为被删索引)
    params: dict = field(default_factory=dict)  # 缺陷参数(中心/半径/强度等)
    n_affected: int = 0

    def save(self, path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @staticmethod
    def load(path) -> "DefectRecord":
        return DefectRecord(**json.loads(Path(path).read_text()))


def _region_mask(model: GaussianModel, center, radius, z_max=None) -> np.ndarray:
    d2 = ((model.means[:, :2] - np.asarray(center[:2])) ** 2).sum(-1)
    m = d2 < radius ** 2
    if z_max is not None:
        m &= model.means[:, 2] < z_max
    return m


def inject_hole(model: GaussianModel, center=(0., 0., 0.), radius=1.0,
                z_max=0.3, seed=0) -> tuple[GaussianModel, DefectRecord]:
    """地面圆形区域删高斯成洞(z_max 限定只删近地面, 避免把柜体掏空)."""
    mask = _region_mask(model, center, radius, z_max)
    idx = np.nonzero(mask)[0]
    rec = DefectRecord("hole", seed, idx.tolist(),
                       params={"center": list(center), "radius": radius, "z_max": z_max},
                       n_affected=len(idx))
    return model.delete(idx), rec


def inject_floater(model: GaussianModel, center=(0., 0., 1.5), n=2000,
                   spread=0.4, scale=0.05, opacity=0.9, seed=0) -> tuple[GaussianModel, DefectRecord]:
    """在 center 附近注入 n 个漂浮噪声高斯."""
    rng = np.random.default_rng(seed)
    c = np.asarray(center, np.float32)
    pts = c + rng.normal(0, spread, (n, 3)).astype(np.float32)
    ghost = GaussianModel(
        means=pts,
        scales=np.full((n, 3), scale, np.float32),
        quats=np.tile(np.array([0., 0., 0., 1.], np.float32), (n, 1)),
        rgb=rng.uniform(0, 1, (n, 3)).astype(np.float32),
        opacity=np.full(n, opacity, np.float32),
        K=model.K, w2cs=model.w2cs, H=model.H, W=model.W,
    )
    rec = DefectRecord("floater", seed, list(range(model.n, model.n + n)),
                       params={"center": list(center), "n": n, "spread": spread,
                               "scale": scale, "opacity": opacity}, n_affected=n)
    return model.append(ghost), rec


def inject_color(model: GaussianModel, center=(0., 0., 1.0), radius=1.5,
                 tint=(1., 0., 0.), mix=0.7, seed=0) -> tuple[GaussianModel, DefectRecord]:
    """球域内颜色向 tint 混合污染."""
    mask = _region_mask(model, center, radius)
    idx = np.nonzero(mask)[0]
    rgb = model.rgb.copy()
    rgb[idx] = np.clip(rgb[idx] * (1 - mix) + np.asarray(tint, np.float32) * mix, 0, 1)
    rec = DefectRecord("color", seed, idx.tolist(),
                       params={"center": list(center), "radius": radius,
                               "tint": list(tint), "mix": mix}, n_affected=len(idx))
    return model.with_arrays(rgb=rgb), rec


def inject_pose_jitter(model: GaussianModel, sigma_deg=1.0, sigma_t=0.02,
                       seed=0) -> tuple[GaussianModel, DefectRecord]:
    """对所有 w2cs 加小幅旋转+平移扰动(检验冻结机位验收的敏感性).

    扰动是确定性的: 扰动量保存进 record.params["deltas"], repose 修复按它逆变换,
    不依赖任何原始备份 —— 闭环可独立复现.
    """
    if model.w2cs is None or len(model.w2cs) == 0:
        raise ValueError("模型无 w2cs, 无法注入位姿扰动")
    rng = np.random.default_rng(seed)
    w2cs = model.w2cs.copy()
    n = len(w2cs)
    ang = np.deg2rad(rng.normal(0, sigma_deg, n)).astype(np.float32)
    axis = rng.normal(0, 1, (n, 3)).astype(np.float32)
    axis /= np.linalg.norm(axis, axis=1, keepdims=True)
    # Rodrigues
    K_ = np.zeros((n, 3, 3), np.float32)
    K_[:, 0, 1] = -axis[:, 2]; K_[:, 0, 2] = axis[:, 1]
    K_[:, 1, 0] = axis[:, 2];  K_[:, 1, 2] = -axis[:, 0]
    K_[:, 2, 0] = -axis[:, 1]; K_[:, 2, 1] = axis[:, 0]
    ca, sa = np.cos(ang)[:, None, None], np.sin(ang)[:, None, None]
    dR = ca * np.eye(3)[None] + sa * K_ + (1 - ca) * (axis[:, :, None] @ axis[:, None, :])
    dt = rng.normal(0, sigma_t, (n, 3)).astype(np.float32)
    w2cs[:, :3, :3] = dR @ w2cs[:, :3, :3]
    w2cs[:, :3, 3] += dt
    rec = DefectRecord("pose_jitter", seed, list(range(n)),
                       params={"sigma_deg": sigma_deg, "sigma_t": sigma_t,
                               "deltas": {"ang": ang.tolist(), "axis": axis.tolist(),
                                          "dt": dt.tolist()}},
                       n_affected=n)
    return model.with_arrays(w2cs=w2cs), rec


INJECTORS = {"hole": inject_hole, "floater": inject_floater,
             "color": inject_color, "pose_jitter": inject_pose_jitter}


def inject(model: GaussianModel, defect_type: str, seed=0, **params):
    if defect_type not in INJECTORS:
        raise ValueError(f"未知缺陷类型 {defect_type}, 可选 {DEFECT_TYPES}")
    return INJECTORS[defect_type](model, seed=seed, **params)
