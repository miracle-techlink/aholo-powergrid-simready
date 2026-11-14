# Video to SimReady

**从真实场景视频，到可交互、可验证的机器人仿真环境。**

[![CI](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml) [![Python](https://img.shields.io/badge/Python-3.10%2B-3776AB)](pyproject.toml) [![License](https://img.shields.io/badge/License-MIT-blue)](LICENSE)

[文档导航](docs/README.md) · [Pipeline 配置](docs/pipeline.md) · [模块架构](docs/architecture.md) · [能力与待办](docs/release-status.md) · [部署](docs/deployment.md)

系统把视频观测、三维重建、资产编译、仿真和独立验收分层，保存输入身份、参数来源、阶段回执和失败证据。当前通用入口生成 Gaussian PLY 对应的 USD 场景候选；完整资产、视觉保真度和机器人任务分别验收。

## 演示

| 变电站 · Lite3 + Z1 | 星巴克 · G2 |
|---|---|
| [![变电站任务画面](docs/report/figures/substation_navigation.jpg)](examples/scenes/README.md) | [![星巴克展示画面](docs/report/figures/starbucks.jpg)](examples/starbucks_simready/README.md) |
| 物理按压、独立任务审计与轨迹回放 | 已准备场景中的编排巡览 |

[双桌搬杯](examples/starbucks_grasp/README.md)另有固定基座抓取、放置与接触验收。以上案例依赖专用资产与机器人配置；通用新视频入口尚未自动达到同等交付质量。[结果与限制](docs/release-status.md)分别记录。

## 快速开始：CPU 场景与恢复

需要 Python 3.10+ 和系统 FFmpeg；该示例不需要 GPU、模型账号或私有资产。

```bash
git clone https://github.com/miracle-techlink/video-to-simready.git
cd video-to-simready
python -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[physical-assets]'
python examples/pipeline/create_fixture.py --out /tmp/simready-inputs
simready run --pipeline /tmp/simready-inputs/pipeline.json --out /tmp/simready-run
simready run --pipeline /tmp/simready-inputs/pipeline.json --out /tmp/simready-run --resume
```

示例使用合成视频和高斯，验证 USD 导出、参数读回和恢复合同。运行摘要为 `/tmp/simready-run/summary.json`。新输入使用新目录；配置检查、阶段停止和局部升级见 [Pipeline 指南](docs/pipeline.md)。

## 真实视频与重建后端

规范配置支持 `artifact`（导入已有结果）、`aholo`（云任务）和 `command`（外部算法）。新增 baseline 通过统一 bundle 接入，可用 [baseline 矩阵](examples/baselines/README.md)冻结输入并比较；示例跑通不代表真实模型精度已评测。

**历史案例的上游三维重建使用群核科技 Aholo API；本项目负责重建结果到仿真资产、机器人交互和独立验收。** Aholo 的模型训练由服务方提供，本仓库开源客户端和下游工程。

- [Aholo 平台与 Key](https://labs.aholo3d.cn/) · [官方接口文档](https://labs.aholo.ai/api-docs/intro)
- [本项目客户端](src/simready/ingestion/aholo_client.py) · [提交与恢复](src/simready/ingestion/aholo_reconstruction.py)
- [真实视频用法](examples/new_video/README.md) · [本地算法适配示例](examples/reconstruction_backends/README.md)

新云提交须显式选择 `--submit-reconstruction`；Isaac/RTX 运行另需匹配环境、资产和许可证确认，见[部署指南](docs/deployment.md)。旧 `--video`、`--scene` 入口保持兼容。

## 示例与扩展

| 目标 | 入口 |
|---|---|
| 已有变电站／星巴克场景 | [场景准备与启动](examples/scenes/README.md) |
| 多类语义、逐物体物性 | [物性流程](docs/physical-assets.md) · [CPU 语义夹具](examples/semantic_assets/README.md) |
| 物性估计与搬杯评测 | [NeRF2Physics 接线](docs/nerf2physics.md) · [鲁棒性评测](docs/cup-robustness.md) |
| Agent 修复与经验复用 | [运行手册](docs/runtime.md)；合成演示：`python -m examples.repair_workflow.run_learning_demo --out runs/repair-demo` |
| 新机器人 | [设备端口与适配](examples/robots/README.md) |
| 独立验收 | [评测协议](docs/evaluation.md) |

物性参数区分先验、推断与测量；当前语义代理不等于完整封闭网格。修复插件和机器人任务按配置组合，未对任意新视频默认全自动执行。

## 开发与交付

```bash
python -m pip install -e '.[test,dev,physical-assets]'
python scripts/check_architecture.py
python -m pytest -q
```

[CI/CD](docs/ci-cd.md)要求静态检查、Python 3.10/3.12 完整测试和隔离 wheel 验证全部通过后合并；main 复验后交付带校验和的安装包。GPU 与真实模型结果单独保存。新增功能按[开发规范](docs/development.md)和[模块边界](docs/architecture.md)接入。

历史实验、白皮书和已退役工具见[历史索引](docs/history.md)。问题可在 [Issues](https://github.com/miracle-techlink/video-to-simready/issues) 提交。
