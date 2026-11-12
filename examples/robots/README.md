# 同一个接口接入 G2、Lite3 与 Z1

`simready.simulation.robot_interface` 提供 `describe()`、`observe()`、
`command(capability, target)` 与 `joint_indices(names)`。它绑定已有机器人驱动；
机器人名字不会自动带来导航、步态、IK 或 TCP 控制能力。

| 接入对象 | 工厂 | 已绑定命令 | 观测来源 / 根姿态坐标系 |
| --- | --- | --- | --- |
| G2 星巴克演示 | `kinematic_usd_interface` | `root_pose` | USD authored transform / parent |
| 独立 Z1 | `articulation_interface` | `joint_drive` | 当前物理后端缓冲区 / world |
| 独立 Lite3 | `articulation_interface` | `joint_drive` | 当前物理后端缓冲区 / world |
| Lite3+Z1 组合 | `articulation_interface` | `joint_drive` | 当前物理后端缓冲区 / world |

独立 Z1/Lite3 的例子是驱动接入模板，CPU 测试只验证合同与分发；它们没有独立任务的
GPU 成功证据。现有物理任务使用组合 Lite3+Z1。G2 当前只有编排运动，尚无物理导航驱动。
未绑定的 `navigate`、`tcp_pose` 等命令会抛出 `ValueError`；物理接口也拒绝 `root_pose`。

## 独立机械臂与组合机器人

[bind_articulation.py](bind_articulation.py) 的函数接收完成初始化的 Isaac articulation。
名称来自经核对的机器人合同，四元数顺序来自当前安装的后端；不能按模型名猜测。

```python
from examples.robots.bind_articulation import bind_z1, bind_lite3_z1

# standalone_z1 已加载并完成 simulation reset/update。
arm_port = bind_z1(
    standalone_z1,
    arm_joints=z1_contract["groups"]["arm"],
    quaternion_order=backend_quaternion_order,  # 显式 "xyzw" 或 "wxyz"
)

# composed_robot 是完成安装与标定的单个 articulation。
robot_port = bind_lite3_z1(
    composed_robot,
    leg_joints=rig_contract["groups"]["legs"],
    arm_joints=rig_contract["groups"]["arm"],
    passive_joint_units=passive_tool_units,  # 无额外关节时 {}；滑动柱塞用 "m"
    quaternion_order=backend_quaternion_order,
)
```

独立 Lite3 同样使用 `articulation_interface`，传入 `components=("mobile_base",)`、
`groups={"base": leg_names}`、每个腿关节的 `"rad"` 单位与后端四元数顺序。
每个主动关节必须属于且只属于一个 group；`passive_joints` 补齐被动关节。
导入模型含有未声明的夹爪、关节缺失、组重叠或多机器人 batch 都会被拒绝。
被动关节仍占据完整命令张量中的通道，其弹簧静止目标及 effort 由驱动负责。

## 下发与观测合同

```python
from simready.simulation.robot_interface import JointDriveTarget

info = robot_port.describe()
arm_indices = robot_port.joint_indices(rig_contract["groups"]["arm"])

# 控制器按真实后端顺序填充这两个原生张量，shape 必须都是 (1, joint_count)。
# target_position/target_effort 是已通过限位、碰撞与有限值检查的驱动输出。
robot_port.command(
    "joint_drive",
    JointDriveTarget(tuple(info["joint_names"]), target_position, target_effort),
)
sim.step(render=False)
composed_robot.update(dt)
state = robot_port.observe()
```

`joint_names` 是 articulation 的实际顺序，不能假定它等于合同中 groups 的拼接顺序。
命令中的名字必须逐项匹配；position/effort 的顺序均与之相同。
接口在任何后端写入前检查名字与 shape，不复制张量到 CPU，不检查设备端数值。
驱动继续负责有限值、限位、控制频率和安全检查。该接口是进程内可信驱动接口，
不是接收任意外部张量的 RPC 服务。

`describe()` 输出 schema version、mode、capabilities、components、joint_groups、
passive_joints、batch_size 与单位。位置用 m，角度用 rad；旋转关节速度/effort
分别为 rad/s、N*m，滑动关节为 m/s、N。输出四元数固定为 **xyzw**；输入后端
为 wxyz 时只在后端张量上重排。`root_frame` 与 `observation_source` 明确来源。

物理 `observe()` 返回当前 `joint_position`、`joint_velocity`、`root_position` 和
`root_quaternion`；相应 shape 为 `(1, joints)`、`(1, joints)`、`(1, 3)`、`(1, 4)`。
这些是最近一次物理 update 的状态，不是命令目标或期望 TCP；调用方必须先更新
articulation。返回的张量可能与后端共享存储，应只读使用。

G2 的 `RootPoseTarget(position_m, yaw_rad)` 表示 parent 坐标系中的 XYZ 和绕 +Z 的 yaw。
USD 适配器只接受 meter、Z-up stage 上顺序为 translate/rotateZ 的两个变换操作，
并拒绝额外 scale、reset stack 等会改变该解释的配置。它直接读取当前 USD 操作，
以相同批次形状返回 `root_position`、`root_quaternion`，标记为 `usd_authored`，
不生成关节状态或物理测量。这里的 parent 与物理接口的 world 不可直接混用。

导航策略、步态模型、机器人装配、安装外参、TCP 标定、IK、碰撞检查及工具传感器
需要机器人专用驱动提供。共享接口不会把独立机械臂自动装到机身上，也不会把
G2 编排视频变成导航成功证据。

CPU 回归（包含纯 USD，不启动 Isaac/GPU）：

```bash
.venv/bin/python -m pytest tests/simulation/test_robot_interface.py
```
