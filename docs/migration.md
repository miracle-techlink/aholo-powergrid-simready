# v0.3 → v0.4 迁移

v0.4 将当前运行时统一到 `src/simready/`，以职责划分模块。发行包仍叫 `simready-pipeline`，重新安装后获得 `simready` 命令。Python import 路径有破坏性变更；旧命令通过薄转发层兼容。

```bash
python -m pip install -e '.[test,dev]'
simready --help
```

## 包与公开接口

| 旧接口 / 模块 | 新接口 / 模块 |
|---|---|
| `simready_bridge.pipeline.run_campaign` | `simready.workflows.run_repair_workflow` |
| `simready_bridge.infra.run` | `simready.execution.run_task_graph` |
| `simready_bridge.agent` | `simready.agents`；具体供应商放 `providers/` |
| `AgentProvider` / `Decision` / `ProviderResult` | `DecisionProvider` / `RepairDecision` / `DecisionResult` |
| `create_provider` | `create_decision_provider` |
| `CodexProvider` / `ResponsesProvider` / `RuleProvider` | `CodexCliProvider` / `OpenAIResponsesProvider` / `RuleBasedProvider` |
| `simready_bridge.plugins.PluginRegistry` | `simready.repair.RepairPluginRegistry` |
| `PluginRegistry.proposals` / `.compile` | `RepairPluginRegistry.propose_repairs` / `.build_task_plan` |
| `simready_bridge.benchmark.evaluator.evaluate` | `simready.evaluation.scoring.evaluate_candidate` |
| `simready_bridge.benchmark.comparison.compare` | `simready.evaluation.regression.compare_scorecards` |
| `simready_bridge.pipeline.verification.verify` | `simready.evaluation.verification.verify_candidate` |
| `simready_bridge.benchmark.scheduler.plan` | `simready.repair.planning.plan_repairs` |
| `simready_bridge.memory.MemoryStore` | `simready.experience.EpisodeStore` |
| `simready_bridge.skills.SkillCatalog` | `simready.experience.SkillLibrary` |
| `simready_bridge.ingest.frames` | `simready.ingestion.video_frames` |
| `simready_bridge.geometry` | `simready.geometry.cameras` |
| `simready_bridge.artifacts` | `simready.core.artifacts` |
| benchmark / pipeline 中的 evidence helpers | `simready.core.evidence` |
| benchmark 中的基础 validation helpers | `simready.core.validation` |
| `simready_bridge.process.ProcessTimeout` | `simready.execution.processes.ProcessTimeoutError` |

`run_repair_workflow` 的关键字参数是 `base_dir`、`output_dir`、`decision_provider`；替换旧 `base`、`out`、`provider`。`run_task_graph` 使用 `base_dir`、`output_dir`、`cache_dir`，原位置参数顺序保持。

SQLite 连接统一由 `ExperienceDatabase.transaction()` 管理。扩展代码若使用过 `MemoryStore.connect()`，改用 `EpisodeStore.database.transaction()`；业务调用优先使用 EpisodeStore / SkillLibrary，避免直接依赖表结构。

## 命令迁移

| 旧命令（仍可转发） | 推荐命令 |
|---|---|
| `simready-ingest --video ... --out ...` | `simready ingestion extract --video ... --out ...` |
| `simready-pipeline run --config ... --out ...` | `simready workflows run --config ... --out ...` |
| `simready-pipeline doctor` | `simready agents doctor` |
| `simready-pipeline skill-show --memory ... --skill-id ...` | `simready experience skills show --database ... --skill-id ...` |
| `simready-pipeline skill-promote ...` | `simready experience skills promote ...` |
| `simready-pipeline skill-retire ...` | `simready experience skills retire ...` |
| `simready-infra --plan ... --inventory ... --out ... --cache ...` | `simready execution run --plan ... --inventory ... --out ... --cache ...` |
| `simready-benchmark run ...` | `simready evaluation run ...` |
| `simready-benchmark worker ...` | `simready evaluation worker ...` |
| `simready-benchmark plan --registry ... --defects ... --out ...` | `simready repair plan --catalog ... --defects ... --out ...` |

原 `--memory` 和 `--registry` 参数仍分别作为 `--database` 和 `--catalog` 的别名。`python -m simready <domain> <action>` 与统一命令等价；旧 `python -m simready_bridge...` 不保留。

## 数据与证据

- workflow、protocol、candidate、任务、插件及 SQLite schema 均保留原版本；不批量改写历史运行目录和经验数据库。
- workflow JSON 中的 `agent`、`registry`、`memory` 等字段保持兼容。配置文件不要求固定名字，旧 `campaign.json` 仍可通过 `--config` 显式传入；新示例与运行配置快照使用 `workflow.json`。
- 验收版本仍为 `real2sim-bench/0.3`，阈值与指标定义不因重命名变化。`evaluator_files` 与 verifier 快照以模块相对路径为键，避免两个 `__init__.py` 相互覆盖；解析此字典的外部代码不能假定键只含文件名。
- 模块身份变化使任务缓存和 Skill 兼容性键变化；旧记录保留，但不会自动跨代码身份复用。需要在新版本重新执行、验收和晋升。
- 示例注册表记录脚本位置；迁移检出目录后应重新生成示例输入。自己维护的任务 argv / inputs 中若写有仓库旧路径，也需按下表更新。

## 配置、示例与历史脚本

| 旧路径 | 新路径 |
|---|---|
| `configs/benchmark/{suite,asset_consistency}.v1.json` | `configs/evaluation/` 下同名文件 |
| `configs/benchmark/plugin_registry.v1.json` | `configs/repair/adapter_catalog.v1.json`（仅规划） |
| `examples/agent/run_demo.py` | `examples/repair_workflow/run_learning_demo.py` |
| `examples/agent/fixture_worker.py` | `examples/repair_workflow/synthetic_renderer.py` |
| `examples/infra/make_demo.py` | `examples/task_execution/prepare_demo.py` |
| `examples/infra/fixture_worker.py` | `examples/task_execution/synthetic_task.py` |
| `examples/benchmark/make_fixture.py` | `examples/scene_evaluation/prepare_fixture.py` |
| `scripts/benchmark_infra.py` | `scripts/benchmark_task_execution.py` |
| `legacy/s0_ingest/` | `legacy/video_ingestion/` |
| `legacy/s1_aholo/` | `legacy/aholo_reconstruction/` |
| `legacy/rsi_3dgs/` | `legacy/gaussian_repair/` |
| `legacy/scene_bench/` | `legacy/scene_evaluation/` |
| `legacy/warehouse_3dgs/` | `legacy/warehouse_experiments/` |
| `legacy/submission_template/` | `legacy/hackathon_submission/` |

历史源码在 sdist 中保留，不打进运行时 wheel。历史算法的 CPU 回归继续执行；目录迁移不代表 GPU/Isaac 业务链已重新验证。模块职责见 [架构说明](architecture.md)，新增代码遵循 [开发规范](development.md)。
