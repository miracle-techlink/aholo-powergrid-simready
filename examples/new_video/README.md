# 新视频入口：视频到场景与巡览

`simready run --video` 构建新场景；`simready run --scene` 复现已经验收过的机器人示例。这两种模式共用CLI和部署工具，但交付的证据不同。

## 首次运行

先安装本仓库、宿主 ffmpeg，并按[部署说明](../../docs/deployment.md)准备 `simready-isaac:6.1.0` 镜像与 NVIDIA RTX 环境：

```bash
python -m pip install -e '.[ingest,aholo]'
# AHOLO_API_KEY 由本机环境或密钥管理器提供，勿写入配置或提交仓库。
simready run --video /path/to/input.mp4 --out runs/new-scene \
  --submit-reconstruction --accept-eula
```

此命令实际串接：

1. 来源视频哈希、抽帧与帧清单。
2. Aholo上传、任务凭据、有限时长监控及PLY/SPZ下载。
3. PLY属性检查、SH排列转换、USD可见外观生成。
4. 坐标变换、可选地面估计、有界体素碰撞与显式对象生成。
5. 独立USD结构检查。
6. 原生RTX巡览、编码核验、产物清单与时间账本。

首次云提交必须有 `--submit-reconstruction`。没有该参数且没有可恢复的凭据时，程序快速拒绝，不提交新任务。默认云worker时限为7200秒，可用 `--reconstruction-timeout` 改变；时限覆盖上传、监控、下载，不保证供应商在期限内完成。SDK安装、镜像构建和第三方服务收费不包含在运行时长中。

无场景配置也能构造高斯外观和估计碰撞，并自动选择基于包围盒的诊断相机。**源坐标方向未知时不猜地面；没有尺度锚点时不声称米制标定；巡览不是机器人导航。**

## 坐标、资产和参数的入口

默认模板为 [scene.json](scene.json)。按实际输入改好配置后运行：

```bash
simready run --video /path/to/input.mp4 --out runs/new-scene-configured \
  --scene-spec examples/new_video/scene.json \
  --submit-reconstruction --accept-eula
```

[declared_objects.json](declared_objects.json) 演示可独立编辑的柜体和弹簧按钮。它的坐标是示例值，不应直接当成新视频的测量结果。

| 字段 | 含义与准备方法 |
|---|---|
| `source_to_world` | 4×4列向量Sim(3)：`p_world = sR p_source + t`，正尺度、正交旋转；世界Z向上。显式变换同时声明已处理坐标方向。 |
| `scale_provenance` | `status`只接受`verified`或`unverified`，另填`method/evidence`。米制尺度需要已知长度、标定相机轨迹或测量锚点；填写verified只是用户提供的声明，不是程序独立验证。 |
| `floor_plane` | 世界坐标平面`normal·p + offset = 0`及证据；省略时，只有已声明Z向上才尝试近水平RANSAC拟合，并保留估计标记。 |
| `collision` | 体素大小、采样数、代理数上限与仿真摩擦参数。默认采样最多10万点、代理最多2000个；超出代理数时自动加粗并记录实际体素大小。 |
| `objects` | 显式`box`或`button`对象，需独立ID、世界坐标、尺寸/材质/证据。按钮生成刚体、滑动关节、行程与弹簧驱动；设备开关语义及机器人接触任务需另外绑定。 |
| `camera` | 可选`eye/target/orbit_degrees`，均在世界坐标中；未提供则按当前场景包围盒计算巡览轨迹，不复用旧场景机位。 |
| `render` | 输出宽高、帧数、fps、子帧。默认1280×720、120帧、24fps、8子帧；不是性能最优配置承诺。 |

物理质量、摩擦、刚度不能仅从一段单目视频确定。当前版本支持显式参数并记录来源；不会用图像颜色伪造材料测量。当前自动化也不包括任意柜门/桌椅的完整纹理网格重建、全资产语义分割或全按钮功能推断。

## 复用重建、只导出与恢复

成功的重建会产生可复用manifest，后续场景配置不需要重新付费重建：

```bash
simready run --video /path/to/input.mp4 --out runs/another-scene-candidate \
  --reconstruction-manifest runs/new-scene/reconstruction/reconstruction.json \
  --scene-spec examples/new_video/scene.json --accept-eula
```

复用时检查视频SHA、PLY/SPZ大小与SHA；不会读取API key或联网。它证明文件绑定未变化，不独立证明一个外部提供的manifest正确描述了视频和模型的对应关系。

仅CPU构建与结构检查（所选解释器需要匹配的NumPy、SciPy和包含3DGS schema的OpenUSD/pxr）：

```bash
simready run --video /path/to/input.mp4 --out runs/scene-cpu \
  --reconstruction-manifest /path/to/reconstruction.json \
  --backend local --python /path/to/usd/python --prepare-only
```

失败或中断后重复相同命令，并增加 `--resume`。输入、配置、执行选项与运行源码身份必须相同；已完成阶段的产物逐个校验哈希后复用。失败worker进入新attempt目录，旧日志不会覆盖。修改场景参数或升级代码时新建输出目录，并用上一轮的reconstruction manifest复用底稿。

```bash
simready run --video /path/to/input.mp4 --out runs/new-scene \
  --submit-reconstruction --accept-eula --resume
```

未知提交结果（已有submit intent但没有服务端receipt）不会自动重发POST，必须先恢复任务凭据，避免重复任务。`--dry-run`只读计划，不创建目录、导出资产、联网或启动GPU。

## 输出与验收含义

```text
runs/new-scene/
├── run-plan.json                 # 来源、配置、源码身份
├── summary.json / history/      # 本次状态、耗时及历史
├── stages/                     # 可恢复阶段及产物哈希
├── observations/attempt-0001/   # 帧与manifest
├── reconstruction/             # receipt、PLY/SPZ、可复用manifest
├── worker_inputs/attempt-0001/  # 只读worker输入
└── execution/attempt-0001/
    ├── scene/
    │   ├── scene.usda          # 可打开的组合场景
    │   ├── appearance.usdc     # 可见3DGS外观
    │   ├── collision.usda      # 独立隐藏碰撞代理
    │   ├── objects.usda        # 显式可编辑对象/关节
    │   └── asset_catalog.json / preparation.json
    ├── structure-audit.json    # 独立结构检查
    ├── render/scene_survey.mp4  # 原生巡览
    └── delivery.json           # 文件哈希和worker阶段时间
```

`COMPLETE`表示所选流水线已执行完毕。`SCENE_EXPORTED`表示CPU导出和结构检查通过；`SCENE_SURVEY_READY`另包含巡览与编码核验。二者均保持`task_validated=false`，不等于全场重建保真度、真实物理参数或机器人任务通过。现有Lite3+Z1任务仍由`simready run --scene substation`执行独立任务审计。

## 服务器执行

在已准备好相同仓库、镜像、密钥环境和输入视频的RTX服务器上，运行相同命令即可；可通过SSH调用：

```bash
ssh rtx-server 'cd /srv/simready && .venv/bin/simready run \
  --video /srv/data/input.mp4 --out /srv/runs/new-scene \
  --submit-reconstruction --accept-eula'
```

新视频模式的本轮实测范围为本机与Docker；没有远端服务器成功证据。入口不自动同步本地文件或安装服务器SDK。
