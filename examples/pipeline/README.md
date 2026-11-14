# Pipeline 示例

本目录同时提供 CPU 合同演示和真实视频的配置模板。先按根目录 README 安装依赖；配置、恢复及验收边界见[工程指南](../../docs/README.md#规范流水线)。

## CPU 快速开始

生成器提供测试视频、两个合成高斯、显式动态 box 和完整配置，不执行模型推断：

```bash
python examples/pipeline/create_fixture.py --out /tmp/pipeline-inputs
simready run --pipeline /tmp/pipeline-inputs/pipeline.json --out /tmp/pipeline-run --dry-run
simready run --pipeline /tmp/pipeline-inputs/pipeline.json --out /tmp/pipeline-run --until reconstruction
simready run --pipeline /tmp/pipeline-inputs/pipeline.json --out /tmp/pipeline-run --resume
```

修改配置或 scene spec 后，用 `--upgrade` 替换 `--resume`，显式接受新计划并重新验证阶段复用。配置路径以配置文件目录为基准。

## 真实视频

先准备 [Isaac 容器](../../README.md#isaac-sim-worker)及宿主 Aholo 依赖。凭据通过宿主环境提供，不写入配置。以下命令会上传视频并提交新云任务：

```bash
python -m pip install -e '.[ingest,aholo]'
simready run --video /path/to/input.mp4 --out runs/new-scene \
  --scene-spec examples/pipeline/scene.json \
  --submit-reconstruction --accept-eula
```

新提交必须显式 `--submit-reconstruction`；已有来源绑定的重建可离线复用：

```bash
simready run --video /path/to/input.mp4 --out runs/reused-scene \
  --reconstruction-manifest /path/to/reconstruction.json \
  --scene-spec examples/pipeline/scene.json --accept-eula
```

仅 CPU 导出使用 `--backend local --prepare-only`，解释器需安装 `physical-assets` 依赖。真实重建产物必须绑定同一视频 SHA256、可解码 PLY 及文件哈希；导入不会读取云端凭据或提交任务。云 worker 默认期限 7200 秒，可由 `--reconstruction-timeout` 调整，期限不保证第三方服务完成。

## 场景配置

[scene.json](scene.json) 是无对象的预览模板；[declared_objects.json](declared_objects.json) 演示柜体和弹簧按钮。坐标和参数是示例值，使用前须替换成当前场景证据。

| 字段 | 合同 |
|---|---|
| `source_to_world` | 4×4 Sim(3)，`p_world = sR p_source + t`，正尺度、正交旋转、世界 Z 向上 |
| `scale_provenance` | `verified/unverified`、方法与证据；已知长度或标定提供米制锚点，声明不等于独立测量 |
| `floor_plane` | 世界平面及证据；省略时仅在方向已声明的条件下估计地面 |
| `collision` | 体素、采样及代理数量上限、声明的接触参数 |
| `objects` | 独立 ID、世界坐标和尺寸、材质及来源；当前支持 box 和 button |
| `camera` / `render` | 巡览机位与宽高、帧数、fps、子帧；巡览不代表机器人导航 |

无尺度锚点不声称米制标定，源方向未知不猜地面；完整遮挡几何、真实物性和设备功能仍需独立验证。

## 恢复与输出

`--video` 中断后用相同命令加 `--resume`；输入、代码和执行配置必须相同。修改配置后选择新目录并复用 reconstruction manifest；需要局部升级时使用规范 `--pipeline` 入口。未知云提交状态不会自动重发，须先恢复服务端凭据。

每次运行保存计划、`summary.json`、`stages/` 回执、观测与重建来源、冻结 worker 输入、USD、独立检查和可选巡览。失败产生新 attempt，旧日志保留。

`SCENE_EXPORTED` 表示导出和结构检查通过；`SCENE_SURVEY_READY` 另包含巡览与编码检查。两者均保持 `task_validated=false`。远端须预先准备源码、镜像与输入；当前入口不自动同步文件或部署 SDK。
