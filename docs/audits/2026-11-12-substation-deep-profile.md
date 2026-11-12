# 变电站深度 profiling：实际 GPU 热点与后台视口对照

实验 ID：`DEEP-20261112-01`。日期：2026-11-12。承接 [56 分钟复杂度审查](2026-11-12-substation-complexity.md)。

**现在已定位到物理引擎的具体 kernel，并实测了一个渲染候选。** 物理最重的调用是 PhysX `fetch_results()`，其中铰接体 TGS 约束求解是已采集 GPU 时间的第一大项。渲染存在启用的后台预览视口；关闭它后，一组相同 40 帧的渲染调用时间下降 20.84%。这些是新诊断结果，不是完整 E2E 已优化的声明。

## 1. 为什么不能只按循环次数排序

关注的是 `调用次数 × 单次成本`，并区分嵌套范围。1.48 亿个接触向量的范数由 NumPy 批量处理，并不意味着有 1.48 亿次 Python 函数调用。新诊断轮中，整段范数与掩码/索引/最大值筛选只有 2.7654 秒；相比之下，14,264 次物理结果获取用了 141.3319 秒。

历史 55:50.67 窗口仍由三轮完整录制 22:16.10、三轮物理 14:23.30、工程区间 16:10.93、资产 2:53.56、非重叠审计 0:06.78 组成。优化工作针对录制和物理；这里没有重写历史账本。

## 2. 实际执行了什么

- 对冻结任务运行一次完整物理诊断，14,264 步；循环内增加明确的 host 计时边界，另计 getter、CPU 转换、PhysX 提交和结果获取。
- 用 Nsight Systems 2025.3.2 采集三个 100 步窗口：2000–2099 导航、10000–10099 按压、12000–12099 回撤。使用 CUDA、NVTX、OS runtime trace。
- 录制 96 个连续帧进行渲染诊断；再用两个相同的 40 帧窗口做后台视口开/关的单组对照。均为原轨迹帧 350–389、1080p、首帧 16 子帧、其余 2 子帧。
- 后两次在输出 ordinal 20–31 采集同一个 12 帧窗口的 Vulkan batch trace。

所有运行使用隔离脚本和新产物目录。没有修改 main 的生产运行逻辑、冻结基准或交付视频。原有 Isaac streaming 进程和桌面负载继续存在，因此不能将本轮与下午历史数字的差异归为优化收益。

## 3. 物理大头已拆到引擎内部

完整新诊断轮的 host 时间如下；子项存在包含关系，不能把整表相加。

| 调用/代码区域 | 次数或范围 | 秒 |
|---|---:|---:|
| 主循环 sim.step | 14,264 次 | **145.4017** |
| └ PhysX fetch_results | 14,264 次，包含在上行 | **141.3319** |
| └ PhysX simulate | 14,264 次，包含在上行 | **2.1426** |
| cpu 转换 wrapper | 198,865 次，分布在其他范围中 | 11.2166 |
| 设置目标、力矩及 write_data_to_sim | 14,264 组，每组三个语句 | 8.2654 |
| 物理后根状态读取及 Euler 转换 | 14,264 组 | 6.4687 |
| PD 力矩等设备侧表达式的 host 调用 | 14,264 组 | 5.3466 |
| 在线机械臂 segment 碰撞检查分支 | 执行 889 次检查；含每步分支判断 | 4.3953 |
| 环境接触矩阵 getter、回 CPU 和重排 | 14,264 次 | 2.4510 |
| 接触矩阵向量范数 | 14,264 批 | **1.5987** |
| 接触掩码、索引和最大值筛选 | 14,264 批 | **1.1667** |

`fetch_results()` 占本轮 `sim.step` 的 **97.20%**。这不是说 `fetch_results` 自己在 CPU 上执行了全部物理计算；它等待物理任务及结果完成，GPU 求解与任务调度的成本会落在这个宿主等待边界。

所有 `loop.*` 区域的互斥 host 时间合计 215.0796 秒，主任务自报循环墙钟 237.1726 秒；其余约 22.0930 秒包括诊断 wrapper、NVTX/cProfile、capture 开关与未包围的循环开销。这是诊断运行，不能当作原版循环固有的 22 秒开销。初始化至导出 spans 为 251.1278 秒。

## 4. 真正的 GPU kernel 排名

三个窗口共 300 步，实际采到 **147,918 次 CUDA kernel，平均 493.06 次/步**。以下按三个窗口的 kernel 累计设备时间合并；它不是整轮 GPU 时间的外推。

| GPU kernel | 300 步内调用数 | 平均每步 | 累计设备时间 | 作用 |
|---|---:|---:|---:|---|
| **artiSolveInternalConstraintsTGS1T** | **9,600** | **32** | **0.6370 s** | 铰接体内部约束求解 |
| stepArticulation1TTGS | 7,200 | 24 | 0.2000 s | 铰接体子步推进 |
| artiApplyTgsSubstepForces | 7,200 | 24 | 0.1539 s | TGS 子步力作用 |
| artiPropagateImpulses2TGS | 9,600 | 32 | 0.1352 s | 铰接体冲量传播 |

第一项平均约 **66.36 μs/次，占已采集 kernel 累计时间的 39.56%**。它应排在自定义算子/引擎优化的前列，而不是先写一个索引筛选 CUDA kernel。

当前机器人求解参数为位置迭代 24、速度迭代 8，与采样中多个 kernel 每步 24/32 次的规律相吻合。Omni Physics 官方文档说明 GPU 求解的有效迭代数与未休眠对象的最大设置有关，因此需要同时检查活跃场景对象和求解配置，不能只看机器人自由度。[Omni Physics Simulation Control](https://docs.omniverse.nvidia.com/kit/docs/omni_physics/110.1/dev_guide/simulation_control/simulation_control.html)

每 100 步窗口观察到约 3,406–3,740 次 `cudaStreamSynchronize`；小 kernel、多次提交和同步都值得处理。它们分布在引擎和状态读写中，不能将所有同步都算到我们写的 `cpu()` 函数上。

采样窗口中，本进程可见的 CUDA kernel/内存活动区间合并后约占完整控制循环时间的 28.6%–32.7%；这不是整卡利用率，也不表示其余时间全部可以删掉。`fetch_results` 内可见设备工作与宿主等待也相互重叠。线程等待总和能大于窗口墙钟，不能把多线程 futex/nanosleep 时间加成新的账本。

## 5. 渲染找到可测试的额外工作

运行时 inventory 确认有三个 RenderProduct：

| 渲染目标 | 分辨率 | 相机 |
|---|---|---|
| 默认后台预览视口 | 1280×720 | /OmniverseKit_Persp |
| 从物理场景保留的 RGB 相机目标 | 1280×720 | /World/TaskRGBCamera |
| 当前交付录制目标 | 1920×1080 | /World/TaskReviewCamera |

后台视口存在且 `updates_enabled=True`。候选仅执行：

```python
from omni.kit.viewport.utility import get_active_viewport
viewport = get_active_viewport()
if viewport is not None:
    viewport.updates_enabled = False
```

候选没有降低分辨率、子帧数或帧数；没有禁用目标成片相机；没有删除物理碰撞或修改轨迹。保留 RGB 相机目标的实际成本仍未做独立消融，不能说三个目标每个都完整重复渲染了一遍。

| 相同 40 帧短片 | 原配置 | 关闭后台预览 | 变化 |
|---|---:|---:|---:|
| rep.orchestrator.step 合计 | **23.2395 s** | **18.3965 s** | **减少 20.84%** |
| 外层进程时间，含 Nsight/启动/收尾 | 49.2410 s | 43.9960 s | 减少 10.65% |
| 相同 12 帧 trace 中的 render step | 6.2426 s | 4.9954 s | 减少约 20.0% |
| 该 trace 可见 Vulkan 工作区间并集 | 5.0763 s | 3.9767 s | 设备侧工作区间也缩短 |

这是**单组短片诊断对照**，没有重复随机化顺序或隔离后台负载，因此 20.84% 是本次观测，不是普遍加速保证。Vulkan batch 时间是提交工作区间，不能当作单个 shader 的纯计算时间；RTX 渲染热点不能靠给 NumPy 换一个 PyTorch CUDA 算子来直接解决。

对两个 40 帧视频逐帧解码比较：平均 MAE **1.3526/255**，平均 PSNR **42.93 dB**，最大单帧 MAE **3.3377/255**。存在像素差异，尚无预先约定的本项验收阈值；这次没有宣称画面完全等价，候选**未合入交付版本**。

## 6. 验证结果与测量限制

物理任务成功，独立审计 20 项通过，停止事件仍在 21.852 秒，在线路径检查 889 次，输出仍是 714 帧轨迹。但与冻结轨迹不是逐位一致：机器人位置分量最大差约 **0.355 mm**，四元数分量最大差 0.0007046，按钮位置分量最大差约 **0.0072 mm**。本次是诊断运行，不能把它冒充已经通过严格等价门槛的性能候选；差异的具体来源未隔离。

Nsight 采集也保留了限制：

- 当前 Linux perf 权限不支持 CPU 调度采样；使用 CUDA/NVTX/API trace，没有修改系统权限。
- 当前驱动配置不支持统一内存迁移 trace，未报告该指标。
- 第一份 Vulkan individual trace 超出每帧 8192 个 GPU 时间戳限制，有明确丢失记录；已保留失败证据，渲染对照使用后续 batch trace，没有这一超限告警。
- 限定 capture 窗口仍有事件完整性提醒，因此 kernel 次数写作“采到”，不承诺窗口外的精确总计；Vulkan 图形工作使用 Vulkan 表，不能用 CUDA 表没有渲染 kernel 来推断 GPU 没干活。
- 物理外层 Nsight 默认等待派生进程，主应用退出后仍等待，最终外层记录 320.4278 秒；已在 trace 生成后结束这个诊断 wrapper。这个数字不是物理 worker E2E。后续渲染设置 `--wait=primary`。

## 7. 下一步按实际收益排序

1. **后台视口优化候选先做完整录制和图像质量验收。** 已有短片收益证据；需要正式配对复测后才能推广。
2. **检查录制副本遗留的 RGB 相机 RenderProduct。** 只在录制副本中做移除/禁用对照；物理视觉识别仍需要这一路相机。
3. **针对 TGS 铰接体求解及 kernel 提交链优化。** 先进一步查主要 kernel 的占用、依赖与启动成本，再决定是否改 PhysX 引擎路径。不能假设用 `torch.compile(sim.step)` 就能捕获引擎内部执行。
4. **合并设备状态读取和控制写入，减少同步。** 当前命令写入 8.27 秒、状态读取若干秒，是真实的第二梯队热点。
5. **固定索引、FK 缓存、接触归约作为小项推进。** 它们可优化，但单独的范数/掩码合计只有约 2.77 秒。

降低 24/8 求解迭代数可能减少 kernel 数量，但会改变仿真精度，属于需要重新验收的质量—速度实验；本轮没有偷偷改动该参数。GPU trace 的读取和重叠区间解释采用 [Nsight Systems 官方指南](https://docs.nvidia.com/nsight-systems/UserGuide/index.html)。

## 8. 原始证据与代码

- [汇总 JSON](../../runs/substation_deep_profile_20261112/summary.json)
- [完整物理 host 计时](../../runs/substation_deep_profile_20261112/physics_spans/host_spans.json)
- [导航 GPU 采样](../../runs/substation_deep_profile_20261112/physics_trace_1.json)、[按压采样](../../runs/substation_deep_profile_20261112/physics_trace_2.json)、[回撤采样](../../runs/substation_deep_profile_20261112/physics_trace_3.json)
- [原配置 Vulkan trace](../../runs/substation_deep_profile_20261112/render_batch_trace.json)、[关闭视口 trace](../../runs/substation_deep_profile_20261112/render_no_viewport_trace.json)
- [运行时渲染目标 inventory](../../runs/substation_deep_profile_20261112/render_no_viewport_clip/render_product_inventory.json)、[40 帧画面对比](../../runs/substation_deep_profile_20261112/viewport_video_comparison.json)
- [计时与 capture helper](../../runs/substation_deep_profile_20261112/deep_profile.py)、[隔离插桩生成器](../../runs/substation_deep_profile_20261112/build_diagnostic.py)、[trace 分析脚本](../../runs/substation_deep_profile_20261112/analyze_capture.py)
- [关闭视口候选脚本](../../runs/substation_deep_profile_20261112/workspace/scripts/render_panel_stop_no_viewport.py)

同一运行目录中保留 `.nsys-rep`、SQLite、Python 栈采样、原始 stdout/stderr、配置与哈希。生产基准继续参照 [Infra / Kernel 优化账本](../benchmarks/substation_optimization_benchmark.md)。
