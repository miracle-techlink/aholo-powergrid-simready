# 重建后端与 baseline 比较

一个生成器提供单后端配置和两成员矩阵。只需 CPU、FFmpeg 和项目依赖；生成的是合成视频及单点 Gaussian，不是实际模型重建或质量成绩。

```bash
python examples/reconstruction/prepare_fixture.py --out /tmp/reconstruction-inputs
```

输出 `provider.json`、`synthetic_cpu.json`、`artifact.json`、`matrix.json` 以及所需的来源与环境文件。

## 单后端

```bash
simready run --pipeline /tmp/reconstruction-inputs/synthetic_cpu.json \
  --out /tmp/reconstruction-run --until reconstruction
```

`provider.json` 也可作为自有 pipeline 的 `stages.reconstruction` 配置；复制配置时保留其显式 base_dir 和冻结依赖。当前支持：

| provider | 用途 |
|---|---|
| `artifact` | 校验已有 reconstruction.json 的来源与哈希，导入不表示新推断 |
| `aholo` | 云任务提交／恢复，新任务须显式授权 |
| `command` | 冻结 argv、实现、环境、CPU/CUDA 资源，经统一执行器运行 |

真实算法替换 `cpu_fixture.py` 时，在输出目录写 `reconstruction.json`：`schema_version=1`、`status=COMPLETE`、`source_video_sha256`，以及 `artifacts` 的 `kind/path/sha256/bytes`。支持 `{python}`、`{video}`、`{out}`、`{input:NAME}` 占位符；解释器／命令使用明确路径，模型与数据纳入依赖身份。

主链要求可解码 binary Gaussian PLY；mesh 或 RGB point cloud 需要另接 converter 和验收。环境版本清单是操作者声明；未知训练帧、相机和尺度保持未知。完整接入规则见[工程指南](../../docs/README.md#开发与扩展)。

## 比较后端

```bash
simready workflows baseline-matrix --matrix /tmp/reconstruction-inputs/matrix.json \
  --out /tmp/baseline-run --dry-run
simready workflows baseline-matrix --matrix /tmp/reconstruction-inputs/matrix.json \
  --out /tmp/baseline-run --until reconstruction
```

默认止于 reconstruction，只有 `--full` 才继续资产和交付。dry-run 不创建输出；实际运行使用新目录。矩阵保留所有成员的成功、失败、未执行及未知质量，不自动排名或晋升后端。

`fixed_observations` 比较固定观测和非重建配置，`end_to_end` 比较端到端差异。声明一致最多得到 `DECLARED_INPUTS_MATCH`，不证明模型未访问留出视频；缺声明时为 `COMPARABILITY_UNCONFIRMED`。

真实质量比较需 `purpose=quality_comparison`、冻结的 `evaluation.protocol`、`evaluation.camera_evidence` 和每个成员的 `candidate`，均使用 `{path,sha256}`。候选还须绑定视频、重建和相机：

```json
{"comparison_evidence": {
  "source_video_sha256": "<视频 SHA256>",
  "reconstruction_sha256": "<本次 PLY SHA256>",
  "camera_sha256": "<共享 camera evidence SHA256>"
}}
```

protocol/candidate 的 fit 声明须与重建训练帧一致，参考视图须属于不相交的 heldout 集。缺证据、合成／估计参考或合成后端保持 `NOT_EVALUATED`；sealed 诊断为 `WITHHELD`。现有示例没有复现 VGGT、AnySplat 等真实模型。
