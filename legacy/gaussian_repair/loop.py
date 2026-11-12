"""RSI 闭环: 损坏→检测→修复→验收→(回滚|沉淀).

run_cycle() 一次完整循环, 全部产物落盘可审计:

  out_dir/
    defected.npz / defect_record.json
    detection.json / repaired.npz / repair_summary.json
    gate.json (验收判定) / accepted.npz (回滚则为干净模型副本)
    frames/{clean,defected,repaired}_vXXX.png + compare.mp4
    metrics.json / skill_store.json
"""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Optional

import numpy as np

from .accept import (
    GateConfig,
    evaluate_gate,
    pick_defect_visible_indices,
    pick_eval_indices,
)
from .defects import INJECTORS, DefectRecord
from .detect import (
    DetectionReport,
    detect_color_outliers,
    detect_density_hole,
    detect_isolated,
    detect_opacity_outliers,
    detect_scale_outliers,
)
from .model_io import GaussianModel, load_model, model_digest, save_model, sha256
from .render import render_view, side_by_side, write_video
from .repair import (
    RECIPES,
    RepairResult,
    color_blend,
    delete_indices,
    inpaint_from_views,
    repose,
)


def _cost_guard_reserve(db_path: Optional[str], request: dict, max_submissions=64):
    """成本记账: 有 db 才记; 记账失败不阻塞主流程(离线管线)."""
    if not db_path:
        return None
    try:
        from legacy.scene_evaluation.cost_guard import CostGuard

        return CostGuard(db_path).reserve(request, max_submissions=max_submissions)
    except Exception as e:  # noqa: BLE001
        return {"action": "LEDGER_UNAVAILABLE", "error": str(e)}


def _detect_for(
    defect_type: str, model: GaussianModel, record: DefectRecord
) -> DetectionReport:
    """按缺陷类型选检测器组合(也可被显式调用绕过)."""
    p = record.params
    if defect_type == "floater":
        iso = detect_isolated(model)
        sc = detect_scale_outliers(model)
        op = detect_opacity_outliers(model, hi=0.85)
        # 并集, floater 注入点同时满足孤立/高 opacity
        from .detect import union

        return union(iso, sc, op)
    if defect_type == "color":
        return detect_color_outliers(model)
    if defect_type == "hole":
        return detect_density_hole(
            model, center=p["center"], radius=p["radius"], z_max=p.get("z_max")
        )
    return DetectionReport("none", [])


def _repair_for(
    defect_type: str,
    model: GaussianModel,
    report: DetectionReport,
    record: DefectRecord,
    views_dir=None,
) -> RepairResult:
    p = record.params
    if defect_type == "floater":
        return delete_indices(model, report)
    if defect_type == "color":
        return color_blend(model, report)
    if defect_type == "hole":
        return inpaint_from_views(
            model,
            center=p["center"],
            radius=p["radius"],
            z_max=p.get("z_max", 0.3),
            views_dir=views_dir,
        )
    if defect_type == "pose_jitter":
        return repose(model, record)
    raise ValueError(f"无配方: {defect_type}")


def default_defect_params(defect_type: str, clean: GaussianModel) -> dict:
    """按缺陷类型生成"可见且可修"的默认参数.

    位置对齐相机位姿均值; 强度足以在冻结机位产生可测 PSNR 下降.
    """
    if clean.w2cs is None or not len(clean.w2cs):
        return {}
    R = clean.w2cs[:, :3, :3]
    t = clean.w2cs[:, :3, 3]
    cam_pos = -np.einsum("nij,nj->ni", R.transpose(0, 2, 1), t)
    c = cam_pos.mean(0)
    # 相机前方 3m 处(沿平均视线)
    fwd = R[:, 2, :].mean(0)  # w2c 的 z 轴反方向 = 视线
    fwd = fwd / (np.linalg.norm(fwd) + 1e-9)
    p = c + fwd * 3.0
    base = {"center": p.tolist()}
    if defect_type == "floater":
        return {**base, "n": 3000, "spread": 0.8, "scale": 0.3, "opacity": 0.95}
    if defect_type == "color":
        return {**base, "radius": 2.0, "tint": [1.0, 0.0, 0.0], "mix": 0.8}
    if defect_type == "hole":
        # hole 打地面: 用相机均值正下方
        g = c.copy()
        g[2] = 0.0
        return {"center": g.tolist(), "radius": 2.0, "z_max": 0.3}
    return base


def run_cycle(
    model_path,
    defect_type: str,
    out_dir,
    defect_params: Optional[dict] = None,
    seed: int = 0,
    views_dir=None,
    gate: Optional[GateConfig] = None,
    cost_db: Optional[str] = None,
    device="cuda",
) -> dict:
    """完整闭环. 返回汇总 dict(同时写 metrics.json)."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    gate = gate or GateConfig()
    defect_params = defect_params or {}

    clean = load_model(model_path)
    clean_digest = model_digest(clean)

    # 缺陷参数默认值: 位置/强度自动对齐相机视场(见 default_defect_params),
    # 否则缺陷可能加在视野外或强度不可见, 冻结机位验收失真(伪 100 PSNR).
    defect_params = dict(defect_params)
    for k, v in default_defect_params(defect_type, clean).items():
        defect_params.setdefault(k, v)

    # 1 损坏
    inject = INJECTORS[defect_type]
    defected, record = inject(clean, seed=seed, **defect_params)
    defected_path = save_model(defected, out / "defected.npz")
    record.save(out / "defect_record.json")
    _cost_guard_reserve(
        cost_db,
        {
            "provider": "rsi_3dgs",
            "endpoint": f"inject/{defect_type}",
            "provider_version": "1.0.0",
            "input_sha256": [clean_digest],
            "parameters": {"seed": seed, **defect_params},
        },
    )

    # 2 检测
    report = _detect_for(defect_type, defected, record)
    report.save(out / "detection.json")

    # 3 修复
    result = _repair_for(defect_type, defected, report, record, views_dir=views_dir)
    repaired = result.model
    save_model(repaired, out / "repaired.npz")

    # 4 验收(冻结机位): 优先选能看到缺陷中心的机位
    center = defect_params.get("center")
    if center is not None:
        idx = pick_defect_visible_indices(clean.w2cs, center, gate.eval_views)
    else:
        idx = pick_eval_indices(len(clean.w2cs), gate.eval_views, gate.seed)
    gate_result = evaluate_gate(
        clean, defected, repaired, gate, indices=idx, device=device
    )

    # 缺陷可见性自检: 缺陷态对干净态无影响 → 验收失真, 标记并强制回滚
    defected_psnr = [v.psnr_defected for v in gate_result.views]
    ineffective = all(p > 60 for p in defected_psnr)  # 所有机位都近乎无损
    if ineffective:
        gate_result.accepted = False
        gate_result.reason = (
            f"INEFFECTIVE_DEFECT: 缺陷在冻结机位不可见(min psnr={min(defected_psnr):.1f}), 验收无意义; "
            + gate_result.reason
        )

    gate_result.save(out / "gate.json")

    # 5 回滚 or 沉淀
    final = repaired if gate_result.accepted else clean
    save_model(final, out / "accepted.npz")

    # 对比帧 + 视频
    frames = []
    for i in idx[:6]:  # 视频最多 6 机位
        w2c = clean.w2cs[i]
        a = render_view(defected, w2c, device=device)
        b = render_view(final, w2c, device=device)
        frames.append(
            side_by_side(
                a,
                b,
                f"defected v{i}",
                f"{'repaired' if gate_result.accepted else 'rolledback'} v{i}",
            )
        )
    if frames:
        write_video(frames, out / "compare.mp4", fps=2)

    # 从 gate 视图指标换算 PSNR/SSIM 均值, 便于报告
    mean_psnr_defected = float(np.mean([v.psnr_defected for v in gate_result.views]))
    mean_psnr_repaired = float(np.mean([v.psnr_repaired for v in gate_result.views]))
    mean_ssim_repaired = float(np.mean([v.ssim_repaired for v in gate_result.views]))

    # skill 沉淀
    skill = {
        "id": f"rsi_{defect_type}_v1",
        "defect_type": defect_type,
        "recipe": result.recipe,
        "detector": report.detector,
        "accepted": gate_result.accepted,
        "mean_recovery": gate_result.mean_recovery,
        "mean_gain_db": gate_result.mean_gain_db,
        "mean_psnr": {
            "defected": mean_psnr_defected,
            "final": mean_psnr_repaired if gate_result.accepted else 100.0,
        },
        "mean_ssim_final": mean_ssim_repaired if gate_result.accepted else 1.0,
        "scope": f"model_digest:{clean_digest[:16]}",
        "evidence": "gate.json",
        "next_action": (
            "复用配方"
            if gate_result.accepted
            else "回滚并补证据或更换配方，保持验收门限"
        ),
    }
    (out / "skill_store.json").write_text(
        json.dumps([skill], indent=2, ensure_ascii=False)
    )

    metrics = {
        "defect_type": defect_type,
        "seed": seed,
        "model": str(model_path),
        "clean_digest": clean_digest,
        "defected_sha256": defected_path,
        "ineffective_defect": ineffective,
        "n_affected": record.n_affected,
        "n_detected": len(report.indices),
        "repair": result.summary(),
        "gate": {
            "accepted": gate_result.accepted,
            "mean_recovery": gate_result.mean_recovery,
            "mean_gain_db": gate_result.mean_gain_db,
            "worst_residual_db": gate_result.worst_residual_db,
            "reason": gate_result.reason,
        },
        "psnr": {
            "defected": mean_psnr_defected,
            "final": mean_psnr_repaired if gate_result.accepted else 100.0,
        },
        "ssim_final": mean_ssim_repaired if gate_result.accepted else 1.0,
        "rolled_back": not gate_result.accepted,
    }
    (out / "metrics.json").write_text(json.dumps(metrics, indent=2, ensure_ascii=False))
    return metrics
