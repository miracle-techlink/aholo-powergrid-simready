# Aholo「60 分钟」计时更正与重建 benchmark 边界

2026-11-12。**此前把 3610.902637 秒称为云端重建耗时，并加本地处理估算约 75 分钟 E2E，口径不成立，应撤回。** 这个数字是客户端两条事件之间的墙钟间隔，不能代表服务端计算或持续排队时间。

本次检查历史成本账本、原始状态快照、下载脚本与恢复日志，并对两个已有 worldId 分别执行一次只读 GET（均 HTTP 200）。没有重新提交重建、没有下载新的模型、没有改变已有任务。脱敏查询结果保存在 `runs/aholo_timing_audit_20261112/read_only_remote_status.json`。

## 1. 成功任务实际输入

- worldId：`3FO4K4RMI0FI`，终态 `SUCCEEDED`，名称 `mv_panel_45s`。
- 输入：`/home/nvidia/Desktop/simready_pipeline/data/mv_panel_45s.mp4`；ffprobe 确认 **45.000 秒、1280×720、30fps、11,314,038 bytes**。
- 档位：历史提交账本明确为 **low**。此前 91.6 秒 + normal 的另一个作业失败。两个不同配置的结果不足以证明平台有固定的「时长×质量上限」。
- 结果：PLY 36,997,781 bytes，SPZ 2,907,955 bytes。下载文件约 39.91 MB（十进制），没有逐文件下载计时。

这不是完整 91 秒视频 normal 档的性能基准。裁短视频或降低档位都改变工作量与覆盖范围，不能作为等价速度优化。

## 2. 原始时间线

以下均显示为 2026-11-10 北京时间；标明时间属于服务器还是客户端。

| 事件 | 时间 / 时长 | 来源与解释 |
|---|---|---|
| 成功尝试的视频上传 | 7.8 秒 | ledger 的 `upload.secs`；**在 60 分钟之外** |
| 服务端创建 | 15:58:16.000 | `remote.json.createTime` |
| 客户端提交回执记账 | 15:58:39.202 | ledger 的第二次 `submit.ts` |
| 服务端最近更新 | 16:03:06.000 | `remote.json.updateTime`；目前和历史查询均为 SUCCEEDED |
| 客户端下载结束记账 | 16:58:50.105 | ledger 的 `done.ts`；代码在 PLY/SPZ 下载之后才写入 |
| 客户端 submit → done | **3610.902637 秒，60分10.90秒** | 旧「60分钟」的真实定义 |
| 服务端 createTime → updateTime | **290 秒，4分50秒** | 同一服务端时钟的元数据更新时间跨度 |
| 服务端 update → 客户端 done 的原始时间差 | **3344.104902 秒，55分44.10秒** | 跨时钟差值，包含尚未拆开的恢复/观测/下载窗口；不是已测 GPU 时间 |

官方定义 `createTime` 为创建时间、`updateTime` 为最近修改时间，两者均是 Unix 毫秒。没有定义后者为 GPU 完成计时，因此 **290 秒也不能标成精确训练时长**。[官方 World schema](https://labs.aholo.ai/api-docs/en/openapi.json)

本地提交回执晚于服务端创建约 23.20 秒。缺少当时请求跨度与时钟同步记录，不能把不同机器的时间直接相减后当成互斥阶段。上表的三条间隔也不能相加。

## 3. 为什么判断客户端恢复/接回结果是优先排查项

`runs/aholo_panel.log` 从恢复任务开始，随后首次查询立即得到：

```text
resume worldId: 3FO4K4RMI0FI
status=SUCCEEDED progress=1.0 ply=Y elapsed=0s
downloaded splats.ply 36997781 bytes
downloaded splats.spz 2907955 bytes
```

这里的 `elapsed=0s` 是恢复后重新启动的计时器，不是重建耗时为零。日志证明下载发生在续跑路径；旧脚本只有 upload、submit、done 三类账本事件，每次查询还会覆盖同一个 remote.json，没有保存完整状态历史或进程心跳。

后续从历史工具执行记录中找到了具体故障：**轮询发生 SSL EOF 异常，脚本没有捕获并重试，因而退出；当时没有自动重启守护，结果接回依赖后续会话恢复检查。** 完整时间线见第 8 节。仍不能精确认定 55 分 44 秒全部是空等：异常本身没有发生时间戳，且服务端 updateTime 不是专门的完成计时。连续正常运行时的 20 秒轮询间隔本身解释不了这段小时级间隔。

失败尝试也存在同样问题：旧客户端事件相差约 89.49 分钟，但对应失败 world `3FO4K4RMJIWY` 的服务端 create→update 只有 6 秒。原始第一条 submit 的 worldId 还是 null，不能严谨地把它直接关联为该 world 的提交回执。失败的内部原因与真实计算耗时均未记录。

## 4. 60 分钟包含与不包含什么

```text
视频准备 → 上传（7.8秒，旧60分钟之外）
       → 创建作业并记 submit
       → [云端排队 / 预处理 / 重建 / 导出，内部耗时未知]
       → [客户端观测、非活跃窗口、恢复、结果下载，未单独计时]
       → 下载后记 done
       → PLY解码 / 坐标对齐 / 资产修复 / 物理 / 录像（旧60分钟之外）
```

官方暴露 PENDING、PREPROCESSING、RUNNING 和终态；当前返回字段没有每个阶段的持续时间、GPU 型号、训练迭代数或 kernel 时间。云端内部可能怎样实现不能由本地日志编造。[官方 API](https://labs.aholo.ai/api-docs/en/openapi.json)

## 5. 优化优先级与 benchmark 合同

| 优先级 | 要做的事 | 验收方式 |
|---|---|---|
| P0 | 在已有 nohup/setsid 脱离基础上，补网络异常的有界重试和进程退出后的自动重启；监控独立于 Agent 会话；持久化 receipt、逐次状态、心跳和恢复事件 | 完成被观测→下载启动的延迟、心跳缺口、恢复耗时；恢复使用原 worldId，不自动重复付费提交 |
| P0 | 固定输入 hash、45秒/完整视频范围、分辨率、质量档位，分别记上传/提交/状态观测/下载/转换/对齐 | 同配置配对复测，禁止只报 submit→done 并标为 GPU 耗时 |
| P1 | 向服务端取得真实排队、预处理、重建、导出 span；没有字段就记 null | 分离排队与计算；本地状态轮询只产生带采样误差的阶段区间 |
| P1 | 相同源覆盖和统一质量门槛下，对照 Aholo 与可控的 VGGT→gsplat 分支 | 检查几何、相机、源资产完整性、多视角保真和后续修复成本；不能仅比较训练秒数 |
| P2 | 在有本地热点证据后做模型驻留、批处理、稀疏/分块、CUDA 算子或多场景并行 | 只归因于我们能修改的执行路径；本机 CUDA 无法直接改第三方云端 kernel |

建议云重建 track 的事件至少包括：`input_ready`、`upload_started/completed`、`submit_started/accepted`、每次 `status_observed`（含服务器原始时间字段）、`monitor_resumed`、`terminal_observed`、`download_started/completed`、`model_import_completed`、`alignment_completed`。每条事件绑定 attempt、worldId、输入和配置 hash，并保存本进程 monotonic 时间及 UTC 时间。单独记录失败、监控离线窗口和 API token/计费证据，缺失字段保持 null。

这次是计时审查与只读取证；没有把上述常驻监控或新云端 benchmark 宣称为已完成上线。

## 6. 对 E2E 估算的更正

「60分钟云重建 + 15分钟本地 = 75分钟」不能继续用于估计新任务。

若仅按历史 **45秒、low** 案例，暂将服务端 290 秒元数据跨度作为规划参考，再加上传 7.8 秒和既有本地服务量 912.77 秒，得到约 **20分11秒 + 尚未单独计时的提交/观测/下载/模型接入等开销**。这只是量级估算，且复用已有场景配方与审核输入，不能当作任意新场景/完整91秒视频的实测 E2E 或交付承诺。

当前可靠结论是：应先消除客户端恢复与结果接回的分钟级盲区，再根据实际云端 span 决定是否优先做重建计算优化。本轮没有证明算力提速了约55分钟，也没有证明真实重建必需60分钟。

## 7. 证据与修订原则

- 原始 ledger：`/home/nvidia/Desktop/simready_pipeline/runs/aholo_panel/cost_ledger.jsonl`
- 原始状态：`/home/nvidia/Desktop/simready_pipeline/runs/aholo_panel/remote.json`
- 恢复日志：`/home/nvidia/Desktop/simready_pipeline/runs/aholo_panel.log`
- 原脚本：`/home/nvidia/Desktop/simready_pipeline/scripts/s1_aholo/submit_panel.py`，done 位于下载之后；转换为 model.npz 是另一脚本。
- 新的脱敏只读查询：`runs/aholo_timing_audit_20261112/read_only_remote_status.json`
- [可复算更正摘要](../benchmarks/aholo_timing_correction_20261112.json)

已冻结的历史 JSON/实验输入保持原样，其中字段 `historical_aholo_cloud_success_seconds` 命名失准；使用本更正摘要解释为 `historical_client_submit_to_download_receipt_s`，不得继续把它当服务端 runtime。当前文档入口同步更正，原始证据不覆盖。

## 8. 后续定位：SSL 异常退出，缺少自动恢复

本节取自 2026-11-10 当时的工具命令/结果记录，补回已被覆盖的旧日志。这里只保存任务相关的执行证据，没有导出完整会话。

| 北京时间 | 已核实事件 | 含义 |
|---|---|---|
| 15:58:53 | 通过 `setsid nohup` 启动 `submit_panel.py` | 当时已经脱离终端，但没有崩溃后自动重启的守护 |
| 15:59:05–15:59:18 | 查询显示 PREPROCESSING，随后 RUNNING | 最初轮询确实正常运行 |
| 16:00:24 | 主会话本轮结束；后续记录直到 16:56 才恢复 | 记录中没有连续的上层巡检 |
| 16:56:02 | 恢复会话收到四个后台 Agent 无完成记录的通知，其中包括 Aholo 产物接入监听 | Agent 监听没有形成可靠的常驻监督；通知不等价于精确的进程退出原因 |
| 16:56:54 | `tail` 查到 `requests.exceptions.SSLError`，内层为 `UNEXPECTED_EOF_WHILE_READING`，请求路径为该任务的状态 GET | 确认网络/TLS 查询异常；此时看到的是已发生的错误，不能把 16:56:54 当作错误发生时刻 |
| 16:57:16 / 16:58:14 | 进程检查没有活跃 submit_panel；日志末尾仍是同一 SSL 错误 | 轮询已经退出，后续没有自动接手 |
| 16:58:30 | 恢复操作清空 aholo_panel.log | 解释当前磁盘日志为什么只剩成功恢复，旧异常要从工具结果找回 |
| 16:58:45.776 | 创建并启动 `/tmp/aholo_watch.sh`，非零退出后休眠60秒再重试，继续使用原 worldId | 这时才补上外层重试；没有重新提交成功任务 |
| 16:58:50.105 | ledger 写入下载完成，已有约39.91 MB PLY/SPZ | 从恢复命令发出到 done 约 **4.33 秒**；不是精确的下载独立计时 |

代码原因很直接：旧轮询循环直接调用 `j = c.get(wid)`；`Client.get()` 中的 `requests.get(..., timeout=30)` 若抛出连接异常，外层没有 `try/except` 重试，所以 Python 进程退出。`nohup/setsid` 可以脱离启动终端，**不会替异常退出的 Python 自动重新启动**。监听 Agent 也未持续接管该故障，最终要等恢复会话才重启。

因此这段时间的主要问题是 **故障恢复延迟和监控生命周期**，不是39.91 MB文件持续下载了55分钟。网络层为什么发生 EOF 尚无包级证据；初始命令只清空了小写代理变量，恢复命令还清空大写变量，但同时增加了重试，不能据此单独认定代理是唯一根因。没有证据显示这里要等本地 GPU 或 CUDA kernel。

恢复时写的临时 shell 守护只说明当时能恢复，并不等于当前仓库已有完整的生产任务服务。下一版应将 GET 重试、状态持久化、进程监督、终态判定、心跳缺口和失败通知统一实现；通过“中断一次状态请求、杀掉一次监控进程、保持同一 worldId 完成下载”的故障注入测试验收。不得把网络失败当作重新提交付费重建的理由。

本机脱敏证据：`runs/aholo_timing_audit_20261112/client_failure_timeline.json`。精确的异常发生时刻、55分钟内部各段分配和原始 EOF 网络原因仍为未知。
