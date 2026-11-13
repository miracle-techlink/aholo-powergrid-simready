# 从场景视频到机器人交互演示：SimReady 技术白皮书

本白皮书面向由场景视频构建机器人交互仿真环境的技术问题，围绕“原片观测—三维重建—坐标与尺度对齐—来源约束的资产修复—物理与交互建模—机器人感知、导航与操作—独立验收—原生演示交付”展开。系统以外观、碰撞和交互三个运行层组织场景，并以标准观测、缺陷定位、受约束修复与独立复验构成质量闭环。文中说明关键方法的原理、信息流、假设与验证依据，结合变电站和星巴克案例呈现实际结果。

本次增订同时覆盖技术方法与软件实现：补充阶段交接、三维资产物理化、感知与运动规划原理，并新增“软件架构与模块构造”专章。十个代码领域逐一说明内部组件、输入输出、状态归属、真实调用链、失败处理及扩展接口；区分通用新视频构建、已有变电站物理任务、星巴克编排展示和独立修复框架。本版共 45 页、19 图、24 表、16 个编号公式、10 条编号参考文献。全部页面为中文 A4 竖版，架构与方法图提供可编辑矢量和生成源码。运行代码保持 v0.7.0，历史实验数据保持原身份。

发布链接：

- [技术白皮书发布页](https://github.com/miracle-techlink/video-to-simready/releases/tag/technical-report-2026-11-13-v5)
- [PDF 直接下载](https://github.com/miracle-techlink/video-to-simready/releases/download/technical-report-2026-11-13-v5/simready-technical-report-zh.pdf)
- [中文架构与方法图册](https://github.com/miracle-techlink/video-to-simready/releases/download/technical-report-2026-11-13-v5/simready-architecture-figures.pdf)
- [可编辑图与生成代码](https://github.com/miracle-techlink/video-to-simready/releases/download/technical-report-2026-11-13-v5/simready-architecture-figures.zip)
- [软件架构与代码模块构造](https://github.com/miracle-techlink/video-to-simready/blob/main/docs/software-architecture.md)
- [视频到 Demo 流程总览](https://github.com/miracle-techlink/video-to-simready/blob/main/docs/report/pipeline-overview.md)
- [GitHub 代码仓库](https://github.com/miracle-techlink/video-to-simready)
- [新视频启动示例](https://github.com/miracle-techlink/video-to-simready/tree/main/examples/new_video)

## 已有结果与适用范围

已有变电站场景经离线配准和资产修复后，完成 Lite3+Z1 固定任务、20/20 独立审计与 713 帧原生 1080p 回放；该 v0.6 入口耗时 1095.164 秒，不含新重建和前置资产准备。星巴克历史完整视频为 900 帧原生 4K 的 G2 编排展示，不作为物理导航验证。

v0.7 新视频入口已串接观测、重建接入、通用 USD 准备、结构检查与巡览。使用历史原片与操作者声明关联的 PLY，CPU 导出为 7.951 秒，恢复为 0.246 秒；12 帧巡览为 231.874 秒，含冷启动。该输出仍有明显模糊与空洞；本轮没有新云重建、独立尺度或机器人任务验证，不能将巡览生成等同于任务级 Demo 已自动完成。

白皮书将这两条实际路径和可组合的修复框架分别说明。任意新视频到任务级 Demo 仍需补齐自动语义资产化、可靠尺度与物理参数、真实修复插件和跨场景任务适配；统一 benchmark 的框架与当前已取得的实验结果分开陈述。

## 修订与复现

本次修改仅涉及文档、图示和生成脚本，原实验清单和运行源码保持一致。图形与 PDF 可使用仓库脚本重建，中文图需要 Noto Sans CJK SC 字体。PDF 内嵌字形，十二张架构/方法 SVG 保留可编辑文字。

- [v0.7 原运行证据](https://github.com/miracle-techlink/video-to-simready/blob/main/docs/report/new-video-validation.json)
- [v0.6 任务复现证据](https://github.com/miracle-techlink/video-to-simready/blob/main/docs/report/one-command-validation.json)
- [保留的逐模块版本与 v0.7 安装包](https://github.com/miracle-techlink/video-to-simready/releases/tag/technical-report-2026-11-13-v3)

本版本不重复发布软件安装包；代码入口与部署方式沿用 v0.7。原创代码采用 MIT，第三方资产、视频、权重、SDK 与服务遵循各自许可。
