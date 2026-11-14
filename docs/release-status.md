# main 的范围与待验收项

main 保留核心 video → reconstruction → assets/USD → independent validation → optional Isaac Sim worker。完整功能版本保存在 [`aloho-hackson`](https://github.com/miracle-techlink/video-to-simready/tree/aloho-hackson)。

| 已有实现 | 验证边界 |
|---|---|
| artifact / Aholo / command 后端、baseline 矩阵 | 合同与恢复可测；新算法仍需真实模型／数据对照 |
| 语义代理、逐对象参数、NeRF2Physics 数值接入 | 代理不是完整网格；参数读回不是实物标定 |
| 分层 USD、结构检查、固定观测验收 | 未验证的尺度、外观与覆盖率保持未评估 |
| 嵌套取消、GPU UUID 租约、容器清理与缓存 | 单机执行合同；远端状态不明时不能宣称已取消 |
| Isaac Sim 容器、巡览与物性探针 | GPU 验证与 CPU CI 分开；合成场景不代表真实重建质量 |

优先补充真实 baseline 对照、多视图完整资产、尺度与背景补洞、独立真实物性数据和多场景密封评测。main 不包含机器人专用任务或 Agent 自动修复；这些能力的旧结果只在完整分支解释。

工程验收以 [main CI](https://github.com/miracle-techlink/video-to-simready/actions/workflows/test.yml) 和对应提交的安装包为准。历史报告和数据没有改写，也没有作为新 main 的质量成绩。
