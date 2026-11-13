这四个域由 `workflows.repair_loop.run_repair_workflow()` 组合为开发闭环，分别回答“候选是否改善”“哪些修改可以执行”“本轮选哪项修改”“哪些结果值得复用”。编排器持有当前候选、预算和轮次；验收器持有判据；插件持有任务模板；Agent 只有方案选择权；经验库保存带证据的结果与参数配方。它们是可单独调用的基础设施，当前 `simready run --video` 默认链路并不调用这套修复闭环。

调用关系为：**初始独立验收 → 缺陷与历史检索 → 注册表生成方案 → Provider 选择 → 执行任务图 → 独立复验与回归比较 → 更新候选、写入经验**。每轮状态保存在 `round_000/` 等目录，根目录的 `workflow.json`、`progress.json` 和 `result.json` 记录配置、进度与最终候选；SQLite 经验库位于工作流配置的 `memory` 路径。四域均不授予正式发布权限。

### 验收模块 evaluation：把原始观测转换为可复核判定

验收由合同校验、观测计算和判定三层组成。`validate_protocol()` 固定视图、参考来源、检查项和阈值，`validate_candidate()` 将候选绑定到协议哈希；两者检查声明的拟合帧与评估帧是否重叠。`observe()` 读取带哈希的参考与候选文件，计算 RGB、可选深度、实例和资产图指标，返回 `metrics`、逐区域测量、问题列表与实际输入清单。RGB 必须具有相同的 `H×W×3` 尺寸，参考与候选深度须互为同尺寸的二维米制数组，当前并未进一步核对深度与 RGB 的分辨率相等；验收器不会临时缩放图像或调整曝光来提高得分。`score()` 按协议的运算符和阈值判定；缺测或阈值未标定产生 `INCOMPLETE`，必选项决定整体是否通过。

开发闭环的 `verify_candidate()` 在新 Python 进程中调用 `evaluate_candidate()`，不接受 Agent 提供的分数。计算前后复核协议、候选、观测文件与验收代码的哈希，输出 `observations.json`、`scorecard.json` 和 `verifier_code/` 源码快照。开发阶段另输出 `defects.json`：缺陷保留检查编号、测量值、阈值、资产与像素区域，但根因仍为 `UNCONFIRMED`。`compare_scorecards()` 要求前后协议与检查定义一致，只对必选检查判断改善或退化；至少一项改善且无退化才接受替换。因此，“接受一个更好的候选”和“候选全部通过”是两个独立结果。

<!-- table: 验收模块按协议、观测、独立评分与回归拆分。 -->
<!-- columns: 0.95,2.05 -->

| 核心组件 | 输入 → 输出及职责 |
| --- | --- |
| `protocol.py` | 协议、候选 JSON → 校验后的合同；固定身份、视图与检查定义 |
| `observations.py`、`asset_graph.py` | 原始 RGB／深度／实例／资产图 → 数值指标及定位证据 |
| `verification.py`、`scoring.py` | 合同路径与预期哈希 → 独立进程生成的测量、评分和开发缺陷 |
| `regression.py` | 前后评分卡 → 改善、退化、候选接受与整体通过状态 |

扩展新 metric 时，应在观测计算层实现原始数据读取与计算，在 `observe()` 中显式汇总稳定的指标键，再加入协议检查的 `metric/op/limit/calibration/required/track`；需要定位时补充缺陷映射。当前没有动态发现 metric 的插件机制。另一入口 `scene_package.inspect_scene()` 专门检查 USD 引用闭包、有限包围盒、高斯与碰撞分离、滑动关节合同，其结构通过不能替代外观或任务验收。`sealed_release` 应经 `evaluate_candidate()` 或评测 CLI 调用，不使用需要读取缺陷文件的开发包装器 `verify_candidate()`；密封阶段不输出开发缺陷，工作流和经验库也拒绝消费密封协议；但评分与观测文件仍在本地，真正的密封访问隔离还需操作系统权限。哈希证明内容身份，不证明参考来源或渲染生产者可信，评分卡始终保留 `release_authorized=false`。

### 修复模块 repair：把缺陷绑定到受限、可追踪的修改

修复域有两套不同入口。`plan_repairs()` 面向能力目录，按缺陷类型、优先级和写入域冲突生成非执行计划，并列出需要补证据、标定或延后的动作。仓库的 `configs/repair/adapter_catalog.v1.json` 标记 `execution_enabled=false`，其中适配器仍为 `pending`；登记脚本线索不等于已经接入可执行修复。真正执行使用 `RepairPluginRegistry`，要求注册表显式开启执行，并提供参数模式、有限参数变体、任务模板和产出候选的最终任务。

注册表加载时验证参数的字段集合、类型、有限值、上下界与枚举，将任务模板、参数模式和声明输入文件的哈希纳入插件实现版本。`propose_repairs()` 根据协议类型、场景族、可修缺陷筛选插件，加入兼容的活跃技能配方，并排除同一候选已失败的动作。方案包含插件及版本、参数、资产集合、缺陷类型和内容派生的 `proposal_id`。`build_task_plan()` 复制可信模板，写入 `bindings/parameters.json`，绑定当前候选、协议与已核验观测文件；保留输入名不能被插件配置覆盖，修复任务关闭隐式缓存复用。

<!-- table: 修复规划与可信任务执行分别受合同约束。 -->
<!-- columns: 0.95,2.05 -->

| 调用位置 | 数据与权限边界 |
| --- | --- |
| `plan_repairs()` | 能力目录 + 开发缺陷 → `PLAN_ONLY` 计划；不启动脚本 |
| `RepairPluginRegistry.propose_repairs()` | 缺陷 + 兼容技能 + 失败历史 → 有限、带版本的方案集合 |
| `build_task_plan()` → `run_task_graph()` | 选定方案 → 绑定输入的任务图、执行记录及新 `candidate.json` |
| `run_repair_trial()` → 独立验收 | 新候选 → `RepairTrialResult`；由外层工作流决定是否推进候选 |

扩展可执行 plugin 需要实现任务工作程序，声明输入、输出、资源和超时，保证 `result_task` 产出 `candidate.json`，再登记适用场景族、缺陷类型、参数模式与变体；仅往能力目录添加名称不能执行。当前随仓库的学习示例使用受限的合成增益插件，注册表还限制此类 `fixture` 只用于合成真值的观测标定协议。任务模板具有实际执行能力，属于需审查的可信配置；输入哈希复核可以发现改变，并不等于操作系统沙箱。修复器没有修改验收门槛或批准发布的权限，失败任务也不能借“生成了文件”绕过独立复验。

### 决策模块 agents：在既定方案集合内进行决策

Agent 接口由 `DecisionProvider.decide(context, out, timeout)` 定义，返回 `DecisionResult`，其中包含 `RepairDecision`、提供者标识、用量与耗时。工作流构造上下文：当前缺陷、注册方案、按作用域检索的成功与失败经验、活跃技能和剩余轮次。决策只有 `repair`、`stop`、`need_evidence` 三种动作；修复时必须引用当前集合中的 `proposal_id`，不能提交任意参数、脚本或 shell 命令。`RepairDecision.parse()` 拒绝额外字段、未知方案及过长解释，提供者返回后工作流再次解析，防止替换适配器绕过合同。

<!-- table: 不同决策来源共用同一受限方案选择合同。 -->
<!-- columns: 0.95,2.05 -->

| 本地适配器 | 决策方式与持久记录 |
| --- | --- |
| `RuleBasedProvider` | 优先兼容技能，再按注册优先级与方案编号确定选择；显式报告零 token，无模型调用 |
| `CodexCliProvider` | 调用本地 CLI 的只读、临时会话，关闭 shell、执行与多 Agent 工具；保存 schema、事件日志、决策与用量，并拒绝出现工具调用的事件流 |
| `OpenAIResponsesProvider` | 通过环境变量凭据发送带严格决策 schema 的请求；保存决策与用量，限制响应大小，HTTP 错误不自动重试付费请求 |

这些适配器复用相同的选择合同，不拥有验收器或插件执行权。模型上下文通过 `prompt()` 限制字节数，并把观测和记忆标为数据；CLI 适配器使用本地登录且移除子进程的 API key 环境变量，API 适配器单独从指定环境变量取凭据。模型适配器的日志位于该轮 `agent/`；规则适配器直接返回结果，不创建该日志目录。工作流统一写入 `context.json` 和 `decision.json`。`normalize_usage()` 仅接受合法的实际 token 计数；用量缺失时，工作流标记 `USAGE_UNKNOWN`，在启动修复任务前停止，而不是把未知成本当零。

扩展 provider 只需实现同一 `decide()` 合同并在 `create_decision_provider()` 中显式注册类型，同时处理超时、结构校验和用量；也可由调用方注入实现供测试。提供者不能自行增加修复方案或把自然语言“成功”写成评分。轮数、调用次数、token 与总墙钟预算由外层编排器统一执行。这是对仓库适配器实现的说明，不代表已验证任意外部模型、账号或新版服务接口。

### 经验模块 experience：把试验结果变成有适用范围的配方

经验域把证据保存和技能生命周期分开。`ExperienceDatabase` 管理 SQLite 模式、WAL 事务与外键，`EpisodeStore` 保存追加式试验记录，`SkillLibrary` 管理配方版本和状态。每个 episode 绑定场景、场景族、策略身份 `policy_key`、候选哈希、插件版本、参数、动作键及证据文件。`append()` 复核证据路径与哈希，并重新读取前后评分卡计算 `ACCEPTED/REJECTED`；没有后验评分的执行错误记录为 `ERROR`，不信任调用者自填的成功标签。数据库保存证据引用而非文件副本，证据删除或变化后，该经验不再进入可信检索。

`policy_key` 由验收与核心代码身份、基准、检查定义和资产策略生成，使不同评估规则下的经验不会混用。`retrieve()` 按场景族、策略身份和缺陷类型检索；负经验 `failed_actions()` 进一步绑定同一场景和候选哈希，避免把一次失败外推到任意新候选。`SkillLibrary.distill()` 只从“修复被接受且候选整体通过”的 episode 提取插件版本与参数配方，先生成 `candidate` 状态。`eligible()` 要求同一配方至少在两个不同场景通过，并至少包含一次声明为 `validation` 角色的开发回归；这里的 validation 不使用 `sealed_release` 数据。

<!-- table: 经验数据库中的证据、配方及生命周期。 -->
<!-- columns: 0.95,2.05 -->

| 持久对象 | 作用与状态约束 |
| --- | --- |
| `episodes` | 内容哈希作为记录身份；保存成功、拒绝与执行错误，读取时复核证据 |
| `skills`、`skill_evidence` | 配方与版本关联支持 episode；`candidate → active` 必须过晋升门槛，可退役为 `retired` |
| 活跃技能检索 | 场景族、策略身份、插件实现版本相容，缺陷类型匹配，并重新检查证据与晋升资格 |
| `performance_experiments` | `PerformanceMemory` 单独保存性能试验；仅精确基准内复用，不自动变成跨场景修复技能 |

新增 skill 应先产生可复核的成功 episode，再蒸馏、跨场景验证和晋升；手写一个“经验提示”不能获得活跃技能资格。显式 `promote()` 和工作流自动晋升都走同一门槛，`auto_promote_skills` 默认关闭。复用技能导致回归时，工作流调用 `retire()`；退役技能不能静默重新启用。插件实现变化会改变版本，旧配方不会直接进入新插件的方案集合。这里的 RSI 是证据驱动的参数配方累积、选择与淘汰，不更新基础模型权重，也不把开发技能晋升解释为场景发布认证。

源码对应：`src/simready/{evaluation,repair,agents,experience}/`；跨域组织与权限检查位于 `workflows/config.py`、`workflows/repair_loop.py` 和 `workflows/repair_step.py`。上述说明来自静态源码核查，不新增运行验证结论。
