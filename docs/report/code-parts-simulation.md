### 仿真模块 simulation：场景、交互、机器人与证据如何组合

`simulation` 域承接两类输入：一类是重建 PLY 与显式场景规格，另一类是已经准备好的环境、机器人和任务合同。它没有一个包办所有工作的全局仿真对象。场景构造器产出 USD 与清单；任务进程持有 Isaac 对象和运行状态；独立审计重新读取测量；录像进程消费场景或轨迹。外层工作流负责启动顺序、退出码与交付完整性，域内模块负责各自的数据解释和运行行为。

#### 场景构造与资产封装：生成新候选，或搬运已有准备产物

新视频的构造链是 `scene_preparation.prepare_scene(ply, output, spec)` 调用 `gaussian_asset.read_ply`、`decode_vertices` 和 `write_appearance`，再生成碰撞及显式对象。解码后的高斯位置、尺度为 $N\times3$，四元数为 $N\times4$、顺序 wxyz，不透明度为 $N$；$d$ 阶球谐有 $K=(d+1)^2$ 个系数，USD 中展平为 $(NK)\times3$ 并保存 `elementSize=K`。`validate_sim3` 检查列向量约定的 $4\times4$ 正尺度相似变换，外观父节点持有该变换。低层导入器也能写 RGB 点云，但当前新视频结构验收要求 Gaussian 外观，不能把点云导出能力等同于整条入口支持。

构造器在内存中持有已解码数组、有限采样、地面候选和体素代理，调用 `fit_floor_plane`、`voxel_proxies` 后写入三个资产层。对象只来自显式 box/button 规格，不从点集自动获得语义身份。文件先在临时目录组合并重新打开，检查刚体关节引用和输入哈希，完成后再将目录移到目标位置。

<!-- table: 两种准备路径的构造责任。导出已有资产不等于重建或修复资产。 -->
<!-- columns: 0.85,1.15,1.00 -->

| 构造单元 | 输入与内部对象 | 输出与拒绝条件 |
|---|---|---|
| `gaussian_asset` | PLY 属性表 → 有限数组、尺度/透明度解码、SH 重排 | `appearance.usdc`；拒绝不支持的格式、缺失通道、非法数值与超限输入 |
| `scene_preparation` | 外观、Sim3、地面声明、碰撞预算、对象规格 | `scene.usda` 引用外观、`collision.usda`、`objects.usda`；另写目录和准备报告，拒绝未知字段及覆盖已有产物 |
| `asset_bundle.BundleExporter` | 已准备资产规格 → `copy()` 递归复制 USD/材质/策略，维护来源到目标的映射 | 相对引用包及 `bundle.json`；拒绝缺失依赖和不支持的自定义 MDL 依赖 |

`BundleExporter.export()` 当前按 `starbucks` 和 `substation` 两类合同组织入口。变电站导出器把控制清单、场景配置、装配报告和策略重写成包内路径；同一 USD 字节在不同目录可能解析不同依赖，因此复制身份同时区分来源上下文。`verify_bundle()` 核对清单路径、大小、哈希和入口绑定，证明包内文件身份一致；它不执行物理或来源保真评测。运行中需要复制图层时，`copy_layer_with_resolved_paths()` 保持原引用的解析含义，避免换目录后引用悄悄改变。

`scene_contract.mission_config()` 是已有任务的配置入口：解析场景、控件、装配和策略路径，并限定当前意图为 `stop_simulated_load`。新场景候选不会仅凭一个 `scene.usda` 自动进入这一任务合同。`preparation.json` 中的 `physics_ready` 也仅表示结构碰撞、支撑面和用户尺度声明齐备；`task_validated` 仍为 false，独立结构验收由另一域的 `evaluation.scene_package` 执行。

#### 交互装配与接触状态：USD 持有物理结构，适配器持有事件状态

已有变电站通过 `panel_scene.load_config()` 校验来源、坐标、代理碰撞和出生点，再由 `panel_scene.prepare_scene(config_path, out, rig_report)` 复制机器人根图层，把环境引用到 `/World/PanelEnvironment`。它移除原平地、停用环境中重复的物理场景，并核对允许的刚体、地面碰撞与出生点支撑，输出运行用 `scene.usda`、解析配置和 `scene_preflight.json`。这里执行 USD 构造而不推进物理；当前实测任务是在已经启动的 Kit worker 内调用它，并非独立的后台场景服务。文件中的 `motion_schedule`、`phase_at` 和 `arm_fraction_at` 服务于时间编排示例，不决定实测按压任务的阶段。

`press_tool.build_spring_plunger(stage, flange_path, cfg)` 在机械臂法兰上建立安装件、独立动态柱塞、碰撞几何及滑动弹簧关节；`add_payload_inertia()` 把壳体质量与惯量并入法兰。返回值记录接触刚体、关节名、行程和参数，供任务创建执行器、接触视图及被动关节分组。柱塞的位置和速度由 PhysX 持有，构造函数本身不产生接触事件。

事件链从 `ButtonSample(sequence, timestamp_s, button_prim, counterpart_prim, depth_m, contact_force_N)` 开始。`ContactButton` 保存当前语义状态、允许触发标记、按压/释放计数及上一样本身份；`observe()` 校验绑定接触对和时间连续性，再按力、位移及去抖条件更新状态。它返回事件与反馈，不写按钮或机器人位姿。`apply_usd_feedback()` 只更新已有的语义、颜色和发光命令属性，发光命令也需材质另行绑定。

`PanelControl` 将控件规格转换为 `ButtonParameters` 并构造 `ContactButton`，另持有 `latched` 和最近样本。需要锁存时，它通过调用方提供的 `write_joint_target` 回调设置关节目标；`reset_latch()` 要求接触已撤离。实测任务选择瞬时按钮，传入的回调一旦被调用就报错，防止任务主动压下按钮。`RotaryControl` 是另一套基于关节角和接触的事件观察器；存在该组件不表示旋钮已被当前按压任务操作验证。通用 `scene_preparation` 生成的弹簧按钮也未自动绑定这些事件适配器。

#### 机器人执行：具体任务组装感知、规划与能力端口

`substation_mission.run()` 是物理任务的组装位置。它启动 Isaac Lab 应用，解析任务合同、组合场景、安装工具，创建 `SimulationContext`、机器人 `Articulation`、按钮 cap/base 的 `RigidObject`、RGB-D 采集器及 PhysX 接触视图。随后构造 `PanelControl`、`Chain`、`ArmScenePlanner` 与 `articulation_interface`，加载冻结的 ONNX 腿部策略。物理步长为 0.002 s，腿部策略每 6 步更新一次，将 $(1,45)$ 观测映射为 $(1,12)$ 动作。策略推理当前使用 CPU execution provider，物理与原生相机仍在 Isaac 环境执行；不能因此称整项任务为纯 CPU 运行。

<!-- table: 物理任务内部的数据接力与状态所有者。 -->
<!-- columns: 0.85,1.15,1.00 -->

| 单元 | 输入 → 处理 → 输出 | 状态与责任 |
|---|---|---|
| `panel_perception` | $H\times W\times3$ RGB → 唯一面板候选；$H\times W$ 深度与相机变换 → 世界目标点 | 函数不持有任务状态；歧义或深度不足拒绝定位，适用范围限已建模面板族 |
| `kinematics.Chain` | USD 六轴关节帧与限位 → `fk(q)`、`solve(target, seed)` → 位姿或关节解 | 实例持有关节链；$q$ 为六维、目标为 $4\times4$，IK 成功不授予执行权限 |
| `ArmScenePlanner` | 链、场景碰撞包围盒、当前基座和关节 → `plan` / `check_segment` / `check` | 持有几何代理与装配排除项；离散路点筛选及在线检查，不宣称连续碰撞认证 |
| `RobotInterface` | 能力名与目标 → 已绑定 handler；`observe()` → 后端当前状态 | 持有描述和回调，不拥有导航器或物理积分；后端持有真实状态 |

`RobotInterface` 由 `RobotDescription`、观测回调和能力 handler 构成，提供 `describe`、`observe`、`command`、`joint_indices`。物理工厂只绑定 `joint_drive`：位置和 effort 张量均为 $(1,J)$，关节顺序必须与后端完全一致。Lite3+Z1 加柱塞时 $J=19$，包含 12 腿关节、6 臂关节及 1 个被动关节；根位置与四元数为 $(1,3)$ 和 $(1,4)$，输出顺序 xyzw。工厂按原生顺序写位置目标、effort 目标并提交，不把张量拷到 CPU 检查数值；有限值、限位、稳定性和接触约束由具体控制器负责。观测可能共享后端存储，应在 step/update 后读取，不能当作可修改副本。

实测监督状态保存在 `substation_mission.run()` 的局部变量中：`phase`、阶段起点、稳定时段、目标表面、臂路点、按压事件、上一策略动作和失败原因。主循环从 `robot.data` 与接触视图直接采集测量，经统一端口下发驱动命令，推进 `STAND → NAVIGATE → SETTLE → REACH → PRESS → WITHDRAW → RETRACT → SUCCEEDED`；已知停靠点与 PhysX 机身状态提供接近控制，RGB-D 重新定位提供臂目标。接触、速度、倾角、超时或规划失败会终止成功路径，并保存测量与失败原因。`--scan-only` 仅到 `SCAN_COMPLETE`，不是完整按压通过。

`task_state.Mission` 则是与 SDK 无关的 CPU 监督组件：接收不可变 `Observation`，由 `tick()` 持有阶段、时钟、连续观测和故障锁存，供适配与测试使用。当前物理 runner **没有调用它**；其阶段集合也不同，不能把 CPU 模板测试算作实测主循环覆盖。新增机器人可复用端口形式，但需重做关节映射、执行器、步态策略、安装/TCP 和传感器标定；`Chain.from_usd()` 当前明确读取 Z1 的六关节路径，规划器也含该装配的几何约定，均需相应适配。新增场景应先改场景/控件合同、检测器适用条件和停靠/接触配置，再验证任务；只修改 USD 路径不足以完成迁移。

#### 独立验收、录像与预检：记录、判定和呈现分别执行

物理进程把当前状态写入 `sensors.jsonl`，保存 RGB-D、`motion_plans.json`、`metadata.json`、`result.json` 和 `measured_replay.npz`。回放的 `body_poses` 为 $L\times B\times7$，包含每个采样时刻、刚体的位置和 xyzw 四元数；按钮 cap 位姿另存。控制器的 `passed` 只是输入证据之一。

`mission_audit.main()` 在独立进程重新构造 `PanelControl` 和 `Chain`：从传感器重放事件，从保存的 RGB-D 重跑检测与定位，从关节和刚体轨迹重算 FK/TCP，再核对阶段、接触、释放、回撤与禁止位姿写入等条件。启用被动柱塞时为 17 个基础项加 3 个弹簧项。输出 `audit.json` 绑定结果、传感器、回放及审计代码哈希；比较基准属于指定仿真任务，不证明真实电气功能或整场景认证。

<!-- table: 三种原生视频实现的运动来源不同。 -->
<!-- columns: 0.80,1.15,1.05 -->

| 录像单元 | 构造及状态来源 | 交付与边界 |
|---|---|---|
| `mission_video` | 复制隔离回放层，禁用刚体/关节积分，逐帧写入保存的实际姿态 | 25 fps 原生任务回放及来源摘要；不重新产生物理成绩 |
| `scene_video` | `camera_samples()` 从包围盒或显式相机生成轨迹，`render_scene()` 采集 RGB | 帧清单、相机、MP4、ffprobe 与哈希凭据；只有场景巡览，无机器人任务 |
| `cafe_video` | `route_pose` / `patrol_pose` 产生编排目标，USD 端口写根位姿，轮向和轮转另由局部编排更新 | G2 原生展示；`observe()` 读取 authored 状态，不是物理测量或导航反馈 |

`mission_video` 本身检查 `result.passed`；完整入口中，先运行独立审计、审计失败则不继续录像，以及最终核对审计与视频来源哈希，均由 `workflows.scene_delivery` 保证。单独启动录像器不能代替这一交付合同。新视频路径则由 `video_execution` 顺序调用场景准备、独立结构检查和巡览；编码成功也不把任务标志置真。

预检另有两层。`environment_check` 检查 SDK 模块和 ffmpeg，`--require-lab` 增加物理任务依赖，`--launch` 只做十次 Kit 更新的启动检查。`preflight.inspect_rig()` 检查装配身份、坐标和关节分组，明确返回 `task_ready=false`；它是可调用的静态检查工具，不是每条任务入口都会自动执行的总门禁。`simulation_lease()` 由变电站物理与回放入口使用协作文件锁，拒绝已存在的 GPU 锁且不自动回收未知锁；它不是所有渲染器共用的跨容器调度器。资源排队、环境就绪、结构可加载和任务通过分别承担不同验证责任。
