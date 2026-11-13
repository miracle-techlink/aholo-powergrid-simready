# 视频到可交互仿真场景：模块原理、运行系统与可复现证据

SimReady Pipeline v0.7.0 面向从场景视频构建可观察、可验证的仿真环境，提供覆盖来源登记、三维重建接入、坐标与尺度、分层资产、物理交互、标准观测、修复与 RSI、机器人运行时、独立验收及原生渲染的技术方案。本次公开 27 页中文技术报告、14 幅技术配图、12 个编号公式及可编辑架构图，逐模块说明算法、输入输出、代码实现和验证范围；同时发布新视频启动入口、本地 Docker 与服务器部署说明，以及星巴克和变电站的复现示例。

新增 `simready run --video` 将观测登记、可恢复重建接入、USD 场景准备、独立结构检查与巡览编码串接为一条命令。已准备的变电站路径保留 Lite3+Z1 感知、控制与物理审计；星巴克保留 G2 编排展示。系统明确区分场景生成、视觉保真、尺度验证和机器人任务成功，所有结果绑定输入、配置、源码与产物身份。

## 发布链接

- [本版发布页](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v3)
- [技术报告 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v3/simready-technical-report-zh.pdf)
- [九张架构与方法图 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v3/simready-architecture-figures.pdf)
- [可编辑图与生成源码 ZIP](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v3/simready-architecture-figures.zip)
- [代码仓库与 README](https://github.com/miracle-techlink/aholo-powergrid-simready)
- [新视频入口说明](https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/new_video)
- [星巴克 / 变电站示例](https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/scenes)
- [本地 Docker / 服务器部署](https://github.com/miracle-techlink/aholo-powergrid-simready/blob/main/docs/deployment.md)

## 本版新增与验证

- 重建层保存任务凭据，显式提交新任务，支持有限等待、离线产物接入和恢复；来源与状态不明时停止，不重复提交。
- 资产层转换标准 3DGS PLY 的球谐、尺度和透明度，接入显式 Sim(3)，生成有界体素碰撞、可选地面及声明的盒子/弹簧按钮。未知尺度保留为未验证。
- 交付层独立重读 USD，检查层引用、边界和关节结构，核验必需产物与哈希；巡览记录相机、逐帧图像、编码参数和真实视频探测结果。
- 恢复只复用输入、配置、运行代码及产物哈希一致的阶段；失败目录和历史记录保留。

真实历史原片与 PLY 的离线复用完成 CPU 导出（7.951 秒）、完整恢复（0.246 秒）和 Docker 巡览（231.874 秒，640×360、24 fps、12 帧）。GPU 成功运行绑定 `2814514` 的 104 个运行源码文件，包含冷启动。原片/PLY 配对为操作者声明；本轮没有重新独立建立该对应。首次渲染后关闭顺序使编码未执行，交付检查正确拒绝，修正后完整重跑成功；失败记录一并保留。详见 [v0.7 验证清单](https://github.com/miracle-techlink/aholo-powergrid-simready/blob/main/docs/report/new-video-validation.json)。

最终运行代码通过 427 项本地回归、1 项跳过，以及 Ruff、格式、编译和架构检查。测试覆盖产物缺失、来源/规格不符、损坏恢复记录、路径逃逸、无效 USD 引用和 worker 失败；网络提交和恢复使用受控客户端测试，未新提交付费重建。

## 结果边界与历史证据

新入口生成的是场景候选。当前原高斯仍有明显模糊、空洞与浮影，未通过视觉保真度 benchmark；没有独立米制尺度验证或机器人任务。新入口不默认执行 SAM/DINO、Agent 修复、完整纹理补建、全资产语义标注或机器人控制。`SCENE_SURVEY_READY` 仅说明巡览产物可交付，不能等同于商业质量或历史修复后场景。

v0.6 已准备变电站完成 Lite3+Z1 固定任务、20/20 独立审计和 713 帧 1080p 视频，入口 1095.164 秒；星巴克完成 8 帧短预览。它们不包含新重建或前置资产准备，与本次通用场景构建分开记录。历史完整星巴克为 900 帧原生 4K 编排展示，不是物理导航。参见 [v0.6 证据](https://github.com/miracle-techlink/aholo-powergrid-simready/blob/main/docs/report/one-command-validation.json) 与 [历史证据](https://github.com/miracle-techlink/aholo-powergrid-simready/blob/main/docs/report/evidence.json)。

G2 和 Lite3+Z1 的统一设备接口分别提供 authored 根位姿与物理关节驱动；新机器人仍需策略、关节映射、安装/TCP、传感器标定和任务验收。远端完整任务、跨场景统一 benchmark、多场景无人交付及 15 分钟性能目标尚未完成。性能优化按用户要求暂停。

本版以新 tag 发布，保留 [v0.6](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v2) 和 [首版](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13)。原创代码采用 MIT；第三方视频、机器人资产、权重、SDK 和服务遵循各自许可，未随源码重分发。
