# 星巴克与变电站：两个独立的启动示例

两个配置使用**已配准、已修复的场景资产**复现视频。它们会核验原片哈希并抽帧，再执行场景任务。新视频云重建另走 `simready ingestion aholo`；不会把原场景的机器人坐标直接套在新重建上。

## 推荐入口：一条命令执行到视频与审计

在仓库中安装 `python -m pip install -e .`，按下文准备 `data/{scene}/bundle.json`，并按[部署说明](../../docs/deployment.md)准备 Isaac 镜像后：

```bash
simready run --scene substation --accept-eula
# 或运行星巴克；两个 GPU 任务顺序执行。
simready run --scene starbucks --accept-eula
```

默认使用 Docker，自动创建 `runs/{scene}-时间戳-唯一编号/`。不再需要自行选择配置文件或串接各阶段。入口依次核验资产、检查 SDK/ffmpeg、抽帧、执行任务、独立审计和录像，最后核对交付文件。变电站审计失败会停止录像；星巴克只执行编排运动与渲染。

```text
runs/{scene}-…/
├── run-plan.json          输入、执行范围和配置
├── summary.json           COMPLETE / FAILED、失败阶段、视频路径与哈希
└── execution/
    ├── environment.json  实际 worker 环境
    ├── launch.json       启动命令与退出状态
    └── delivery/
        ├── timing.json   各阶段耗时
        ├── observations/
        ├── mission/      仅变电站：审计、传感器和实测轨迹
        └── render/       MP4 与渲染凭据
```

已有完整资产清单时，可将导出与执行合成一次调用：

```bash
simready run --scene substation --bundle-spec /path/to/substation.bundle.json \
  --bundle-python /path/to/isaac/python --accept-eula
# 只检查计划，不导出、不启动 GPU、不创建输出目录：
simready run --scene substation --dry-run
# 使用已经安装的本机 Isaac 环境：
simready run --scene substation --backend local \
  --python /path/to/isaac/python --accept-eula
```

`--assets` 可覆盖默认资产目录，`--config` 可覆盖场景/渲染配置，`--out` 可指定尚不存在的输出目录。`--bundle-spec` 打包的是已准备的场景产物，不会执行新视频重建或自动补齐缺失资产。下面保留资产准备方法和底层入口，供调试单独阶段使用。

## 1. 准备资产包

将 `starbucks.bundle.example.json` 或 `substation.bundle.example.json` 复制到自己的素材目录，填写真实路径。相对路径以 JSON 所在目录为基准；绝对路径仅用于导出输入，不留在导出的 USD 依赖中。

星巴克需要源视频、已有 Gaussian USD、准备好的 G2 运动姿态 USD，及可选的球谐颜色修正层。变电站需要源视频与任务配置；任务配置须包含四个路径：

```json
{
  "environment_manifest": "controls/manifest.json",
  "scene_config": "scene/scene_config.json",
  "rig_report": "robot/lite3_z1.runtime.json",
  "locomotion_policy": "robot/policy.onnx"
}
```

这四项补充到完整任务配置，而非替代导航、相机、接触、工具参数。完整参数模板见 [substation.mission.example.json](substation.mission.example.json)。环境配置需要 Z-up / meter USD、地面碰撞路径、允许动态控件清单、视觉来源和机器人起点。`rig_report` 包含 `rig_path`、12 个腿关节和 6 个臂关节的组、限位与 home。不能仅凭四个文件名构造有效场景。

```bash
# ISAAC_PYTHON 指已装 pxr/OpenUSD 的 Python；导出不启动 Kit/GPU。
ISAAC_PYTHON=/path/to/isaac/python
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
"$ISAAC_PYTHON" -m simready simulation export-bundle \
  --spec /path/to/starbucks.bundle.json --out data/starbucks
"$ISAAC_PYTHON" -m simready simulation export-bundle \
  --spec /path/to/substation.bundle.json --out data/substation
```

导出递归复制 USD 层、纹理、网格和策略，重写成包内相对引用，记录重写前后哈希。除下述 SDK 白名单外，没有找到的依赖会报错，不静默忽略。包内仅保留必要控制规格，不复制整个工作目录。第三方模型需要用户自行取得相应授权；本次公开 Release 不分发这些资产包。

本地自定义 MDL 仅支持 **UTF-8 自包含模块**；含 `import`、限定模块名 `::` 或纹理、`light_profile`、`bsdf_measurement` 资源类型时明确拒绝，不递归解析 MDL 内部依赖，也不编译材质。相对 USD 层存在的本地 MDL 优先复制，包括同名 `OmniPBR.mdl`；仅当没有本地文件时，SDK 白名单中的 `OmniPBR.mdl` 可保留外部依赖，未知缺失模块失败。该支持范围不代表任意 MDL 材质均可迁移。

## 2. 启动星巴克视频

```bash
OMNI_KIT_ACCEPT_EULA=YES simready workflows scene --config examples/scenes/starbucks.json \
  --assets data/starbucks --out runs/starbucks-001 --python "$ISAAC_PYTHON"
```

输入：原视频与已准备的 Gaussian/G2 资产；处理：抽帧登记 → 原生 RTX / DLAA / 光照 → 编排的 G2 轨迹 → 编码；输出：`render/g2_starbucks_4k.mp4`，默认 4K / 30 fps / 900 帧。这里的轨迹是可视化演示，不是物理导航证明。需要快速检查时，复制配置为新的 preview 配置并降低帧数/分辨率；不要把预览计时与完整交付混用。

## 3. 启动变电站完整任务

```bash
OMNI_KIT_ACCEPT_EULA=YES simready workflows scene --config examples/scenes/substation.json \
  --assets data/substation --out runs/substation-001 --python "$ISAAC_PYTHON"
```

输入：原视频、完成估计补建的柜体/按钮、Lite3+Z1、冻结步态策略、任务配置；处理：抽帧登记 → 场景预检 → 已知地图导航 → RGB-D 唯一目标定位 → 碰撞检查与 Z1 按压 → 独立接触审计 → 真实轨迹回放；输出：`render/navigation_vision_z1_stop.mp4`、`mission/audit.json` 和原始证据。

配置中的 714 帧记录历史接受版本长度；新物理运行的实际轨迹决定录像帧数，不截断或补齐成宣称的结果。25 fps 采样约定冻结。`scene_delivery` 会在 physics/audit 任何一个非零退出时停止。

## 4. 本地与远端使用同一配置

```bash
python scripts/launch_isaac.py --backend docker --assets data/starbucks \
  --config examples/scenes/starbucks.json --out runs/cafe-docker --accept-eula
python scripts/launch_isaac.py --backend ssh --ssh-host rtx-server \
  --repo /srv/simready --assets /srv/data/substation --out /srv/runs/grid-001 \
  --config examples/scenes/substation.json --accept-eula
```

详细环境安装、资产同步和验证范围见 [部署说明](../../docs/deployment.md)。远端 SSH 尚未执行完整任务；此处提供启动方式示例。现有接受任务的复现不调用 LLM；如另行启用修复工作流，Agent token 与确定性 worker 的 GPU 时间分别统计。
