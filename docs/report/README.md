# 技术报告：方法、实现与复现

- [在线阅读修订版](technical-report.md)
- [修订版发布页与 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v2)
- [三张架构图独立 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v2/simready-architecture-figures.pdf)
- [架构图与生成源码 ZIP](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v2/simready-architecture-figures.zip)
- [首版报告](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13)
- [历史结果与哈希](evidence.json)
- [v0.5 包装核验](packaging-validation.json)
- [v0.6 一键运行实测](one-command-validation.json)
- [发布文案](release-notes.md)

v0.6.0 修订版共 18 页、8 图，按“问题定义 → 输入输出与资产合同 → 分阶段方法 → Agent 决策与独立验收 → 机器人运行时 → 复现与限制”重排。M1–M6 每阶段说明输入、处理、输出和证据，并标明可执行、离线准备与研究支路。

三张核心图 `architecture_overview`、`repair_closed_loop`、`robot_control_runtime` 使用原生矢量表示，并在 PDF 中各占横向专页。场景画面来自已有原视频或原生渲染视频的抽帧；接触曲线来自公开 `contact_trace.csv`。图像缩放用于排版，不宣称原分辨率或新的实验结果。

本次一键变电站流程通过 20/20 审计并新录 713 帧 1080p 视频，总 wall 1095.164 秒；星巴克只重跑 640×360、8 帧短预览。耗时包含冷 shader，不含新重建和此前资产准备，作为功能验证记录。报告单列代码身份、后续两处失败分支保护的 CPU 验证，以及与历史结果的区别。

重新编译需要 XeLaTeX / ctex 字体；已有矢量图无需 GPU 或私有数据：

```bash
python scripts/build_technical_report.py
```

重建三张核心架构图：

```bash
python -m pip install -e '.[report]'
python scripts/build_architecture_figures.py
python scripts/build_technical_report.py
```

图形输出包括可编辑 SVG、矢量 PDF 和 PNG。报告 PDF 输出为 `dist/simready-technical-report-zh.pdf`；新 tag 为 `technical-report-2026-11-13-v2`，首版 Release 保留。第三方机器人资产、权重和原视频不包含在源码包中。
