"""rsi_3dgs 命令行.

用法:
  python -m rsi_3dgs.run run --model ../warehouse_3dgs/3dgs_fused/model.npz \
      --defect floater --out runs/floater_001 [--param n=2000] [--views ../warehouse_3dgs/views_pc]

  python -m rsi_3dgs.run run-all --model ... --out runs/ [--views ...]   # 三类缺陷串行

子命令(单步调试):
  inject / detect / repair / accept  —— 每步独立落盘, 可单独重跑
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .accept import GateConfig, evaluate_gate, pick_eval_indices
from .defects import INJECTORS, DefectRecord
from .detect import DetectionReport
from .loop import _detect_for, _repair_for, run_cycle
from .model_io import load_model, save_model
from .repair import (
    RepairResult,
    color_blend,
    delete_indices,
    inpaint_from_views,
    repose,
)


def _parse_params(items):
    """--param k=v 列表 → dict, v 尝试 json 解析."""
    out = {}
    for it in items or []:
        k, _, v = it.partition("=")
        try:
            out[k] = json.loads(v)
        except json.JSONDecodeError:
            out[k] = v
    return out


def cmd_run(a):
    m = run_cycle(
        a.model,
        a.defect,
        a.out,
        defect_params=_parse_params(a.param),
        seed=a.seed,
        views_dir=a.views,
        gate=GateConfig(min_recovery=a.min_recovery, eval_views=a.eval_views),
        cost_db=a.cost_db,
    )
    print(json.dumps(m, indent=2, ensure_ascii=False))
    return 0 if m["gate"]["accepted"] else 2


def cmd_run_all(a):
    results = {}
    for defect in ("hole", "floater", "color"):
        out = Path(a.out) / defect
        results[defect] = run_cycle(
            a.model,
            defect,
            out,
            defect_params=_parse_params(a.param),
            seed=a.seed,
            views_dir=a.views,
            cost_db=a.cost_db,
        )
    summary = {
        k: {
            "accepted": v["gate"]["accepted"],
            "mean_recovery": v["gate"]["mean_recovery"],
            "mean_gain_db": v["gate"]["mean_gain_db"],
        }
        for k, v in results.items()
    }
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    (Path(a.out) / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False)
    )
    return 0 if all(v["accepted"] for v in summary.values()) else 2


def cmd_inject(a):
    model = load_model(a.model)
    defected, rec = INJECTORS[a.defect](model, seed=a.seed, **_parse_params(a.param))
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    save_model(defected, out / "defected.npz")
    rec.save(out / "defect_record.json")
    print(json.dumps({"n_affected": rec.n_affected}, indent=2))


def cmd_detect(a):
    model = load_model(a.model)
    rec = DefectRecord.load(a.record)
    rep = _detect_for(rec.defect_type, model, rec)
    rep.save(a.out)
    print(json.dumps({"detector": rep.detector, "n": len(rep.indices)}, indent=2))


def cmd_repair(a):
    model = load_model(a.model)
    rec = DefectRecord.load(a.record)
    rep = DetectionReport.load(a.detection)
    res = _repair_for(rec.defect_type, model, rep, rec, views_dir=a.views)
    save_model(res.model, a.out)
    print(json.dumps(res.summary(), indent=2))


def cmd_accept(a):
    clean = load_model(a.clean)
    defected = load_model(a.defected)
    repaired = load_model(a.repaired)
    gate = GateConfig(min_recovery=a.min_recovery, eval_views=a.eval_views)
    idx = pick_eval_indices(len(clean.w2cs), gate.eval_views, gate.seed)
    g = evaluate_gate(clean, defected, repaired, gate, indices=idx)
    g.save(a.out)
    print(json.dumps({"accepted": g.accepted, "reason": g.reason}, indent=2))
    return 0 if g.accepted else 2


def build_parser():
    p = argparse.ArgumentParser(
        prog="rsi_3dgs",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    def base(sp, model=True):
        if model:
            sp.add_argument("--model", required=True)
        sp.add_argument("--seed", type=int, default=0)

    sp = sub.add_parser("run", help="单缺陷完整闭环")
    base(sp)
    sp.add_argument("--defect", required=True, choices=list(INJECTORS))
    sp.add_argument("--out", required=True)
    sp.add_argument("--views")
    sp.add_argument("--param", action="append")
    sp.add_argument("--min-recovery", type=float, default=0.8)
    sp.add_argument("--eval-views", type=int, default=8)
    sp.add_argument("--cost-db")
    sp.set_defaults(fn=cmd_run)

    sp = sub.add_parser("run-all", help="hole/floater/color 三连")
    base(sp)
    sp.add_argument("--out", required=True)
    sp.add_argument("--views")
    sp.add_argument("--param", action="append")
    sp.add_argument("--cost-db")
    sp.set_defaults(fn=cmd_run_all)

    sp = sub.add_parser("inject", help="仅注入缺陷")
    base(sp)
    sp.add_argument("--defect", required=True, choices=list(INJECTORS))
    sp.add_argument("--out", required=True)
    sp.add_argument("--param", action="append")
    sp.set_defaults(fn=cmd_inject)

    sp = sub.add_parser("detect", help="仅检测")
    base(sp)
    sp.add_argument("--record", required=True)
    sp.add_argument("--out", required=True)
    sp.set_defaults(fn=cmd_detect)

    sp = sub.add_parser("repair", help="仅修复")
    base(sp)
    sp.add_argument("--record", required=True)
    sp.add_argument("--detection", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--views")
    sp.set_defaults(fn=cmd_repair)

    sp = sub.add_parser("accept", help="仅验收")
    sp.add_argument("--clean", required=True)
    sp.add_argument("--defected", required=True)
    sp.add_argument("--repaired", required=True)
    sp.add_argument("--out", required=True)
    sp.add_argument("--min-recovery", type=float, default=0.8)
    sp.add_argument("--eval-views", type=int, default=8)
    sp.set_defaults(fn=cmd_accept)
    return p


def main(argv=None):
    a = build_parser().parse_args(argv)
    return a.fn(a) or 0


if __name__ == "__main__":
    sys.exit(main())
