# Isaac Sim 部署与运行

## 环境分层

1. **编排 Python**：3.10+，安装本仓库即可检查配置、哈希和计划，不需要 CUDA。
2. **图形 worker**：Isaac Sim 6.1.0 / Python 3.12 / NVIDIA 驱动、RTX 能力；负责星巴克视频和物理轨迹回放。
3. **物理 worker**：在图形环境增加 Isaac Lab 3.0.0、PhysX 后端、ONNX Runtime、OpenCV、SciPy，负责 Lite3+Z1。
4. **云重建**：普通 CPU / 网络节点，`.[aholo]` 与 `AHOLO_API_KEY`。与本地 RTX 容器解耦，避免 GPU 空等远端任务。

受支持 GPU 与容器要求以 [NVIDIA 系统要求](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html) 和 [官方容器安装](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html) 为准。本项目固定已用的 6.1.0，不自动切换 latest。A100、H100 没有 RT Cores，不属于官方支持的 Isaac Sim RTX 渲染硬件。

## Docker 本地启动

先安装 Docker 与 NVIDIA Container Toolkit，完成 NVIDIA 镜像许可与必要的 NGC 登录。仓库不保存登录信息。

```bash
docker pull nvcr.io/nvidia/isaac-sim:6.1.0
docker build -t simready-isaac:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
# 仅渲染星巴克可用较小的 render 阶段：
docker build --target render -t simready-isaac-render:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
mkdir -p runs/docker-check
python scripts/launch_isaac.py --backend docker --assets data/substation \
  --out runs/docker-check --doctor --launch-smoke --accept-eula
python scripts/launch_isaac.py --backend docker --assets data/substation \
  --out runs/substation-001 --config examples/scenes/substation.json --accept-eula
```

`--doctor` 检查环境；`--launch-smoke` 启动 Kit 并执行 10 次 update。仅验证安装不等于场景或物理验收。`--dry-run` 打印启动命令；不启动容器、不接受许可、不提交云任务。

输入挂载 `/workspace` 和 `/assets` 为只读，输出写入 `/output/delivery`。以宿主 UID/GID 运行，并增加官方镜像的 1234 组以读取 `/isaac-sim`；用户目录位于 `/output/.isaac-home`；Kit cache/data/logs 分别挂载到 `.isaac-cache`、`.isaac-data`、`.isaac-logs`，避免以宿主 UID 写入 SDK 只读目录。每次使用唯一容器名；中断只停止该次创建的容器。默认没有宿主 Docker socket、特权模式或图形显示共享。

镜像不包含场景数据、模型权重、API 密钥或用户账号。镜像首次构建与冷启动单独计时；场景交付记录不混入安装时间。若目标镜像更改了默认 UID/GID、SDK 路径或 Python ABI，先修改部署合同并重新做 doctor。

## SSH 服务器运行

服务器先准备同版本代码、镜像及资产。可使用下面的显式同步命令；不使用 `--delete`，不会清理服务器已有文件。

```bash
# rtx-server 是 ~/.ssh/config 中的服务器别名。
ssh rtx-server 'mkdir -p /srv/simready /srv/data/substation /srv/runs'
rsync -a --exclude=.git --exclude=.venv --exclude=runs --exclude=data \
  --exclude=dist --exclude=build ./ rtx-server:/srv/simready/
rsync -a data/substation/ rtx-server:/srv/data/substation/
ssh rtx-server 'cd /srv/simready && python3 -m venv .venv && .venv/bin/pip install -e .'
ssh rtx-server 'cd /srv/simready && docker build -t simready-isaac:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac'
python scripts/launch_isaac.py --backend ssh --ssh-host rtx-server \
  --repo /srv/simready --assets /srv/data/substation --out /srv/runs/substation-001 \
  --config examples/scenes/substation.json --accept-eula
rsync -a rtx-server:/srv/runs/substation-001/delivery/ runs/downloaded-substation/
```

SSH 传输只负责执行一个已引用且正确转义的命令，不是分布式集群调度器。输入包和 output 路径都解释为服务器路径。长时间运行建议在服务器的 `tmux`/作业系统中执行同一条 Docker 命令；当前 SSH 连接中断不提供跨节点作业现场恢复。已有 Aholo receipt 的网络恢复是独立功能。

## 已验证与未验证

本次核验记录见 `docs/report/packaging-validation.json`。本地资产包导出、相对 USD 引用、CPU 测试与启动命令检查使用真实文件执行；Docker 的实测范围按该记录列出。远端服务器尚未重新执行完整任务，因此不报告远端耗时或多 GPU 加速比。容器启动和打包的功能验证与已暂停的优化实验分开记账。

## 错误定位

- `Permission denied /isaac-sim`：检查镜像 1234 组权限与 `--group-add`；不要对宿主目录做全局 chmod。
- `File doesn't exist: isaacsim.exp.base.kit`：SDK 需要完整 experience 路径；图形 worker 根据 `EXP_PATH` 定位。
- `isaaclab_physx` 缺失：仅有基础 Isaac Sim 镜像不足以运行该物理脚本；使用包含匹配 Isaac Lab 的派生镜像。
- bundle 哈希不符：重新导出原始资产包；不要把 receipt 中的哈希改成“通过”。
- 物理审计失败：保留 `result.json`、`sensors.jsonl`、RGB-D 与轨迹；停止交付录像，检查场景、接触和环境版本。
