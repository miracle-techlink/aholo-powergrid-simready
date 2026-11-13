# 从场景视频到机器人交互演示：技术白皮书

本版以“视频如何成为可交互、可验证的机器人演示”为主线，按技术白皮书重写。所有报告页面为 A4 竖版，架构图、方法图与测量曲线使用中文说明，保留必要公式、标准缩写和模型名称。本版共 **26 页、16 图、10 表、12 个编号公式、8 条参考文献**。

- [在线阅读](technical-report.md) · [模块覆盖核查表](module-coverage.md)
- [本版发布页](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v4)
- [技术报告 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v4/simready-technical-report-zh.pdf)
- [九张架构与方法图 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v4/simready-architecture-figures.pdf)
- [可编辑图与生成源码 ZIP](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v4/simready-architecture-figures.zip)
- [新视频运行说明](../../examples/new_video/README.md) · [发布文案](release-notes.md)

## 阅读主线

正文先定义从视频走向可交互仿真的信息缺口，再解释观测与重建、来源约束的资产修复、物理交互、机器人感知与控制、闭环质量验收及原生演示交付。代码映射、命令和字段约定移入附录，工程索引另行保留。

[视频到 Demo 的流程总览](pipeline-overview.md)提供各阶段的输入、核心处理和输出。主总图区分历史变电站任务级链路与当前通用新视频入口，明确已经贯通的部分和仍需准备的环节。

## 图示与真实验证

九张矢量图包括总体方法、新视频执行链、修复闭环、机器人控制，以及输入/高斯、配准/几何、按钮接触、观测/验收、渲染/证据五张方法图。SVG 保留可编辑文字，PDF 保留矢量。其余图来自实际视频抽帧或测量曲线；原始画面没有生成式修饰。

v0.7 使用历史原片与操作者声明关联的 PLY 重新构建：CPU 命令约 7.951 秒，完整恢复约 0.246 秒；Docker 的 12 帧、640×360 巡览约 231.874 秒。结构和编码检查通过，原高斯仍明显模糊、缺面，未通过视觉保真度、独立尺度或机器人任务验收。没有提交新云重建，不能据此报告完整重建 E2E。代码身份、首轮失败与成功重跑均保留在 [v0.7 验证清单](new-video-validation.json)。

历史证据独立保留：[v0.6 已有场景入口](one-command-validation.json)、[v0.5 包装核验](packaging-validation.json)、[早期任务与评测](evidence.json)。旧报告可从 [v0.6 Release](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v2) 和 [首版 Release](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13) 下载。

## 重建文档

需要 XeLaTeX、ctex 和相应中文字体。已有图无需 GPU 或私有数据：

```bash
python scripts/build_technical_report.py
```

重新生成全部九张架构/方法图及测量曲线（需要安装 Noto Sans CJK SC 中文字体）：

```bash
python -m pip install -e '.[report]'
python scripts/build_architecture_figures.py
python scripts/build_new_video_figure.py
python scripts/build_method_figures.py
python scripts/build_report_figures.py --plots-only
python scripts/build_technical_report.py
```

报告输出为 `dist/simready-technical-report-zh.pdf`。第三方机器人资产、权重、原视频与 Isaac SDK 不包含在源码包中。

本版只修订白皮书组织、图示语言与排版，保留 v0.7 运行代码和原实验数据。[此前逐模块版](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v3)仍可下载。
