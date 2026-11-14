# NeRF2Physics 隔离输入适配

运行包不会导入或安装上游 NeRF、BLIP-2、OpenCLIP 或 OpenAI SDK。它对外部固定产物执行
CPU cosine/softmax 回归，或导入官方对象质量预测；二者均不表示本次执行了新的视觉模型。

```bash
python examples/nerf2physics/create_fixture.py --out runs/nerf2physics-fixture/input
python -m simready.ingestion.nerf2physics \
  --request runs/nerf2physics-fixture/input/request.json \
  --out runs/nerf2physics-fixture/estimation
```

以上仅生成 `synthetic_fixture`，不能用于公开数据准确率或真实模型接入完成的结论。
`feature_regression` 表示对缓存特征做新回归，`official_prediction_replay` 表示导入已有质量区间。
所有证据引用包含 SHA256；请求内部相对路径统一相对于请求 JSON，输出候选引用全部使用绝对路径。

真实请求必须提供完整 40 位上游 commit、对应源码文件及 MIT LICENSE 的本地哈希引用。
`model_identity` 记录 CLIP backbone/checkpoint，未知权重摘要明确为 null。公共数据对象与当前场景
对象的关系必须显式声明并附证据；适配器不会通过名字相同自动认定是同一对象。

特征 NPZ（`allow_pickle=False`）包括 `patch_features[V,N,D]`、`visible[V,N]` bool、
`text_features[M,D]` 和按文本嵌入顺序排列的 `material_names[M]`。payload 中材料区间单位必须
为 `kg/m^3`（密度或质量积分）或 `1`（动摩擦）。质量积分另需 `source_points[N,3]`、
`query_points[Q,3]`、明确的 `voxel_volume_m3`、体积证据和请求级 `scale_evidence`。
只支持与官方 carving 路径对应的最近邻体素积分；没有实现厚度体积估计。密度候选取可见点均值。

独立评分：

```bash
python -m simready.evaluation.nerf2physics --predictions preds_mass.json \
  --ground-truth filtered_product_weights.json --scene-ids heldout-scene-ids.json \
  --out mass-evaluation.json
```

输入预测为官方 `{scene_id: [low_kg, high_kg]}`，真值为 `{product_id: measured_kg}`。
必须显式列出评估集，缺失/非法预测保留在覆盖率分母中；ADE、ALDE、APE、MnRE 对有效预测计算。
评分不设置物理验收阈值，也不把估计区间当统计置信区间。

## 官方核查及当前实证状态

2026-11-14 核查：[官方仓库](https://github.com/ajzhai/NeRF2Physics)、
[最终回归实现](https://github.com/ajzhai/NeRF2Physics/blob/master/predict_property.py)、
[模型配置](https://github.com/ajzhai/NeRF2Physics/blob/master/feature_fusion.py)、
[评分定义](https://github.com/ajzhai/NeRF2Physics/blob/master/evaluation.py)、
[MIT 许可证](https://github.com/ajzhai/NeRF2Physics/blob/master/LICENSE)。

官方默认 CLIP 为 `ViT-B-16 / datacomp_xl_s13b_b90k`，temperature 为 0.1，质量积分 correction
factor 为 0.6。材料提案由语言模型产生；动摩擦提案限定对织物表面的滑动，不能自动赋给任意
夹爪/桌面。Shore 硬度不转换为弹簧参数。

当前会话 exec 出站被拒绝，web 可读源码文本但不能取得官方 commit API、ABO-500 和摩擦数据
压缩包，本地也没有对应缓存。因此真实数据、权重和下载 SHA256 尚未取得，完整官方版本锁与
真实模型/公开数据评分状态为 **BLOCKED**。不填写猜测 commit 或下载哈希。接口测试及合成演示
完成并不解除这一阻断。官方数据地址为仓库 README 链接的 Box 分发；源 ABO 数据许可须在
下载后独立核对，不能因为上游代码是 MIT 就假定数据也使用 MIT。
