<!-- frontmatter -->

# 视频到可交互仿真场景

**模块原理、运行系统与可复现证据**

SimReady Pipeline · v0.7.0 · 2026 年 11 月 13 日

<!-- endfrontmatter -->

## 摘要

视频记录外观，机器人交互还需要坐标、尺度、碰撞、关节、控制与验收。本文按十个模块解释这些条件如何连接：输入抽帧、三维重建、坐标尺度、资产生成、物理交互、观测缺陷、修复配方、机器人运行时、独立验收和渲染交付。每个模块给出真实数据合同、算法与参数、图示、代码位置和证据边界。系统以外观、碰撞和交互三个运行层表达场景，以单独的来源证据链绑定输入、估计、配置与结果。

实现提供三条可组合路径。新视频默认路径完成观测登记、Aholo 或既有产物接入、参数化 USD 准备、结构审计和相机巡览；它不自动调用 Agent 修复、SAM 分割、DINO 特征核查或机器人任务。已准备变电站路径使用 Lite3+Z1 的测量驱动任务和独立物理审计；G2 路径只做编排展示。修复框架在冻结观测协议下接受无退化的开发改进，RSI 复用参数配方而不训练基础模型。

v0.7 使用历史原片和操作者声明关联的 PLY，完成 CPU 导出、哈希匹配恢复和 12 帧原生巡览；GPU 命令 wall 为 231.874 秒，画面仍有明显模糊和空洞。它没有新云重建或机器人任务，不代表视觉质量达标。v0.6 已准备变电站的 20/20 审计、一次按压和 713 帧 1080p 视频单列保留，入口 wall 为 1095.164 秒。结构可加载、巡览可编码、原片保真和任务成功分别判定。

<!-- toc -->

<!-- unnumbered -->

## 问题定义与实际调用路径

给定视频 $V$、可选来源声明和场景规格 $S$，系统首先构造场景候选 $C$，再根据执行路径生成结构或任务证据 $E$。输出不是一个没有条件的“仿真就绪”标签，而是场景文件、参数来源、具体检查结果和交付层级。例如，看见红色按钮、生成滑动关节、测得有效按压是三个不同命题，分别需要图像对应、物理结构和绑定传感器。

### 三条路径，而非每次都执行十个模块

<!-- table: 十个模块与实际调用关系。数字对应正文模块编号，括号区分同一模块内的不同验收或渲染分支。 -->
<!-- columns: 0.75,1.05,1.20 -->

| 路径 | 默认执行的模块 | 输出与边界 |
|---|---|---|
| 新视频场景候选 | 1→2→3→4→9（结构）→10（巡览） | SCENE_SURVEY_READY；对象为显式规格，模块 5 仅可生成弹簧 primitive，无机器人任务 |
| 已准备机器人场景 | 来源核验/抽帧→5（已绑定）→8→9（任务）→10（回放） | 已装配 Lite3+Z1 的固定任务；G2 单独走 authored 运动与渲染分支 |
| 开发修复框架 | 6→7→9（候选质量），可重复 | 需冻结观测、真实插件与单独配置；不由默认新视频入口自动调用 |

新视频默认可以保留高斯外观、估计有界碰撞并生成巡览；未知向上方向时不猜地面，无外部尺度锚点时不声明米制精度。独立 box/button 来自使用者规格，不是自动实例分割。来源 SHA-256 约束文件身份，不能单独证明语义、几何或尺度真实。

### 输入、表示与验证责任

<!-- table: 三个运行层与来源证据链。后者是跨层记录，不是第四种几何层。 -->
<!-- columns: 0.70,1.05,1.25 -->

| 对象 | 数据表示 | 需要的验证 |
|---|---|---|
| 外观 | Gaussian 参数、独立几何和材质 | 来源对应、固定视角重投影与局部缺陷 |
| 碰撞 | 地面和有限体素/对象代理 | 尺度、支撑、穿透、薄结构与可达空间 |
| 交互 | 限位关节；另行绑定接触和状态机 | 参数来源、力/位移、触发/释放、任务回撤 |
| 来源证据链 | 视频/帧哈希、声明、变换、配置、版本与产物哈希 | 身份一致、估计可见、运行记录不混用 |

3DGS [1] 提供外观表示，VGGT [2] 是独立研究支路，SAM [3] 和冻结 DINOv2 [4] 曾用于离线场景处理；Aholo [7] 是外部重建服务。Isaac Sim / Isaac Lab [5,6] 承担仿真和原生渲染。本项目实现的是这些产物之间的合同、准备、运行与验收机制，不重新声明基础模型或供应商内部训练算法。

<!-- landscape -->

### 新视频路径的实际数据流

<a id="fig-new_video_pipeline"></a>
![新视频入口的宿主与 worker 边界。原视频供云重建，观测帧另作参考登记；宿主绑定产物与声明，worker 生成 USD、独立检查结构，再做诊断巡览。可选对象来自显式规格；Agent 修复、SAM/DINO 和机器人任务不在默认调用链中。](figures/new_video_pipeline.svg)

prepare-only 在结构检查后结束；巡览完成也不将 task 或独立尺度验收置真。来源变化、未确认提交或产物缺失时停止，resume 只复用身份与产物哈希一致的已完成阶段。

<!-- endlandscape -->

## 输入视频、抽帧与来源登记

这一模块回答“后续读到的图像来自哪个文件、按什么规则产生”。它输出可复核的观测集合，不负责推断几何、物理尺度或场景完整性。[图](#fig-module_input_gaussians)(a) 给出抽样、筛选与来源绑定的关系。

### 输入输出与关键参数

输入是非空源视频和显式抽帧参数；CPU 用 ffmpeg 的 fps 滤镜生成 JPEG。输出 `manifest.json` 保存 `video_sha256`、`fps`、筛选配置及 `frames[]`。每个帧记录包含 `index`、`sample_index`、`nominal_time_s`、`split`、相对 `path`、`sha256`、`laplacian_variance` 和 `mean_brightness`；图像读入后的数组为 $H\times W\times3$。

<!-- table: 抽帧模块的代码默认值。历史案例和新运行以各自 manifest 中的实参为准。 -->
<!-- columns: 0.85,0.65,1.50 -->

| 参数 | 默认值 | 作用 |
|---|---|---|
| fps | 2.0 | fps 滤镜的名义采样频率 |
| min_laplacian | 60.0 | 灰度离散拉普拉斯方差下限 |
| dedup_bits | 6 | 256 位梯度哈希的最大重复距离 |
| heldout_every | 10 | 保留序列中每 10 帧取一个留出样本 |

新 `--video` 入口显式覆盖为 `min_laplacian=0`、`dedup_bits=0`：保留模糊帧及其测量，只过滤与前一保留帧哈希完全相同的样本。它与底层函数默认 60/6 不同，不能混报。

### 原理：筛选不能改变来源语义

令灰度图为 $I$。四邻域离散拉普拉斯衡量局部强度变化，方差用作清晰度启发式：

$$
L_{u,v}=I_{u-1,v}+I_{u+1,v}+I_{u,v-1}+I_{u,v+1}-4I_{u,v},\qquad S(I)=\operatorname{Var}(L).
$$

去重将灰度图缩到 $32\times32$，取交错像素差得到 $16\times16$ 梯度，以梯度均值二值化为 256 位签名。当前帧与前一张已保留图的汉明距离不大于阈值时被丢弃；这是局部去重，不是对整段视频的全局聚类。保留帧索引满足 `index % heldout_every == heldout_every // 2` 时进入 heldout，其余进入 train。

名义时间为 $t_i=i/f$，其中 $i$ 是 fps 滤镜输出索引；它不是读取原始视频 packet/frame PTS 得到的实测时间戳。改变采样频率、筛选阈值或去重规则都应产生新的 manifest。提取前后重新检查源文件哈希，零帧存活或 ffmpeg 失败时不发布成功清单。

### 实现与证据

代码对应 `ingestion/video_frames.py` 的 `extract_frames`、`gradient_hash` 与 `hamming_distance`。历史变电站原片为 91.626 秒、1280×720，已保存观测集合为 136 帧（122 参考/训练、14 留出）；这些数量属于历史配置，不能从当前默认值反推。相邻帧高度相关，同视频划分仅提供诊断组织。Aholo 上传的是完整视频，不能把本地 heldout 标签当作云服务未见过的独立测试输入。

<a id="fig-module_input_gaussians"></a>
![输入登记与高斯表示原理。(a) 名义时间网格上的抽样、筛选和来源记录；(b) 均值、旋转、尺度、不透明度和球谐属性；(c) 由渲染后端投影、着色与合成。图为算法示意，非重建精度实验。](figures/module_input_gaussians.svg)

## 三维重建、任务凭据与高斯表示

视频字节经过重建服务产生空间外观，文件又需经过明确解码才能进入 USD。服务任务管理和高斯表示是两种不同职责：[图](#fig-new_video_pipeline)(a) 表示任务与来源路径，[图](#fig-module_input_gaussians)(b,c) 表示重建产物如何成为可渲染数据。

### 输入输出：云任务与可复用产物

`reconstruction_stage.prepare_reconstruction` 读取当前视频 SHA-256，并输出 `reconstruction.json`。其合同包含 `schema_version=1`、`status=COMPLETE`、`source_video_sha256`，以及 `artifacts[{kind,path,sha256,bytes}]`。PLY 必需，SPZ 可选；相对路径不得越出清单目录。复用前重新核对文件大小和哈希。

显式 `--submit-reconstruction` 才允许创建新云任务。已有 job receipt 绑定 world ID，可恢复监控；若已记录提交意图却缺少确认 receipt，则阻断再次 POST，避免把结果未知当作未提交。完整 provider receipt/status/assets 可恢复已下载结果；外部 manifest 是使用者的来源声明。哈希匹配证明文件与声明一致，不独立证明视频与三维几何确实正确配对。

Aholo [7] 的上传、轮询和下载受一个监督时限约束，结果保存为 PLY/SPZ 与凭据。供应商内部训练算法为黑盒，本项目不将服务输出改称自行训练的重建模型。当前交付案例采用 Aholo 底稿；VGGT [2] 估计相机/深度再接 gsplat 的支路为独立研究，历史 v3 的 PSNR 14.2357 dB、SSIM 0.6205、LPIPS 0.6117 未达 22 dB 门槛，没有替换交付底稿。

### 原理：从 PLY 属性到各向异性高斯

对 $N$ 个高斯，位置为 $N\times3$，旋转为 $N\times4$，尺度为 $N\times3$，不透明度为 $N$。解码器按 3DGS [1] 的存储约定，将 log-scale $\ell_i$ 和 opacity logit $o_i$ 转换为

$$
\mathbf s_i=\exp(\boldsymbol\ell_i),\qquad \alpha_i=\frac{1}{1+e^{-o_i}},\qquad
\Sigma_i=R(q_i)\operatorname{diag}(\mathbf s_i^2)R(q_i)^{\mathsf T}.
$$

协方差描述空间椭球，球谐系数描述随视线变化的颜色；实际投影与合成由 OpenUSD/RTX 后端完成。PLY 旋转以 `wxyz` 读取并归一化，不能直接套用机器人接口的 `xyzw` 约定。阶数 $d\in\{0,1,2,3\}$ 对应 $K=(d+1)^2$ 个球谐基；`f_dc_*` 与 channel-major 的 `f_rest_*` 重排为 coefficient-major，输出系数数组为 $(NK)\times3$，USD `elementSize=K`。

只接受 vertex-first、binary little-endian PLY；默认最多 200 万点、顶点表 768 MiB。零四元数、不完整球谐阶数、非有限值以及指数解码后非正或溢出的尺度均拒绝。RGB uint8 点云可由导入器写成 USD Points，但当前新视频结构验收要求单一 Gaussian 外观；这一导入分支不等于已通过完整场景交付。

### 实现与证据

代码为 `ingestion/reconstruction_stage.py`、`ingestion/aholo_reconstruction.py`、`simulation/gaussian_asset.py`。本轮新视频入口复用操作者声明来源关联的历史 PLY，没有提交新云重建。v0.5 只对已有 Aholo 任务执行过一次只读 GET 和结果复用，也不能当作一次新的云端到端重建。

## 坐标配准、尺度与地面估计

三维数组并不自带真实米制尺度、重力方向或机器人坐标系。本模块将“数值变换有效”“尺度来源有声明”“尺度经过独立标定”分开记录。[图](#fig-module_registration_geometry)(a) 给出正向相似变换与显式世界系。

### 输入输出与坐标合同

输入包括重建点 $P\in\mathbb R^{N\times3}$、可选 `source_to_world` 四阶矩阵、`world_up_axis`、`scale_provenance{status,method,evidence}` 和 `floor_plane{normal,offset,evidence}`。输出保存变换、`bounds_world{min,max}`、地面估计及来源标记。默认不提供变换时使用单位阵，但不因此确认真实尺度或源坐标的向上方向。

$$
\mathbf p_W=sR\mathbf p_S+\mathbf t,\qquad
T_{WS}=\begin{bmatrix}sR&\mathbf t\\0&1\end{bmatrix},\quad s>0,\quad R^{\mathsf T}R=I,\quad\det R=1.
$$

`validate_sim3` 要求矩阵有限、末行为 $[0,0,0,1]$，线性块满足 $A^{\mathsf T}A\approx s^2I$、$\det A>0$，且尺度位于 $10^{-8}$ 到 $10^8$。这拒绝剪切、反射和非均匀缩放。外观 USD 将变换写在高斯父 Xform 上，保留球谐的局部参考系；不能只旋转中心而忽略高斯方向和颜色坐标。

USD 的 `metersPerUnit=1` 与 `upAxis=Z` 是文件约定，不是测量证据。兼容字段 `metric_scale_verified` 仅在显式变换、`status=verified` 及非空 method/evidence 同时存在时置真；它表示使用者声明齐全。独立尺度验证仍单独为假。

### 原理：已知向上方向下的地面假设

只有显式提供变换或声明 `world_up_axis=Z`，才允许自动地面拟合。未知 up 时仍能生成外观与体素碰撞，但不猜测地面。拟合最多取 1 万个确定性样本，使用高度最低 40% 的点作为候选，执行 128 次三点 RANSAC；法向距 +Z 不超过 20°，默认点面阈值为 0.05 个世界单位。

$$
\mathbf n^{\mathsf T}\mathbf p+d=0,\qquad
\left|\mathbf n^{\mathsf T}\mathbf p_i+d\right|\leq\tau_{\mathrm{floor}}.
$$

最优内点至少为 20 个且不少于候选的 15%，随后用 SVD 重估法向与偏移；退化、内点不足或方向不符则不产生地面。结果标为 `estimated_not_reviewed`。外部提供的平面也保留 `user_declared_not_independently_verified`，不会因进入 spec 就升级为真值。

### 实现与证据

实现对应 `gaussian_asset.validate_sim3` 与 `scene_preparation.fit_floor_plane`。正确的矩阵检查只能证明变换属于规定模型；正确的拟合残差只能说明点对该平面的一致性。是否米制、是否真实地面，还需要外部尺度锚点、相机/测量标定和独立观测。

<a id="fig-module_registration_geometry"></a>
![坐标与资产生成原理。(a) 正向 Sim(3) 与世界系；(b) 点集到有界体素碰撞代理；(c) 外观、碰撞和交互三个运行层。图中几何用于说明数据变换，不作为场景真值。](figures/module_registration_geometry.svg)

## 独立资产、碰撞代理与 USD 组合

本模块把重建外观变成可加载、可检查的场景候选。它自动执行的是属性导入、有限占据估计和显式规格生成，不是从任意视频自动分割出完整柜体、控件、线缆和电气语义。[图](#fig-module_registration_geometry)(b,c) 对应碰撞构造与三层组合。

### 输入输出：分离运行层与来源记录

输入为解码后的高斯、模块 3 的变换与可选 `objects[]`。输出 `appearance.usdc`、`collision.usda`、`objects.usda`，由 `scene.usda` 用相对引用组合；`asset_catalog.json` 保存对象、floor、collision 统计，`preparation.json` 保存输入/spec 哈希、尺度声明、世界包围盒与限制。

外观层保留 Gaussian 的 positions、orientations、scales、opacities 和 SH；它不挂实体碰撞。碰撞层为不可见静态代理。显式 `box` 使用 `bounds_world{min,max}`，显式 `button` 使用位置、轴、行程和弹簧参数。对象最多 256 个，必须有唯一合法 USD ID；类别与 evidence 保留为声明，`automatic_semantic_detection=false`。

### 原理：有资源上限的占据代理

碰撞只使用不透明度至少 0.1 的高斯中心，默认确定性抽取最多 10 万点，配置硬上限为 20 万点。若存在地面，仅保留平面上方有符号高度 $\mathbf n^{\mathsf T}\mathbf p+d>2\tau_{\rm floor}$ 的点，平面下方点也被排除。令原点 $\mathbf o$ 为采样点的逐轴最小值，体素索引和箱体为

$$
\mathbf g_i=\left\lfloor\frac{\mathbf p_i-\mathbf o}{h}\right\rfloor,\qquad
B_{\mathbf g}=[\mathbf o+h\mathbf g,\;\mathbf o+h(\mathbf g+\mathbf1)].
$$

默认 $h=0.25$ 个世界单位，每体素至少 2 点才保留，目标上限 2000 个代理（配置硬上限 5000）。数量超限时 $h$ 每轮加倍，并记录 requested/actual voxel size。与显式对象包围盒重叠的代理被剔除，避免阻挡按钮行程；剔除也可能留下间隙，需要后续检查。有限采样和粗化会遗漏薄结构，体素箱体不是闭合表面重建。

新入口的 box/button 是规格驱动的独立几何，不会自动继承原片中的实例对应。既有变电站还包含离线的 SAM 区域关联、平面配准、纹理恢复和估计柜面补建；这套案例工程不能自动归为新入口的默认能力。其冻结 DINOv2/边缘/灯位/RGB 辅助权重为 0.45/0.20/0.25/0.10，是差异核查配方，不是基础模型训练。

### 实现与证据

实现为 `scene_preparation.prepare_scene`、`voxel_proxies` 和 `gaussian_asset.write_appearance`。已有包迁移另由 `asset_bundle` 递归复制 USD 引用并重写相对路径；相同 USD 字节在不同来源目录中不被错误合并。自定义 MDL 仅支持 UTF-8 自包含模块，含 import/资源依赖/限定模块名时拒绝；仅 SDK 白名单 OmniPBR.mdl 可保留外部依赖。

当前低压案例的 121 按钮、49 旋钮是资产候选统计；另一个未统一配准高压场景的 22/6 不可相加。导出闭包通过和对象数量都不能替代全场景实例完整性、尺寸或来源一致性评测。

## 物理交互：关节、接触与状态

“生成一个可动按钮”和“确认一次有效操作”属于两个层次。[图](#fig-module_button_contact)(a) 是显式弹簧关节，(b,c) 是另行绑定的测量与事件适配器；新入口生成前者，不自动完成后者。

### 输入输出与物理参数

新按钮规格给出 `position_world[3]`、轴 X/Y/Z、半径、行程、cap 高度、刚度、阻尼、质量与最大驱动力。输出为运动学底座、动态 cap 和 prismatic joint；两物体均挂 RigidBodyAPI，底座另设 `kinematicEnabled=true`，限位和 body 目标写入 USD。默认半径 0.02 m、行程 0.004 m、cap 高度 0.01 m、刚度 400 N/m、阻尼 4 N·s/m、质量 0.02 kg、最大驱动力 8 N；米制尺度未经独立验证时，这些只是声明单位下的仿真预设。

以相对平衡位置的关节坐标 $q$ 表示，弹簧和阻尼的回复机制可写为

$$
F_{\mathrm{spring}}=-kq-c\dot q.
$$

实际积分、接触和驱动限幅由 PhysX 完成。弹簧驱动力不等于外部接触传感器测得的力，不能据此直接生成操作成功。新对象明确记录 `physical_parameters_measured=false`、`semantic_function_verified=false` 和未绑定状态转换的 spring 语义。既有 Z1 工具上的被动柱塞采用另一组参数：4 mm 行程、800 N/m；它与新按钮的 400 刚度默认值不是同一组件。

### 原理：持续测量触发事件

`ContactButton` 另需绑定 asset key、按钮与工具 Prim、采样间隔、按压/释放阈值、去抖时长以及语义状态转换。输入 `ButtonSample` 为 sequence、timestamp、两个 body 标识、depth 和 contact force；输出保存 state、armed、按压/释放累计计数、event、rearmed 和显示反馈。

$$
b_k=\mathbf1[d_k\geq d_{\rm press}\land F_k\geq F_{\rm press}],\qquad
N_{\rm press}=\left\lceil t_{\rm debounce}/\Delta t\right\rceil.
$$

armed 时连续 $N_{\rm press}$ 个有效采样满足条件才产生一次状态边。触发后必须持续低于更低的释放阈值才重新 armed，避免一次接触重复计数。缺帧或采样断档清零累计；重复、过快或无效测量直接拒绝。默认按压/释放去抖均为 0.04 秒，实际阈值由具体按钮合同提供。

### 实现与证据

物理生成位于 `scene_preparation._write_objects`；测量适配位于 `button.py` 和 `panel_controls.py`。适配器不移动按钮或机器人，只更新已声明语义/颜色字段。历史及 v0.6 变电站均有真实物理按压和独立事件重算；新生成场景尚不能继承这些机器人任务成绩。

<a id="fig-module_button_contact"></a>
![物理交互的三个层次：显式弹簧 primitive、绑定接触对的测量、持续按压/释放后的状态转换。图中的时序曲线是原理示意；新生成按钮的物理结构不等于任务适配器已绑定。](figures/module_button_contact.svg)

## 标准观测、指标与缺陷定位

外观差异必须落到固定视角、区域和实例，才能成为可执行的修复输入。本模块属于可组合的通用验证框架；新视频默认路径不自动调用它，也不因为生成了巡览视频就获得来源一致性评分。[图](#fig-module_observation_acceptance)(a,b) 对应固定观测和缺陷定位。

### 输入输出与观测形状

输入协议包含 `benchmark_id`、`phase`、`profile`、`views[]` 与 `checks[]`。每个 view 绑定 `view_id`、`asset_id`、`frame_id`、`split`、`reference_kind` 和 RGB 文件哈希；split 与 phase 一致，可附相机、深度（`depth_unit=m`）、实例与 ROI。候选通过同一 `protocol_sha256` 提供对应 views 和资产引用。phase 区分 development 与 sealed_release；训练、位姿拟合、纹理拟合及 Skill 学习使用过的帧/图像哈希不得与声明评测集重叠。

RGB 读为 $H\times W\times3$ 的 8 位图像并归一化到 $[0,1]$，参考与候选必须同尺寸，不做隐式缩放或曝光拟合。深度为 $H\times W$、单位 m；实例 JSON 至少含 `instance_id`、category、`center_px[2]`。ROI 是四个整数像素坐标 `bbox_xyxy`，越界或小于 SSIM 所需的 11×11 区域会被拒绝。

### 原理：从原始数组重算度量

对固定区域的所有像素通道，令样本数为 $M$：

$$
\operatorname{MSE}=\frac1M\sum_j(I_j-\widehat I_j)^2,\qquad
\operatorname{PSNR}=-10\log_{10}\max(\operatorname{MSE},10^{-12}).
$$

SSIM 使用 11×11 高斯窗口、$\sigma=1.5$、总体协方差和常数 $0.01^2,0.03^2$，裁去边界 5 像素后平均。结果同时报告整帧均值、低分位和局部最差值，避免局部明显缺陷被全图平均掩盖。深度只在参考有效像素上统计候选可用比例，并在共同有效位置计算 MAE；缺失候选深度不能按零误差处理。

实例按显式 ID 对应，输出缺失、多余、类别错误、关键实例缺失以及共同实例中心误差的 P95。资产图另检查类别、父子关系、位置、尺度、材料、状态和 affordance 等字段。声明 view/ROI 完成率是“协议要求的观测是否齐全”，不是现实表面覆盖率。

### 缺陷记录与实现证据

每项 check 定义 metric、比较算子、limit、required、severity、track 与 calibration。测量缺失或阈值未标定时记为 INCOMPLETE；越界为 FAIL。缺陷输出含 `check_id`、kind、severity、metric、measured、criterion、asset_ids 和 view/ROI evidence，根因保留 `UNCONFIRMED`。缺少观测或来源证据不被当作当前已具备条件的修复动作。

实现为 `evaluation/protocol.py`、`observations.py`、`asset_graph.py` 和 `scoring.py`。这些程序重算已提供的数组，不自动拍摄真实参考视角，也不认证图像生产者。当前真实场景尚缺完整冻结多视角、尺度和实例真值；合成 observer calibration 只能核查框架行为。

<a id="fig-module_observation_acceptance"></a>
![观测与验收的分工。(a) 冻结 RGB、深度和实例；(b) 缺陷绑定 view/ROI；(c) 开发候选回归；(d) USD 结构与机器人测量审计分别运行。三类验收使用不同输入和判定，不能把一种通过替代另一种。](figures/module_observation_acceptance.svg)

## 修复决策、执行与 RSI 配方复用

本模块把缺陷转换为有边界的执行提案，并保存成功与失败经验。它是独立修复工作流，不是当前 `--video` 默认调用的一步。输入协议和门槛保持固定，Agent 只选择已注册动作；详细回路见 [图](#fig-repair_closed_loop)。

### 输入输出与决策权限

候选 $C_k$ 包含资产与证据引用，缺陷 $D_k$ 来自模块 6。能力目录按插件 handles、资产与 writes 域聚合，冲突写域串行处理；它只生成 PLAN_ONLY。可执行注册表必须另外启用，并声明版本、参数类型/范围、输入、可信 argv、DAG 和最终 `candidate.json`。

提案包含 `proposal_id`、`plugin_id`、`plugin_version`、parameters、asset_ids、defect_kinds 和可选 skill_id。插件版本附带任务模板与实现输入的哈希。Agent 读取提案与兼容经验，输出 `RepairDecision(action,proposal_id,reason)`；rules、Codex CLI 与 Responses API 是决策提供方，模型文字不直接成为 shell 命令。

### 原理：改善与退化分别判定

执行器在新目录绑定输入、参数和任务 DAG，产出新候选；新的 verifier 进程再计算 scorecard。比较前要求 protocol hash、check 集合、required、metric、op 和 limit 均不变。仅对 required=true 的必选检查判定改善与退化；令 $\mathcal I_k$ 为必选项改善集合，$\mathcal R_k$ 为必选项退化集合，则

$$
\operatorname{accept}(C_{k+1})\iff |\mathcal I_k|>0\;\land\;|\mathcal R_k|=0.
$$

PASS 变为非 PASS、原有有限值变为缺失、或数值沿比较算子的错误方向移动，都可能构成退化；数值比较容差为 $10^{-9}$。即便仍是 FAIL/INCOMPLETE，只要有改善且无退化，也可以接受为中间候选。最终整体 PASS 另行记录，`release_authorized=false` 不随开发改进改变。

### Memory、Skill 与停止条件

Episode 保存输入/协议哈希、插件版本、参数、决策、前后评分和成本。只有 ACCEPTED 且整体 candidate_passed 的 episode 才可蒸馏为候选 Skill；晋升还要求至少两个不同场景的通过证据，其中至少一次 role=validation。自动晋升需显式启用 `auto_promote_skills`，默认关闭；也可单独调用 promote。检索检查 family、policy_key、defect kind 和插件版本，退化时退役；不把不同版本的成绩直接迁移。

能力规划器默认每轮最多 4 动作、最多 4 轮，连续 2 轮无进展停止；实际工作流预算以运行配置为准。缺测量、无匹配插件、执行失败或超预算也会退出并保留记录。修复副作用和最终证据不隐式缓存重播；未知 token usage 保持未知。

实现对应 `repair/planning.py`、`plugin_registry.py`、`agents`、`workflows/repair_loop.py`、`evaluation/regression.py` 与 `experience/skills.py`。RSI 复用的是受证据约束的参数配方，不是训练基础模型权重。真实插件与跨场景回归尚未覆盖任意原视频；进程和哈希边界也不等于恶意插件 OS 沙箱。

<!-- landscape -->

### 修复闭环的决策与证据路径

<a id="fig-repair_closed_loop"></a>
![修复工作流的可执行边界。提案、可信 worker 和独立 verifier 分离；接受/拒绝均保存 episode，经过跨场景回归的参数配方才可晋升。此图描述开发机制，不表示默认新视频流水线自动调用了 Agent。](figures/repair_closed_loop.svg)

<!-- endlandscape -->

## 机器人运行时、感知与设备端口

已有变电站任务由 Lite3 底盘、Z1 六轴机械臂与被动工具组成；G2 是另一条 authored USD 编排分支。本模块接收已装配、已标定和有策略的机器人，不因导入模型而自动获得导航能力。[图](#fig-robot_control_runtime) 对应真实任务控制链。

### 输入输出形状与控制频率

已有物理步长为 0.002 秒，腿部策略每 6 步更新。PPO ONNX 的输入为 $(1,45)$：机身角速度 3、投影重力 3、速度命令 3、12 个腿关节相对默认角度、12 个关节速度与前一动作 12。角速度乘 0.25、关节速度乘 0.05，输出 $(1,12)$ 经默认关节位姿和动作缩放得到目标；这些缩放属于该冻结策略的接口。

统一 `RobotInterface` 通过 describe/observe/command 声明能力、单位与分组。Lite3+Z1 的 12 腿、6 臂、1 被动工具共 19 个关节，物理 joint_position/joint_velocity/目标张量形状均为 $(1,J)$；根位置为 $(1,3)$，四元数为 $(1,4)$。调用方先 step/update，再读取当前后端状态；端口不主动推进仿真，也不把目标冒充测量。

### 原理：图像目标到有限关节运动

检测器使用 HSV 连通域、中心环和两排灯/按钮的相对布局，仅对已建模白色面板族接受唯一红色瞬时控件候选。零候选或歧义均失败。中心邻域深度仅保留 $0.05<d<4$ 的有限值，至少 3 个样本取中位数。设图像为 $W\times H$、像素 $(u,v)$、焦距 $f$：

$$
\mathbf p_C=\left[\frac{u-W/2}{f}d,-\frac{v-H/2}{f}d,-d,1\right]^{\mathsf T},\qquad
\mathbf p_W=T_{WC}\mathbf p_C.
$$

OpenUSD 相机沿负 Z，像素纵坐标变号；输出可见表面点经安装变换与工具 TCP 转为机械臂目标。配置中的真实碰撞前表面只供审计比较，不输入 IK。Z1 链从 USD 构造 FK，以关节限位约束数值 IK，再做离散路径和在线包围盒检查。监督器按 Stand、Navigate、Settle、Reach、Press、Withdraw、Retract、Success 推进；到位、停稳和接触条件由实际测量决定。

### 能力差异、实现与证据

<!-- table: 设备端口统一访问方式，而不统一任意机器人的高层策略。 -->
<!-- columns: 0.65,1.20,1.15 -->

| 接入 | 能力与观测 | 当前范围 |
|---|---|---|
| G2 | root_pose；USD authored transform，父坐标系 | 编排 XYZ/yaw；无物理导航 |
| Lite3+Z1 | joint_drive；当前 articulation，世界坐标系 | 冻结策略与场景控制器；拒绝 root_pose |
| 独立 Lite3/Z1 | 同一 articulation 工厂的绑定模板 | CPU 合同验证，无独立 GPU 任务成功记录 |

旋转关节采用 rad、rad/s、N·m，滑动关节采用 m、m/s、N；输出四元数为 xyzw。后端关节顺序、驱动/被动分组必须完整且无重复，命令形状逐项匹配。新构型仍需策略、安装/TCP、传感器标定与独立任务验收。

实现为 `substation_mission.py`、`panel_perception.py`、`kinematics.py`、`task_state.py`、`robot_interface.py`；模板位于 examples/robots。v0.6 变电站完整任务和 G2 短预览分别有记录；新 `--video` 默认不加载机器人或调用本模块。

<!-- landscape -->

### 控制、测量与审计回路

<a id="fig-robot_control_runtime"></a>
![已有机器人任务的控制回路。物理目标经过设备接口进入 PhysX，测量返回监督器并保存日志；审计重新读取日志，展示相机与机器人 RGB-D 相机分离。该路径只适用于已准备的任务合同。](figures/robot_control_runtime.svg)

<!-- endlandscape -->

## 独立验收：结构、候选质量与任务

三类验收回答不同问题，不能共享一个含混的“通过”。[图](#fig-module_observation_acceptance)(c,d) 对照开发回归、结构检查和测量任务审计。统一汇总保存实际验收范围，而不把单项通过升级为完整场景认证。

### 输入输出与三类门槛

<!-- table: 验收输入、判定与输出范围。新视频默认只进入第一行；另外两行需独立配置和执行。 -->
<!-- columns: 0.70,1.15,1.15 -->

| 类型 | 独立读取的输入 | 判定和输出 |
|---|---|---|
| USD 结构 | scene.usda、引用层、schema 与世界 bounds | 可解析、引用包内、有限边界、合法关节；structure-audit |
| 冻结候选质量 | protocol、candidate、哈希绑定的图像/深度/实例 | 重算指标与固定 checks；PASS/FAIL/INCOMPLETE |
| 机器人任务 | sensors、events、RGB-D、replay、配置与运动学链 | 重算接触、位移、姿态、FK、回撤等；mission audit |

结构检查以独立进程重新打开场景，拒绝组合错误、包外或缺失引用；包围盒必须非空且有限。当前要求恰有一个 Gaussian 外观，且 Gaussian 不能挂 CollisionAPI。Prismatic joint 必须有有限且 low<high 的限位，body1 指向实际 RigidBody；统计碰撞体和关节，但不执行动力学。输出的 `reconstruction_fidelity_validated`、`metric_scale_independently_validated`、`robot_task_validated` 均为 false。

### 原理：重算结果与冻结比较

候选质量 verifier 在新进程中校验 protocol/candidate 哈希，读取原始观测，再检查运行前后输入与代码未变化，输出 observations、scorecard 和开发缺陷。seal 阶段不把细粒度修复反馈发给 Agent；仍需部署可信采集/发布服务，哈希本身不认证生产者。

任务审计重放按钮状态适配器，确认一次实际按压、事件序列和模拟负载只由接触改变；再复算图像定位、关节 FK 与工具端点。当前合同要求停靠图像点到碰撞前表面误差小于 3 mm，FK 位置差小于 $10^{-5}$ m，保存工具点的重算差小于 $10^{-6}$ m。这些阈值检查仿真实现和回放的一致性，不是实机测量精度。

另检验位移大于 1 m、阶段顺序、正足地接触、非目标接触、力上限、按钮释放、臂回撤、关节速度、倾角和根/按钮位姿写入计数。基础 17 项加被动工具压缩、未到底、释放 3 项，形成当前 20 项合同。通过要求所有必选项成立：

$$
\operatorname{task\_pass}=\bigwedge_{j\in\mathcal R}\operatorname{check}_j.
$$

### 实现与证据

结构检查位于 `evaluation/scene_package.py`；候选观察/评分/回归位于 `evaluation/verification.py`、`scoring.py`、`regression.py`；物理审计位于 `simulation/mission_audit.py`。v0.6 的 20/20 来自已执行物理任务；新场景结构通过不继承此结果。未布置的接触对、其他布局、真实电气功能和完整几何真值均超出该合同。

## 原生渲染、轨迹回放与交付

渲染把场景或已验收轨迹转为可检查视频。新场景巡览与机器人实测回放是两条路径，[图](#fig-module_render_evidence)(a,b) 分开画出，随后共用帧校验、编码和凭据链。

### 输入输出：两种运动来源

巡览读取 scene.usda、preparation bounds 和 `camera{eye,target,orbit_degrees}`、`render{width,height,frames,fps,subframes}`。相机 eye/target 为世界系三元组；不提供时以 bounds 中心为目标，沿 $[1,-1,0.55]$ 方向乘最大边长设置初始视点，默认旋转 60°。

$$
\mathbf e_j=\mathbf t+R_z(\theta_j)(\mathbf e_0-\mathbf t),\qquad
\theta_j=\theta_{\max}\frac{j}{\max(1,F-1)}.
$$

它是诊断相机轨迹，不进行自由空间或遮挡可达规划，`camera_clearance_validated=false`。默认输出配置为 1280×720、120 帧、24 fps、8 个渲染子帧；实际验证若改为短预览，必须报告实参，不能继承完整默认工作量。

物理回放读取已通过任务审计的 `measured_replay.npz`：steps 为长度 $L$，body_poses 为 $L\times B\times7$（位置 3、xyzw 旋转 4），并关联关节与传感器日志。独立回放层关闭刚体积分，写入测量变换，跟随或特写相机只用于展示；不重新模拟另一条轨迹。

### 原理：原生帧到可追溯视频

RTX 逐相机姿态以 `delta_time=0`、暂停时间线渲染，等待写入完成后检查帧数与分辨率。PNG 重命名为连续编号再交给 ffmpeg，以配置 fps 编码 H.264；ffprobe 独立核对宽、高、帧数和平均帧率。新巡览还保存每帧 mean/std 与哈希；全帧近乎均匀时拒绝，但通过这一检查不等于画质和重建保真达标。

输出包括 `camera.json`、逐帧图像、MP4、`render.json` 和交付汇总，绑定 scene/spec/video 哈希。CPU prepare-only 在结构审计后输出 SCENE_EXPORTED；完成巡览才输出 SCENE_SURVEY_READY，robot_task_validated 仍为 false。宿主阶段 receipt 与实现/计划身份及所有记录产物绑定，resume 只复用哈希未变的成功阶段，失败保留 stage、错误和日志。

### 实现与证据

新巡览为 `simulation/scene_video.py`、`workflows/video_execution.py` 与 `video_pipeline.py`；已准备任务回放为 `mission_video.py`，G2 编排为 `cafe_video.py`。v0.6 变电站新录 713 帧原生 1080p，G2 只新录 8 帧 640×360；历史 714 帧任务片和 900 帧 4K 星巴克片保持独立版本。v0.7 新视频的 12 帧巡览在下一节单列。

<a id="fig-module_render_evidence"></a>
![两条原生渲染路径与交付证据。(a) 新场景的诊断巡览；(b) 已通过审计的物理测量回放；(c) 原生 RGB、编码、探测和哈希绑定。相机运动、画面可见和任务成功是不同判定。](figures/module_render_evidence.svg)


## 新视频入口与本轮验证范围

该入口接受视频，自动导出可见高斯、带估计标记的有界碰撞和巡览。安装 SDK/镜像、云账号和输入许可仍须预先准备；以下云调用须显式授权提交，本轮实际使用的是离线重建清单：

```bash
simready run --video input.mp4 --out runs/new \
  --submit-reconstruction --accept-eula
```

离线已有 PLY 使用 `--reconstruction-manifest`；可选 `--scene-spec` 参考 examples/new_video 的格式，提供 Sim(3)、尺度来源、向上方向或地面、独立 box/button、相机及渲染规格。`--prepare-only` 只执行 CPU 导出和独立结构审计；`--resume` 保留同一来源、规格与代码身份，重新核验产物，不能把输入变化隐藏在旧完成标记后。

### 结果、失败与恢复的记录范围

宿主计划包含 observations、reconstruction、worker_inputs 和 scene_delivery；worker 顺序执行 prepare、structural audit、native render。输出汇总区分 SCENE_EXPORTED 与 SCENE_SURVEY_READY，另列 geometry/scale/task 验收状态。默认不执行模块 6、7、8，也不运行模块 9 的物理任务审计。CPU 纯导出、短 GPU 巡览和新云重建必须各自有证据。

本轮使用历史真实原片及操作者声明配对的既有 Aholo PLY，重新执行准备与结构审计；没有提交新云任务。公开记录 `docs/report/new-video-validation.json` 绑定 21 份证据和 104 个运行源码文件，源码与 commit `2814514` 一致。

本次显式提供历史视觉对齐的 Sim(3)、估计地面与近景相机；尺度来源仍为 unverified，未放入独立 box/button。抽帧保存 183 张参考图，外观导入 156764 个三阶 SH 高斯。碰撞从 107008 个满足条件的点中抽样 10 万个，在上限约束下把体素边长从 0.25 粗化为 4.0 个声明世界单位，保留 582 个代理，加估计地面共 583 个 collider。这是资源有界估计，不是细尺度几何恢复；本轮未验证自动地面拟合、无配置相机或新增交互对象的动力学。

GPU 首轮遇到 Isaac 应用关闭会直接退出进程、使后置编码未执行的问题，交付检查正确拒绝完成。编码和凭据写入移到关闭前，第二轮完成；失败目录与 224.378 秒耗时保留，不计为成功交付。

<!-- table: v0.7 新视频入口的实际功能验证。短巡览、纯 CPU 导出与恢复分别计时；均不包含新云重建。 -->
<!-- columns: 0.75,1.05,1.20 -->

| 核验 | 实测结果 | 范围 |
|---|---|---|
| CPU 导出 | 命令 wall 7.951 s；结构审计通过 | 历史视频/PLY 重新生成场景，无 GPU |
| 完整恢复 | 命令 wall 0.246 s；4 阶段哈希匹配复用 | 不启动 worker，不重新计算已完成阶段 |
| Docker 短巡览 | 命令 wall 231.874 s；SCENE_SURVEY_READY | 最终源码与 2814514 一致，包含冷启动 |
| 视频探测 | 640×360、24 fps、12 帧、0.5 s；4 子帧、相机扫角 20° | 短功能验证，未跑默认 120 帧完整工作量 |
| CPU 回归 | 427 passed、1 skipped | 代码路径与失败保护；不能代替画质/任务实测 |

命令 wall 包含 Python 命令启动与退出；宿主 summary 的 invocation 为 231.744 秒，worker 为 224.477 秒。worker 中场景准备 1.081、结构审计 0.227、渲染 223.035 秒；宿主抽帧 6.450、已有重建清单复用 0.073 秒。窗口起止不同且包含额外编排开销，不能把嵌套计时相加。SDK/镜像安装、历史云重建及人工场景规格准备均不在该窗口中；本轮是功能验证，不是加速基准。

### 可见质量与通过范围

检查第 0、6、11 帧后，原始 Aholo 底稿的模糊、表面缺失与空洞仍明显，近景结构和控件无法可靠辨识。通用生成器保留该外观，并估计不可见碰撞代理；它没有自动执行历史变电站的照片纹理恢复、柜体补建和实例修复。结构、帧数和编码通过只证明功能路径可运行，未通过外观保真 benchmark，也未完成独立米制尺度或机器人任务验收。

![v0.7 新视频路径实际输出的第 6 帧，原图 640×360，未锐化或修补。可见大面积模糊、表面缺失和空洞；SCENE_SURVEY_READY 表示场景巡览可交付，不表示外观保真达标。](figures/new_video_survey_frame.png)

## 已准备场景的一键复现

v0.6.0 的 `simready run` 核验输入和环境，再顺序执行来源观测、任务、审计和录像。首次使用先安装源码包，按场景说明准备 `data/substation/bundle.json`，并按部署说明构建 Isaac Docker 镜像；SDK 和第三方数据不是命令自动下载的内容。

```bash
python -m pip install -e '.[ingest,aholo]'
simready run --scene substation --accept-eula
```

默认后端是 Docker，资产路径为 data/场景名，输出为带 UTC 时间和唯一 ID 的新目录。将场景改为 starbucks 即运行其 G2 视频流程；两者都不提交云任务。CPU 编排支持 Python 3.10+，物理环境需要匹配的 Isaac Sim 6.1.0 / Isaac Lab 3.0.0、Python 3.12 与 RTX GPU。

### 输入与后端选项

<!-- table: 统一入口的输入与执行后端选项。远端部署须事先准备。 -->
<!-- columns: 0.75,1.15,1.10 -->

| 选项 | 作用 | 条件 |
|---|---|---|
| `--assets` | 指定已有 bundle 目录 | 与 bundle-spec 互斥 |
| `--bundle-spec` / `--bundle-python` | 先导出已有资产规格，再执行场景 | 导出解释器须有 pxr；不做新资产构造 |
| `--backend local` / `--python` | 使用本机 Isaac Python | 也可由 ISAAC_PYTHON 指定 |
| `--backend ssh` / `--ssh-host` / `--repo` | 在已有远端检出中调用 Docker | 代码、镜像、数据预先准备，不自动传输 |
| `--dry-run` | 输出只读计划 | 不建目录，不导出，不启动进程/GPU/云 |

```bash
simready run --scene substation --dry-run
simready run --scene substation --bundle-spec /path/to/substation.bundle.json \
  --bundle-python /path/to/pxr/python --accept-eula
```

### 结果与失败检查

根目录保存 `run-plan.json` 和 `summary.json`；execution 目录保存环境、场景和阶段状态。变电站的审计、传感器与录像位于 `execution/delivery/`，视频位于其 render 子目录。若传入 bundle spec，导出的输入包放在根目录的 bundle 子目录，与执行输出隔离。

只有该场景要求的环境、阶段和交付检查全部通过，汇总才标记 COMPLETE；变电站还须通过物理审计，星巴克不据此声明物理成功。失败或中断保留阶段、错误与日志位置。显式输出目录必须不存在；SSH 只读计划不能替代远端执行证据。

包哈希检查、CPU 迁移闭包和完整 GPU 任务分别记录，不能相互替代。下一节保留 v0.6 已准备场景入口的实测，不能转用为 v0.7 新视频路径的成绩。

## v0.6.0：已有场景入口实测

v0.6 本轮在 aarch64、NVIDIA GB10、驱动 580.159.03 上运行默认 Docker 入口，使用修复后镜像与最终导出的场景包。汇总、输入和代码身份、视频规格及哈希记录在 `docs/report/one-command-validation.json`。

<!-- table: v0.6.0 v0.6 本轮功能验证。完整物理、短预览和 CPU 负例分别报告。 -->
<!-- columns: 0.75,1.15,1.10 -->

| 核验 | v0.6 本轮结果 | 验证范围 |
|---|---|---|
| 变电站完整流程 | 20/20 审计；模拟 28.508 s，位移 1.294082 m，一次按压 | 包与环境检查、抽帧、物理、独立审计、原生回放和交付检查 |
| 变电站视频 | 1920×1080、25 fps、713 帧、28.52 s | v0.6 本轮测量轨迹的回放，非历史 714 帧视频复用 |
| 星巴克短预览 | 640×360、30 fps、8 帧、0.267 s；2 个渲染子帧 | G2 编排入口与交付检查，非 4K 重跑或物理导航 |
| CPU 与静态检查 | 297 passed、1 skipped；Ruff、格式、编译和架构检查通过 | 包含失败分支与输出保护，不替代 GPU 任务 |
| bundle spec 实际导出 | 导出 30 个文件；缺少 Isaac 的解释器在环境检查处按预期失败 | 验证 CPU 导出与失败汇总，未执行 GPU 阶段 |

### 时间窗口

<!-- table: v0.6 本轮入口的阶段 wall 时间。总计包含阶段外检查与编排开销。 -->
<!-- columns: 0.75,1.15,1.10 -->

| 阶段 | 变电站 / s | 星巴克预览 / s |
|---|---|---|
| 来源观测 | 7.528 | 7.710 |
| 物理任务 | 679.012 | 不适用 |
| 独立审计 | 3.792 | 不适用 |
| 原生录像 | 403.469 | 272.256 |
| 入口总 wall | 1095.164 | 281.670 |

变电站入口总时长为 18 分 15.164 秒，内部工作流 wall 为 1093.885 秒。入口计时从初始只读计划完成后开始，包含检查、worker 启动、冷 shader 编译和编排开销；不包含 SDK/镜像安装、新重建或此前资产准备。总 wall 不等于阶段和；这些是功能验证耗时，不是加速基准。

### 代码身份与证据边界

变电站整轮运行的 96 个源码文件与 commit `10c250b` 一致。完成后仅改动启动错误分类、拒绝已有内部 worker 输出两条失败路径，新增 5 个 CPU 负例；控制、物理和渲染代码未变。星巴克预览使用最终源码，变电站未在这两处保护改动后再次重跑。

已目视检查变电站 6、22、28 秒画面及星巴克第 0、4、7 帧。物理成功仍由独立审计判定；单次任务与短预览不能外推跨场景成功率。SSH、新视频构建和新机器人策略均不属于这轮证据。

## 历史案例：任务测量与外观质量

本节图像和数值保留原接受任务的版本归属，不作为本轮新视频入口的输出。

### 原片与物理任务

![历史原片中的柜内观测。原片与回放相机不同，不能作为像素对齐的重建对照。](figures/source_frame.jpg)


![历史接受任务的导航回放帧。Lite3+Z1 沿已知场景接近停靠区；柜体含原片约束的补建与显式估计，图像不作为几何真值。](figures/substation_navigation.jpg)

![历史接受任务的按压回放帧。画面约为 1.22 N、3.09 mm；序列峰值见表，不以单帧 HUD 代替日志。](figures/substation_press.jpg)

<!-- table: 历史接受任务的传感器与审计结果，保留原版本归属。 -->
<!-- columns: 0.75,1.15,1.10 -->

| 指标 | 历史接受值 | 解释 |
|---|---:|---|
| 模拟时长 / 水平位移 | 28.528 s / 1.2945 m | 单个选定任务 |
| 有效按压 / 审计 | 1 次 / 20 项通过 | 接触与位移共同触发 |
| 按钮最大行程 / 峰值接触力 | 3.130 mm / 1.228 N | 来自完整传感器序列 |
| 工具最大 / 最终压缩 | 1.533 / 0.040 mm | 被动柱塞 |
| 近距离定位误差 | 1.111 mm | 相对仿真碰撞前表面 |
| 最大倾角 | 4.606° | 当前任务范围内 |
| 规划阶段 / 在线碰撞检查 | 3 / 889 | 绑定的路径与传感器集合 |

上述结果不是大样本成功率、现实柜体定位精度或商业安全认证。独立审计重算事件、图像目标和 FK；零非目标接触只适用于已绑定集合。

v0.5 接入统一接口后，另一轮 Docker 物理任务模拟 28.508 秒、移动约 1.294544 m，通过 20/20 项审计，未重录视频。这是独立核验，不替换本节历史峰值或后续回放的视频指标。两次证据的代码、镜像和输入哈希见包装核验清单。


### 实测接触与回放

![接受任务的接触时序。目标接触力、按钮位移和工具压缩来自 sensors.jsonl，而不是动画关键帧。](figures/contact_trace.svg)

回放不重新运行物理，避免数值积分产生另一条轨迹。普通阶段采用机身上方约 45° 的跟随镜头，按压和回撤时切到接触特写；展示相机不参与控制。

历史视频为 1920×1080、25 fps、714 帧、28.56 秒。最后一个采样约在 28.522 秒，成功事件约在 28.526 秒，末帧 HUD 因而可能仍为 RETRACT；最终状态以审计日志为准，不能为了画面改写事件。

资产补建使近景边缘更清楚，但不会增加真实观测。视频验收与来源一致性、几何和物理成功分别判定；不得降低分辨率、帧数或子帧数后把预览计时当作完整交付性能。


### 星巴克外观与 G2 编排


![星巴克原生 4K 视频中的 G2。机器人边缘清楚，背景仍可见高斯重建的涂抹与缺失区域。](figures/starbucks.jpg)

**输入：**已准备 Gaussian USD、G2 姿态和编排参数。**方法：**修正 Graphdeco 风格高阶球谐系数的通道排列，设置灯光、DLAA 和去噪，按通道往返轨迹更新根部位置、轮向与轮转。**输出：**历史 3840×2160、30 fps、900 帧、30 秒原生视频。**证据：**原生帧、编码参数和输入哈希；没有物理导航审计。

<!-- table: 星巴克不同处理操作的作用范围。画面改善不增加真实观测。 -->
<!-- columns: 0.75,1.15,1.10 -->

| 改动 | 实际改变对象 | 能证明什么 |
|---|---|---|
| 球谐通道修正 | 高斯颜色解释 | 修复格式错误 |
| 纹理恢复与独立几何 | 三维资产与材质 | 指定区域清晰且可编辑，仍需来源验证 |
| 灯光、DLAA、去噪 | 渲染图像 | 改善成像，不增加真实观测 |
| Difix 历史预览 | 二维生成式细节 | 仅用于展示，不认证几何或控件类别 |

当前报告的任务接受证据使用原生渲染。v0.5 封装验证中的 G2 Docker 测试只有 640×360、8 帧；最终端口另完成对应真实 USD 位姿核对。这些检查没有重新生成整段 4K 视频。

## 历史时间口径

下表保留此前接受任务和 v0.5 包装核验的版本归属。它们与 v0.6 的已有场景入口实测使用独立记录，不合并为同一次实验。

<!-- table: v0.5 历史包装核验。当时的分项通过不合并为未经执行的全组合通过。 -->
<!-- columns: 0.75,1.15,1.10 -->

| 核验 | 已完成 | 边界 |
|---|---|---|
| 已有资产本机完整流程 | 抽帧、物理、20 项审计与 714 帧录像；总 wall 756.632 s | 接口接入前版本，无云重建/资产再处理 |
| 统一接口 Docker 物理 | 28.508 s 模拟过程、20/20 审计；启动总 wall 704.019 s | 依赖修复前镜像，含冷 shader，未重录 |
| G2 接口 | 初版 8 帧 Docker 预览，最终端口真实 USD 位姿核对 | 非完整 4K/900 帧重跑 |
| 最终导出器 | 两场景重新导出、CPU 迁移闭包检查 | 新包未重复 GPU 任务 |
| 依赖修复后镜像 | urllib3 完整分发校验与 Kit 启动检查 | 未重跑全物理/审计 |
| SSH / Aholo | 命令构造；已有云任务一次 GET 续接 | 无远端连接，无新付费重建 |

当时的最终接口、修复后镜像与新导出包未组合重跑完整 GPU 流程。版本、输入和证据哈希见包装核验清单；不能把分项通过合并为未经执行的全组合通过。

### 三类时间不能混用

历史 3350.67 秒（55 分 50.67 秒）是三次物理、三次录像和工程工作的窗口，不是新视频 E2E。一次历史本地阶段合计为 912.77 秒，其中资产 169.13、物理 296.54、审计 3.35、录像 443.74 秒；它也不包含新云重建。

已有资产本机核验的 756.632 秒由抽帧 8.353、物理 297.568、审计 4.267、录像 446.381 秒及约 0.064 秒编排开销构成。它没有包含资产再处理，不能据此宣称突破 15 分钟全工作量目标。

历史渲染候选两次完整录像中位约 364.95 秒，相对历史对照约下降 18.56%，只代表冻结视频工作量的实验。15 分钟全工作量目标未达，性能优化保持暂停。

Aholo 历史服务器 create→last-update 约 290 秒，本地 submit→receipt 约 3610.90 秒，差距包含 SSL EOF 后的监控中断。服务器更新时间不等于训练结束时刻；宿主等待不等于 GPU kernel 时间。Agent token 与固定 worker/GPU 时间另行记录，usage 未知不填零。

## 限制与后续验证

新视频路径实现的是场景候选导出与诊断巡览。外部 PLY 配对依赖来源声明，尺度/地面/对象存在估计或外部输入；默认无完整实例分割、纹理修复、真值标定和机器人任务。后续应先补独立视角、测量尺度和实例对应，再评估哪些缺陷可由真实插件修复，而非仅增加结构通过项。

观测框架需要预先冻结覆盖范围和标定门槛。开发候选的无退化改善不等于最终密封集通过；成功率应按预声明场景与任务采样，保留失败、缺证据和不适用样本。RSI 参数配方尚无任意新视频、跨布局的完整泛化成绩。

机器人只覆盖已准备地图与目标；G2 无物理导航，独立 Lite3/Z1 无独立任务成功记录。新构型需要策略、安装/TCP、传感器和接触标定。结构 audit、物理仿真与视频成像分别约束不同风险，不构成真实电气设备的安全认证。

15 分钟完整新视频工作量目标未达到，性能优化保持暂停。恢复实验时须固定资产、代码、步长、帧数、分辨率、子帧和门槛，并先测 A/A 波动。安装、云等待、冷启动、工程排错和有效 worker 时间分别记账；短预览不作为完整交付加速对照。

<!-- pagebreak -->

## 参考文献

- [1] Kerbl 等. 3D Gaussian Splatting for Real-Time Radiance Field Rendering. SIGGRAPH, 2023. 官方项目：https://repo-sam.inria.fr/fungraph/3d-gaussian-splatting/
- [2] VGGT. 官方模型与实现：https://github.com/facebookresearch/vggt
- [3] Segment Anything. 官方模型与实现：https://github.com/facebookresearch/segment-anything
- [4] DINOv2. 官方模型与实现：https://github.com/facebookresearch/dinov2
- [5] NVIDIA Isaac Sim. 系统要求与容器部署：https://docs.isaacsim.omniverse.nvidia.com/latest/installation/requirements.html
- [6] Isaac Lab. 官方仿真与机器人学习框架：https://github.com/isaac-sim/IsaacLab
- [7] Aholo. 重建服务 API：https://labs.aholo.ai/api-docs/en/openapi.json

<!-- endreferences -->

<!-- appendix -->

## 公开入口与证据索引

项目仓库：https://github.com/miracle-techlink/aholo-powergrid-simready

v0.6 报告与源码发布：https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13-v2

- v0.7 新视频功能实测：`docs/report/new-video-validation.json`。
- v0.6 已有场景入口实测：`docs/report/one-command-validation.json`。
- 历史接受任务：`docs/report/evidence.json`。
- v0.5 包装核验：`docs/report/packaging-validation.json`。

每份记录保留各自的输入、代码与证据哈希，不以目录名或相近数值合并实验。统一运行方法见 README.md，场景与机器人准备见 examples/scenes/ 和 examples/robots/。

原创代码采用 MIT。原视频、机器人资产、权重、Isaac SDK 和云服务遵循各自许可，源码发布不重新授权这些材料。旧报告与历史实验保持独立版本，修订版不覆盖旧 Release。
