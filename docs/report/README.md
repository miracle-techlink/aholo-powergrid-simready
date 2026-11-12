# 公开技术报告

- [在线阅读](technical-report.md)
- [发布页与 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13)
- [结果与哈希](evidence.json)
- [封装核验](packaging-validation.json)
- [可直接使用的发布文案](release-notes.md)

报告为 15 页、11 幅图。图中照片来自真实视频抽帧，绘图来自公开的 `contact_trace.csv`、`evidence.json` 与 DOT 源文件。报告图为排版缩放，不宣称是原分辨率图像。第三方机器人资产和原始视频不包含在发布的源代码包中。

重新编译需要 XeLaTeX / ctex 字体。已有矢量图无需 GPU 或私有数据：

```bash
python scripts/build_technical_report.py
# 可选：重建示意图和曲线，需要 Graphviz 与 matplotlib。
python -m pip install -e '.[report]'
python scripts/build_report_figures.py
python scripts/build_technical_report.py
```

PDF 输出为 `dist/simready-technical-report-zh.pdf`。发布包中的 SHA256SUMS 用于校验下载文件。
