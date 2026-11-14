# 历史场景评测算法

保留五个有 CPU 回归覆盖的模块：

| 模块 | 用途 |
|---|---|
| `bench.py` | 检查候选、相机与渲染证据，计算同机位条件图像指标 |
| `cost_guard.py` | SQLite 请求去重和提交额度账本；未接入正式 Aholo 提交入口 |
| `evaluate_iteration.py` | 核查前后候选及其评测绑定 |
| `rsi_loop.py` | 历史候选接受与回退决策 |
| `align_reconstruction.py` | 稳健配准与残差计算 |

历史渲染、参考准备、相机搜索和特定场景修复脚本见[固定提交源码](https://github.com/miracle-techlink/video-to-simready/tree/6574907ded8aa8dac34de90d988303aede07d8b3/legacy/scene_evaluation)。旧脚本依赖外部实验资产，不再作为当前运行命令。

这些指标不是官方 V2WScore，也不能证明独立几何真值或整店保真度。原云重建使用过完整视频，后续划出的片段只能称优化阶段留出数据。新任务使用[统一评测](../../docs/evaluation.md)；历史阈值和成绩不自动迁入新协议。
