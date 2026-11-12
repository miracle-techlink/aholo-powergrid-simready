# 视频到可交互仿真场景：方法与复现 · 修订版

v0.6.0 重写了技术报告的方法叙事，并新增 `simready run` 统一场景入口。报告共 18 页、8 图，以 M1–M6 六个阶段说明输入、处理方法、产物和证据，将当前可执行流程、离线资产准备与 VGGT→gsplat 研究支路分开。

三张原生矢量图分别解释整体数据依赖、Agent 修复决策与独立验收、机器人控制与测量回路。PDF 为每张核心图设置横向专页，源码保留可重建的图形。资产表示统一为外观、碰撞、交互三个运行层，来源证据贯穿各阶段。

统一入口从已准备的星巴克或变电站 bundle 开始，或先导出已有 bundle spec，再执行来源核验、任务和录像。它生成独立运行计划与汇总，不自动下载第三方资产、安装 Isaac SDK 或把新视频全自动资产化。

## 阅读与运行

- [修订版技术报告](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v2)
- [修订版 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v2/simready-technical-report-zh.pdf)
- [架构图独立 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v2/simready-architecture-figures.pdf)
- [架构图与生成源码 ZIP](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v2/simready-architecture-figures.zip)
- [README 与统一入口](https://github.com/miracle-techlink/aholo-powergrid-simready)
- [场景准备](https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/scenes)
- [机器人接入](https://github.com/miracle-techlink/aholo-powergrid-simready/tree/main/examples/robots)
- [保留的首版报告](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13)

## 本次一键运行验证

- 变电站完成包与环境检查、抽帧、物理任务、20/20 独立审计及原生回放：模拟 28.508 秒、位移 1.294082 米、一次按压；新视频为 1920×1080、25 fps、713 帧、28.52 秒。
- 星巴克使用最终源码完成 640×360、30 fps、8 帧短预览，验证 G2 编排入口；未重跑完整 4K 视频，不代表物理导航。
- CPU 回归为 297 passed、1 skipped；Ruff、格式、编译和架构检查通过。已有 bundle spec 实际导出 30 个文件，缺少 Isaac 的解释器在环境检查处按预期拒绝，未执行 GPU。

变电站与星巴克入口总 wall 分别为 1095.164 秒、281.670 秒，包含冷 shader 和启动检查，不含新重建或此前资产准备；这是功能验证，不作加速比较。变电站整轮运行绑定 `10c250b` 的 96 个源码文件，随后仅修改两处失败分支保护并通过 5 个 CPU 负例；控制、物理和渲染未变，未重跑整轮变电站。完整记录见 [v0.6 验证清单](https://github.com/miracle-techlink/aholo-powergrid-simready/blob/main/docs/report/one-command-validation.json)。

## 历史证据与能力范围

历史变电站接受任务由 Lite3+Z1 完成已知地图接近、RGB-D 目标定位、接触按压与回撤，通过 20 项独立审计并生成 714 帧原生 1080p 视频。星巴克历史视频为 900 帧原生 4K 的 G2 运动编排，不作为物理导航证据。旧测量保持原版本归属，没有被重报为 v0.6.0 新入口的实验。

G2 设备端口仅提供 USD `root_pose`；Lite3+Z1 提供物理 `joint_drive`。独立 Lite3/Z1 为绑定模板与 CPU 合同验证；新构型仍需策略、关节映射、安装/TCP、传感器标定及任务验收。导航、感知和规划尚未统一为任意机器人的通用任务运行时。

M3 仍有离线配准、补建和物理参数准备；未观测面与尺度保留估计标记。Aholo 供应商内部训练不由本项目实现，DINOv2 用于冻结特征核查，RSI 复用有证据的参数配方，不更新基础模型权重。15 分钟全工作量目标未达，性能优化继续暂停。

此前包装核验详见 `docs/report/packaging-validation.json`，与本次入口验证独立记录。SSH 尚无远端完整任务证据。原创代码采用 MIT，第三方视频、机器人模型、权重和 SDK 按各自许可准备。本次以新 tag 发布，不覆盖旧 Release。
