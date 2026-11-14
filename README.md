# Video to SimReady

核心 video → reconstruction → USD pipeline，以及隔离的 Isaac Sim 容器部署。

[![CI](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml/badge.svg?branch=main)](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml) · [工程指南：架构、测试与扩展](docs/README.md)

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

此示例使用合成输入，验证 USD 导出、独立参数读回和恢复。真实视频与重建复用见[视频示例](examples/pipeline/README.md#真实视频)，多算法对比见[baseline 矩阵](examples/reconstruction/README.md#比较后端)。

## Isaac Sim worker

```bash
docker build -t simready-isaac-core:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
python scripts/launch_isaac.py --assets examples/pipeline --out runs/isaac-check \
  --doctor --launch-smoke --accept-eula
```

执行前准备 Docker、NVIDIA Container Toolkit、适用的 NVIDIA 许可与 GPU 环境。源码与输入只读挂载；镜像不包含场景、模型权重或密钥。环境身份和运行约束见[容器与运行](docs/README.md#容器与运行)。

## 开发边界

模块分工、baseline 接入、测试与合并流程统一见[工程指南](docs/README.md)。公开示例验证接口与恢复，完整几何、真实物性与模型精度需各自评测。唯一命令入口为 `simready`；专用场景、Agent 及旧 CLI 别名使用完整分支。
