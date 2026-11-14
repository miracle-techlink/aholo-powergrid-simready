# Video to SimReady

核心 video → reconstruction → USD pipeline，以及隔离的 Isaac Sim 容器部署。

[![CI](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml) · [文档](docs/README.md) · [Pipeline](docs/pipeline.md) · [容器部署](docs/deployment.md)

| 分支 | 内容 |
|---|---|
| `main` | 核心流水线、baseline 接入、资产与物性、独立验收、执行设施、Isaac Sim 部署及必要测试 |
| [`aloho-hackson`](https://github.com/miracle-techlink/video-to-simready/tree/aloho-hackson) | 分离前的完整版本：Aholo 黑客松、专用机器人任务、Agent 修复／经验、研究代码、视频物料与历史报告 |

main 保留 `artifact`、`command`、`aholo` 重建后端。Aholo 云端模型由服务方提供，本仓库实现客户端与下游处理；其他算法通过冻结输入、环境身份和统一 bundle 接入。

## CPU 快速开始

需要 Python 3.10+ 与系统 FFmpeg；无需 GPU、账号或私有数据。

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

此示例使用合成输入，验证 USD 导出、独立参数读回和恢复。真实视频与重建复用见[视频示例](examples/new_video/README.md)，多算法对比见[baseline 矩阵](examples/baselines/README.md)。

## Isaac Sim worker

```bash
docker build -t simready-isaac-core:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
python scripts/launch_isaac.py --assets examples/pipeline --out runs/isaac-check \
  --doctor --launch-smoke --accept-eula
```

执行前准备 Docker、NVIDIA Container Toolkit、适用的 NVIDIA 许可与 GPU 环境。源码与输入只读挂载；镜像不包含场景、模型权重或密钥。完整步骤、镜像身份及 GPU 验收见[部署指南](docs/deployment.md)。

## 开发边界

八个领域的依赖由[架构合同](docs/architecture.md)与 CI 约束。配置、阶段恢复和局部升级见 [Pipeline](docs/pipeline.md)；新增模型保留独立 worker 环境。公开示例用于合同验证，完整几何、真实物性与模型精度仍需各自评测。

v0.9 移出了专用场景与 Agent 命令，迁移到完整分支或核心入口见[迁移说明](docs/migration.md)。PR 必须通过双 Python 回归、隔离 wheel 流水线和合并门槛；main 交付绑定提交身份的安装包。
