# 历史报告与源码

这里保存按版本解释的证据入口。当前用法以[文档导航](README.md)为准；旧图、实验成绩和草案不自动成为当前 pipeline 能力。

| 材料 | 入口与用途 |
|---|---|
| v0.7 中文白皮书 | [报告源码与重建](report/README.md) · [已发布 PDF／图册](https://github.com/miracle-techlink/video-to-simready/releases/tag/technical-report-2026-11-13-v5) |
| v0.5–v0.7 运行证据 | [包装](report/packaging-validation.json) · [已有场景](report/one-command-validation.json) · [新视频](report/new-video-validation.json) |
| 工程审查 | [audits](audits)：按日期保存问题、修复与当时验证范围 |
| 实验与测量 | [benchmarks](benchmarks)：杯任务、物性响应、计时及已暂停性能实验 |
| 研究算法 | [legacy](../legacy/README.md)：保留数值回归与后续适配所需核心 |
| 退役脚本／旧设计 | [精简前固定源码](https://github.com/miracle-techlink/video-to-simready/tree/6574907ded8aa8dac34de90d988303aede07d8b3)：旧搬杯辅助、历史场景执行层、完整研究草案和旧图 |

需要复现旧工具时使用其固定提交及当时环境、数据和命令，不将它们与当前 worker 混跑。历史审计文件和原始测量记录保留原文；新结果写入新运行或新的版本记录。
