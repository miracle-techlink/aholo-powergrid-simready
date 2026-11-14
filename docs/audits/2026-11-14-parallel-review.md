# 并行代码复审与修复（2026-11-14）

本轮由三个子 agent 分别检查执行基础设施、工作流交付、物性与任务验收，主 agent 检查经验复用、修复循环和共享几何，并交叉复核接口及历史格式。保留此前工作树改动；本轮未提交 Git、未启动云任务、模型推断、新 GPU 物理任务或性能提速实验。

审查以可复现的错误路径和调用关系为依据，覆盖当前主入口及其依赖；不表示每条硬件路径都已执行。目标架构和迁移顺序仍以[软件架构](../software-architecture.md)为准。NeRF2Physics / GaussianProperty 尚未集成，语义、研究物性估计与机器人任务尚未全部进入新视频主链。

## 已修复的错误

| 领域 | 原问题 | 实施与验证方向 |
|---|---|---|
| 执行取消 | DAG / 并行 worker 异常退出时，线程池可能等待未取消的子进程；回执可能停在 RUNNING | 共用进程执行器增加可选取消事件；等待线程结束前通知取消；清理所属进程组；记录 INTERRUPTED / CANCELLED；提交失败释放租约 |
| 执行输入 | 非有限或非正超时可传到进程层；CPU worker 可继承旧 GPU 标识 | 启动前校验总期限；CPU worker 清除继承的 `SIMREADY_GPU_UUID`；保持原调用兼容 |
| 缓存读取 | 缓存入口、清单或输出根软链接可绕过预期产物范围；清单接受重复 JSON 键 | 拒绝上述软链接并使用严格 JSON 读取；保留文件摘要验证 |
| 新视频恢复 | 阶段 result 与其实际产物可能不一致；恢复路径仅核对文件，未核对结果含义 | fresh / resume 共享阶段验证；重建、worker 输入、交付结果都绑定已检查产物。`cache_hit/new_submission/resumed` 保持本次调用语义，允许 completion 已写但阶段回执丢失后的合法恢复 |
| 输入冻结 | worker 执行或语义投影后输入改变，可能将旧计算与新来源配对 | 固定 PLY、spec、worker 输入，以及语义清单、RGB、mask、depth、标定；跨阶段重验摘要 |
| 场景交付 | 结构审计未完全绑定当前依赖；已有场景直接入口未经过相同最终验收 | 宿主复核根层、完整依赖与交付清单；已有场景计划绑定 config 和资产来源；直接 `workflows scene` 在通过验收后发布 COMPLETE |
| 搬杯实验 | 已冻结的源资产 / manifest 未在后续回合复查 | 准备前后、执行后检查资产、协议、manifest 和执行代码；USD 参数读回绑定原始样本 |
| 搬杯统计 | 仅相信 PASS 字段；缺失检查、worker 状态或审计退出码时仍可能计成功 | 同时要求完整必需检查、成功退出码和 worker PASS；缺失、未运行及失败仍计入分母 |
| 原始轨迹 | 必需资产 / USD 依赖、采样时钟、帧数、传感器矩阵检查不足；去重阶段名漏掉状态回退 | 独立审计验证完整来源、数组形状和时钟；按连续状态变化验阶段顺序；报告记录原始结果和轨迹摘要 |
| 物性验收 | 声明对象中额外关节未进入独立检查 | 增加 joint inventory。碰撞体清单原本已有检查，本轮只增加其回归用例，没有重复实现 |
| 经验复用 | 相同参考数据换 scene_id 可充当第二场景验证；技能证据未完全核对配方 | 从绑定 protocol 读取 RGB 摘要；晋升要求两个场景的参考图像内容不重叠，并匹配配方及验证角色；调用方不能伪造派生来源字段 |
| 修复循环 | provider 中断可能不写终态，未返回用量的调用可能被当作完整零用量 | finally 保存终态；外部中断原样传播；调用前标记用量未知，收到有效用量后再记账 |
| 相机几何 | 两个消费者重复验证内参，杯定位可静默接受非刚体相机位姿 | 共用 `geometry.pinhole_intrinsics` 和刚体校验；显式声明 ingestion → geometry 依赖；合法输入的两条反投影结果一致 |

主要代码入口：

- 执行：[`processes.py`](../../src/simready/execution/processes.py)、[`task_graph.py`](../../src/simready/execution/task_graph.py)、[`artifact_cache.py`](../../src/simready/execution/artifact_cache.py)。
- 工作流：[`video_pipeline.py`](../../src/simready/workflows/video_pipeline.py)、[`scene_delivery.py`](../../src/simready/workflows/scene_delivery.py)、[`semantic_assets.py`](../../src/simready/workflows/semantic_assets.py)。
- 验收：[`cup_transfer.py`](../../src/simready/evaluation/cup_transfer.py)、[`cup_robustness.py`](../../src/simready/evaluation/cup_robustness.py)、[`physical_assets.py`](../../src/simready/evaluation/physical_assets.py)。
- 复用与几何：[`episodes.py`](../../src/simready/experience/episodes.py)、[`skills.py`](../../src/simready/experience/skills.py)、[`repair_loop.py`](../../src/simready/workflows/repair_loop.py)、[`cameras.py`](../../src/simready/geometry/cameras.py)。

## 兼容性和证据边界

历史 episode 在原文件摘要有效时仍可读取，包括带 protocol 但尚无派生 RGB 摘要的格式。缺少来源证据的历史 skill 不再自动晋升或复用，即使数据库状态为 active；需要重新生成合格验证证据。文件内容不同不能证明真实独立采集，本轮检查只排除内容相同的参考文件重复计数。

历史搬杯 episode 如果缺少 `worker_status`、审计退出码或必需检查，新汇总器不会自动判为成功。应从原始 worker / audit 证据重建记录，不直接补写 PASS。历史文件本轮未改写。只读重新审计历史 dev-01 / dev-02，仍分别失败 / 通过，偏差为 43.341 mm / 8.290 mm；这不构成新物理运行，也不表示已修复失稳。

本轮保持控制器、物性默认值、物理频率和任务成功门槛。相机入口现在拒绝畸变的 pinhole 矩阵、非有限值及缩放 / 反射位姿，调用方应先正确标定，不能靠跳过验证保持运行。

## 集成验收

最终结果登记在 `runs/parallel_review_20261114/validation.json`；该运行目录不进入安装包。

- 全仓 pytest：**720 passed、1 skipped，55.34 秒**，较上轮增加 74 项回归；Ruff、257 个 Python 文件格式、十领域 / 17 个公开函数的静态依赖和 diff 检查通过。回归包含超时 / 取消、完整证据拒绝、合法恢复、历史格式和内容重复拒绝。
- 本地 CPU 真实 CLI：合成 Gaussian 输入 → USD 构造 → 独立结构 / 物性检查 → 交付；fresh 和 resume 均 COMPLETE，物性检查 PASS，resume 复用四个已核验阶段。未运行机器人或物性预测模型。
- 三场景规则修复示例：前两场构造开发 / 验证证据，第三场复用合格 skill；全部 DEVELOPMENT_PASS，不授权生产发布。
- 使用现有环境 `build --no-isolation` 构建 wheel / sdist；逐字节核对 wheel 中 133 个运行模块，不含 examples / runs / legacy / tests 或已移走的咖啡馆 helper。未执行联网干净环境安装。
- 首轮全仓回归期间仍有一处验收源码在收敛，性能工作流的合成测试正确报告 `Frozen campaign evidence changed`。保留首轮日志；源码固定后的定向测试和最终全仓结果另存，不把首轮算作通过。

## 尚有优化空间：下一批按依赖推进

| 优先级 | 待处理项 | 实施约束与验收 |
|---|---|---|
| P1 | 缓存未完整约束继承环境 | TaskSpec 目前只将显式 env / runtime_identity 纳入身份，worker 仍继承宿主环境。定义允许继承的变量、隐私字段和结果相关环境身份；测试“环境变化不得误复用”，不把凭证写入清单 |
| P1 | Isaac / 容器和部分直接 subprocess 的期限与取消未统一 | 逐个委托 execution，保存所属容器 ID、总期限和取消回执；验证只终止本次容器、失败保留产物；先复用现有执行器，不增另一套调度框架 |
| P1 | 物性模型和新视频主链未接通 | 按 PHYS-01 / PIPE-01 完成候选合同、独立研究环境、真实样本、独立评分与显式退化策略；源码存在或离线重放不能代替真实推断 |
| P1 | 搬杯失稳仍待修复 | 用开发轨迹定位夹持 / 转移 / 释放问题；固定新控制器后用新的留出样本成对比较，所有失败保留；仿真真值反馈仍需明确标注 |
| P2 | 仿真初始化与大函数职责集中 | cup 的 SimulationApp 初始化还可能在任务级结果保护范围外；substation 状态、相机、规划与记录混合。按生命周期、控制、观测、记录拆分，使用同资产 GPU 行为回归后删除旧分支 |
| P2 | 工作流验证逻辑仍偏长 | 按重建、输入、交付合同拆 `video_pipeline` 的阶段验证，复用现有规范实现；公开入口兼容；不通过搬文件增加新的包装层 |
| P2 | 运行缓存快照和外部机器人指令边界 | `seed_runtime_cache` 保留源软链接，需独立核查快照隔离；robot port 当前依赖可信控制器的有限值与限位校验，接外部控制前补显式边界 |

当前架构继续采用模块化单仓库和隔离 worker。下一批优先统一执行身份 / 生命周期，同时推进物性候选合同；研究模型、控制器行为修复和规模化部署分别验收，不混入本轮可靠性修复的完成声明。
