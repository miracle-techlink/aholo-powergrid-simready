# 开发规范

## 命名

- Python 包名统一 `simready`，命令统一 `simready <domain> <action>`。发行包保留 `simready-pipeline`。
- 目录按职责命名：`ingestion`、`assets`、`simulation`、`workflows`、`agents`、`repair`、`execution`、`evaluation`、`experience`、`geometry`、`core`。不在当前运行时增加 `s0_*`、具体场景名或宽泛的 `infra/bridge`。
- 文件名用 `snake_case`，说明对象或行为：`task_graph.py`、`task_worker.py`、`repair_loop.py`、`video_frames.py`。禁止新增 `runner.py`、`scheduler.py`、`utils.py`、`helpers.py`、`common.py`、`manager.py` 作为职责不明的容器。
- 类用 `PascalCase`，体现角色：`CodexCliProvider`、`RepairPluginRegistry`、`EpisodeStore`、`SkillLibrary`。异常类以 `Error` 结尾。
- 公开函数用“动词 + 对象”：`run_task_graph`、`evaluate_candidate`、`verify_candidate`、`plan_repairs`。文件上下文清楚的内部数学函数可以用领域术语；不要把矩阵变量机械扩写成长句。
- 路径参数表明含义，如 `base_dir`、`output_dir`、`cache_dir`；带单位的数值用 `_s`、`_gb`、`_m` 等后缀。JSON 的既有 schema 字段不能仅为改名而悄悄破坏兼容性。

## 模块与接口

1. 一个模块拥有一类状态与行为。数据库 schema 属于 `experience.database`；执行计划属于 `execution`；验收规则属于 `evaluation`。
2. 使用明确的绝对 import。跨域依赖必须符合 [架构合同](../configs/architecture/module_boundaries.json)。禁止 runtime 导入 `legacy`、`tests`、`examples` 或旧包名；不修改 `sys.path`。
3. 公开边界函数必须写参数/返回类型及职责 docstring。`JsonObject` 表示外部数据边界，具体字段在加载后必须验证。不要用类型注解替代运行时验证，也不要为假类型安全大量添加 `Any` 或忽略规则。
4. 长流程按独立职责拆开。主循环管状态；单轮实现管执行与验收；基础工具不依赖上层业务。避免为了层数好看创建空 service/interface/repository 目录。
5. `cli.py` 只处理参数、调用和退出码；`__main__.py` 最多 8 行，只委托 `cli.main`。提供 `--help` 不应加载可选模型或启动外部服务。
6. Agent 供应商实现放 `agents/providers/`，统一 `DecisionProvider` 协议。重建方法通过 `ingestion/reconstruction_providers` 的合同接入；新增外部算法优先使用冻结的 command 配置和输出适配。具体模型依赖留在隔离 worker，不能放进宿主 CLI。
7. 历史实现只在 `legacy/` 维护，命名说明用途；迁移成正式 worker 必须有明确输入、输出、资源、环境身份、验收证据与测试。

## 代码风格与检查

Ruff 固定版本，使用 `E4/E7/E9/F/I/N/B/UP`：基础错误、导入排序、命名、常见缺陷和现代 Python 写法。格式统一由 Ruff 生成。历史代码执行严重错误检查与格式检查，不为了通过新命名规则对实验算法做无证据的大改。

```bash
ruff check src/simready tests examples scripts
ruff check legacy --select E9,F63,F7,F82
ruff format --check src/simready tests examples scripts legacy
python scripts/check_architecture.py
python -m pytest -q
python -m build
```

`check_architecture.py` 检查目录清单、模块命名、import 方向、依赖环、入口厚度和公开函数签名。测试会故意引入反向依赖、旧包名及缺失注解，确保检查器确实拒绝这些回归。它是静态规则检查，不能防御刻意构造的动态导入；代码审查仍需检查此类绕过。

## 测试与变更

`tests/<domain>/` 对应同名运行时模块，公共合成输入放 `tests/fixtures/`；不要从另一个测试文件导入 fixture。架构和 CLI 兼容测试放 `tests/architecture/`，旧算法 CPU 回归放 `tests/legacy/`。

测试应验证行为、失败处理、证据一致性或兼容性。重构必须保留已有质量阈值与数据语义。涉及 GPU/外部 API 的效果，需记录实际运行证据；合成示例或 mock 通过不等同于真实场景验收。

PR 描述写清职责变化、旧→新 API、兼容边界和验证结果。迁移表同步到 `docs/migration.md`，当前架构同步到 `docs/architecture.md`；旧审计报告留在 `docs/audits/`，不改写历史结果。

PR 自动执行静态检查、Python 3.10 / 3.12 CPU 回归与隔离 wheel 验证；`merge-gate` 要求所有检查成功。main 保护规则、合并顺序和合并后构包交付见 [CI/CD 流程](ci-cd.md)。工作流及保护配置随代码审查，CPU 检查不替代 GPU／外部模型验收。
