"""rsi_3dgs — 3DGS 场景 RSI(重建-自检-修复)标准化管线.

损坏注入 → 离群检测 → 局部修复 → 冻结机位验收 → skill 沉淀.

设计原则:
- 所有阶段纯函数 + dataclass 状态, 可单测, 可复现(显式 seed)
- 模型/视图/缺陷/修复/指标全部落盘可审计(sha256 digest)
- 修复接受门控: 冻结机位渲染前后对比, 不达标自动回滚
- 与 scene_bench 的 cost_guard / skill_store 协议对接
"""
__version__ = "1.0.0"

from .model_io import GaussianModel, load_model, save_model, model_digest  # noqa: F401
