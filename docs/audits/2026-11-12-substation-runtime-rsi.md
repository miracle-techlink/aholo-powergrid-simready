# 变电站 Pipeline benchmark 与首轮 RSI 实测

日期：2026-11-12。实验：`RSI-RENDER-20261112-01`。结论：**REJECTED**。未通过验收，自动保留 baseline；失败经验已写入 memory。没有替换交付版本。

## 1. 实际覆盖范围

已实现可复跑的“冻结协议 → 原配置 A/A → 固定阈值 → 候选 → 独立验收 → 接受/回退 → memory”闭环。本轮运行完整 **714 帧、1920×1080、25 fps、28.56 秒**视频，复用冻结的场景与已验证物理轨迹。没有重新做 Aholo/VGGT 重建，也没有重新执行物理任务。

第一候选 `disable_background_viewport` 只关闭后台预览视口，保留成片相机、轨迹、分辨率、子帧数和编码。协议绑定输入、代码和验证器；本轮使用实验启动时的代码快照，后续代码开发没有改变这份协议。

## 2. 耗时与结论

| 项目 | 实测 |
|---|---:|
| 原配置样本数 | 2 |
| 原配置逐次耗时 | 447.2770, 448.9106 秒 |
| 原配置中位数 | 448.0938 秒 |
| 候选样本数 | 1 |
| 候选逐次耗时 | 360.8313 秒 |
| 当前样本的耗时降幅 | 19.47% |
| 实验结果 | REJECTED |
| 当前选择 | baseline |

耗时包含 worker 启动、录制、编码及退出。候选在第一次全片质量验收失败后停止重复，因此上述降幅只是单次候选观测，不能宣称已验证加速。这是录制阶段的比较，不是整条 pipeline E2E 提速。机器仍有桌面和既有 streaming 负载。

## 3. 独立质量验收

在看到候选之前，用两份原配置视频校准阈值。完整解码 714 帧，比较每帧和 6×8 局部网格、时序误差、边缘强度，以及固定 13 帧 SSIM。没有配准、曝光拟合、裁掉首帧或移除异常帧。

| 指标 | A/A 自然波动 | 冻结门槛 | 首次候选 | 判定 |
|---|---:|---:|---:|---|
| mean_mae | 0.576241 | 0.9644 | 0.860474 | PASS |
| p95_frame_mae | 0.751621 | 1.3774 | 1.034715 | PASS |
| max_frame_mae | 0.828942 | 1.7434 | 3.873434 | FAIL |
| p99_tile_mae | 1.840809 | 3.2612 | 2.385152 | PASS |
| max_tile_mae | 2.923696 | 5.3855 | 6.704823 | FAIL |
| mean_temporal_error_mae | 0.597001 | 0.9955 | 0.643906 | PASS |
| min_ssim | 0.991154 | 0.9847 | 0.990078 | PASS |
| sharpness_ratio | 0.999505 | [0.97, 1.03] | 0.998102 | PASS |

误差类指标要求不高于门槛；SSIM 要求不低于门槛；边缘强度比要求落在区间内。首次候选最大帧误差位于第 **0 帧**，MAE **3.873434/255**。完整逐帧结果留在 quality.json。

帧 MAE 超限索引：`[0, 1, 2, 3, 4, 5, 6, 7]`；局部网格 MAE 超限的帧索引：`[0, 1]`。因此问题集中在起始片段，不能只删除第 0 帧后宣称通过。

下一候选应检查关闭视口与首帧预热/渲染历史的关系，例如保留初始预热再关闭视口。该因果解释仍是假设；不能把跳过首帧、放宽 SSIM 或降低采样当作修复。修订方案需要新协议和完整验收。

## 4. 本次实验成本账本

北京时间 **21:41:41–22:03:39**，campaign 墙钟 **1318.1764 秒**。下面包含为比较而执行的多次完整录制和独立检查，不能当成生产 pipeline 单次耗时。

| 独立进程 profile | 状态 | 秒 |
|---|---|---:|
| baseline_0/timing/profile.json | SUCCESS | 447.2770 |
| baseline_1/timing/profile.json | SUCCESS | 448.9106 |
| calibration_observation/timing/profile.json | SUCCESS | 30.3774 |
| round_1/trial_0/timing/profile.json | SUCCESS | 360.8313 |
| round_1/verification_0/timing/profile.json | SUCCESS | 30.3318 |
| worker 服务时间合计 | — | 1317.7282 |
| campaign 其余时间：哈希、决策、记账等 | — | 0.4481 |

各 worker 均不调用 LLM，规则选择器也不调用 LLM，因此这部分 provider token 为 **0**。当前工程 Agent 的 token 没有绑定会话用量证据，记为 **未知**，不能计作 0，也没有证据计算 token 节省比例。

另保留一次初始化失败：v1 adapter 解引用了虚拟环境 Python 符号链接，启动时报 numpy 缺失，worker 约 0.035 秒，campaign 约 0.119 秒。已修复为保留 venv/bin/python 路径并添加回归测试；这次失败不计入 v2 耗时，也不算 GPU 实验。

## 5. RSI memory 和可复用范围

`memory.sqlite` 已保存本轮结论、配方、质量证据、时间证据和文件哈希。相同 benchmark 下，已有失败候选会被跳过；成功配方只在相同 benchmark 中选用，证据被修改或丢失时失效。当前 `cross_scene_skill_active=false`。

本轮方案由工程 Agent 提出，执行选择器为 rules；仓库已有 Codex/Responses provider 接口，当前没有新增模型 API 调用或模型权重训练。原有跨场景 SkillLibrary 的两个成功场景门槛保持不变。

本地查询：

```bash
cd /home/nvidia/algotithm/simready_infra_pr
.venv/bin/python -m simready experience runtime-history \
  --database /home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2/memory.sqlite \
  --benchmark-key 55c8f054c3894cbcf2ba5191ce4f840c32c9997dbb9b84285a0703b49abdd650
```

## 6. 代码与证据

- benchmark / RSI 使用说明：`/home/nvidia/algotithm/simready_infra_pr/docs/benchmarks/runtime_rsi.md`
- 实验根目录：`/home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2`
- 协议：`/home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2/campaign/protocol.json`
- 固定质量阈值：`/home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2/campaign/calibration.json`
- 独立验收：`/home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2/campaign/round_1/verification_0/quality.json`
- 完整实验记录：`/home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2/campaign/round_1/episode.json`
- 汇总 JSON：`/home/nvidia/algotithm/simready_infra_pr/docs/benchmarks/substation_runtime_rsi_20261112.json`
- benchmark key：`55c8f054c3894cbcf2ba5191ce4f840c32c9997dbb9b84285a0703b49abdd650`
- 协议文件 SHA256：`cfd2cf26b6ecffe5af0358a88cab43edb43a1a60c3f6b6b0e9412dc60a3cce87`

最终开发版本通过 **157 项测试**、Ruff、格式、9 个领域模块的架构检查，以及 wheel/sdist 构建。实验期间新增的代码身份、证据绑定与数值边界检查经过合成回归测试；真实 GPU 试验使用启动时冻结的实现副本，不能把后续增强声称为本轮执行过的代码。

如复跑本轮冻结实现，设置 `PYTHONPATH=/home/nvidia/algotithm/simready_infra_pr/runs/substation_speed_rsi_20261112_v2/benchmark/verifier`，使用同一 protocol，并指定新的输出目录。新的实现或候选必须重新冻结版本，不覆盖本轮证据。完整本地 E2E、物理优化和跨场景吞吐仍是后续 track。
