# 接口迁移与历史源码

当前运行时统一在 `src/simready/`，安装包仍叫 `simready-pipeline`。推荐入口为 `simready run --pipeline pipeline.json --out <新目录>`，配置见[规范 pipeline](pipeline.md)。`--video`、`--scene` 和下列兼容入口继续保留。

## 保留的兼容入口

| 旧入口 | 推荐入口 |
|---|---|
| `simready-ingest --video ... --out ...` | `simready ingestion extract --video ... --out ...` |
| `simready-infra ...` | `simready execution run ...` |
| `simready-benchmark run/worker ...` | `simready evaluation run/worker ...` |
| `simready-benchmark plan ...` | `simready repair plan ...` |
| `simready-pipeline run ...` | `simready workflows run ...` |
| `simready-pipeline doctor` | `simready agents doctor` |
| `simready-pipeline skill-show/skill-promote/skill-retire ...` | `simready experience skills show/promote/retire ...` |

`--memory`、`--registry` 分别仍是 `--database`、`--catalog` 的别名。`python -m simready` 与 `simready` 等价；旧 `simready_bridge` Python import 不再提供。

## 资产模块的唯一实现

以下旧 import 都是薄别名，和新模块共享状态；没有保留第二套实现。

| 旧模块（省略 `simready.`） | 当前实现 |
|---|---|
| `simulation.gaussian_asset` | `assets.appearance.gaussian_asset` |
| `simulation.semantic_projection` | `assets.association.semantic_projection` |
| `simulation.physical_spec/physical_authoring/category_priors/physical_candidate` | `assets.physics` 下同名模块 |
| `simulation.scene_preparation` | `assets.compilation.scene_preparation`，旧 `python -m` 仍委派新入口 |
| `simulation.depth_projection` | `geometry.depth_projection` |
| `workflows.video_pipeline.VideoStages` | `workflows.stage_ledger.VideoStages` |

场景专用 `workflows.appearance_collision` 已移至 `examples/starbucks_simready/appearance_collision.py`，不再属于正式运行时接口。更早的函数重命名表见[原迁移记录](https://github.com/miracle-techlink/video-to-simready/blob/6574907ded8aa8dac34de90d988303aede07d8b3/docs/migration.md)。

## 运行与证据

- 旧运行仍按原计划严格恢复。使用 `--pipeline` 时创建新目录；之后 `--resume` 要求计划相同，`--upgrade` 保存新旧计划并仅复用身份一致、重新核验通过的阶段。依赖 secret 的 command 不支持恢复或升级复用。
- scene spec、workflow、protocol、candidate、任务与 SQLite schema 未因清理而变化；旧回执和经验库不批量改写。代码身份变化可能使缓存和 skill 不再兼容，需要重新执行与验收。
- 自定义任务 argv 若引用旧脚本，应迁至当前入口并重新生成示例输入。新研究后端使用显式 command 合同；当前交付仅支持 Gaussian PLY，扩展接口不代表其他算法已验证。

## 移出的历史脚本

旧搬杯辅助脚本 `annotate.py`、`prepare_cafe.py`、`repair_corner.py`，失效抽帧转发，以及历史高斯修复执行层、场景渲染与测量脚本，均保留在[清理前固定提交](https://github.com/miracle-techlink/video-to-simready/tree/6574907ded8aa8dac34de90d988303aede07d8b3)。新抽帧使用 `simready ingestion extract`；搬杯使用 `examples/starbucks_grasp/run.py`。

仍有数值回归依赖的算法、warehouse 研究代码和当前黑客松工具见 [legacy 说明](../legacy/README.md)。保留源码随 sdist 提供，不进入 wheel；报告构建的中间文件不进入源码包。历史 audits、benchmarks 和发布证据保留原样。
