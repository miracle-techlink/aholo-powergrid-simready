# 搬杯稳定性候选与职责拆分

当前交付是**可选择的控制修复候选、CPU 回归和冻结重跑物料**。默认仍为 `baseline-v1`，`stabilized-v2` 尚未取得新的物理通过结果。本会话的 NVIDIA 驱动检查失败、Docker socket 不可访问，因此没有重新运行 Isaac，也没有把 CPU 指令重放称为物理验证。

## 失败定位

只使用旧实验的 development 101/102 做开发诊断；旧 heldout 901/902 不用于调参。原 dev-01 的失稳发生在松爪之前：

| 阶段 | 杯倾角 | 观测 |
|---|---:|---|
| LIFT 结束 | 8.11° | 开始出现杯相对夹爪的移动与转动 |
| HOLD 的 7.075 秒 | 首次超过 15° | 双侧接触仍可存在 |
| HOLD 结束 | 17.31° | 本阶段相对掌部位移 12.33 mm、转动 11.88° |
| TRANSFER 结束 | 17.44° | 沿途基本保持已经形成的错误姿态 |
| PLACE 结束 | 29.99° | 仅按位置修正，没有修正已发生的倾斜 |
| 最终 | 77.46° | XY 放置误差 43.34 mm |

这些观测说明“有双侧接触”不能证明杯的姿态稳定；它们本身不足以证明失稳仅由某一种摩擦参数造成。

修改前源码和 hash 存于 `runs/cup_stability_20261114/baseline_snapshot/`；该快照与历史 rollout 的 worker hash 分别记录，不冒充同一个版本。诊断逐阶段数据见 `development_diagnosis.json`。基于两条历史 development 观测重建各 5400 帧 baseline 指令，最大差异为 `1.79e-7 rad`，来自 PLACE 的 IK 重算；这是 CPU 指令比对，既不是逐位相同，也不是新物理轨迹。

## 模块边界

| 模块 | 负责 | 不负责 |
|---|---|---|
| `simulation/cup_grasp.py` | CLI、阶段编排、失败终态和收尾 | 具体控制公式、USD 场景装配 |
| `simulation/cup_runtime.py` | SimulationApp、stage attach/detach、传感器、步进、IK 接口 | 控制策略选择、成功判定 |
| `simulation/cup_control.py` | 版本化控制、五次插值、反馈命令与释放门控 | 修改杯位姿、修改碰撞、独立验收 |
| `simulation/cup_recording.py` | 复制测量 buffer、保存轨迹、实现源码身份、原 worker 初检 | 物理执行、最终独立验收 |
| `evaluation/cup_transfer.py` | 原独立验收 | 生成动作或选择有利样本 |
| `workflows/cup_robustness.py` | 冻结参数与代码、逐回合执行、成对汇总 | 调参、训练、自动提升候选为默认 |

初始化失败、步进失败、记录失败和中断均尝试关闭已打开的物理资源与应用。失败前采集的轨迹可以保留，但不完整阶段仍会被原独立审计拒绝。

## 候选的控制行为

`--controller stabilized-v2` 显式启用候选。使用模拟器真值姿态和接触力，在原 240/480 Hz 步进中以 20 Hz 更新反馈；物理步频、阶段时长和独立验收阈值未改变。

- LIFT/HOLD/TRANSFER/PLACE 中观察杯倾角与相对掌部滑移，用有界腕部修正尽早减小倾斜。修正限制为 8°/s、累计 20°；额外关节偏移限制为 0.3 rad/s。
- 滑移或倾角触发小幅增加闭合量，最大 0.75 rad；任一指侧观测力超过 20 N 时抑制继续收紧。这是位置命令的保护条件，不是经过验证的接触力上限。
- 松爪前要求连续 0.15 秒观察到正向目标桌支撑和小倾角。未满足时明确记录 `RELEASE_BLOCKED` 并结束失败回合，不跳过失败、不放宽审计门槛。
- 杯仍为动态刚体，没有设置杯的位置、姿态或附件关节；没有策略训练或自主视觉定位的新声明。

新反馈的 IK、接触效果和控制边界仍需物理验证。CPU 测试证明命令方向、限幅、因果观测使用、版本身份和失败处理，不证明杯会成功放稳。

## 冻结与重跑

开发协议是 `examples/starbucks_grasp/robustness_development.json`（101/102）。新的独立留出协议为 `robustness_stability_heldout_v2.json`（11001–11004）。四个样本的两版独立 USD 已准备并完成参数读回，最终冻结身份见：

`runs/cup_stability_20261114/frozen_heldout_pair_v3/comparison_manifest.json`

两版都保持 `PREPARED / NOT_RUN`，任务结果为 `null`。准备过程没有产生成功率。所有样本都保留在成对报告中，缺失执行显示 `NOT_RUN`。取消会记为 `CANCELLED` 并立即传播，后续样本不再启动；已完成或失败的回合仍保留在 campaign 和 comparison 报告中。中断后的部分尝试标为 `INCOMPLETE` 物理验证，不能显示成完全未运行。旧 901/902 在成对入口中被拒绝。

最终清单同时冻结 backend、本地解释器的调用路径和文件 hash、镜像引用、GPU 选择器，以及 worker / cleanup / audit 三项期限（默认 7200 / 15 / 300 秒）。解释器路径保留虚拟环境软链接的调用语义。当前使用的 `simready-isaac:6.1.0` 是可变 tag，**不是不可变运行环境**；改用镜像 digest、固定 GPU UUID 后仍需在运行回执核对实际环境。文件 hash 也没有冻结本机所有依赖库和驱动。

在已恢复 GPU/Isaac/Docker 的同一环境，先跑 development 成对验证：

```bash
.venv/bin/python -m simready workflows cup-robustness \
  --protocol examples/starbucks_grasp/robustness_development.json \
  --assets runs/starbucks_visual_delivery_20261113/grasp_delivery_final_v2/task/planned \
  --out runs/cup_stability_development_physical \
  --paired --accept-eula
```

如果源码、配置和资产仍匹配最终冻结清单，再跑新的留出样本：

```bash
.venv/bin/python -m simready workflows cup-robustness \
  --protocol examples/starbucks_grasp/robustness_stability_heldout_v2.json \
  --assets runs/starbucks_visual_delivery_20261113/grasp_delivery_final_v2/task/planned \
  --out runs/cup_stability_heldout_physical \
  --comparison-manifest runs/cup_stability_20261114/frozen_heldout_pair_v3/comparison_manifest.json \
  --paired --accept-eula
```

输出目录必须全新。`--comparison-manifest` 会拒绝源码、运行配置、物理步频、样本、审计器或资产身份变化。切换 backend / 镜像 / 本地解释器 / GPU 选择器 / 期限后，应重新准备清单。若 development 导致修改候选，应在查看新留出物理结果之前重新准备并冻结版本。留出结果揭晓后不得再把同一组结果用于调参后宣称独立泛化。四组新样本仍只是小规模验证，不能据此承诺通用机器人稳定性。

默认控制器切换需人工审查新物理证据；本入口不会自动切换。
