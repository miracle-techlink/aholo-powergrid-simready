# 能力与待办

状态日期：2026-11-14。本页统一记录当前能力；配置、实现位置和命令分别归 [Pipeline](pipeline.md)、[架构](architecture.md)和各示例。最新工程检查以 [main CI](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml) 为准，CPU 与 GPU 证据分开。

## 已有能力及验证范围

| 能力 | 已有实现／证据 | 边界 |
|---|---|---|
| 规范 pipeline | 配置、阶段停止、恢复与局部升级；CPU USD 交付通过 | 当前接收 Gaussian PLY，完整资产化仍有专用步骤 |
| 重建适配／比较 | artifact、Aholo、command 统一合同与 baseline 矩阵 | 合成适配示例已跑通；未新增 VGGT、AnySplat 等模型质量成绩 |
| 语义与物性 | 掩码／RGB-D 关联、逐对象参数、独立 USD 读回；历史多类模型与 PhysX 夹具有记录 | 代理不等于完整网格，参数先验不等于实测 |
| NeRF2Physics | 数值适配、候选导入、质量编译已接主链 | 尚无本项目新视觉推断和公开测量集成绩 |
| 执行设施 | 单机嵌套取消、统一设备租约、隔离恢复、身份约束缓存 | 真实 GPU／Docker 生命周期回归与跨节点调度分别待验收 |
| 搬杯 | 原控制器四组物性样本 3/4 通过；反馈候选与新留出配置已冻结 | 小样本仿真；候选未完成独立成对物理验证，默认仍为原控制器 |
| Agent / RSI | 注册方案、独立复验、接受／回退与经验晋升 | 新视频未默认覆盖全部缺陷，不等于策略训练 |
| CI/CD | PR 门槛、双 Python 回归、隔离 wheel 验证、main 构包交付 | 不替代实物标定、GPU 任务或模型质量验证 |

证据：[模块化验收](audits/2026-11-14-modular-pipeline.md)、[执行与物性接线](audits/2026-11-14-remaining-delivery.md)、[多类物性](benchmarks/multiclass-physics-20261113/README.md)、[搬杯四样本](benchmarks/cup-robustness-20261114/README.md)。

## 待完成

| 顺序 | 下一步 | 验收条件 |
|---|---|---|
| 1 | 真实 baseline 与参数模型 | 固定代码／权重／数据划分；实际推断、独立参考数据与质量结果；不得用接口样例代替 |
| 2 | 搬杯反馈候选 | 冻结控制器，在新留出参数上与原控制器成对比较，保留失败且不改门槛 |
| 3 | 自动资产完整化 | 多视图实例关联、尺度链、完整 mesh／纹理、背景补洞；逐对象固定视角与碰撞验收 |
| 4 | 多场景 benchmark | 有实例、相机、尺度与留出标注；分层指标和密封系统测试 |
| 5 | 更多交互与真实机器人 | 门、抽屉、旋钮及 G2 动力学导航按任务分别验证 |
| 6 | 工程产品化 | 新视频修复插件闭环、通用剪辑／配音入口、远端完整复现和跨节点调度 |

物性细化路线见[物性计划](plans/physical_properties.md)，动作训练边界见[随机化计划](plans/action_domain_randomization.md)。性能提速研究按原指示暂停。

## 历史结果

变电站 20/20 审计、G2 编排视频、早期 gsplat 实验与 v0.5–v0.7 入口的结果保留在[历史索引](history.md)，不合并为当前新视频的成功率。已有 gsplat 实验不等于已验证的完整 VGGT→gsplat 可插拔后端。
