# v0.4 模块化与命名审查

日期：2026-11-12。基线：v0.3，提交 `c9a59e2`。范围：仓库运行时、历史源码、示例、测试、配置、打包与当前文档；未修改外部真实场景资产或重新执行 GPU/Isaac 重建。

## 发现与改动

| 原问题 | 处理 | 防止回归 |
|---|---|---|
| 包名 `simready_bridge`、发行名、多个命令不一致 | 统一 Python 包和 CLI 为 `simready`，发行名保留兼容 | 安装测试、旧命令转发测试、迁移表 |
| `pipeline/runner` 与 `infra/runner` 难区分 | `workflows/repair_loop` 管修复闭环；`execution/task_graph` 管任务 DAG | 明确导入方向，禁止宽泛容器文件名 |
| benchmark 内混入修复 scheduler | 移到 `repair/planning`；验收、回归判断、独立 verifier 留在 `evaluation` | `evaluation → repair/agents/workflows` 的反向依赖会失败 |
| Skill 与 Memory 的数据库归属不清 | 合为 `experience`，数据库 schema/事务由 `database.py` 拥有 | 原证据校验、并发写入、篡改与晋升回归测试保留 |
| 业务流程混合多个层次 | 提取 `repair_step` 与 `task_worker`；provider 提示与 usage 处理分开 | 既有工作流失败、预算、回退与缓存测试 |
| 基础校验藏在 benchmark 中，被其他模块间接引用 | 迁到 `core.validation` / `core.evidence` | `core` 禁止引用业务模块 |
| `s0/s1` 和场景实验名被当作当前架构入口 | 历史目录按用途改名，统一隔离在 `legacy` | runtime 禁止导入历史、示例和测试模块 |
| API 多为 `run/plan/compile`，意图和参数含糊 | 改为 `run_repair_workflow/run_task_graph/plan_repairs/build_task_plan` 等 | 七个声明的公开边界函数检查注解与职责说明 |
| 测试横向互相导入 fixture，目录与业务无对应 | 公共 fixture 独立；测试按领域组织 | 安装包、跨目录 CLI 与架构反例测试 |
| 历史审计混在当前说明中 | 当前文档按架构/运行/执行/验收/开发/迁移组织，旧审计归档 | README 单一入口与相对链接核验 |

验收源码身份改为保留模块相对路径，避免 `core/__init__.py` 与 `evaluation/__init__.py` 的同名覆盖。完整回归发现了快照子目录未创建的问题，已修复，并新增同名模块快照及哈希完整性测试。

## 验证结果

本地 Python 3.14.6；CI 配置在 Python 3.10 / 3.12 执行同套检查。

- **139 项测试通过**，包含原有 118 项和新增架构/CLI/快照回归。实际测试覆盖故障和证据约束，不仅检查文件改名。
- Ruff 命名、导入、常见错误和格式检查通过；legacy 严重错误与格式检查通过。
- 架构检查通过：9 个领域、7 个声明的公开函数、无声明依赖环、无非法静态跨域 import。
- [源码清单](source_inventory-v0.4.json)：140 个 Python 文件完成 AST 解析、编译和 SHA256 记录；其中运行时 61、历史实验 52、示例 9、测试 15、工具脚本 3。全文件静态扫描不等于逐条路径的运行证明。
- sdist 与 wheel 构建成功。干净环境只安装 wheel、无可选依赖，从仓库外验证 8 个统一入口帮助、4 个旧命令入口和公开 import；wheel 仅包含 `simready` 和发行元数据。
- 三个合成场景各一轮通过：第一场景产生候选 Skill，第二个 validation 场景使其晋升，第三场景检索并复用。规则 provider 不调用 LLM，token 计数为 0。
- 四场景、16 个合成 CPU 任务在串行、并行与热缓存模式下输出哈希一致；热缓存命中 16 项。该检查证明调度与缓存兼容，不代表真实重建加速比例。

## 兼容边界与未覆盖项

旧 console 命令继续转发，Python `simready_bridge` 导入路径已移除，迁移见 [完整映射](../migration.md)。既有 JSON / SQLite schema、验收指标和阈值保持。源码身份变化会使旧缓存与旧 Skill 不再自动跨版本复用，但不会修改旧证据。

本轮未执行真实 Aholo 请求、Codex/Responses 付费调用、GPU 训练、Isaac 渲染/物理或机器人控制。真实 worker 尚未全部接入；合成测试与静态扫描不能保证不存在 bug，也不能证明真实场景达到交付质量。新进程验收仍是状态分离，生产发布所需权限隔离仍待实现。
