# 历史技术报告（v0.7）

本目录保留 v0.7 的方法说明、原始实验结果与证据，不代表当前代码架构或最新验证状态。当前使用方式见[架构说明](../architecture.md)和[规范 pipeline](../pipeline.md)。

- [报告正文](technical-report.md) · [模块覆盖记录](module-coverage.md)
- [已发布报告与图册](https://github.com/miracle-techlink/video-to-simready/releases/tag/technical-report-2026-11-13-v5) · [报告 PDF](https://github.com/miracle-techlink/video-to-simready/releases/download/technical-report-2026-11-13-v5/simready-technical-report-zh.pdf)
- [清理前完整材料与生成源码](https://github.com/miracle-techlink/video-to-simready/tree/6574907)
- 历史证据：[v0.7 新视频](new-video-validation.json)、[v0.6 已有场景](one-command-validation.json)、[v0.5 包装](packaging-validation.json)、[早期任务](evidence.json)。

正文通过 `<!-- include: 文件名.md -->` 引用同目录分节，PDF 构建器展开它们。实验数据与真实截图保留原样；不再使用的旧图及重复 PNG 可从上述固定提交或 Release 追溯。

安装 XeLaTeX、ctex 和中文字体后，可用现有图重建，无需 GPU 或私有数据：

```bash
python scripts/build_technical_report.py
```

输出为 `dist/simready-technical-report-zh.pdf`，编译中间文件保存在 `dist/report-build/`；PDF 中的相对源码链接固定到清理前提交 `6574907`。

如需重画矢量图，先安装 `.[report]` 和 Noto Sans CJK SC 字体，再运行：

```bash
python scripts/build_architecture_figures.py
python scripts/build_new_video_figure.py
python scripts/build_method_figures.py
python scripts/build_software_figures.py
python scripts/build_asset_simready_figure.py
python scripts/build_report_figures.py
```

默认只生成 SVG/PDF；需要 PNG 时增加 `--export-png`，图片写入 `dist/report-figures/`。真实物性响应曲线及其[测量数据](../benchmarks/multiclass-physics-20261113/physical-response.csv)继续保留，不由示意图生成器覆盖。
