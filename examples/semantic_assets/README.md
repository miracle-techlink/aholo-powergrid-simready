# 从分割到逐物体物性的入口

分层和验收边界见[工程指南](../../docs/README.md#规范流水线)。默认模型为 Grounding DINO + SAM；SegFormer 是可选研究基线。

## 1. 无需模型、GPU或私有数据的贯通示例

```bash
python -m pip install -e '.[physical-assets]'
python examples/semantic_assets/create_fixture.py --out runs/semantic-input
simready workflows semantic-assets \
  --ply runs/semantic-input/source.ply \
  --scene-spec runs/semantic-input/scene_spec.json \
  --segmentation-manifest runs/semantic-input/segmentation/manifest.json \
  --calibration runs/semantic-input/calibration.json \
  --out runs/semantic-result
```

该夹具的柜、桌、椅、地面标签由代码显式生成，标记为人工／夹具输入；它验证掩码、坐标和物性接口，不测试模型准确率。预期 `CANDIDATE_READY`、4 个静态代理和独立 USD 审计 `PASS`，`release_authorized=false`。

## 2. 真实图像：一条命令完成模型分割、三维关联与物性写入

```bash
python -m pip install -e '.[physical-assets,segmentation]'
simready workflows semantic-assets \
  --image /data/scene/image.png \
  --calibration /data/scene/calibration.json \
  --ply /data/scene/splats.ply \
  --scene-spec /data/scene/scene_spec.json \
  --out runs/semantic-real
```

`calibration.json` 必须对应同一张图、同一份 PLY 和同一坐标变换。已有 `simready run --video` 巡览现会输出 `calibration/frame_000000.json` 等逐帧相机文件。修复后场景宜通过 `depth_observation` 同时采集 RGB 和 z-depth，防止沿用原始高斯深度造成错位。真实视频帧的相机参数必须先配准，不能套用渲染相机。

在已按[容器与运行说明](../../docs/README.md#容器与运行)配置的 Isaac Python 环境内，RGB-D 采集命令为：

```bash
simready simulation depth-observation \
  --scene /data/scene/repaired.usda \
  --scene-spec /data/scene/scene_spec.json \
  --ply /data/scene/splats.ply \
  --camera-calibration /data/scene/calibration.json \
  --out runs/observation
```

使用返回的 `runs/observation/image.png` 和 `calibration.json` 运行上面的物性入口，重新推理掩码。原相机文件定义取景；新观测的图像／深度具有新的来源哈希。候选场景外观仍来自 PLY，接回已有修复场景和机器人任务需要单独处理原碰撞体与控件。

默认 CPU 推理。已安装匹配 CUDA PyTorch 的环境可以加 `--device cuda:0`；请求 CUDA 而环境不支持时明确失败。首次运行会下载独立模型权重，离线缓存可加 `--local-files-only`。将模型装入独立环境时使用 `--segmentation-python /path/to/model-env/bin/python`，该环境也应安装本项目。

输出目录包含：

- `segmentation/manifest.json`、逐对象 `masks/`、原始 `raw_masks/` 与 `overlay.png`。
- `projection/scene_spec.json`、像素或高斯点索引，以及接受／拒绝原因。
- `scene/scene.usda`、独立对象／材质层和 `asset_catalog.json`。
- `physical_audit.json`：独立进程打开 USD，与输入 spec 比较。
- `workflow.json`：逐阶段耗时、状态和本工作流的 LLM 用量。

`REVIEW_REQUIRED` 会保留候选，但退出码为 2；图像类别歧义、深度不足、代理重叠或地面重复碰撞不能自动交付。USD 审计 `PASS` 只表示所声明的参数确实写入。

## 3. 使用自己的场景图像

提供具有使用权限的场景图像；模型权重由对应 worker 环境准备：

```bash
simready ingestion ground --image /data/scene/image.png \
  --output-dir runs/scene-masks
```

这是 2D 模型示例；图片本身没有提供可直接套用的三维标定。默认类别是 `cabinet,door,table,chair,floor,robot`，可用 `--categories` 指定其他英语类别。机器人区域参与遮挡排除，不套用家具物性。

## 4. 分割与参数替换

已有授权分割模型可输出[导入清单](../../src/simready/ingestion/semantic_segmentation.py)，再通过 `simready ingestion segment --image ... --import-manifest ... --output-dir ...` 标准化。显式导入不会标记为本次模型推理。

`--priors-file` 接受“类别 → physics”映射；自动先验只允许静态接触参数，不从图片编造质量。需要动态对象时，修改已生成的 `scene_spec.json`，按参数来源填写质量／惯量与尺度声明，随后使用[物性示例](../physical_assets/README.md)中的 author → audit 两步。柜门铰链、可抓取物和柔性体需要对应几何与运动模型，不能仅靠类别名生成。
