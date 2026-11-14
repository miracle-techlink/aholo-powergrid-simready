# 第二轮并行优化：执行合同与工作流收敛

本轮延续[第一轮并行复审](2026-11-14-parallel-review.md)，三个子 agent 分别实现任务环境合同、Isaac 启动生命周期、视频阶段合同拆分，主 agent 集成已有场景预算、迁移示例并统一验收。既有工作树改动保留，未运行研究模型、云作业、新 GPU 仿真或性能提速实验。

## 已实施的模块调整

| 责任 | 唯一实现与调用方 | 结果 |
|---|---|---|
| 环境合同 | `execution/task_environment.py`；schema 校验、graph 一次冻结、worker 消费冻结值 | 环境变化参与显式缓存身份，排队后不重读宿主环境；旧未声明合同任务仍可执行但关闭缓存 |
| 进程执行 | 复用 `execution/processes.run_process`；Isaac 本地 / Docker client、底层 SSH client、已有场景 bundle 导出调用 | 有限期限、所属进程组清理、独立 stdout / stderr；未引入另一套线程池或资源调度器 |
| 容器清理 | `execution/isaac_container.py` | 每次随机 ownership 标签；检查标签后按完整 ID 删除；执行期限和清理宽限分列，清理失败不冒充成功 |
| 启动状态 | `execution/isaac_launcher.py` | 本地 / Docker / 底层 SSH 共用启动回执生命周期；正常退出、异常、超时、取消、中断分别保存，重试不覆盖旧记录 |
| 视频验收 | `workflows/video_contracts.py` | 重建、worker 输入与交付分别有显式合同，fresh / resume 共用；旧 `verify_delivery` 直接重导出，未保留第二份校验实现 |
| 主入口预算 | `video_pipeline` / `scene_pipeline` | `--worker-timeout` 默认 7200 秒，已有场景 `--bundle-timeout` 默认 1200 秒；进入计划并传给执行器；TIMEOUT / CANCELLED 保留至主入口 |
| shader 缓存复制 | `isaac_launcher.seed_runtime_cache` | 源 / 目标软链接和相互重叠提前拒绝；暂存完整复制再发布，普通失败可回退，避免共享可写链接与递归复制自身 |

视频主模块由 639 行降为 487 行，`execute_video` 由 278 行降为 219 行；迁出的验收代码仍作为正式实现保留。本轮增加环境与生命周期功能及负例测试，仓库总行数没有因此下降，不能把文件移动称为总量减法。已删除的是主函数内重复的校验路径和若干直接进程执行路径。

工作流拥有阶段与业务成功判定，执行层拥有进程 / 容器生命周期，合同模块只检查证据并不启动 worker。内层仿真阶段本轮未全部迁成独立进程 session，以免外层取消时留下脱离所属进程组的子任务。后续嵌套取消传播需单独完成合同和测试。

## 接口和兼容性

任务 schema 仍为 v1，新增可选 `environment`：

```json
{
  "environment": {"inherit": ["PATH", "LANG"], "secrets": []},
  "env": {"OMP_NUM_THREADS": "4"}
}
```

显式合同只继承列出的变量，`env` 为非秘密覆盖值。普通有效环境在覆盖后计算摘要，缺失值与空值不同；`secrets` 只保存名称，缺失必需凭据在启动前失败，并禁止本任务读写缓存。旧 v1 无合同任务保持完整继承，但回执标明 `legacy_environment_untracked`，不再误复用缓存。已迁移两个 CPU 示例；修复插件原本强制 `cache=false`，仍保持该语义。

`plan.json` 中的环境字面量被脱敏并标记，不能直接重放这份脱敏回执；执行需使用原始配置。环境保护针对执行器元数据，可信 worker 自行输出的日志与产物没有自动脱敏承诺。详细字段与迁移规则见[执行接口](../execution.md#任务接口)。

`launch_worker` 保留整数返回值，增加可选 `timeout_s=7200`、`cleanup_timeout_s=15`、`cancel_event=None`。启动日志转为回执旁的 stdout / stderr 文件；终端打印路径。执行期限与额外清理宽限不冒充整条 pipeline 的总期限。新视频恢复必须沿用相同 worker 期限；代码身份变化后的旧运行继续按原规则拒绝复用，不自动改写历史记录。

底层 SSH 启动器保存单独的本地回执并将期限传到远端；断连、超时或取消的远端状态为 UNKNOWN。已有场景 `simready run --backend ssh` 仅传递 worker / bundle 期限，SSH 客户端本身尚未迁入该本地回执合同。完整参数见[部署说明](../deployment.md)。

## 交叉审查补修

- 缓存复制目标落在源缓存内部时，暂存区可能递归复制自身：现已在创建暂存目录前拒绝重叠，并规范化 `..`。
- 清理异常路径曾使用 Python 3.11 的 `add_note`：保留 Python 3.10 兼容，附注不能替换原异常。
- Docker 身份解析必须区分容器选项与 worker 参数；worker 的 `--name` / `--label` 不应被解释为容器身份。
- 缓存临时目录清理失败不能遮蔽原复制异常或 KeyboardInterrupt；保留原始错误及可检查的现场。
- TIMEOUT 与 CANCELLED 使用现有状态命名；新视频单列 `stage-failures/`，失败重验不覆盖 `stages/` 中的成功证据。

## 验证与边界

完整记录保存在 `runs/infra_convergence_20261114/validation.json`。全仓最终 **820 通过 / 1 跳过，61.96 秒**，比上轮增加 100 项回归。Ruff、262 个 Python 文件格式、十领域 / 17 个公开函数的架构检查与 diff 检查通过。

验证覆盖真实 CPU 子进程与其所属进程组、显式环境的缓存变化、环境冻结、秘密元数据、旧计划禁缓存、受控 Docker 替身、SSH UNKNOWN、缓存复制失败，以及视频 / 已有场景入口的期限传播。Docker 替身测试不等价于真实 daemon / GPU 清理验证；没有新增机器人或模型成绩。构包使用当前环境，不把它报告为联网干净机器安装。

实际 CLI 集成检查也通过：合成 Gaussian 输入生成真实 USD，独立物性读回 PASS；worker 30 秒期限和独立日志写入启动回执，恢复复用四阶段。16 任务 CPU 夹具首次执行 0 命中、第二次 16 命中；改变一个场景首阶段的声明环境后为 12 命中，其四个阶段重新执行；旧无环境合同的 16 任务可执行且 0 命中。三场景规则修复全部 DEVELOPMENT_PASS。上述命中数是正确性检查，不作为性能加速成绩。

原始集成日志保留：主 agent 提前跑 scene 测试时，新 `timeout_s` 调用已写而 launcher 尚未完成，出现一次接口未就绪失败；接口落地后的 47 项 scene 定向测试通过。此暂态未计入最终通过。

## 下一批收敛顺序

1. 补齐嵌套 worker / 直接阶段入口的期限传播和所属容器实机取消验证，迁移剩余直接 subprocess；继续保持一个执行器实现。将已有场景 SSH 客户端迁至同一回执合同。
2. 将仿真生命周期、观测、控制阶段和轨迹记录分开；保持现有控制参数，使用相同资产的 GPU 行为回归后删除旧分支。搬杯失稳作为独立行为修复，用新留出样本评估。
3. 固定物性候选合同和研究环境，通过现有执行层接 NeRF2Physics，再组合到新视频主链。GaussianProperty 复用相同合同；两者目前均未集成。

统一 GPU 租约、完整跨流程 TaskSpec / attempt 迁移和远端生产调度仍未完成；不能因为启动器已有期限就将 INFRA-01 整批标为完成。
