# 技术报告：逐模块原理、运行与证据

v0.7.0 报告共 **27 页、14 图、12 表、12 个编号公式**。按视频到交付拆成十个模块，每个模块解释输入输出、原理、关键参数、图示、代码位置和验证范围；排版参考提供的第一视角交互预测报告，使用连续单栏正文、编号公式、可点击目录与交叉引用。

- [在线阅读](technical-report.md) · [模块覆盖核查表](module-coverage.md)
- [本版发布页](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v3)
- [技术报告 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v3/simready-technical-report-zh.pdf)
- [九张架构与方法图 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v3/simready-architecture-figures.pdf)
- [可编辑图与生成源码 ZIP](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v3/simready-architecture-figures.zip)
- [新视频运行说明](../../examples/new_video/README.md) · [发布文案](release-notes.md)

## 十个模块

1. 输入视频、抽帧与来源登记。
2. 三维重建、任务凭据与高斯表示。
3. 坐标配准、尺度与地面估计。
4. 独立资产、碰撞代理与 USD 组合。
5. 物理交互：关节、接触与状态。
6. 标准观测、指标与缺陷定位。
7. 修复决策、执行与 RSI 配方复用。
8. 机器人运行时、感知与设备端口。
9. 独立验收：结构、候选质量与任务。
10. 原生渲染、轨迹回放与交付。

总图区分三条实际路径：新视频的场景构建与巡览、已准备场景的机器人任务、开发阶段的修复闭环。十模块目录不表示默认新视频入口已经自动调用全部模块。

## 图示与真实验证

九张矢量图包括总体方法、新视频执行链、修复闭环、机器人控制，以及输入/高斯、配准/几何、按钮接触、观测/验收、渲染/证据五张方法图。SVG 保留可编辑文字，PDF 保留矢量。其余五图来自实际视频抽帧或测量曲线；原始画面没有生成式修饰。

v0.7 使用历史原片与操作者声明关联的 PLY 重新构建：CPU 命令约 7.951 秒，完整恢复约 0.246 秒；Docker 的 12 帧、640×360 巡览约 231.874 秒。结构和编码检查通过，原高斯仍明显模糊、缺面，未通过视觉保真度、独立尺度或机器人任务验收。没有提交新云重建，不能据此报告完整重建 E2E。代码身份、首轮失败与成功重跑均保留在 [v0.7 验证清单](new-video-validation.json)。

历史证据独立保留：[v0.6 已有场景入口](one-command-validation.json)、[v0.5 包装核验](packaging-validation.json)、[早期任务与评测](evidence.json)。旧报告可从 [v0.6 Release](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v2) 和 [首版 Release](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13) 下载。

## 重建文档

需要 XeLaTeX、ctex 和相应中文字体。已有图无需 GPU 或私有数据：

```bash
python scripts/build_technical_report.py
```

重新生成全部九张方法图：

```bash
python -m pip install -e '.[report]'
python scripts/build_architecture_figures.py
python scripts/build_new_video_figure.py
python scripts/build_method_figures.py
python scripts/build_technical_report.py
```

报告输出为 `dist/simready-technical-report-zh.pdf`。第三方机器人资产、权重、原视频与 Isaac SDK 不包含在源码包中。
