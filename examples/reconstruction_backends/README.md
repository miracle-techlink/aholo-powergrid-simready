# 可替换重建后端

`plan_provider(config, video)` 只读冻结输入、文件哈希、环境身份和声明；`prepare_provider(config, video, out, timeout_s, authorize_cloud=False)` 返回原有重建结果与 `bundle_path`。当前交付路线要求真正可解码的 binary Gaussian PLY，mesh、RGB point cloud 或只有文件后缀的伪 Gaussian 都会被拒绝。

三种显式 provider：

- `artifact`：`manifest: {path, sha256}` 指向已经冻结的 `reconstruction.json`。导入只说明复用既有产物，不说明重新训练模型。
- `aholo`：委托原 Aholo 提交/恢复逻辑；新的云任务仍要求显式授权。未知训练帧、相机坐标和尺度保持 unknown。
- `command`：明确 argv、代码及数据 `input_files`、算法版本及实现哈希、`runtime_identity` 环境版本清单、显式 `environment` 和 `resources.kind`（cpu/cuda）。经统一 run_process 与 device_lease 执行；不是任意代码的安全沙箱。环境版本清单属于操作者声明，不是自动环境认证。

运行以下 CPU 合同演示不下载权重、不读取 GPU：

```bash
.venv/bin/python examples/reconstruction_backends/prepare_fixture.py --out runs/provider_fixture_inputs
.venv/bin/python -c 'from pathlib import Path; from simready.core.artifacts import read_json; from simready.ingestion.reconstruction_providers import prepare_provider; print(prepare_provider(read_json("runs/provider_fixture_inputs/provider.json"), Path("your-video.mp4"), Path("runs/provider_fixture_result"), 30))'
```

`cpu_fixture.py` 写出一个人造 Gaussian，只验证后端替换、来源绑定、数值解码、恢复和期限控制；不是新视频重建，不代表模型质量。将 argv 替换为真实外部模型入口时，需要输出同源 `reconstruction.json`（schema_version=1、status=COMPLETE、source_video_sha256、artifacts 中 kind/path/sha256/bytes）。命令占位符只支持 `{python}`、`{video}`、`{out}`、`{input:NAME}`；可执行文件须使用绝对路径。

通用 bundle 包含算法身份、训练/留出帧声明、坐标/尺度状态、所有产物哈希和冻结依赖。声明帧集需要 evidence，重复和交叉帧会被拒绝；尺度 verified 需要度量单位和 evidence，但仍标注为操作者证据声明，不宣称独立认证。没有帧声明时不能据此报告 heldout 质量。输入依赖必须保留；重定位整个包前须一起迁移并重新冻结依赖。

仓库 `legacy/warehouse_experiments/train_3dgs.py` 使用 Isaac 已知相机位姿与随机初始化训练 gsplat；目前没有找到完成的 VGGT→gsplat 流程。本目录没有声称运行 VGGT，也没有下载或替换其权重。该组合应先作为显式外部 command 实现并提供真实运行证据，或导入其已冻结产物。
