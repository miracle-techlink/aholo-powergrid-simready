# 语义、物性与参数估计示例

三种输入共用核心资产编译及独立读回；合成夹具用于接口验证，不提供真实重建或实物精度成绩。先安装 `.[physical-assets]`，分层与采用规则见[工程指南](../../docs/README.md#规范流水线)。

## 显式物性

生成柜体、桌、椅、地面和按钮的示例声明，随后编译并独立重开 USD：

```bash
python examples/assets/physical_fixture.py --output runs/physical-input
python -m simready.assets.compilation.scene_preparation \
  --ply runs/physical-input/source.ply \
  --spec runs/physical-input/scene_spec.json --output runs/physical-scene
simready evaluation physical-assets --scene-directory runs/physical-scene \
  --scene-spec runs/physical-input/scene_spec.json --out runs/physical-audit.json
```

`PASS` 表示对象材质、碰撞、刚体模式、质量／惯量及关节等与声明一致，不能用导出的 catalog 自证参数真实。

在已准备的 [Isaac 容器](../../README.md#isaac-sim-worker)中运行合成动力学探针，直接复用统一 launcher：

```bash
python scripts/launch_isaac.py --assets examples/assets --out runs/physical-response \
  --accept-eula --worker -m simready.simulation.physical_probe --out /output/probe
```

本机 SDK 使用 `--backend local --python /path/to/isaac/python`，worker 的 `--out` 改为宿主实际路径；worker argv 必须放最后。协议比较摩擦滑块和相同外力下不同质量刚体，输出 `probe/trajectories.csv` 与 `probe/probe.json`；统一 `launch.json` 的 `seconds` 保存启动到清理的耗时。每次使用新输出目录。

## 语义关联

```bash
python examples/assets/semantic_fixture.py --out runs/semantic-input
simready workflows semantic-assets --ply runs/semantic-input/source.ply \
  --scene-spec runs/semantic-input/scene_spec.json \
  --segmentation-manifest runs/semantic-input/segmentation/manifest.json \
  --calibration runs/semantic-input/calibration.json --out runs/semantic-result
```

夹具显式生成四类 mask 及相机，预期四个静态代理和独立 USD 审计通过；不是分割模型成绩。真实图像先安装 `.[physical-assets,segmentation]`，改用：

```bash
simready workflows semantic-assets --image /data/scene/image.png \
  --calibration /data/scene/calibration.json --ply /data/scene/splats.ply \
  --scene-spec /data/scene/scene_spec.json --out runs/semantic-real
```

默认 Grounding DINO + SAM、CPU 推理；匹配 CUDA 环境可用 `--device cuda:0`，独立环境用 `--segmentation-python`，已有离线权重用 `--local-files-only`。首次模型下载与权限由运行环境提供。

图像、PLY、坐标和标定必须相互对应。巡览会输出逐帧 calibration；采集修改后场景的 RGB 与 z-depth 可用：

```bash
simready simulation depth-observation --scene /data/scene/repaired.usda \
  --scene-spec /data/scene/scene_spec.json --ply /data/scene/splats.ply \
  --camera-calibration /data/scene/calibration.json --out runs/observation
```

使用返回的新图像及标定重新分割；真实视频相机须先配准，不能套用渲染相机。第三方 mask 可按[导入格式](../../src/simready/ingestion/semantic_segmentation.py)提供，并明确标记导入。

输出保留 segmentation、projection、scene、physical_audit.json 和 workflow.json。`REVIEW_REQUIRED` 保留候选并返回 2；`--priors-file` 替换类别先验时只自动采用静态接触参数，动态质量、惯量及尺度须明确声明。遮挡几何、关节和任务另行验收。

## NeRF2Physics

对缓存特征做 CPU cosine/softmax 回归，或导入官方预测；不在本次运行 NeRF、CLIP 或材料语言模型：

```bash
python examples/assets/nerf2physics_fixture.py --out runs/nerf2physics-input
python -m simready.ingestion.nerf2physics --request runs/nerf2physics-input/request.json \
  --out runs/nerf2physics-estimation
```

夹具模式为 `synthetic_fixture`；真实输入使用 `feature_regression` 或 `official_prediction_replay`。请求相对路径基于 request 文件；真实输入须绑定上游 40 位 commit、本地源码与许可证哈希、模型／权重身份、对象关联及尺度证据，不能按名字猜测对象一致。

NPZ（禁用 pickle）包含 `patch_features[V,N,D]`、布尔 `visible[V,N]`、`text_features[M,D]` 和有序 `material_names[M]`。材料区间单位为 `kg/m^3` 或无量纲 `1`；质量积分另需 `source_points[N,3]`、`query_points[Q,3]`、`voxel_volume_m3` 及体积证据。当前只实现 carving 最近邻体素积分，没有厚度估计。

独立评分读取官方 `{scene_id:[low_kg,high_kg]}` 预测和 `{product_id:measured_kg}` 真值：

```bash
python -m simready.evaluation.nerf2physics --predictions preds_mass.json \
  --ground-truth filtered_product_weights.json --scene-ids heldout-scene-ids.json \
  --out mass-evaluation.json
```

缺失／非法预测保留在覆盖率分母；ADE、ALDE、APE、MnRE 对有效预测计算，不设置物理验收阈值，材料区间不是统计置信区间。相对织物的摩擦不能赋给任意接触对，Shore 硬度不转为弹簧参数。

依据 [NeRF2Physics 官方代码](https://github.com/ajzhai/NeRF2Physics)、[回归](https://github.com/ajzhai/NeRF2Physics/blob/master/predict_property.py)、[特征](https://github.com/ajzhai/NeRF2Physics/blob/master/feature_fusion.py)、[评分](https://github.com/ajzhai/NeRF2Physics/blob/master/evaluation.py)及 [MIT 许可](https://github.com/ajzhai/NeRF2Physics/blob/master/LICENSE)。真实视觉推断及公开数据评分尚未完成；数据许可独立核对。
