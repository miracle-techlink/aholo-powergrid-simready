# 视频到可交互仿真场景：完整 Pipeline 技术报告

本项目面向真实视频到机器人交互仿真场景的构建与验证，建立了涵盖视频观测、三维高斯重建、坐标对齐、原片约束的资产修复、USD 场景组合、机器人控制与独立验收的完整技术链路。系统将场景外观、碰撞几何、交互组件和来源证据分层组织，并提供受约束的 Agent 决策、插件调度及 memory/skill 复用机制。在变电站选定任务中，Lite3 与 Z1 完成已知地图导航、RGB-D 目标定位、物理按压和回撤，记录一次有效接触，通过 20 项独立审计，生成 28.56 秒原生 1080p 视频；同时提供星巴克场景的 4K 运动演示。现公开 15 页中文技术报告、11 幅技术配图、模块化代码及两个场景启动示例，并补充本地 Docker 与 SSH 服务器部署方式。报告明确区分实测结果、估计补建和仍需验证的自动化能力。

发布链接：

- 报告发布页：https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13
- PDF 直接下载：https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13/simready-technical-report-zh.pdf
- GitHub 代码仓库：https://github.com/miracle-techlink/aholo-powergrid-simready
- 场景启动示例：https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/scenes

## 本次交付

- `simready-pipeline` v0.5.0：10 个有明确边界的模块；仿真 worker 已从本机外部 bridge 迁入包内。
- 星巴克、变电站配置与 USD 依赖导出工具；输入哈希检查、独立输出、阶段日志和时间账本。
- 本机/Docker/SSH 入口与 Isaac 镜像定义；不停止已有服务，不内置密钥。
- Aholo receipt 恢复客户端、独立验收、Agent/Memory/Skill 与冻结性能实验代码。
- PDF、报告源文件、公开指标与封装核验记录。

## 结果范围

星巴克机器人使用编排轨迹，不能据此宣称自主导航；变电站结果来自一个已接受的模拟任务，柜体补建、尺度和物理参数仍有估计部分。完整任意视频自动资产化、真实电气功能、跨场景通用成功率和 15 分钟全工作量目标均未获验证。当前任务按用户要求停止性能优化，重点为封装与复现。

发布包不包含原视频、机器人模型或策略权重，需按各自许可准备。原始视频和准备好的两个场景包在本地交付目录提供使用入口；公开说明与数据合同可直接复用。
