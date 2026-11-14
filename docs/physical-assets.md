# 语义与逐对象物性

`simready workflows semantic-assets` 组织分割、三维关联、物性声明、USD 编译与独立读回。公开合成输入和完整命令见[语义示例](../examples/semantic_assets/README.md)、[物性示例](../examples/physical_assets/README.md)；质量估计接线见 [NeRF2Physics](nerf2physics.md)。

| 阶段 | 实现 | 合同 |
|---|---|---|
| 分割 | ingestion.grounded_segmentation / semantic_segmentation | 图像身份、原始 mask、冲突与拒绝清单；推断与导入分开 |
| RGB-D 观测 | simulation.depth_observation / camera_calibration | 同帧 RGB、camera-z depth、K/c2w 与来源 |
| 三维关联 | assets.association.semantic_projection | mask 与标定／深度或 PLY；保留原像素、点索引和歧义 |
| 参数 | assets.physics | SI 单位、接触对、来源／范围、质量、惯量、运动类型 |
| 编译 | assets.compilation.scene_preparation | 外观、碰撞、对象层、catalog 与逐对象写入 |
| 验收 | evaluation.physical_assets / physical_receipt | 独立进程重开 USD，对比输入、实际绑定和文件身份 |
| 响应 | simulation.physical_probe | 冻结合成协议、PhysX 轨迹与解析对照 |

深度路径使用 `p_world = T_c2w · [z K⁻¹(u,v,1), 1]`；RGB、深度与相机必须匹配。PLY 路径用可见中心近似投影，不模拟完整高斯透明度，因此关联可能需要人工复核。两个路径当前生成可见表面盒体代理，不推断完整遮挡几何。

默认类别先验只生成静态接触参数，不从类别编造质量。动态对象需显式质量、尺度与运动模型；质心和惯量随局部坐标与单位声明。物性来源区分先验、推断、测量，未知值保持未知，摩擦不能脱离接触对解释。

候选缺测或关联不清时保留 `REVIEW_REQUIRED`；独立读回通过仅说明参数被正确写入，不证明材料、质量或碰撞代理准确。门、抽屉等关节及真实任务还需各自几何和动力学验收。

模型、投影与 USD 可以使用独立解释器。有限总预算、阶段期限、继承取消和 GPU 租约复用 execution；失败保留日志和终态，不发布假成功。
