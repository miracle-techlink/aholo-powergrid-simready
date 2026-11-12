"""修复验收门控: 冻结机位前后对比 → 保留/回滚.

指标(每冻结机位): psnr/ssim/rgb_mae; 汇总均值 + 最差机位.
验收规则(可配 GateConfig):
- 修复后相对受损态 PSNR 恢复率 >= min_recovery
- 不允许任何机位 PSNR 回退超过 max_regress_db(防"修一处坏一处")
恢复率定义: (psnr_repaired - psnr_defected) / (psnr_clean - psnr_defected)
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import List, Optional, Sequence

import numpy as np

from .model_io import GaussianModel
from .render import psnr, render_view, ssim


@dataclass
class GateConfig:
    min_recovery: float = 0.8  # 恢复率门限(作战文档验收线)
    max_regress_db: float = 0.5  # 单机位允许的最大残余回退(修复后仍差于干净态的量)
    min_gain_db: float = 0.5  # 修复相对受损态必须拿到的最小 PSNR 增益
    eval_views: int = 8  # 冻结机位抽样数
    seed: int = 0


@dataclass
class ViewMetrics:
    view_idx: int
    psnr_clean: float
    psnr_defected: float
    psnr_repaired: float
    ssim_repaired: float
    recovery: float


@dataclass
class GateResult:
    accepted: bool
    mean_recovery: float
    min_recovery: float
    worst_residual_db: float
    mean_gain_db: float
    views: List[ViewMetrics] = field(default_factory=list)
    reason: str = ""

    def save(self, path) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        d = asdict(self)
        Path(path).write_text(json.dumps(d, indent=2, ensure_ascii=False))


def pick_eval_indices(n_views: int, k: int, seed=0) -> List[int]:
    if type(n_views) is not int or n_views < 1 or type(k) is not int or k < 1:
        raise ValueError("Positive view count and sample count required")
    rng = np.random.default_rng(seed)
    k = min(k, n_views)
    return sorted(rng.choice(n_views, k, replace=False).tolist())


def pick_defect_visible_indices(
    w2cs: np.ndarray, center, k: int, min_cos=0.3
) -> List[int]:
    """选能看到缺陷中心的机位: 视线与(相机→缺陷)方向夹角余弦 >= min_cos.

    保证验收机位对缺陷敏感; 否则多数机位渲染不出差异, 恢复率被稀释.
    """
    center = np.asarray(center, np.float64)
    scored = []
    for i, w2c in enumerate(w2cs):
        R, t = w2c[:3, :3], w2c[:3, 3]
        pos = -R.T @ t
        fwd = R[2]  # OpenCV camera looks along positive Z.
        d = center - pos
        dn = d / (np.linalg.norm(d) + 1e-9)
        scored.append((float(dn @ fwd), i))
    scored.sort(reverse=True)
    picked = [i for cos, i in scored if cos >= min_cos][:k]
    if len(picked) < k:  # 可见机位不足时补余量最高的
        picked += [i for _, i in scored if i not in picked][: k - len(picked)]
    return sorted(picked)


def evaluate_gate(
    clean: GaussianModel,
    defected: GaussianModel,
    repaired: GaussianModel,
    cfg: GateConfig,
    indices: Optional[Sequence[int]] = None,
    device="cuda",
) -> GateResult:
    """在冻结机位上对比 clean/defected/repaired 三态渲染, 判定是否接受修复."""
    w2cs = clean.w2cs
    if w2cs is None or len(w2cs) == 0:
        raise ValueError("干净模型无冻结机位 w2cs")
    idx = (
        list(indices)
        if indices is not None
        else pick_eval_indices(len(w2cs), cfg.eval_views, cfg.seed)
    )
    if (
        not idx
        or len(set(idx)) != len(idx)
        or any(type(i) is not int or not 0 <= i < len(w2cs) for i in idx)
    ):
        raise ValueError("Evaluation needs unique valid view indices")
    views: List[ViewMetrics] = []
    for i in idx:
        w2c = w2cs[i]
        ref = render_view(clean, w2c, device=device)
        im_d = render_view(defected, w2c, device=device)
        im_r = render_view(repaired, w2c, device=device)
        p_c, p_d, p_r = 100.0, psnr(ref, im_d), psnr(ref, im_r)
        denom = max(p_c - p_d, 1e-6)
        views.append(
            ViewMetrics(
                i,
                p_c,
                p_d,
                p_r,
                ssim(ref, im_r),
                float(np.clip((p_r - p_d) / denom, -1, 2)),
            )
        )
    rec = [v.recovery for v in views]
    mean_rec = float(np.mean(rec))
    min_rec = float(np.min(rec))
    # 残余回退: 修复后仍差于干净态的量(越小越好)
    worst_residual = float(max(v.psnr_clean - v.psnr_repaired for v in views))
    mean_gain = float(np.mean([v.psnr_repaired - v.psnr_defected for v in views]))
    accepted = (
        mean_rec >= cfg.min_recovery
        and worst_residual <= cfg.max_regress_db + 1e-6
        and mean_gain >= cfg.min_gain_db
    )
    reason = (
        f"mean_recovery={mean_rec:.3f}(门限{cfg.min_recovery}) "
        f"mean_gain={mean_gain:+.2f}dB(门限{cfg.min_gain_db}) "
        f"worst_residual={worst_residual:.2f}dB(门限{cfg.max_regress_db})"
    )
    return GateResult(
        accepted, mean_rec, min_rec, worst_residual, mean_gain, views, reason
    )
