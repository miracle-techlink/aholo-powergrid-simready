# Isaac Sim 部署与运行

## 环境分层

1. **编排 Python**：3.10+，安装本仓库即可检查配置、哈希和计划，不需要 CUDA。
2. **图形 worker**：Isaac Sim 6.1.0 / Python 3.12 / NVIDIA 驱动、RTX 能力；负责星巴克视频和物理轨迹回放。
3. **物理 worker**：在图形环境增加 Isaac Lab 3.0.0、PhysX 后端、ONNX Runtime、OpenCV、SciPy，负责 Lite3+Z1。
4. **云重建**：普通 CPU / 网络节点，`.[aholo]` 与 `AHOLO_API_KEY`。与本地 RTX 容器解耦，避免 GPU 空等远端任务。

受支持 GPU 与容器要求以 [NVIDIA 系统要求](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html) 和 [官方容器安装](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/install_container.html) 为准。本项目固定已用的 6.1.0，不自动切换 latest。A100、H100 没有 RT Cores，不属于官方支持的 Isaac Sim RTX 渲染硬件。

资产迁移范围见 [场景准备说明](../examples/scenes/README.md)。自定义 MDL 仅支持 UTF-8 自包含模块，带 `import`、限定模块名或资源类型的模块明确拒绝；本地文件优先，只有 SDK 白名单 `OmniPBR.mdl` 可保留外部依赖。导出器不解析 MDL 内部依赖或编译材质，资产打包成功仍需目标环境的场景验证。

## Docker 本地启动

先安装 Docker 与 NVIDIA Container Toolkit，完成 NVIDIA 镜像许可与必要的 NGC 登录。仓库不保存登录信息。

```bash
docker pull nvcr.io/nvidia/isaac-sim:6.1.0
docker build -t simready-isaac:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
# 仅渲染星巴克可用较小的 render 阶段：
docker build --target render -t simready-isaac-render:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
mkdir -p runs/docker-check
python scripts/launch_isaac.py --backend docker --assets data/substation \
  --out runs/docker-check --doctor --require-lab --launch-smoke --accept-eula
python scripts/launch_isaac.py --backend docker --assets data/substation \
  --out runs/substation-001 --config examples/scenes/substation.json --accept-eula
```

`--doctor` 检查环境，`--require-lab` 增加物理 worker 依赖检查；`--launch-smoke` 启动 Kit 并执行 10 次 update。仅验证安装不等于场景或物理验收。`--dry-run` 打印启动命令；不启动容器、不接受许可、不提交云任务。

输入挂载 `/workspace` 和 `/assets` 为只读，输出写入 `/output/delivery`。以宿主 UID/GID 运行，并增加官方镜像的 1234 组以读取 `/isaac-sim`；用户目录位于 `/output/.isaac-home`；Kit cache/data/logs 分别挂载到 `.isaac-cache`、`.isaac-data`、`.isaac-logs`，避免以宿主 UID 写入 SDK 只读目录。每次使用唯一容器名；中断只停止该次创建的容器。默认没有宿主 Docker socket、特权模式或图形显示共享。

每次任务使用新的输出目录；已有 `delivery/` 时拒绝再次运行。输出不能与资产目录重叠，也不能等于或包含源码目录，可放在源码内的 `runs/` 下。首次启动写入独占的 `launch.json`，后续环境检查或失败重试另写 `launch-UUID.json`，保留启动记录历史。

镜像不包含场景数据、模型权重、API 密钥或用户账号。镜像首次构建与冷启动单独计时；场景交付记录不混入安装时间。若目标镜像更改了默认 UID/GID、SDK 路径或 Python ABI，先修改部署合同并重新做 doctor。

物理镜像在安装 Isaac Lab 后运行 [repair_sdk_urllib3.py](../deploy/isaac/repair_sdk_urllib3.py)，将 SDK 中受依赖升级影响的 urllib3 副本替换为完整安装分发，并逐文件核对。版本与替换路径记录在镜像内 `/opt/simready/sdk_urllib3_repair.json`；不将不同版本的零散文件混合。镜像重建后的验证范围需重新记录，不能直接沿用旧镜像的任务结果。

## SSH 服务器运行

服务器先准备同版本代码、镜像及资产。以下代码同步命令在干净的源码检出中执行，排除本地环境和凭据文件；不使用 `--delete`，不会清理服务器已有文件。

```bash
# rtx-server 是 ~/.ssh/config 中的服务器别名。
ssh rtx-server 'mkdir -p /srv/simready /srv/data/substation /srv/runs'
rsync -a --exclude=.git --exclude=.venv --exclude=runs --exclude=data \
  --exclude=dist --exclude=build --exclude=.codex --exclude='.env*' \
  --exclude=auth.json --exclude='*_auth.json' --exclude='storage_state*.json' \
  ./ rtx-server:/srv/simready/
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

本次核验记录见 `docs/report/packaging-validation.json`。本地资产包导出、相对 USD 引用、CPU 测试与启动命令检查使用真实文件执行；Docker 的实测范围按该记录列出。远端 SSH 尚未执行完整任务，因此不报告远端任务成功、耗时或多 GPU 加速比。容器启动和打包的功能验证与已暂停的优化实验分开记账。

| 核验对象 | 完成范围 | 未覆盖范围 |
|---|---|---|
| 最终 Lite3+Z1 接口 | Docker 物理过程 28.508 秒模拟时间，移动约 1.294544 m，一次接触按压，20/20 项审计通过 | 使用依赖修复前镜像与已有资产包，未重录视频 |
| G2 初版接口 | 640×360、30 fps、8 帧 Docker 预览 | 非完整 4K 交付，非物理导航 |
| G2 最终接口 | 真实迁移资产上的 8 个对应 USD 位姿核对 | 未重复 GPU 渲染 |
| 最终物理镜像 | urllib3 完整分发校验，`doctor --require-lab --launch-smoke` 通过 | 未重跑完整物理任务和审计 |
| 最终导出器 | 两个真实场景重新导出，迁移后 USD 依赖闭包在 CPU 检查通过 | 新资产包未重复 GPU 任务 |
| SSH / Aholo | SSH 命令构造与引用检查；Aholo 一次 GET 及旧 receipt 续接 | 无远端连接或任务，无新视频云重建 |

最终接口物理回归的容器启动总 wall 时间为 704.019 秒，其中物理 699.400 秒、审计 3.869 秒，包含冷启动 shader 编译，不与本机旧运行作加速比较。完整物理结果绑定镜像 `67bd8d315c87`；修复后的镜像 `97b65e8d0f56` 仅有上述有限启动核验。最终接口、最终镜像与最终导出包的组合没有重新完成全套 GPU 工作流，清单分别保留各次输入和代码哈希。

已有资产的本机完整复现总 wall 时间为 756.632 秒，其中抽帧 8.353 秒、物理 297.568 秒、审计 4.267 秒、录像 446.381 秒，其余为编排开销。该运行不含新视频云重建或资产再处理，不作为全链路加速结果。统一机器人设备接口是在该次完整复现后新增，后续接口核验单独记录，该次完整运行仅代表接口接入前的封装版本。

部署新机器人时，SDK 可导入 USD 只证明资产可加载。还需按 [机器人接入说明](../examples/robots/README.md) 绑定关节和驱动，核对米/弧度与四元数约定，标定安装变换、传感器和 TCP，并通过稳定性、可达性和接触审计。G2 演示当前没有物理导航驱动；Lite3+Z1 的已接受控制流程也不能自动迁移到任意构型。

## 错误定位

- `Permission denied /isaac-sim`：检查镜像 1234 组权限与 `--group-add`；不要对宿主目录做全局 chmod。
- `File doesn't exist: isaacsim.exp.base.kit`：SDK 需要完整 experience 路径；图形 worker 根据 `EXP_PATH` 定位。
- `isaaclab_physx` 缺失：仅有基础 Isaac Sim 镜像不足以运行该物理脚本；使用包含匹配 Isaac Lab 的派生镜像。
- bundle 哈希不符：重新导出原始资产包；不要把 receipt 中的哈希改成“通过”。
- 物理审计失败：保留 `result.json`、`sensors.jsonl`、RGB-D 与轨迹；停止交付录像，检查场景、接触和环境版本。
