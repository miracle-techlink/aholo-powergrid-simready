# Starbucks Scene Bench v1

原视频重建的本地评测，借鉴 Video2World 的证据和可执行性分层；不是官方 V2WScore。已有固定估计机位的条件画质基线，仍不是独立几何真值或全店保真度认证。

## 已实跑的画质基线（2026-11-08）

在原视频开发片段中做 CPU 特征检索、2D–3D PnP 和训练特征/验证特征拆分，再由 Isaac Sim 以同一原始重建资产渲染720×1280图像。独立 agent 排除拟合点周围区域重新验证对应，审查墙框、桌杯等语义点。未使用保留片段、未改场景、未新增 Aholo 调用。

`benchmark_measured_v1.json` / `baseline_measured_v1.json` 为本轮有渲染证据的配置与结果；初始 `benchmark.json` / `baseline.json` 保留不变，以免把原先未测结果覆盖掉。逐视图审查见 `measurement/ref*_conditional_baseline.json`，定位及拒绝样例见 `localization/localization_summary.json`。

准入状态 `conditional_estimated` 必须带独立审查报告的哈希，报告须绑定精确的参考帧和渲染图，以及 `approved_regions` 中准入区域的 mask 哈希（整图为 null）；仍禁止自动候选晋升。首轮 ref4 整图、ref3 壁画/门框 ROI 准入；ref3 地面未验证，ref5 拒绝。图像误差包含残余相机、几何、光照与外观误差，不能单独归因于纹理。相机只基于开发帧估计，既不是手机原始标定，也不是新视角测试集。PSNR 不跨不同区域或不同视角直接排名。

## 已实现

- `prepare_references.py` 冻结原视频、16 个抽帧位置、开发/隔离/保留片段和 SHA256。原云端重建已使用完整视频，因此这里只能称优化阶段的保留片段，不能宣称重建模型未见过这些画面。
- `audit_usd.py` 在 CPU 上检查资产、依赖、单位和 Gaussian 数组，内部合法性不等于真实布局正确。
- `bench.py` 校验资产哈希；只有附带人工审查记录和哈希化相机证据的同机位图像才计算像素误差、PSNR 与语义锚点偏差。缺失相机标定时报告 NOT_MEASURED，绝不默认通过。相机证据的内容需要人审，文件哈希不能证明标定正确。
- `cost_guard.py` 提供 SQLite 原子去重和提交次数上限，默认 0；未知结果必须查询原任务，不能因超时直接重提。成功结果复用前复核文件哈希。**目前是独立组件，尚未接入旧 Aholo 客户端，因此不能阻止旧脚本独立发起调用。**

运行：

```bash
cd /home/nvidia/algotithm
OPENBLAS_NUM_THREADS=1 python -m scene_bench.bench runs/starbucks_tour/scene_bench_v1/benchmark_measured_v1.json --output runs/starbucks_tour/scene_bench_v1/baseline_measured_v1.json
OPENBLAS_NUM_THREADS=1 python -m unittest scene_bench.test_bench -v
```

报告中的新 SpatialGen 仅作为用户已经指出布局错误的对照，不是目标布局或几何真值。动力学、抓取成功率不适用于当前无交互示范的重建比较。

## 还缺什么才可比较画质与布局

1. 从实拍视频估计相机内外参、相机轨迹和畸变；用原图门框/墙角/桌边检查对齐并记录残差。不能直接把演示视频的任意机位当匹配视角。
2. 标注可见结构的固定语义锚点和动态人物/反射遮罩；需要米制误差时另补独立尺度证据。候选自身点云不能作真值。
3. 用固定匹配相机渲染基线与候选，再填入 `matched_views`。每条包含 id、split、reference/render 的 path+sha256、camera.status=verified/reviewer/evidence，optional mask 与 anchors。锚点为归一化图像坐标，字段 label/reviewer/reference_xy/render_xy。还必须提供 render_provenance 的 path+sha256；其 JSON 包含 candidate_digest（由 bench.candidate_digest 对完整候选 assets 清单生成）、render_sha256、camera_evidence_sha256，避免误用旧场景渲染。该记录由渲染器生成并经独立审查，不能仅靠候选自述证明真实来源。
4. 校准阈值后再启用自动候选晋升；当前全部候选禁止自动晋升。PSNR、对比度、亮度不能单独作为优化目标。保留片段只做最终审计，反复调参会污染它。

## 建议的 agentic RSI 实验

冻结原视频和布局证据 → agent 对失败归因 → 一次只做一种可回滚局部修改 → 同机位开发集比较 → 布局不退步且画质改善才保留 → 阶段结束查看保留片段 → 将有效动作保存为带适用条件的 skill。

优先级：坐标/SH 转换修复、相机匹配 → 漂浮点和背景杂点清理 → 基于多帧证据的局部外观融合 → 必要时单物体重建。整体重新生成容易改掉真实布局，应放在最后。清理阈值过大会删薄结构，纹理平滑过大会抹掉招牌和桌边，必须看原图证据。

每条经验需记录：问题类别、证据文件、适用条件、修改参数、前后候选哈希、开发集指标、审计结果、失败回滚原因、云任务 id 和已知成本；不把提高亮度当通用“画质提升”经验。不涉及模型权重自训练。

成本目标先用“每个被验收改善的新增付费提交数”。Aholo 实际额度、LLM input/output tokens、GPU 秒分别记录；缺账单写 null。不得把本地动作计数当真实 credits，或把缓存命中估算为已证实节省百分比。比较无经验复用与有经验复用时采用同预算、同开发集、冻结审计集，先做小规模消融。

后续真实调用必须把 reserve/提交/原任务查询/产物校验/record 接到同一适配器。请求指纹包含输入内容哈希、endpoint、参数和可获得的服务版本；服务版本未知时记 unknown，不能承诺跨版本生成等价。占位动作不记作 paid/submitted。

参考：[Video2World](https://github.com/AetherLabsAI/Video2World)，本地源码审计见 `runs/starbucks_tour/benchmark_2610_04432/official_repo_audit.json`。
