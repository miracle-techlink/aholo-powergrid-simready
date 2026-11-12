# PowerGrid Sim-Ready：视频到可观测、可修复的仿真场景

本项目把真实视频转换为三维场景、独立资产和机器人可交互的仿真环境，再用冻结观测协议检查修复是否有效。当前样例是变电站/配电房中的 Lite3 四足 + Z1 机械臂。

**本仓库提供可运行的 Agent 修复闭环、Codex/Responses 接口、持久 Memory、版本化 Skill、多场景 DAG 调度与统一 benchmark；历史实验统一放在 `legacy/`。完整视频→交付的真实 worker 适配仍在进行。** 原始视频、训练权重、机器人模型与交付 USD 不包含在源码仓库中。

## 当前真实链路

```mermaid
flowchart LR
  V[原始视频] --> F[抽帧 / 清晰度筛选 / 去重]
  V --> A[Aholo 云端高斯重建]
  F --> G[VGGT 位姿与深度 → gsplat 训练]
  A --> S[模型 / 坐标 / 地面 / 尺度对齐]
  G --> B[独立重建评测分支]
  F --> R[多帧参考 / 分割 / 特征与边缘核查]
  S --> E[独立几何 / 材质 / 碰撞 / 关节]
  R --> E
  E --> I[Isaac：Lite3 + Z1 / 导航 / 视觉确认 / 运动规划]
  I --> O[冻结视角 / 资产 / 物理与任务观测]
  O --> D[缺陷清单 → 修复计划 → 新候选]
  D --> E
  O --> Q[独立验收 → 交付清单与视频]
```

已展示的估计补全版本使用 Aholo 提供空间底稿，再加入显式 Mesh/PBR 资产；可见场景主要来自独立几何。自训练的 VGGT 位姿→gsplat v3 是独立分支，尚未接入该最终场景。VGGT 使用预训练模型，并非本项目重新训练了 VGGT。

Difix 是可选的二维清晰化预览，不能代替可编辑三维资产修复。低置信度补全必须保留来源与估计标签；仿真按钮触发也不证明真实设备已断电。

## 速度审计结论

| 历史记录 | 耗时 | 解释 |
|---|---:|---|
| Aholo 成功任务 | 约 60 分钟 | 提交到成功，含服务端排队；另有约 89.5 分钟后失败的任务 |
| Difix 250 帧、720p | 23.18 分钟 | 本地可选清晰化预览，逐帧串行 |
| 原生渲染 250 帧、4K | 8.55 分钟 | 一次旧场景运行 |
| 近柜候选渲染 250 帧、4K | 20.41 分钟 | 不同候选，不能与上一行当作重复实验 |
| 机器人任务循环 | 4.51 分钟 | 模拟时间约 28.5 秒；不含 Isaac 启动与场景装载 |

这些是分散历史记录，不能相加当作同一版本的端到端耗时。最新最终视频与 v3 训练尚缺完整分阶段计时。主要已知问题是全局 GPU 锁、等待整机 GPU 空闲、重复启动 Isaac/模型、逐帧串行和高频 CPU↔GPU 同步。[审计证据与优化顺序](docs/PERFORMANCE_AUDIT.md)

大规模部署采用 **CPU / CUDA / RTX / 云端配额四个工作池**。优先让不同场景并行，随后减少局部修复的重算，再做单场景模型/内核优化。A100/H100 没有 RT Core，不受 Isaac Sim 官方支持；A100 池用于训练/视觉，Isaac 使用独立受支持设备。[NVIDIA 要求](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)

## 安装与 CPU 快速验证

Python 3.10+，Linux（设备租约依赖 `flock`）。infra 使用标准库；benchmark 依赖 NumPy、SciPy 与 Pillow。不要求 CUDA 或 Isaac 即可跑以下示例。

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q

# 四个合成场景的 DAG；仅模拟阶段耗时，不做真实重建。
python examples/infra/make_demo.py --out runs/demo-input
python -m simready_bridge.infra \
  --plan runs/demo-input/plan.json \
  --inventory runs/demo-input/inventory.json \
  --out runs/demo-first --cache runs/demo-cache --max-workers 4

# 相同输入，使用新运行目录，复用已校验产物。
python -m simready_bridge.infra \
  --plan runs/demo-input/plan.json \
  --inventory runs/demo-input/inventory.json \
  --out runs/demo-reuse --cache runs/demo-cache --max-workers 4

# 合成 RGB 观测器示例：PASS 仍不授权发布。
python examples/benchmark/make_fixture.py --out runs/benchmark-input
python -m simready_bridge.benchmark run \
  --protocol runs/benchmark-input/protocol.json \
  --candidate runs/benchmark-input/candidate.json \
  --out runs/benchmark-result
```

首次示例预期 16 个阶段成功，第二次 16 次缓存命中。目录必须是新的，避免覆盖既有证据。真实工作负载需用自己的 worker、输入文件、固定运行时身份与经过测量的资源 inventory 替换示例任务。

复现调度测试：

```bash
python scripts/benchmark_infra.py --out runs/scheduler-benchmark.json
```

测试同时比较串行、4 路并行和缓存复用，要求所有产物哈希相同。本机记录为 4.57 秒 → 1.20 秒，约 3.82 倍；16 个阶段缓存命中时为 0.012 秒。结果只代表合成 CPU 调度能力；真实 CUDA、Isaac 与端到端加速尚未测量。[已记录的测试结果](docs/benchmarks/scheduler_cpu_20261112.json)

## Agent 闭环与复用

```bash
# 无 API 费用：三个合成场景依次学习、验证、复用。
python examples/agent/run_demo.py --out runs/agent-demo

# 使用本机 Codex 已登录账号；doctor 不打印凭据。
codex login
simready-pipeline doctor
python examples/agent/run_demo.py --out runs/codex-demo --provider codex --scenes 1

# CPU 视频抽帧，需要 ffmpeg。
simready-ingest --video /path/source.mp4 --out runs/frames
```

已实测 Codex CLI 0.167.0 的账号调用。另有 `ResponsesProvider` 通过 API key 接入，HTTP 合同使用 mock 验证。Agent 选择已登记插件；独立验收决定接受或回退；成功经验在跨场景回归后晋升为 Skill，再进入后续场景。完整接口、配置、架构图和已验证范围见 [Agent Runtime](docs/AGENT_RUNTIME.md)。

## 模块边界

| 路径 | 当前职责 |
|---|---|
| `simready_bridge/ingest/` | 可复用抽帧、筛选、逐位去重、train/heldout 来源清单 |
| `simready_bridge/agent/` | Rules、Codex CLI、Responses API 的统一决策接口 |
| `simready_bridge/pipeline/` | 有预算的观测→修复→验收循环 |
| `simready_bridge/plugins/` | 版本绑定的可信任务模板、参数边界与候选动作 |
| `simready_bridge/memory/`、`skills/` | SQLite 经验、版本化配方、晋升、检索、退役 |
| `simready_bridge/infra/` | CPU/CUDA/RTX/云配额、设备租约、DAG、缓存和耗时 |
| `simready_bridge/benchmark/` | 观测、通用资产图、缺陷、独立验收与候选比较 |
| `simready_bridge/artifacts.py`、`process.py`、`geometry.py` | 严格 JSON、哈希、进程清理、坐标约定 |
| `configs/`、`examples/`、`tests/` | 合同、可重现示例和回归测试 |
| `legacy/s0_ingest/` | 旧抽帧入口，转调新的 ingest CLI |
| `legacy/s1_aholo/` | Aholo 上传、提交、轮询、下载；仍需提供独立客户端 |
| `legacy/rsi_3dgs/`、`legacy/scene_bench/` | 历史修复与评分；不与新 benchmark 混算 |
| `legacy/warehouse_3dgs/`、`legacy/submission_template/` | 历史 GPU 实验、USD 工具与参赛提交流程 |

安装包只包含 `simready_bridge`，不自动导入旧 Isaac/训练/提交脚本。历史模块从仓库根目录用 `python -m legacy.…` 运行，依赖独立环境与数据。此仓库没有 `s2_vggt` 至 `s5_eval` 的完整本地快照，不能按这些路径复现。[目录迁移与审查记录](docs/CODE_AUDIT.md)

## 已实现与待完成

已实现：按设备独占而非整机全局互斥、CPU/RAM/云端并发预算、依赖失败传播、超时终止本任务进程组、输入/输出内容绑定缓存、每阶段日志、独立进程重新计算观测，以及结构/物体/零件/关系层面的资产一致性。

待完成：真实 VGGT/gsplat/Difix/Isaac worker 接入、跨机调度、模型热驻留、自动完整视角采集、物理/任务采集器、真实参考资产提取、密封多场景数据集、生产阈值标定与发布权限隔离。新闭环已能执行可信插件 DAG，当前提供的端到端插件是合成校准示例；真实场景插件目录仍为待接入状态。所有验收报告保留 `release_authorized=false`。

- [Agent 接口、Memory/Skill 生命周期与架构图](docs/AGENT_RUNTIME.md)
- [全仓审查、缺陷修复与验证边界](docs/CODE_AUDIT.md)
- [Infra 架构、任务接口、集群方案与底层优化](docs/INFRA_DESIGN.md)
- [耗时审计、代码冗余与可验证的优化顺序](docs/PERFORMANCE_AUDIT.md)
- [统一 benchmark 与通用资产一致性](docs/BENCHMARK.md)

## License

代码采用 MIT。第三方机器人、场景、模型权重和数据的许可由各自来源规定，不随本仓库授予。
