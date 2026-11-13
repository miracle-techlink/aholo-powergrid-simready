### 入口与工作流 workflows：把一次命令组织成可验收的交付

入口层负责选择合同，工作流层负责履行合同。`cli.main()` 通过 `COMMANDS` 将子命令映射到域入口，用 `parse_known_args()` 留下该域自己的参数，再动态导入所选模块；因此顶层帮助不需要初始化 Isaac 或云客户端。`pipeline_entry.main()` 进一步将互斥的 `--video` 与 `--scene` 分流到两种不同流程。`compat_cli` 中的 `pipeline_main()`、`infra_main()`、`benchmark_main()` 和 `ingest_main()` 只把旧命令参数改写为规范入口调用，不保留另一套执行实现，也不兼容旧 Python 导入路径。扩展命令时，应在域内定义参数与返回码，再将入口加入路由表；不应把业务状态塞进顶层解析器。对应实现见 [cli](../../src/simready/cli.py)、[compat_cli](../../src/simready/compat_cli.py) 与 [pipeline_entry](../../src/simready/workflows/pipeline_entry.py)。

新视频流程的状态所有者是宿主侧 `video_pipeline.execute_video()`。`make_plan()` 将源视频 SHA-256、场景规范、渲染参数、后端及包内 Python 源码摘要纳入计划身份，随后顺序执行 `observations → reconstruction → worker_inputs → scene_delivery`。前两步调用摄取域；第三步复制经过校验的 PLY，生成 `scene-spec.json` 和绑定视频、PLY、规范摘要的 `input.json`；第四步经 `launch_worker()` 启动 `video_execution.execute()`。工作进程再次检查输入，依次调用 `simulation.scene_preparation → evaluation.scene_package → simulation.scene_video`，其中 `--prepare-only` 跳过环境检查和渲染。宿主的 `verify_delivery()` 再检查工作进程状态、必需文件、结构审计、输入摘要及录像回执，才能给出 `SCENE_EXPORTED` 或 `SCENE_SURVEY_READY`。这条真实的“宿主编排 → 独立工作进程 → 结构审计 → 宿主验收”链不自动调用机器人、任务审计或 Agent。`VideoStages.run(name, action)` 为每个已完成阶段保存结果字典和产物清单；`--resume` 只有在计划身份、阶段名及所有产物大小和摘要仍匹配时才复用结果，失败尝试保留在独立目录。它是本次运行的阶段恢复机制，不是通用 DAG 调度器。对应实现见 [video_pipeline](../../src/simready/workflows/video_pipeline.py) 与 [video_execution](../../src/simready/workflows/video_execution.py)。

已有场景流程由 `scene_pipeline.prepare_plan()` 选择预设、验证资产包与输出范围；`--bundle-spec` 可先从已准备好的资源导出资产包。`run_pipeline()` 再经 `launch_worker()` 调用 `scene_execution.execute_scene()`，完成环境预检后进入 `scene_delivery.run_scene_delivery()`：变电站链为“参考抽帧 → `substation_mission` 物理任务 → `mission_audit` → `mission_video` 实测回放”，星巴克链为“参考抽帧 → `cafe_video` 的 G2 根位姿编排与录像”。工作进程和宿主均通过 `verify_scene_delivery()` 核查交付，避免把进程返回零直接当作验收通过。该模式要求新输出目录，默认生成唯一运行名，不提供视频模式的同目录 `--resume`。两种模式均默认 Docker，也支持选定解释器的本地执行；只有 `--scene` 提供 SSH 后端，`remote_plan()` 把命令委派给远端仓库中的 Docker 入口，资产和仓库必须已经在远端。SSH 不传输资产，也不保证断线后远端进程已终止；详细回执由远端保存。对应实现见 [scene_pipeline](../../src/simready/workflows/scene_pipeline.py)、[scene_execution](../../src/simready/workflows/scene_execution.py) 与 [scene_delivery](../../src/simready/workflows/scene_delivery.py)。

失败责任沿调用层次分开：启动前的输入或路径拒绝由入口报告 `INVALID`；进入受管理运行后，宿主写 `summary.json`，工作进程写 `pipeline-status.json`，记录当前阶段、失败原因或中断状态，阶段日志保留原始执行证据。`--dry-run` 只做只读计划与输入检查，不启动进程、云任务或 GPU，也不写运行目录；它并不证明运行环境已经可用。自适应修复和性能实验则有独立入口：`load_workflow_config()` 校验预算及输入，`run_repair_workflow()` 管理候选基线与经验，`run_repair_trial()` 执行插件生成的 DAG、调用 `verify_candidate()`，并返回不含晋升副作用的 `RepairTrialResult`；外层才根据 `compare_scorecards()` 决定是否更新基线。`run_performance_workflow()` 管理固定协议下的重复计时与独立质量检查。这些机制可复用执行器与验收器，但不会因调用 `simready run` 自动开启。新增视频阶段应同时声明结果与可校验产物、更新计划身份和交付验收；新增修复动作则接入 `RepairPluginRegistry.build_task_plan()`，保留独立验收路径。

<!-- table: 入口分发、阶段编排与候选修复分别拥有不同的状态。 -->
<!-- columns: 0.90,1.00,1.10 -->

| 内部构件 | 组织方式与调用点 | 持有的状态或输入输出合同 |
|---|---|---|
| `cli.main()` / `pipeline_entry.main()` | 动态域分发；视频、已有场景互斥分流 | 参数与退出码；不持有业务运行状态 |
| `make_plan()` / `VideoStages.run()` | 固定阶段顺序、运行锁、产物逐项核验 | `run-plan.json`、`stages/*.json`；复用绑定计划身份 |
| `video_execution.execute()` / `verify_delivery()` | 工作进程建场景与结构审计；宿主复核 | `input.json → delivery.json`；绑定视频、PLY、规范及交付文件 |
| `prepare_plan()` / `execute_scene()` / `run_scene_delivery()` | 预设与资产包校验、环境预检、场景专用步骤 | 配置、资产包 → 任务或编排录像；`verify_scene_delivery()` 决定验收 |
| `run_repair_workflow()` / `run_repair_trial()` | 外层决策和基线管理，内层执行与比较 | `RepairTrialResult` 传回候选、评分、证据、错误；外层拥有晋升责任 |
| `run_performance_workflow()` | 独立性能实验编排 | 冻结协议、重复试验、质量检查与接受记录；不是默认视频步骤 |

### 摄取模块 ingestion：把外部视频与重建服务变成可追溯输入

摄取域首先解决“后续计算究竟使用了什么”这一问题。`video_frames.extract_frames()` 用 FFmpeg 采样，在 CPU 上计算拉普拉斯方差和梯度哈希，以清晰度阈值和相邻保留帧的哈希距离筛选观测，再写出 `manifest.json`，记录源视频及每帧摘要、采样序号、名义时间和 train/heldout 分组。源视频在处理前后重新计算摘要，空结果不能生成成功清单。函数默认采样率为 2 帧/秒、清晰度阈值 60、去重距离 6；新视频入口明确传入后两项为 0，以保留更多参考观测，但零距离梯度哈希仍会被去重。已有场景入口调用独立抽帧命令，保留其默认筛选。分组本身不意味着已经完成盲测评估。观测帧与云重建是来源相同的两条数据路径：当前 Aholo 客户端上传完整原视频，不读取这些筛选帧。见 [video_frames](../../src/simready/ingestion/video_frames.py)。

云接入分为协议客户端和作业生命周期两层。`aholo_client.Client.upload()` 处理上传令牌、单次或分块上传及上传状态查询，API 授权会话与上传会话分开；真正的重建计算由 Aholo 服务端执行。`aholo_reconstruction.run(..., allow_submit=False)` 在作业锁内决定新建或恢复：新建需显式授权，提交前持久化 `submit_intent.json`，收到服务端作业标识后写 `receipt.json`；只有提交意图而无回执时，提交结果不确定，程序拒绝再次付费提交。恢复时核对视频摘要和 `world_id`，只监控已有作业。下载使用独立请求，先写临时文件，再以大小和 SHA-256 记录到 `assets.json`；已有匹配文件直接复用，后续文件下载失败也保留此前有效产物。`poll_existing_job()` 负责有限等待与可重试传输错误，`supervise_process()` 为上传、监控、下载所在工作进程施加共同墙钟预算。有限等待到期会留下回执供后续恢复，不意味着取消供应商作业。见 [aholo_client](../../src/simready/ingestion/aholo_client.py) 与 [aholo_reconstruction](../../src/simready/ingestion/aholo_reconstruction.py)。

工作流只依赖较高层的 `prepare_reconstruction(video, out, *, artifact_manifest=None, submit=False, timeout=7200)`，不直接理解供应商响应。它将已完成的本地清单、显式导入的产物或 Aholo 作业回执统一为 `reconstruction.json`：要求 `schema_version=1`、`status=COMPLETE`、匹配的 `source_video_sha256`，以及位于清单目录范围内、大小和摘要正确的 PLY/SPZ 文件，其中 PLY 必需。返回值另含 `manifest_path`、`ply_path`、`ply_sha256`、`cache_hit`、`new_submission`、`resumed`，使上层能区分离线复用、新提交和作业恢复。完整本地产物无需密钥或网络；需要继续云监控时才要求密钥，首次新提交仍必须有 `submit=True`。坏清单、错误源绑定或未知提交结果会被拒绝，而非降级成一次新提交。扩展其他重建来源时，可以先生成同一标准产物清单，经离线导入复用后半程；当前没有默认 VGGT 执行器或通用供应商插件注册机制。该层只验证来源绑定与文件完整性，明确不证明几何质量、米制尺度或物理可交互性；本地哈希和复制也不属于云工作进程的超时范围。见 [reconstruction_stage](../../src/simready/ingestion/reconstruction_stage.py)。

<!-- table: 摄取域将视频观测与服务端重建统一为可核查的来源合同。 -->
<!-- columns: 0.90,1.00,1.10 -->

| 内部构件 | 核心算法或状态转换 | 输入输出与失败责任 |
|---|---|---|
| `extract_frames()` / `gradient_hash()` / `hamming_distance()` | 固定频率采样、清晰度筛选、局部去重、确定性分组 | 视频 → 帧与来源清单；源变化或空结果拒绝成功 |
| `aholo_client.Client` | HTTPS 上传协议、令牌会话隔离 | 本地完整视频 → 服务端视频地址；不在宿主计算重建 |
| `aholo_reconstruction.run()` | 授权 → 提交意图 → 作业回执 → 监控 → 校验下载 | `world_id` 与视频摘要共同绑定作业；恢复不重新提交 |
| `prepare_reconstruction()` | 标准清单校验、离线导入、完整回执复用、受监督恢复 | 输出统一 PLY 路径与来源摘要；不把文件完整性升级为场景质量结论 |

### 执行模块 execution：把运行位置、任务调度与业务验收分开

运行位置由 `isaac_launcher.launch_worker()` 管理。它接收显式 argv、仓库、资产输入和独立输出目录，调用 `validate_paths()` 拒绝输出与资产目录重叠或输出包含仓库，再选择本地解释器或 `container_command()`。Docker 使用本次独有容器名，将仓库和资产分别只读挂载到 `/workspace`、`/assets`，将本次输出和 SDK 运行目录挂载为可写；`execute_container()` 在键盘中断时只停止自己创建的容器。本地 `worker_environment()` 优先暴露仓库 `src`；非源码安装时在本次输出下建立仅指向 `simready` 包的源码链接，不把宿主整个 site-packages 自动注入另一版本的 Isaac Python。Docker 仍要求完整源码仓库，本地仍要求所选解释器具备相应依赖。`remote_command()` 负责 SSH 命令引用和主机参数校验，远端详细编排由前述场景入口承担。`launch.json` 只表示启动及进程退出情况，场景与录像正确性仍须由工作流验收。见 [isaac_launcher](../../src/simready/execution/isaac_launcher.py) 与 [isaac_container](../../src/simready/execution/isaac_container.py)。

通用 DAG 是另一组可独立调用的构件，主要入口为 `run_task_graph(plan, inventory, ...)`。`task_schema.validate()` 先检查任务标识、依赖无环性、显式 argv、规范化相对输出、资源要求与固定 `runtime_identity`；`run_task_graph()` 用线程池组织子进程，只有前置任务全部 `SUCCESS` 才启动后继，失败依赖使后继 `SKIPPED`。`ResourcePool.acquire()` 根据声明的 CPU、RAM、cloud 槽位和 GPU 能力分配 `Lease`：CPU/RAM/cloud 是本池内的合作式计数，GPU UUID 的 `flock` 才提供同一 Linux 主机、同一锁目录下的跨进程互斥。租约文件描述符传给子进程，使父调度器异常退出时锁仍可由工作进程持有；`Lease.close()` 释放本次份额。设备清单是外部提供的容量声明，不是实时显存监控；RTX 要求清单显式声明已有外部渲染设备隔离。这里没有跨主机调度、操作系统内存限额或对不合作 GPU 进程的强制隔离。当前一键视频和已有场景流程并不逐阶段调用这套 DAG。

`task_graph._prepare()` 将输入摘要、依赖产物摘要、执行器源码身份、argv、参数、种子、环境声明、运行时身份和加速器类别合成内容键；`artifact_cache.cache_hit()` 重新校验实际文件后才返回命中。`execute_task()` 在排队后、执行后再次检查输入和依赖，验证所有声明输出，再通过临时目录和原子重命名发布缓存。既有损坏缓存保留并报错；同一键产生不同输出也报错，不能静默覆盖。任务重跑必须使用新输出目录，通过缓存复用已完成内容，这与 `VideoStages` 在同一次运行目录恢复阶段不同。通用执行器的 `SUCCESS` 表示进程及声明产物合同成立，不等于视觉质量或机器人任务成功；后者仍由 `verify_candidate()` 或场景专用验收定义。未观测的费用、token、显存峰值保持未知值。扩展任务需明确声明输入、依赖、输出和运行身份；云任务还必须声明 `cache=false` 并具备 cloud 槽位，执行器不会替它自动重试付费操作。见 [task_graph](../../src/simready/execution/task_graph.py)、[task_worker](../../src/simready/execution/task_worker.py) 与 [artifact_cache](../../src/simready/execution/artifact_cache.py)。

底层失败处理也按责任拆分。`run_process()` 以 argv 启动自有进程组，分离标准输出和错误日志，在结束或超时后清理该组；`ProcessTimeoutError` 携带已用时间。`supervise_process()` 只对退出码 75 或信号退出按同一命令有限重启，所有尝试共享一个截止时间，要求工作进程自身能从持久回执安全恢复。`poll_existing_job()` 仅重试既有作业的查询，区分传输重试、服务端终态失败和本地等待超时，不包含提交操作。另有 `run_worker_group()` / `gpu_worker_lease()`，以密封的继承凭证核验父进程与锁身份，最多允许三个合作工作进程共享一份组租约；它也不是默认一键流程的调度方式。DAG 和云监督器具有各自的超时机制，不代表当前所有 Isaac 场景阶段都受统一墙钟期限管理。复用这些原语时必须选择对应状态合同；它们是可信本地任务的执行设施，而非任意第三方代码的安全沙箱。

<!-- table: 执行域的启动器、DAG、缓存及资源租约可独立复用。 -->
<!-- columns: 0.90,1.00,1.10 -->

| 内部构件 | 管理对象与协作方式 | 对上层提供的保证或明确边界 |
|---|---|---|
| `launch_worker()` / `worker_environment()` | 本地解释器、源码注入与独立启动回执 | 不误注入宿主全部二进制依赖；退出状态不替代交付验收 |
| `container_command()` / `execute_container()` / `remote_command()` | 本次容器挂载、GPU 选择、SSH argv | 输入只读挂载；远端路径与资产由远端管理 |
| `task_schema.validate()` / `run_task_graph()` | DAG 校验、就绪队列、并发与截止时间 | 失败传播、逐任务状态和 `result.json`；不默认驱动一键场景 |
| `ResourcePool` / `Lease` | 池内份额与同机设备文件锁 | CPU/RAM 为声明式记账；GPU 互斥依赖合作锁协议 |
| `_prepare()` / `cache_hit()` / `execute_task()` | 内容键、文件校验、执行与原子缓存发布 | 仅复用匹配内容；损坏或冲突不能覆盖为成功 |
| `run_process()` / `supervise_process()` | 自有进程组、日志、有限重启 | 清理自有进程；重试安全性依赖工作进程的回执恢复合同 |
| `poll_existing_job()` | 同一作业的状态查询与退避 | 有限等待；超时不推断云作业已取消 |
| `run_worker_group()` / `gpu_worker_lease()` | 小规模共享租约与父子身份核验 | 最多三个合作进程；不接管已有桌面或其他 GPU 服务 |
