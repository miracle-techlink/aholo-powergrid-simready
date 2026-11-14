# v0.9 分支与接口

`aloho-hackson` 从分离前的 main 提交 `e15d71b77d40aae9867ac00b67adeb4a4af38e01` 创建并推送，保存完整版本。需要历史机器人场景、Agent/Skill、研究工具或报告时使用[该分支](https://github.com/miracle-techlink/video-to-simready/tree/aloho-hackson)；固定快照可用上述完整提交。

main 的发行包仍为 `simready-pipeline`，唯一命令入口为 `simready`：

| 用途 | 保留入口 |
|---|---|
| 规范 pipeline | `simready run --pipeline ...` |
| 直接视频处理 | `simready run --video ...` |
| baseline / 语义 | `simready workflows baseline-matrix/semantic-assets` |
| 采集与候选输入 | `simready ingestion ...` |
| 独立验收 | `simready evaluation ...` |
| 通用 Isaac worker | `simready simulation ...` |
| 任务执行和设备恢复 | `simready execution ...` |

移出 main：`run --scene`、场景预设、搬杯／变电站控制器、agents／repair／experience 命令、性能研究流程，以及 `simready-infra`、`simready-benchmark`、`simready-pipeline`、`simready-ingest` 旧别名。main 不再加载这些实现。

`launch_isaac.py --config` 改为显式 `--worker <Python argv...>`，或 `--doctor`；worker 参数必须放最后。默认镜像改为 `simready-isaac-core:6.1.0`，仅含 Isaac Sim 所需依赖。需要 Isaac Lab 与机器人示例时使用完整分支的部署说明和相应镜像。

资产旧 import 的八个薄别名仍共享规范实现，数据 schema、验收阈值和物性单位保持不变。实现／环境身份改变后需新运行，或按[升级合同](pipeline.md)显式升级；不要改写旧回执来复用失效缓存。
