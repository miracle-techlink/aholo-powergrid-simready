# 星巴克与变电站：两个独立的启动示例

两个配置使用**已配准、已修复的场景资产**复现视频。它们会核验原片哈希并抽帧，再执行场景任务。新视频云重建另走 `simready ingestion aholo`；不会把原场景的机器人坐标直接套在新重建上。

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

导出递归复制 USD 层、纹理、网格和策略，重写成包内相对引用，记录重写前后哈希。没有找到的依赖会报错，不静默忽略。包内仅保留必要控制规格，不复制整个工作目录。第三方模型需要用户自行取得相应授权；本次公开 Release 不分发这些资产包。

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

详细环境安装、资产同步和验证范围见 [部署说明](../../docs/deployment.md)。现有接受任务的复现不调用 LLM；如另行启用修复工作流，Agent token 与确定性 worker 的 GPU 时间分别统计。
