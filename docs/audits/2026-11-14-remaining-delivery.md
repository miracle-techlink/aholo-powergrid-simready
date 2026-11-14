# 嵌套执行、物性接线与杯任务收口

本轮继续在原工作树上实现，保留历史试验和已有修改。三名 agent 分别负责执行基础设施、物性适配／工作流、杯仿真／候选控制，主 agent 负责物性主链接线、交叉审查与完整回归。没有改机器人成功门槛，没有重新启动已暂停的性能提速研究。

## 已实现和实测边界

| 工作 | 实现与验证 | 仍不能声称的结果 |
|---|---|---|
| 嵌套取消 | 管道 EOF 传递取消／父进程死亡；合作确认后有限清理；三层真实 CPU 子进程及独立进程组回归 | 不能回收故意绕开协议的 detached 进程或不可中断内核 I/O；SSH 远端取消未确认 |
| GPU 租约 | launcher、DAG、worker_groups、公开场景及语义 CUDA 入口共用物理 UUID 锁；密封 FD 委派；受控库存下真实锁竞争 | 真实 GPU 和 Docker daemon 本轮不可用，不能将受控 inventory 测试称为 GPU 行为回归 |
| 容器恢复 | 所属容器有限清理，结果不确定时隔离同一设备租约；recover-device 验证 ownership／已知完整 ID 后解除 | 无法确认终止时保留 NEEDS_REVIEW；未执行真实设备恢复 |
| 仿真职责 | 杯任务拆为 cup_runtime、cup_control、cup_recording；main 只协调初始化／循环／终态；两条旧轨迹各 5400 帧 CPU 命令重建最大差异约 1.79e-7 rad | 不是新动力学重跑；其他 mission／cafe runner 仍需按行为回归逐个拆分 |
| NeRF2Physics 接线 | 数值适配／上游输出重放／候选导入，质量编译，主链阶段恢复，真实 CPU USD 导出及独立质量读回 | 合成特征不是新 CLIP 或材料模型推断；无公开测量集新成绩、实物标定或自动对象关联 |
| 搬杯候选 | 从 HOLD 已发生滑移／倾斜定位，加入有界反馈与释放确认；冻结新留出成对配置与 8 份 USD | 默认保持 baseline-v1；尚无候选成对物理成绩，不能称倾倒已消除或限力已验证 |

详细接口见[执行合同](../execution.md)、[NeRF2Physics](../nerf2physics.md)、[杯控制候选](../cup-stability-candidate.md)。最终代码／检查／主链证据索引保存在 `runs/remaining_delivery_20261114/validation.json`，杯任务独立证据保存在 `runs/cup_stability_20261114/delivery_status.json`。

## 最终验证

- 全仓 **936 passed，1 skipped**；Ruff、格式、十领域架构检查、构包及隔离 wheel CLI 导入通过。
- 实际 CPU 五阶段主链通过；合成候选质量 0.04758 kg，USD 读回约 0.047580000013 kg，独立资产检查 PASS。机器人任务与实物验证均为 false。
- 同输入恢复时五阶段全部哈希核验后复用；候选导入链另行运行通过。没有新提交云重建，也没有新视觉模型推断。
- 最终杯控制源码、工作流与 v3 成对 manifest 的 hash 一致；8 份参数审计 PASS，全部物理运行仍为 NOT_RUN。

## 交叉审查修复

- 成功估计的空 stdout／stderr 不能使必需非空产物门槛误拒绝；诊断仍保留在磁盘。
- 导入候选的来源、对象关联和尺度必须是可校验文件引用，不能用任意对象冒充证据。报告区分导入／本次估计，上游版本声明不冒充独立认证。
- 内部 worker 的 TIMEOUT／CANCELLED／INTERRUPTED 传回主流程，失败阶段保留耗时；最终发布前再次检查取消和预算。
- 语义 CPU 投影和 USD 写入也进入可中止子进程，避免外部模型有期限、内部 CPU 重计算却不受控。
- 搬杯批次收到取消即停止后续样本；成对比较保留已尝试和未尝试状态。比较身份纳入运行配置，不能同一冻结清单换解释器或镜像选项执行。
- 清理中断与任务失败分别保留，默认控制和独立验收不随工程拆分变更。

## 结构与减法

保留十个领域。processes 持有子进程生命期，ResourcePool 持有设备竞争和隔离状态；工作流只组合阶段、产物和验收。NeRF 适配在 ingestion，质量采用规则在 simulation，主链冻结／打包在 workflows，独立读回和评分在 evaluation。控制模块不自行判定任务成功，记录模块不改变控制策略。

此次没有把所有流程强改成通用 DAG，也没有增加新的调度服务。TaskSpec 全量迁移、自动多视图实例关联、更多模型和跨节点调度保留在[规范路线](../software-architecture.md)，不与当前单机合同混称完成。

## 复跑顺序与当前环境

1. `.venv/bin/python -m pytest -q`、Ruff、架构检查和构包验证工程合同。
2. 按 `runs/remaining_delivery_20261114/validation.json` 中命令重跑真实 CPU 五阶段视频主链和同输入恢复，检查 adopted mass 的 USD 读回及禁止夸大声明。
3. 在可用 GPU／Isaac 环境按杯候选文档执行 development 诊断回归，再运行已冻结的新留出 baseline/candidate 成对比较；全部失败计入，不改阈值、不提前晋升候选。
4. 取得官方源码、权重和测量集后，固定环境和对象划分，运行真实 NeRF2Physics 推断及独立评分，再决定模型是否适合默认采用。

当前 `nvidia-smi` 不能访问驱动，Docker socket 权限拒绝，官方模型／数据下载受网络限制。具体探测结果保存于 `environment-limits.json` 和 NeRF 研究回执。此处的缺失是实际模型／物理／容器验收，不以通过更多 CPU 测试替代。
