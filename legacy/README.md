# 历史算法与活动工具

`simready` 运行时不导入本目录；保留源码随 sdist 提供，不进入 wheel。

| 目录 | 保留用途 |
|---|---|
| `gaussian_repair` | 高斯模型读写、缺陷检测和局部修复算法，有 CPU 数值回归 |
| `scene_evaluation` | 历史指标、候选比较、成本账本和配准算法；成绩不能混入新评测协议 |
| `warehouse_experiments` | gsplat 训练、点云转换和 Isaac 研究脚本；尚未接入规范 pipeline |
| `aholo_reconstruction` | 旧提交入口的薄转发；新任务用 `simready ingestion aholo` |
| `hackathon_submission` | 当前黑客松的浏览器提交工具，仅用于对应活动 |

旧渲染、修复执行循环、场景专用测量脚本和失效抽帧入口已从活动源码移除，完整源码保留在[固定历史提交](https://github.com/miracle-techlink/video-to-simready/tree/6574907ded8aa8dac34de90d988303aede07d8b3/legacy)。不在仓库内复制另一套归档代码。抽帧改用 `simready ingestion extract`；当前工作流见[规范 pipeline](../docs/pipeline.md)。

研究脚本可能依赖外部资产、模型、CUDA、gsplat、Isaac 或凭据。保留源码和 CPU 检查不代表已重新运行训练或 GPU 任务。
