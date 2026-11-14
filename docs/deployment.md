# Isaac Sim 容器部署

CPU 编译依赖支持高斯 schema 的 OpenUSD 26.8+（`physical-assets` extra）；宿主运行视频／重建编排；容器运行 USD 构造、独立检查、RGB-D、巡览和 PhysX 探针。模型推断保持独立环境，云端凭据不进入镜像或 worker 输入。

## 构建与检查

需要 Docker、NVIDIA Container Toolkit、可运行 Isaac Sim 的 GPU／驱动及适用的 NVIDIA 许可。基础镜像固定为 `nvcr.io/nvidia/isaac-sim:6.1.0`，不自动追随 latest；目标设备要求见 [NVIDIA 文档](https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html)。

```bash
python -m pip install -e '.[physical-assets,aholo]'
docker build -t simready-isaac-core:6.1.0 -f deploy/isaac/Dockerfile deploy/isaac
python scripts/launch_isaac.py --assets examples/pipeline --out runs/isaac-check \
  --doctor --launch-smoke --accept-eula
```

`--doctor` 检查 SDK 与依赖，`--launch-smoke` 再启动 Kit 并更新 10 次。镜像增加 FFmpeg、字体、NumPy、SciPy、Pillow、OpenCV，以及固定 `usd-exchange==3.0.1` 供 CPU 资产编译使用；不安装 Isaac Lab、机器人策略或账号数据。Isaac 基础镜像的 USD 缺少本管线需要的高斯 schema，构建时实际创建 `ParticleField3DGaussianSplat` 并记录版本到 `/opt/simready/usd-environment.json`。CPU 编译与 Kit 渲染在独立进程中运行，两条路径都须实测。默认只发布环境镜像，源码由启动器只读挂载。

## 运行核心 pipeline

```bash
simready run --video /data/input.mp4 --reconstruction-manifest /data/reconstruction.json \
  --scene-spec /data/scene-spec.json --backend docker \
  --image simready-isaac-core:6.1.0 --out runs/scene-001 --accept-eula
```

已有来源绑定 PLY 可离线复用；新 Aholo 提交须另给 `--submit-reconstruction`，由宿主执行。使用 `--prepare-only` 可仅导出与审计 USD。结构或巡览成功不代表完整几何、真实物性或机器人任务通过。

规范 `--pipeline` 配置选择 `execution.backend=docker`、`execution.image` 和 `delivery.mode=prepare/survey`；同时提供带 SHA256 的 `execution.environment_manifest`。环境声明与实际代码身份共同进入计划。可用 `docker image inspect --format '{{.Id}}' simready-isaac-core:6.1.0` 得到不可变镜像 ID，在 image 字段及环境清单中固定该 ID；不同镜像重新验收，不能复用旧成绩。配置及恢复见 [Pipeline](pipeline.md)。

公开 CPU 输入由 `examples/pipeline/create_fixture.py` 生成；公开 PhysX 探针可运行：

```bash
python examples/physical_assets/run_probe.py --out runs/physics-check --accept-eula
```

## 显式 worker 与远端

```bash
python scripts/launch_isaac.py --assets examples/physical_assets --out runs/probe-001 \
  --accept-eula --worker -m simready.simulation.physical_probe --out /output/probe
python scripts/launch_isaac.py --backend ssh --ssh-host rtx-server \
  --repo /srv/simready --assets /srv/inputs --out /srv/runs/check-001 \
  --doctor --launch-smoke --accept-eula
```

`--worker` 后全部内容作为 Python argv，放在命令最后；容器路径使用 `/workspace`、`/assets` 和 `/output`。`--backend local --python /path/to/isaac/python` 可用本机 SDK。远端先准备同版本源码、镜像、输入和宿主 Python；SSH 不自动同步、不提供集群调度，断线后远端状态保持 UNKNOWN。

## 证据与生命周期

源码和输入只读挂载，输出使用独立目录；默认无 Docker socket 或特权挂载。宿主 UID/GID 与镜像 1234 组用于 SDK 权限；用户目录及缓存位于输出目录。`launch.json` 与 stdout/stderr 保存执行结果，重试产生新回执，不覆盖旧证据。

执行与清理期限分开，GPU UUID 租约与其他 core worker 共用。清理只处理本次 ownership 标签和完整 container ID；无法确认停止时保留隔离，不能直接释放设备。取消、期限、缓存和设备恢复见[执行合同](execution.md)。

CI 验证 CPU 合同和安装包，不启动 NVIDIA GPU；实际镜像构建、Kit 启动、USD 导出与渲染须另存该次镜像 ID、输入、日志和结果，不能从 mock 成功推断真实 GPU 可用。
