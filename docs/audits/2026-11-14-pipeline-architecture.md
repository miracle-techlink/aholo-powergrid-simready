# 主流程集成与工程架构复审

审查日期：2026-11-14。对象为本机 `simready_infra_pr` 工作树，包含审查开始前已有的搬杯随机化改动；基准提交 `f07aaa1`。未提交、发布或启动云推断。审查前运行包共 133 个 Python 文件、21,576 行；基线测试为 628 passed、1 skipped。

## 结论与集成实况

NeRF2Physics、GaussianProperty 均未接入本仓库可运行的参数预测链。全文检索只发现方案和状态声明，没有适配器、模型执行、结果转换及公开测量集评分。现有类别先验和 `vision_prior` 来源枚举不构成模型接入。

| 能力 | 现有代码与调用 | 本次判断 |
|---|---|---|
| Aholo / 本地重建复用 | `video_pipeline.execute_video` → `reconstruction_stage.prepare_reconstruction` | 已进新视频入口；本次不重新提交云任务 |
| Grounding DINO + SAM / SegFormer | `semantic_assets.run_semantic_assets` → 分割 worker → `semantic_projection.build_semantic_spec` | 独立工作流已有；新视频主入口不自动调用它 |
| 对象物性 | `category_priors` → scene spec → `physical_spec` → `physical_authoring` | 是有来源的参数声明和预设；没有视觉物性估计 |
| 独立 USD 物性读回 | `evaluation.physical_assets` | 原先仅语义资产入口调用；本轮补入新视频显式对象分支 |
| NeRF2Physics / GaussianProperty | `docs/plans/physical_properties.md` | 规划阶段；推断、转换、数据评测、主流程调用都未完成 |
| 通用任务图 / 资源租约 / 缓存 | `repair_step` → `execution.run_task_graph` | 修复循环使用；视频与物性流程没有统一经过该资源调度 |
| Agent / 经验复用 | `repair_loop`、`plugin_registry`、`experience` | 有独立闭环；不能据此宣称任意视频自动修复已贯通 |
| 动作鲁棒性 | `workflows.cup_robustness` | 固定控制器、独立入口；不是动作训练 |

## 按影响排序的发现

### A1 · P1 · 物性检查未连接新视频交付

`workflows/video_execution.py` 原先在 scene preparation 后只调用 `evaluation.scene_package`。结构检查验证 USD 可打开、碰撞 schema 和关节边界，不比较输入对象的质量、摩擦、材质绑定。即使 scene spec 带显式对象，也不会调用已有的逐对象物性检查。

**本轮已修复**：显式对象分支调用独立检查进程；新增 `physical_asset_validation`，无对象时为 `NOT_EVALUATED`。宿主 `video_pipeline.verify_delivery` 要求审计产物、输入规格和场景哈希匹配。仍保留原交付等级，不把 USD 参数一致性提升为真实物性、动力学或机器人任务通过。

### A2 · P1 · 语义工作流把退出码当作完整验收

`workflows/semantic_assets.py` 原先在审计返回 0 时直接沿用 projection 状态，报告中的 `status` 只用于展示。返回 0 而写出 FAIL、空检查或旧场景报告，仍可能得到 `CANDIDATE_READY`。`KeyboardInterrupt` 也未被异常分支捕获，最终回执会残留 RUNNING。

**本轮已修复**：提取 `evaluation/physical_receipt.py`，统一核对 PASS、非空检查、对象集合、原始规格、catalog、根层和组合 USD 依赖哈希；视频与语义两处复用。中断写 `INTERRUPTED` 后继续抛出，超时写 FAILED 并由 CLI 返回错误。

### A3 · P1 · 执行基础设施并未统一

语义资产自建 `subprocess.run(timeout=...)`，新视频、已有场景、环境探测等也直接启动进程。语义子进程超时只处理直接子进程，通用执行层已有的进程组清理没有参与。`ResourcePool`、`worker_groups`、Isaac launcher 的入口和租约模型也不同。一个目录叫 execution 并不代表所有任务都受它管理。

**本轮部分修复**：语义资产复用 `execution.processes.run_process`，保留 stdout/stderr；没有新增执行器。其余 launcher、容器取消、跨流程资源争用仍待迁移。容器必须由所属容器 ID/name 取消；不能把杀死 Docker 客户端等同于停止容器。

### A4 · P2 · 静态边界检查漏过动态导入

在临时副本的 `core/artifacts.py` 加入 `importlib.import_module("simready.workflows.video_pipeline")`，旧 `check_architecture` 返回空错误列表。工具只遍历 Import/ImportFrom，没有检查动态调用。

**本轮已修复**：检查字面量动态导入、常见别名和 `__import__`；非字面量仅放行显式登记的两个 CLI 路由器。它是结构回归检查，并非任意 Python 元编程的静态证明或运行时隔离。

### A5 · P2 · 工作流层承担场景几何细节

原 `workflows/appearance_collision.py` 有 `/Cafe` 路径、桌子命名和固定接触参数；唯一仓库调用方是星巴克示例。它构造 mesh、绑定材质和更新占用，不承担工作流编排。

**本轮已迁移**：原实现移动到 `examples/starbucks_simready/appearance_collision.py`，调用点更新，支持模块和脚本两种运行方式。算法内容保留，退出 runtime wheel；旧内部导入路径不再提供。没有放宽领域依赖规则来迁就这个特例。

### A6 · P2 · 领域内部仍有大函数，命名不能保证可升级性

审查前 `substation_mission.run` 914 行、`cafe_video.main` 657 行、`semantic_projection.build_semantic_spec` 380 行、`cup_grasp.main` 352 行。初始化、状态转换、控制、记录、渲染混在一起，替换其中一项容易牵动其他行为。

**待迁移**：沿仿真生命周期、任务状态机、观测记录、渲染消费四个职责逐个提取；先固定输入和任务验收，保持旧入口兼容，每次迁移做同资产 GPU 行为回归。本轮没有只为缩短文件而拆散物理控制代码。

## NeRF2Physics 接入边界

[官方仓库](https://github.com/ajzhai/NeRF2Physics)说明重建之外可使用提供的深度图和点云中间产物；还包括特征融合、图像描述、材料候选和属性预测。[预测实现](https://github.com/ajzhai/NeRF2Physics/blob/master/predict_property.py)读取点云、变换、融合特征和材料结果。当前 Aholo PLY 不具备完整的这些输入。方法支持的属性与仿真字段之间还需要明确转换；Shore 硬度不能直接填入弹簧刚度，动摩擦预测也不能直接当静摩擦。

接入应经过以下可独立验收的步骤：

1. 固定上游 commit、模型权重和环境；独立研究 worker 运行官方公开样本并保存原始输入、输出和耗时。公开中间产物重放与新模型调用分开计数。
2. 用公开测量集建立对象分组、单位、数据哈希和密封测试划分；独立评分与类别先验对照。模型调用成功与预测有效分开报告。
3. 建立 `PhysicalCandidate`：对象/部件 ID、坐标尺度、参数名、单位、候选区间、来源、模型版本、原始证据引用和不确定性。缺少接触对、静摩擦或可靠体积时保留未知。
4. 在物性编译层明确选值策略和缺失值策略，经现有 spec、USD writer、独立读回及动力学探针进入场景。估计器不直接写 USD，不能读取封存的评测标签。
5. 把观察、预测、编译、验证接入主工作流，并跑一份新场景完整回执后，才将状态升级为“主链已集成”。GaussianProperty 后续复用同一输出合同。

详细目标架构、infra 归属和迁移顺序维护在[软件架构](../software-architecture.md)，不另起第二套实现计划。

## 本轮验证边界

- 93 项定向测试通过：真实 CPU USD 导出、独立解释器读回、宿主验证、错误报告、依赖篡改、缺失审计、取消与超时、动态导入回归。
- 全仓回归：646 passed、1 skipped；新增 18 项回归用例。
- Ruff、格式、架构检查与字节码编译通过；wheel/sdist 在本地已有构建依赖下以 `--no-isolation` 构建成功。隔离构建因网络无法拉取 setuptools 未完成，未把这次失败计作干净环境验证。
- 主 CLI 合成 Gaussian 夹具：本地 CPU 导出与独立物性审计通过，`SCENE_EXPORTED`、`physical_asset_validation=PASS`；完整命令墙钟约 1.09 秒。再次 `--resume` 复核并复用四个阶段，未重启 worker。这是极小夹具的链路检查，不是性能 benchmark。
- 首次烟测误用了普通 RGB 点云夹具，结构检查以缺少 Gaussian appearance 拒绝，记录保留；随后使用满足重建输入合同的独立 Gaussian 夹具和新运行目录重测，没有降低结构门槛。
- wheel 内含新物性回执校验器、共 133 个运行模块，不再包含咖啡馆专用碰撞 helper；星巴克修复示例的脚本和模块入口 help 均可运行。
- 所有新增物性执行测试使用合成夹具。本轮没有 NeRF2Physics 推断、公开数据集评分、GPU 控制器回归或远端部署结果。

当前交付是代码复审、第一批边界修正和可实施的架构收敛方案。研究模型、统一执行后端和大函数迁移仍明确列为后续工作。

本机复核材料位于 `runs/architecture_review_20261114/`：`pytest-final.log`、`build.log`、`gaussian-smoke-result.json` 和两次烟测的完整产物。生成物不进入运行包；首次不满足输入格式的烟测保留于 `smoke-run/`。
