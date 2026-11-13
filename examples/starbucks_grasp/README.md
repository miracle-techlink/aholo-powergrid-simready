# 星巴克任务区：G2 将重建杯子搬到另一张桌子

此例运行真实 PhysX 刚体接触和关节驱动：观察桌面上的杯子，抓起、保持、搬到另一张桌子、放稳、松爪并退回。视频回放使用仿真实测位姿。执行过程中没有杯子挂接关节，没有直接修改杯子位姿。

**范围是固定基座 G2_90d 的双桌任务，不是移动导航或整店重建验收。** 原星巴克 `root_pose` 编排入口仍然保留；本例新增独立的关节操控入口，尚未取代通用机器人端口。

```mermaid
flowchart LR
    A[准备资产\nG2关节USD + 三视图重建杯 + 两张桌子] --> B[合成头部RGB-D观测]
    B --> C[支撑面与尺寸约束\n生成几何候选]
    C --> D[Grounding DINO + SAM\n确认并分割杯子]
    D --> E[重建网格配准\n估计杯子平移]
    E --> F[USD关节树FK/IK\n笛卡尔路径]
    F --> G[PhysX TGS 240 Hz\n关节驱动与接触反馈]
    G --> H[独立验收\n离桌高度/接触/放置/释放/擦碰]
    H --> I[实测轨迹回放\n移动镜头与视频]
    H --> J[位姿/接触力/哈希/时间账本]
```

## 一键运行

需要已准备资产，Isaac Docker 镜像和模型环境。环境安装、镜像构建见仓库 [Isaac 部署说明](../../docs/deployment.md)。

```bash
python -m pip install -e '.[physical-assets,simulation,segmentation]'
# 首次下载 Grounding DINO / SAM 后，此例用 --local-files-only 离线执行。
python examples/starbucks_grasp/run.py \
  --assets /path/to/prepared_assets \
  --scene corner_v3/scene.usda \
  --out runs/my_cup_transfer \
  --model-python /path/to/model_env/bin/python \
  --render --width 1920
```

`--out` 必须是新目录。省略 `--render` 只做传感、定位、规划、物理执行和验收；省略 `--scene` 使用隔离双桌环境。FFmpeg 用于最后编码。默认 Docker 镜像为 `simready-isaac:6.1.0`，可用 `--image`、`--gpu` 覆盖。私有原片和第三方机器人资产未打进代码仓库。

准备输入的结构：

```text
prepared_assets/
├── task.usda            # 机器人、杯、桌面及初始条件
├── plan.json            # 初始关节配置与案例参数
├── robot.usdc           # 展平的原生 G2_90d 铰接体
├── cup.usdc             # 三视图 Aholo 重建的纹理网格与凸包碰撞体
├── textures/
└── corner_v3/           # 可选的局部修复资产
    ├── scene.usda
    ├── wall_x.png / wall_y.png / table_wood.png
    └── repair.json
```

已有机器人和杯子 USD 时，先生成独立双桌输入：

```bash
python examples/starbucks_grasp/prepare.py \
  --robot /path/to/G2_90d/robot_fixed.usda \
  --cup /path/to/cup_multi.usda \
  --out runs/cup_assets
python examples/starbucks_grasp/run.py \
  --assets runs/cup_assets --out runs/cup_run \
  --model-python /path/to/model_env/bin/python --render
```

这不是任意新视频的自动重建入口。它复用已重建杯子、已知桌面及配置好的场景；不包含云重建时间。

## 模块与证据

| 模块 | 输入、处理与输出 |
|---|---|
| `prepare.py` | 展平引用、复制纹理、设置质量/摩擦/关节驱动，按真实 USD 关节坐标求解右臂 7-DOF 路径；输出 portable USD 与计划 |
| `prepare_cafe.py` | 将本地训练 30,000 步的 3DGS 候选对齐到两个桌面锚点；检查两处锚点同时吻合，保留原模型 |
| `repair_corner.py` | 从配准原片投影墙画、提取木纹；按明确记录的尺寸估计补建局部墙面、地砖、长椅；输出静态碰撞体与来源记录 |
| `cup_localization.py` | 合成 RGB-D、支撑面/尺寸约束、DINO/SAM、裁剪相机内参更新、带离群点剔除的模型配准；不读取杯子真值位姿 |
| `articulated_kinematics.py` | 解析父子关节坐标，提供 FK 和有限位 IK；数值求解成功不等于碰撞通过 |
| `cup_grasp.py` | CPU PhysX TGS，240 Hz，32/8 次位置/速度迭代；驱动原生关节，记录真实刚体状态与接触力 |
| `cup_transfer.py` | 不导入控制器；独立重算杯碰撞网格位置、离桌高度、承托、倾角、释放和环境擦碰，校验文件哈希 |
| `grasp_video.py` | 初始 RGB-D 采集或实测轨迹渲染；回放时禁用动力学，避免二次仿真改变已验证轨迹 |
| `run.py` | 按依赖顺序运行，任一步失败即停止，逐段写入 `timing.json` |

输出 `artifacts/physics/rollout.npz` 保存 5,400 个物理采样点，包含关节实际值、驱动目标、杯子与机器人刚体位姿、接触力以及阶段标签。`artifacts/audit.json` 是独立验收；`G2_two_table_transfer.mp4` 为 22.5 秒、30 fps 的演示。`timing.json` 区分模型、规划、仿真、渲染与编码耗时。

## 明确的先验与限制

- 杯子由既有三视图 Aholo 产物导入，约高 105 mm、直径 80 mm；它是另一组物体照片重建的独立资产，不是从星巴克原视频中自动抽出的同一只杯。
- 杯质量 **0.085 kg**、静/动摩擦 **0.8/0.6**、恢复系数 **0** 都是未测量先验。未模拟纸杯变形，不能据此证明实物不会压坏。
- 已知源桌面中心 `(0.65, -0.13)`、半径 `0.32 m`、高度 `0.85 m`；目标放置点为 `(0.40, -0.70, 0.85) m`。杯子初始位置由图像和深度估计，目标桌面由案例配置提供。
- 配准假设杯子直立、尺度已知；不估计杯子的完整朝向，不覆盖任意物体识别。
- 搬运前的定位来自合成 RGB-D。**放置高度修正仍读取仿真杯子和夹爪位姿**，属于有特权状态反馈，尚非纯视觉闭环。
- 机器人基座固定、躯干五关节锁定，源模型自碰撞关闭。验收覆盖两桌、配置的墙面和长椅，不证明自主导航、全身平衡或真实机器人安全。
- 局部任务区采用显式几何补建。墙画/木纹有原片来源，长椅/地砖/粗糙度是估计；整店、门面、其它桌椅及原片小物件的完整一致性尚未通过验收。
- 正式一键链路没有 LLM 调用；DINO/SAM 是本地模型推理。报告中的 0 API tokens 不包含开发对话，不能当作整个开发过程零 token。
