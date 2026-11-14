# Isaac Sim 部署与运行

## 新视频执行

宿主安装 `.[ingest,aholo]` 与 ffmpeg 后，在相同的完整镜像中构建新场景：

```bash
simready run --video /path/to/input.mp4 --out runs/new-scene \
  --submit-reconstruction --accept-eula
```

重建凭据保存在运行目录；API key 只从宿主环境读取，不写入镜像或worker输入。新视频模式的云重建客户端在宿主 CPU 侧执行上传、监控和下载，重建计算由供应商服务执行，随后将已验证PLY与场景规格作为只读输入送入Isaac worker。worker依次导出USD、独立检查结构、渲染巡览。完整参数、CPU导出、恢复和服务器命令见[新视频示例](../examples/new_video/README.md)。

`--scene-spec`填写坐标/尺度来源和显式对象，不是把旧场景参数自动搬到新视频。含显式对象时，结构检查后还会运行独立物性读回并核对报告哈希；没有对象时该项为 `NOT_EVALUATED`。`--prepare-only --backend local --python /path/to/usd/python`可以只做CPU构建与上述检查；渲染需要Isaac与RTX。当前新视频模式支持本机与Docker；服务器通过SSH执行相同命令，尚无本轮远端实跑结果。进程、取消与设备租约见[执行合同](execution.md)。

## 默认使用统一入口

仓库和已准备资产就绪后，统一入口负责环境检查和完整执行：

```bash
simready run --scene substation --accept-eula
simready run --scene starbucks --accept-eula
```

默认资产为 `data/{scene}`、后端为 Docker；两个任务顺序运行。`--backend local --python /path/to/isaac/python` 选择本机环境，`--backend ssh --ssh-host rtx-server --repo /srv/simready --assets /srv/data/substation` 在已准备的远端检出中调用同一入口。SSH 不自动同步代码或资产，远端环境仍须预先准备。

`--dry-run` 只读取输入和生成计划；`--bundle-spec` 可在启动前导出已有场景。每次使用独立运行目录，根目录的 `summary.json` 给出状态、失败阶段及实际视频路径。完整目录和命令见[场景示例](../examples/scenes/README.md)。下文的 `scripts/launch_isaac.py` 是保留的底层兼容入口，用于检查环境或已有配置。

新视频与已有场景入口均支持 `--worker-timeout`（默认 7200 秒，必须为正有限数），限制一次 Isaac worker 的执行时间；已有场景的 CPU 资产导出另有 `--bundle-timeout`（默认 1200 秒）。期限写入计划，新视频恢复需要保持相同期限。云重建仍使用独立的 `--reconstruction-timeout`，这些值不合并冒充整个管线的单一期限。

底层启动器对应 `--timeout-s` 与 `--cleanup-timeout-s`（默认且最多 15 秒）；Python `launch_worker` 另支持取消事件。执行期限与清理宽限分别记录，超时为 `TIMEOUT`、主动取消为 `CANCELLED`、外部中断为 `INTERRUPTED`。嵌套受管理 worker 通过继承管道接收取消，父进程死亡产生 EOF；已经确认合作的子进程先获得最多 20 秒退出宽限，再清理所属进程组。未遵守协议而主动脱离的进程不在保证范围。新视频失败记录在 `stage-failures/`，成功阶段仍在 `stages/`，失败重验不覆盖原成功回执。

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

若只构建 `simready-isaac-render:6.1.0`，星巴克命令需显式选择该镜像：`simready run --scene starbucks --image simready-isaac-render:6.1.0 --accept-eula`。变电站物理任务使用默认完整镜像。

输入挂载 `/workspace` 和 `/assets` 为只读，输出写入 `/output/delivery`。以宿主 UID/GID 运行，并增加官方镜像的 1234 组以读取 `/isaac-sim`；用户目录位于 `/output/.isaac-home`；Kit cache/data/logs 分别挂载到 `.isaac-cache`、`.isaac-data`、`.isaac-logs`，避免以宿主 UID 写入 SDK 只读目录。每次使用唯一容器名；中断只停止该次创建的容器。默认没有宿主 Docker socket、特权模式或图形显示共享。

每次任务使用新的输出目录；已有 `delivery/` 时拒绝再次运行。输出不能与资产目录重叠，也不能等于或包含源码目录，可放在源码内的 `runs/` 下。首次启动写入独占的 `launch.json`，后续环境检查或失败重试另写 `launch-UUID.json`，保留启动记录历史。

启动器将 worker 输出保存到与启动回执同名的 `.stdout.log` / `.stderr.log`，终端打印路径。Docker 失败 / 超时 / 取消后的清理先检查本次随机 ownership 标签，再按完整 container ID 删除，避免仅凭名称误删别的实例。清理日志与状态单独保留；无法连接 daemon 或确认容器身份时记为未确认，不把“本地客户端退出”当成“容器已删除”。本轮清理验证使用受控 Docker 替身，没有新增真实 GPU 容器实跑。

复用 shader 缓存时使用私有复制：拒绝源 / 目标软链接和相互重叠的缓存目录，先暂存所有文件再发布，普通复制失败不会发布半份新树。缓存预热不提供跨运行共享可写目录，也不表示已经完成性能优化。

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

底层 `launch_isaac.py --backend ssh` 将执行 / 清理期限传给远端，并用 `--ssh-local-output` 指定本地 SSH 日志目录（默认在本地 `runs/ssh-launch-<UUID>`）；远程 `--out` 不用作本地目录。SSH 失败、超时或取消时远端状态保持 UNKNOWN，不能据此确认远端已取消。`simready run --scene ... --backend ssh` 会传递 worker / bundle 期限；其 SSH 客户端本身仍是原路径，尚未迁到上述独立本地回执合同。

## 已验证与未验证

历史 v0.6 的实测记录见 [one-command-validation.json](report/one-command-validation.json)。统一入口在镜像 `97b65e8d0f56` 中，使用最终导出器生成的两套真实资产完成以下执行。远端 SSH 尚未执行完整任务，因此不报告远端成功或多 GPU 加速比。

| 核验对象 | 完成范围 | 未覆盖范围 |
|---|---|---|
| 变电站一键交付 | 抽帧、Lite3+Z1 物理、20/20 项审计、原生 1920×1080 录像；713 帧、28.52 秒 | 不包含新视频云重建与资产准备；不证明全场资产或全部按钮正确 |
| 星巴克一键预览 | 最终接口、镜像与资产；640×360、30 fps、8 帧 Docker 预览 | 非完整 4K 重录，非物理导航 |
| 一键导出分支 | 实际导出 30 个文件，并在缺少 Isaac Sim 的本机环境正确拒绝启动 | 负例只证明导出与前置检查，不作为完整任务成功 |
| CPU 回归 | 297 项通过、1 项可选资产测试跳过；Ruff、格式、编译、架构检查通过 | 不替代 GPU 物理与渲染验证 |
| SSH / 新视频 Aholo | 命令构造与引用测试；既有 receipt 恢复代码 | 无本轮远端任务或新视频云重建 |

变电站统一入口 wall 时间为 **1095.164 秒（18 分 15 秒）**：观测 7.528 秒、物理 679.012 秒、审计 3.792 秒、录像 403.469 秒，另有启动与编排开销。星巴克短片为 **281.670 秒**。计时从初始只读计划完成后开始，包含 worker 启动和冷 shader 编译，不包含 SDK/镜像安装、新重建和事先资产准备；本轮是功能验证，不能据此报告加速比。

完整变电站执行的 96 个运行时源码文件与提交 `10c250b` 一致。其后仅补充“普通启动异常记为 FAILED”和“拒绝覆盖已有执行证据”两个失败分支保护，增加 5 个负例测试；最终源码完成星巴克预览。控制器、物理和渲染逻辑未改变，未为这两处失败分支重复完整变电站 GPU 执行。清单记录前后源码哈希。

v0.5 历史验证保留在 [packaging-validation.json](report/packaging-validation.json)：接口接入前的本机完整复现为 756.632 秒，后续容器物理与审计为 704.019 秒。它们使用的接口、镜像或资产版本与本轮不同，不能拼接成同一次执行，也不能直接作为性能对照。

部署新机器人时，SDK 可导入 USD 只证明资产可加载。还需按 [机器人接入说明](../examples/robots/README.md) 绑定关节和驱动，核对米/弧度与四元数约定，标定安装变换、传感器和 TCP，并通过稳定性、可达性和接触审计。G2 演示当前没有物理导航驱动；Lite3+Z1 的已接受控制流程也不能自动迁移到任意构型。

## 错误定位

- `Permission denied /isaac-sim`：检查镜像 1234 组权限与 `--group-add`；不要对宿主目录做全局 chmod。
- `File doesn't exist: isaacsim.exp.base.kit`：SDK 需要完整 experience 路径；图形 worker 根据 `EXP_PATH` 定位。
- `isaaclab_physx` 缺失：仅有基础 Isaac Sim 镜像不足以运行该物理脚本；使用包含匹配 Isaac Lab 的派生镜像。
- bundle 哈希不符：重新导出原始资产包；不要把 receipt 中的哈希改成“通过”。
- 物理审计失败：保留 `result.json`、`sensors.jsonl`、RGB-D 与轨迹；停止交付录像，检查场景、接触和环境版本。
