"""离群检测: 从受损模型定位可疑高斯索引.

检测器全部为纯函数 model -> DetectionReport, 可组合(union/intersect).

策略(对应缺陷类型):
- opacity 离群: 修复 floater(高 opacity + 大 scale + 孤立)
- scale 离群:   过大/过小高斯
- 密度离群:     局部近邻数过低(孤立点, floater 特征) / 区域空洞(缺失)
- 颜色离群:     与局部邻域中位数色差过大(颜色污染)
- 残差引导:     有参考图时, 重投影残差热区反投回高斯(最强信号, 需渲染)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List

import numpy as np

from .model_io import GaussianModel


@dataclass
class DetectionReport:
    detector: str
    indices: List[int]
    scores: Dict[str, float] = field(default_factory=dict)  # 统计摘要
    params: dict = field(default_factory=dict)

    def save(self, path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(asdict(self), indent=2, ensure_ascii=False))

    @staticmethod
    def load(path) -> "DetectionReport":
        return DetectionReport(**json.loads(Path(path).read_text()))


def _knn_counts(
    pts: np.ndarray, k: int, radius: float, chunk: int = 4096
) -> np.ndarray:
    """Exact radius counts using a spatial tree, never an N-by-N matrix."""
    from scipy.spatial import cKDTree

    if not np.isfinite(radius) or radius <= 0:
        raise ValueError("radius must be positive")
    if not len(pts):
        return np.zeros(0, dtype=np.int64)
    return (
        cKDTree(pts).query_ball_point(pts, np.nextafter(radius, 0), return_length=True)
        - 1
    )


def detect_opacity_outliers(model: GaussianModel, lo=0.02, hi=0.98) -> DetectionReport:
    op = model.opacity
    idx = np.nonzero((op < lo) | (op > hi))[0]
    return DetectionReport(
        "opacity_outliers",
        idx.tolist(),
        scores={"lo": lo, "hi": hi, "n": int(len(idx))},
    )


def detect_scale_outliers(model: GaussianModel, z_thresh=4.0) -> DetectionReport:
    """log(scale) 全局长尾."""
    if not model.n:
        return DetectionReport("scale_outliers", [])
    s = np.log(np.maximum(model.scales, 1e-12))
    mu, sd = s.mean(0), s.std(0) + 1e-12
    z = np.abs((s - mu) / sd).max(-1)
    idx = np.nonzero(z > z_thresh)[0]
    return DetectionReport(
        "scale_outliers",
        idx.tolist(),
        scores={"z_thresh": z_thresh, "z_max": float(z.max())},
    )


def detect_isolated(
    model: GaussianModel, k=8, radius=0.15, max_n=100_000
) -> DetectionReport:
    """kNN 内近邻数过低 → 孤立点(floater 典型特征). 大模型抽样子采样估计."""
    pts = model.means
    if len(pts) > max_n:  # 全量 O(N²) 不可行时退化为随机子样本投票
        rng = np.random.default_rng(0)
        sub = rng.choice(len(pts), max_n, replace=False)
        counts = _knn_counts(pts[sub], k, radius)
        local_idx = sub[counts < k]
    else:
        counts = _knn_counts(pts, k, radius)
        local_idx = np.nonzero(counts < k)[0]
    return DetectionReport(
        "isolated",
        local_idx.tolist(),
        scores={"k": k, "radius": radius, "n": int(len(local_idx))},
    )


def detect_color_outliers(
    model: GaussianModel, k=12, delta=0.35, max_n=200_000
) -> DetectionReport:
    """与 kNN 中位色色差 > delta 的点(颜色污染)."""
    pts, rgb = model.means, model.rgb
    if not model.n:
        return DetectionReport("color_outliers", [])
    n = len(pts)
    if n > max_n:
        rng = np.random.default_rng(0)
        cand = rng.choice(n, max_n, replace=False)
    else:
        cand = np.arange(n)
    from scipy.spatial import cKDTree

    if type(k) is not int or k < 1:
        raise ValueError("k must be positive")
    bad = []
    tree = cKDTree(pts)
    neighbors = min(k + 1, n)
    for start in range(0, len(cand), 16384):
        ci = cand[start : start + 16384]
        _, idx = tree.query(pts[ci], k=neighbors)
        idx = np.asarray(idx).reshape(len(ci), neighbors)
        med = np.median(rgb[idx], axis=1)
        distance = np.abs(rgb[ci] - med).max(-1)
        bad.extend(ci[distance > delta].tolist())
    return DetectionReport(
        "color_outliers", bad, scores={"k": k, "delta": delta, "n": int(len(bad))}
    )


def detect_density_hole(
    model: GaussianModel, center, radius, z_max=0.3, ref_density=10.0, voxel=0.1
) -> DetectionReport:
    """区域空洞: 指定区域内体素占用率显著低于场景基准 → 判为缺失."""
    c = np.asarray(center[:2])
    m = ((model.means[:, :2] - c) ** 2).sum(-1) < radius**2
    if z_max is not None:
        m &= model.means[:, 2] < z_max
    inside = model.means[m]
    area = np.pi * radius**2
    density = len(inside) / max(area, 1e-6) / max(voxel, 1e-6)
    is_hole = density < ref_density * 0.3
    return DetectionReport(
        "density_hole",
        [],
        scores={
            "density": float(density),
            "ref": ref_density,
            "is_hole": bool(is_hole),
        },
        params={"center": list(center), "radius": radius, "z_max": z_max},
    )


def union(*reports: DetectionReport) -> DetectionReport:
    idx = sorted(set().union(*[set(r.indices) for r in reports]))
    return DetectionReport(
        "union:" + "+".join(r.detector for r in reports), idx, scores={"n": len(idx)}
    )


def intersect(*reports: DetectionReport) -> DetectionReport:
    sets = [set(r.indices) for r in reports]
    idx = sorted(set.intersection(*sets)) if sets else []
    return DetectionReport(
        "intersect:" + "+".join(r.detector for r in reports),
        idx,
        scores={"n": len(idx)},
    )
