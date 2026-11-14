# NeRF2Physics 候选到 USD

主入口已接上可选物性阶段：观测 → 来源绑定重建 → `physical_estimation` → 固化 worker 输入 → USD 构造／独立检查。候选和编译决定分别保留，未提供物性选项时沿用原链。当前支持已有特征的数值回归、上游预测重放和候选导入；**不包含本次新运行的 CLIP 或材料语言模型推断**。

数值适配依据 [NeRF2Physics 官方代码](https://github.com/ajzhai/NeRF2Physics)：可见 patch 特征聚合、归一化相似度、温度 softmax、材料区间混合及 carving 体积积分。厚度积分未在此实现。独立质量评分保留缺失预测的覆盖率分母。合成特征只验证计算与接口，不产生真实公开数据集成绩。

## 入口与产物

```bash
.venv/bin/python -m simready.ingestion.nerf2physics --request request.json --out runs/estimate_001
.venv/bin/python -m simready run --video input.mp4 \
  --reconstruction-manifest reconstruction.json --scene-spec scene-spec.json \
  --physical-estimation-request request.json --physical-timeout 300 \
  --backend local --prepare-only --out runs/video_physical_001
```

已存在候选时使用互斥选项 `--physical-candidate physical-candidate.json`。请求示例生成器见 [examples/nerf2physics](../examples/nerf2physics/README.md)。它默认使用合成对象标识；接入 scene spec 时必须显式采用合法且一致的 USD 对象 ID，并更新关联证据及哈希。

估计在受限时长的 CPU 子进程执行。计划冻结请求与递归文件依赖；阶段记录包含候选、回归细节、原始来源、证据副本和编译报告。交付包内引用可搬移；恢复前重验文件哈希及候选→spec→USD 绑定。空诊断日志保留在磁盘，不作为必须非空的成果文件。

`physical/` 中的 `base-spec.json`、`physical-candidate.json`、`compiled-spec.json` 和 `compilation.json` 分别是原配置、估计、采用配置与决定。`pipeline_operation` 区分本次估计或导入；`execution_mode` 描述候选来源。导入来源和上游 commit 属于提交方声明，不因哈希可复核就成为独立认证的官方模型结果。

## 采用边界

- 必须有可校验的来源、对象关联及尺度文件引用；目标精确匹配一个 box body，不能把整物体质量写成按钮 cap 质量。
- scene spec 需要显式米制坐标与尺度来源。关联和尺度仍是操作者声明，未做独立几何／尺度测量。
- 只采用有 kg 单位的有限质量；已有显式质量冲突则拒绝，保留原运动类型和接触参数。静态对象不会因导入质量自动变为动态对象。
- 材料区间不称为统计置信区间。密度不自动变为质量；相对织物的摩擦不写成任意接触对的摩擦；Shore 硬度不写成弹簧刚度。
- `fresh_visual_inference=false`、`real_world_validated=false`；独立 USD 检查通过只证明采用参数被正确写入，不证明估计准确或机器人任务通过。

## 尚需真实研究验收

当前会话未取得官方权重、固定上游版本完整快照或公开测量数据；网络访问被限制。后续需固定源码、权重、数据、对象划分和运行环境，再执行真实特征／材料推断并独立评分。非合成请求要求上游完整 commit、来源和许可文件及模型身份；这些字段也不代替实际运行凭据。具体当前阻塞保留在 `runs/nerf2physics_adapter_20261114/research-receipt.json`。

本次主链与 USD 的真实 CPU 证据见[收口交付记录](audits/2026-11-14-remaining-delivery.md)，不会以历史模型输出或合成 fixture 充作新的模型推断。
