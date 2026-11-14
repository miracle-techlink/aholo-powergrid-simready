# 核心开发规范

- 运行时按[八个领域](architecture.md)组织；正式代码不导入 tests、examples、完整分支或历史包。跨域 import 必须满足[依赖合同](../configs/architecture/module_boundaries.json)。
- CLI 仅处理参数、调用和退出码；`--help` 不加载可选模型或启动 SDK。模型依赖留在隔离 worker。
- 新 baseline 优先复用 command 配置和 reconstruction bundle；新增资产表示需有真实转换及独立读回，不先建立空抽象。
- 公开函数写明类型与职责；文件按对象／行为命名，单位进入字段名。不得悄悄改变既有 schema、质量阈值或数据语义。
- 共享合成输入放 tests/fixtures；测试验证失败处理、数据绑定、取消、恢复或升级，不从另一测试文件导入夹具。
- 通用改进进入 main；机器人专用演示、Agent/Skill、活动资料及研究脚本在 aloho-hackson 维护。修复跨分支复用时单独审查依赖，不整分支回合并。

```bash
python -m pip install -e '.[dev,test,physical-assets]'
ruff check src/simready tests examples scripts deploy
ruff format --check src/simready tests examples scripts deploy
python scripts/check_architecture.py
python -m pytest -q
python -m build
```

CI 的 CPU 检查不替代模型精度或 GPU 运行。涉及镜像、Kit 或渲染时，额外保存实际镜像 ID、输入、版本、日志与验收结果。PR 描述解释行为、兼容变化和验证边界，经过[合并门槛](ci-cd.md)后进入 main。
