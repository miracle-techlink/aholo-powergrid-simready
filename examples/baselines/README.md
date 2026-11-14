# 冻结基线矩阵

矩阵按顺序调用现有规范 pipeline，默认止于 reconstruction，验证后端替换与回执接口。
本目录只有 synthetic CPU command 与 artifact 导入示例：生成的是测试图案视频和单点 Gaussian 文件，不是 VGGT、NeRF 或其他模型训练成果，也不能用于质量排名。

在仓库根目录运行（只需 CPU、ffmpeg 与项目虚拟环境）：

```bash
.venv/bin/python examples/baselines/prepare_fixture.py --out /tmp/simready-baseline-inputs
.venv/bin/python -m simready.workflows.baseline_matrix --matrix /tmp/simready-baseline-inputs/matrix.json --out /tmp/simready-baseline-run --dry-run
.venv/bin/python -m simready.workflows.baseline_matrix --matrix /tmp/simready-baseline-inputs/matrix.json --out /tmp/simready-baseline-run --until reconstruction
```

每次使用新的输出目录。`--dry-run` 不创建输出或启动工作进程。只有显式 `--full` 才继续后续资产、物性和交付阶段；GPU/云后端沿用原有授权与资源合同。`matrix-plan.json` 冻结输入、stage identity、算法与运行环境；`matrix-result.json` 保留每个成员的成功、失败、未执行和未知质量状态，不生成排名或晋升默认后端。

`mode` 分为 `fixed_observations`（固定观测与非重建配置）和 `end_to_end`（端到端管线差异）。前者要求相同帧/尺度声明、非重建阶段配置及共享 camera evidence。满足声明检查最多报告 `DECLARED_INPUTS_MATCH`，不是独立训练泄漏证明：后端仍可能访问完整视频。缺声明报告 `COMPARABILITY_UNCONFIRMED`，仍可执行接口验证。

质量评估是可选项。将 `purpose` 设为 `quality_comparison`，`evaluation.protocol`、`evaluation.camera_evidence` 和每个成员的 `candidate` 均提供 `{path, sha256}` 引用；`evaluation.timeout_s` 默认 300。仅真实留出参考帧、明确且不相交的训练/留出声明、与本次重建 PLY 同 hash 的 candidate artifact 才能接入已有独立 verifier。candidate 还必须带：

```json
{"comparison_evidence": {
  "source_video_sha256": "<视频 SHA256>",
  "reconstruction_sha256": "<本次 PLY SHA256>",
  "camera_sha256": "<共享 camera evidence SHA256>"
}}
```

protocol/candidate 的 `fit_frame_ids`、`fit_image_sha256` 必须与重建 training 声明相同，protocol 所有参考视图必须属于 heldout 声明。独立进程只复算原始文件与指标；训练访问、相机对应和渲染来源仍是调用方声明。缺证据、synthetic/estimated 参考或 synthetic 后端保留 `NOT_EVALUATED`。sealed 协议返回 scorecard 及 WITHHELD 诊断摘要，不接入修复反馈，不作后端选择。
