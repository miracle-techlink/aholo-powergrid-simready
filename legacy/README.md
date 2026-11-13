# 历史实验源码

此目录保留可追溯的实验实现。当前 `simready` 运行时禁止导入这里的模块；这些脚本尚未全部具备可复用 worker 合同，也不随运行时 wheel 安装。

| 目录 | 用途 | 迁移方向 |
|---|---|---|
| `video_ingestion` | 历史抽帧、清晰度筛选、哈希去重 | 新任务使用 `simready ingestion extract` |
| `aholo_reconstruction` | 原视频提交 Aholo、任务轮询与下载 | 新任务使用 `ingestion.reconstruction_stage` 与可恢复的 `aholo_reconstruction`；保留旧脚本作历史追溯 |
| `gaussian_repair` | 高斯缺陷检测、候选修复、渲染与迭代 | 将算法接入 `repair` 注册表，使用统一 `evaluation` 验收 |
| `scene_evaluation` | 参考准备、视角渲染、局部审计、历史场景评测 | 逐个迁移观测适配器，历史指标不混入新协议成绩 |
| `warehouse_experiments` | gsplat 训练/渲染、点云处理、USD / Isaac 实验 | 明确资产、坐标、设备和模型版本后封装 worker |
| `hackathon_submission` | 黑客松提交工具与报告模板 | 仅在对应活动环境中使用 |

旧目录名与新路径的映射见 [迁移指南](../docs/migration.md)。历史 Markdown 内的原实验路径可能指向外部机器或不在仓库中的资产，不作为当前快速开始命令。

这些实验可能依赖未随仓库提供的视频、模型、CUDA、gsplat、Isaac、API 凭据或本地工作目录。此次重构仅迁移名称并更新代码引用，没有重新训练或运行 Isaac。正式接入需要显式输入/输出、不可变版本、资源需求、无副作用的重试策略、独立验收和对应运行证据。
