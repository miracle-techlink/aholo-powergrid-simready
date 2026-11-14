# 星巴克与变电站修复路径复核

> 本文保留修复前的审查结论。后续实现与最终验收见 [2026-11-14 可见资产修复交付](2026-11-14-starbucks-visible-delivery.md)。

核查日期：2026-11-13。范围：变电站最终估计柜体交付、其此前的原片纹理修复、星巴克 grasp_v4 / tour_v6 与当前 pipeline 调用关系。

**结论：星巴克这轮没有复用变电站完整的外观修复链。** 新增结构承托、碰撞和局部人物清理有效，但没有完成“原片清晰来源选择 → 分部件可见资产 → 替换损坏外观 → 独立原片一致性检查”。此前“任务级 Sim-ready”通过的证据不能回答用户指出的视觉空洞和模糊问题。这一迁移缺口仍未完成，本次审查没有重录视频，也没有把旧交付的视觉状态改成通过。

## 1. 最关键的实际产物差异

本次通过 OpenUSD 打开组合后的场景，检查有效可见性，而非只读文档或脚本名称：

| 场景 | 原高斯的有效可见性 | 原整柜照片面 | 本次含义 |
|---|---|---|---|
| 变电站最终 environment.usda | `/World/Visual/Gaussians` 为 `invisible` | 左右 `SourceFace` 都为 `invisible` | 最终主要可见结构来自显式几何与材质 |
| 星巴克搬杯 grasp_v4 | `/Cafe/OriginalVideo/StarbucksSplats` 为 `inherited`，有效可见 | 无整柜照片面 | 原高斯中的模糊与残影继续参与渲染 |
| 星巴克巡逻 tour_v6 | 同上，有效可见 | 无整柜照片面 | 增加后方补面不等于修复前方损坏外观 |

变电站 `/World/EstimatedCabinetFacades` 下实际有 132 个 Mesh prim。历史 `completion.json` 中的 46 个主要部件是另一统计口径，其中 14 个标为 `low_inferred`，不能把 132 当作已确认原片实例数。

机器可读核查：[composed_visibility.json](/home/nvidia/algotithm/simready_infra_pr/runs/repair_reuse_audit_20261113/composed_visibility.json)。检查的变电站资产为 `/home/nvidia/algotithm/simready_bridge/artifacts/panel_estimated_facades_20261111_v2/environment.usda`。

## 2. 变电站实际如何修复

变电站经历了几条有先后关系的路径，不能把历史候选全部当成最终视频使用的方法。

### A. 先将破损外观转换为可编辑结构

[build_panel_corridor_assets.py](/home/nvidia/algotithm/simready_bridge/scripts/build_panel_corridor_assets.py:24) 的 `Builder` 在第 34 行隐藏原高斯，按观测到的柜体平面与场景布局建立柜体、侧面、顶部、地面、房间包络和远端设备。材质显式设置粗糙度、金属度、玻璃及发光参数；接缝和倒角由几何表达。

该阶段保留原高斯作为来源，但呈现范围切换为可编辑的几何。最早的通用控件模板仍有与原片不一致的问题，后续另做原片约束修正，不能复用模板中的重复控件来冒充识别结果。

### B. 原片有清晰信息的区域：配准、选源、保持细节

[fit_panel_reference_atlas.py](/home/nvidia/algotithm/simready_bridge/scripts/fit_panel_reference_atlas.py:49) 使用已有人工核查的平面边界、行列锚点，再以 SIFT + MAGSAC 拟合单应性，保存每帧到 atlas 的映射、来源哈希和有效区域。配准要求至少 25 个内点、中位重投影误差不超过 2.5 px、内点凸包有足够支持。拟合帧与检查帧分开。它针对柜面平面，不适合直接投影整张椅子或弯曲物体。

[refine_panel_texture_detail.py](/home/nvidia/algotithm/simready_bridge/scripts/refine_panel_texture_detail.py:15) 在已接受的拟合帧中按 `有效区域平均绝对 Laplacian × sqrt(覆盖率)` 排序。从原图直接重采样到 2048×2048 atlas，让清晰且连续的来源优先覆盖，只有可见性边界约 4 个纹理像素做羽化，避免整幅混合导致双影。保存逐像素来源索引；留出检查帧不参与选择。历史选中的主要来源为左柜 79 秒、右柜 83 秒。2048 贴图不会补出原片没有的真实文字。

[build_panel_semantic_repair.py](/home/nvidia/algotithm/simready_bridge/scripts/build_panel_semantic_repair.py:27) 将上述纹理绑定到可见表面，取消不受原片支持的重复控件模板，保留有多次观测支持的局部控件外观。该中间阶段本身只改视觉，不能直接算新增物理资产。

[panel_semantic_teacher.py](/home/nvidia/algotithm/simready_bridge/scripts/panel_semantic_teacher.py:35) 使用冻结 DINOv2 的局部特征距离、边缘误差、红绿灯位布局和 RGB 误差检查一致性；不是重新训练 DINOv2。旧目标权重为 0.45 / 0.20 / 0.25 / 0.10。整体推广还要求足够覆盖和遮挡复查；局部指标改善不等于整柜通过。

[run_panel_reference_consistency.py](/home/nvidia/algotithm/simready_bridge/scripts/run_panel_reference_consistency.py:22) 默认 `sharp_source`，串联配准、清晰来源选择、材质增益候选、原生渲染及检查。原片平面锚点和柜体规格仍是场景专用配置。

### C. 来源缺失仍然糊的区域：显式估计部件替换

照片方案仍无法解决被遮挡的下半柜和斜视拉伸；历史整柜画质复查因此失败。最终 [build_panel_estimated_facades.py](/home/nvidia/algotithm/simready_bridge/scripts/build_panel_estimated_facades.py:31) 在第 42 行隐藏左右 `SourceFace`，增加柜体壳、门板、抽屉面、倒角、缝隙、拉手和螺钉，使用显式 PBR 材质。新增几何位于正确的前后层次，避免重叠闪烁。

该脚本逐属性检查原交互控件不变；保留 LV 区既有 121 个按压候选与 49 个旋转候选，不新增未知控件。新增两个柜面碰撞背板后重新执行物理任务。这次外观通过的范围是“估计结构的大面积涂抹消除与边缘清晰度”，不是恢复未知细节、真实尺度或全部原片身份一致性。

**最终大面积变清晰的决定性处理是可见资产替换。** 原片纹理方法负责保留已有信息，CAD/PBR 估计负责没有可靠照片支持的结构；两者不能混称为高斯模型全部修好了。

### D. 没有用于最终成片的处理

- `repair_panel_texture_substrate.py` 的低阶漆面拟合用于尝试修复控件擦除区域；旧整柜复查明确记录候选被拒收，不能当作已成功的整场景修复插件。
- Difix 是历史二维生成式预览，不修改几何或碰撞；最终估计柜体任务视频没有使用它。
- 4K、抗锯齿、灯光和色彩参数改善输出呈现，不能解决错误几何、未观测纹理和实例对应。

## 3. 星巴克当前做了什么，缺了什么

实际入口为 `examples/starbucks_simready/prepare.py`：`build.py → 五帧人物分割 → clean_transients.py → 巡逻姿态准备 → 可选 run.py`。

| 环节 | 变电站已有做法 | 星巴克本轮状态 |
|---|---|---|
| 已观测纹理选择 | 清晰度与覆盖率排序，连续来源优先，边缘羽化 | `bake_observed_texture()` 按中心性 / 深度平方选像素；没有 Laplacian 清晰度排序 |
| 遮挡与分部件处理 | 平面配准、显式来源范围；后续独立控件 | 有粗点云深度遮挡检查，未建立全场桌椅、门窗、装饰实例的完整表面分组 |
| 不可靠外观退出渲染 | 隐藏原高斯；最终隐藏模糊整柜照片面 | 原高斯仍显示，仅抑制部分人物关联点 |
| 缺失区域 | 参数化门板/抽屉等明确标为估计 | 墙面/长椅贴图缺失处最近有效纹理传播；地面使用原片取色哑光补底 |
| 高清可见资产 | 独立部件、倒角、接缝与 PBR | 主要为平面/支撑结构补面，未完成全场可见家具替换 |
| 视觉验收 | 原片反馈与结构清晰度分别验收 | 承托、碰撞、任务与路线检查通过；整场画质和原片一致性仍未通过 |

当前贴图实现：[structural_completion.py](/home/nvidia/algotithm/simready_infra_pr/src/simready/repair/structural_completion.py:73)。当前方法并非简单调灯光，但外观修复深度明显小于变电站最终版本。

## 4. 为什么没有复用

旧实现位于另一个项目目录 `simready_bridge/scripts/`。新仓库保存了变电站已经处理好的 USD/纹理，并通过 `simulation/asset_bundle.py` 打包已有产物；这不等于修复算法已迁移。

当前 [adapter_catalog.v1.json](/home/nvidia/algotithm/simready_infra_pr/configs/repair/adapter_catalog.v1.json:1) 明确写着：

```json
{
  "execution_enabled": false,
  "adapter_status": "pending",
  "legacy_entrypoint_available_in_this_repository": false
}
```

这些字段分别来自目录级开关和各插件条目。配准、几何修复、纹理来源修复均处于待适配状态，星巴克入口未调用它们。旧脚本又硬编码了柜面平面、照片时间、柜体行列与控件规则；迁移应提取通用算法和输入合同，不能直接把柜体尺寸套到咖啡店。

因此，这次是**外观修复实现迁移未完成，同时星巴克另走了较浅的结构补面路径**。不能将“仓库里有修复插件目录”表述成“已复用变电站修复能力”。

## 5. 应当复用的实现顺序与验收

下表是后续开发拆分，尚未宣称完成代码迁移。

| 顺序 | 通用模块责任 | 旧实现来源 | 星巴克第一批对象 / 退出条件 |
|---|---|---|---|
| 1 | 表面、原片、相机、分割实例与受保护资产绑定 | `fit_panel_reference_atlas.py` + 现有 SfM | 两张任务桌、长椅、墙画、门框、地面；每项有来源和局部坐标，曲面不得用整物体单平面 |
| 2 | 清晰来源选择、逐像素来源与窄边羽化 | `refine_panel_texture_detail.py` | 在原片已观测区域保留细节；按留出视角检查边缘、纹理和布局，不使用检查帧选源 |
| 3 | 对无可靠来源的非关键结构生成独立几何和材质 | `build_panel_estimated_facades.py` 的部件与材质机制 | 桌边、桌腿、椅背、长椅和门框；保留原视频空间布局，估计区显式标记 |
| 4 | 替换对应损坏渲染表面，保留可回滚原层 | `build_panel_corridor_assets.py` 的原层/替换层分离 | 按已绑定对象局部替换；保持其它区域的原片背景，避免再次变成与原视频无关的模板咖啡店 |
| 5 | 独立原片一致性、清晰度和结构检查 | `panel_semantic_teacher.py` + 旧画质交付 gate | 红绿灯位指标替换为通用实例/轮廓/关系指标；近中远、左右、遮挡边界分别检查 |
| 6 | 按实际改动重跑受影响物理、感知及任务，再录制 | 当前任务审计与渲染入口 | 改碰撞须重跑物理；改可见资产须重验 RGB-D/定位；先固定视角预览通过，再录完整两段视频 |

应先完成局部固定相机的“原图 / 当前版 / 候选版”比较，确认可见缺陷确实减小，再支付全片录制成本。验收继续区分：结构清晰度、原片保真、物理可用性和任务成功。估计的高清部件不能自动获得原片保真通过。

## 6. 本次检查产物

- 代码检查：配准、清晰纹理、冻结教师、走廊几何、最终柜体替换、星巴克补面与插件目录。
- 产物检查：三套组合 USD 的实际有效可见性、变电站最终清晰度审查 JSON、变电站右侧整柜 4K 图。
- 保存：本报告、组合可见性 JSON、关键脚本哈希清单。没有修改已冻结场景、运动日志或旧视频。
