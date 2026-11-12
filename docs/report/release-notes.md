# 视频到可交互仿真场景：完整 Pipeline 技术报告

本项目将真实视频观测、高斯重建、坐标对齐、原片约束的资产修复、USD 场景组合与机器人任务连接起来，并分别记录外观、碰撞、交互状态和来源证据。本次交付包括 **16 页中文技术报告、12 幅配图**、模块化源码、两个场景示例，以及本机、Docker 和 SSH 启动方式。

变电站的历史接受任务由 Lite3 与 Z1 完成已知地图导航、RGB-D 目标定位、物理按压和回撤，记录一次有效接触，通过 20 项独立审计，生成 28.56 秒原生 1080p 视频。星巴克提供 30 秒原生 4K 的 G2 运动编排演示；该案例不作为自主导航证据。

新增机器人设备接口统一 `describe`、`observe` 和 `command`：G2 仅提供 USD `root_pose`，Lite3+Z1 提供物理 `joint_drive`。独立 Lite3、独立 Z1 目前是绑定模板与 CPU 合同验证，没有独立构型的 GPU 任务成功证据。新构型仍需关节映射、控制策略、安装/TCP 标定与任务验收；导航、感知和规划尚未统一为任意机器人的通用运行时。

## 发布地址

- [技术报告发布页](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13)
- [PDF 直接下载](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13/simready-technical-report-zh.pdf)
- [GitHub 代码仓库](https://github.com/miracle-techlink/aholo-powergrid-simready)
- [场景启动示例](https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/scenes)
- [机器人接入示例](https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/robots)

## 本次交付

- `simready-pipeline` v0.5.0：10 个职责明确的模块，仿真 worker 迁入包内，新增能力声明式机器人设备接口。
- 星巴克、变电站配置与 USD 依赖导出工具，保留输入哈希、独立输出、阶段日志和时间账本。
- 本机、Docker、SSH 入口及 Isaac 镜像定义；实际核验范围由 `packaging-validation.json` 列明。
- Aholo receipt 恢复客户端、独立验收、Agent/Memory/Skill 和冻结性能实验代码。
- PDF、Markdown、原生图源、公开指标与封装核验记录。

## 复现与能力范围

已有资产的本机封装复现总 wall 时间为 756.632 秒，包含抽帧、物理、审计和录像，不含云重建或资产再处理，不作为加速指标。统一设备接口在该次完整运行后新增，后续验证单独记录。

最终 Lite3+Z1 接口的 Docker 物理回归通过 20/20 项审计，未重录视频；G2 完成初版接口的 8 帧低分辨率预览及最终接口的真实 USD 位姿核对。完整物理回归使用依赖修复前镜像和已有资产包。最终镜像仅补做依赖与 Kit 启动检查，新导出包仅补做 CPU 迁移验证，三者最终组合尚未完成完整 GPU 运行。版本、哈希和验证范围保存在 `packaging-validation.json`。

本次 Aholo 核验只对已有云任务执行只读 GET 和 receipt 续接，没有提交新的付费重建，也未测得新视频重建 E2E。远端 SSH 尚未执行完整任务。

柜体补建、尺度和物理参数仍有估计部分。任意新视频全自动资产化、G2 物理导航、跨机器人通用任务运行时、真实电气操作、远端服务器完整复现与 15 分钟全工作量目标仍未完成或未获验证。性能优化保持暂停。

源码中的 MIT 许可不重新授权原视频、机器人模型和策略权重，这些材料需按各自许可准备。场景示例给出公开的数据合同和本地使用入口；发布包不含第三方大资产。
