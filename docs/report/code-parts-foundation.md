### 基础数据模块 core：保存身份，拒绝含糊输入

`core` 只提供文件、证据和基本类型操作，不导入 CUDA、Isaac 或数值计算库。它让云客户端、普通 CPU 校验器和仿真进程使用同一种证据表达。领域规则仍由拥有该数据的模块验证，例如 `core` 不决定关节限位、合格分数或 Skill 晋升条件。

<!-- table: 基础模块的内部构造与保证范围。 -->
<!-- columns: 0.80,1.20,1.00 -->

| 组成 | 实现机制 | 调用方得到的保证 |
|---|---|---|
| `artifacts` | `loads_json` 拒绝重复键、非有限值和溢出；`digest` 分块计算 SHA-256；`object_digest` 固定 JSON 键序和编码 | 相同数据有稳定身份，错误数据不会静默覆盖同名键 |
| `write_json` | 在目标目录建唯一临时文件，flush/fsync 后原子替换 | 不暴露写入一半的 JSON；不提供多文件事务或冲突合并 |
| `evidence` | `reference` 记录路径与摘要；`resolve_ref` 核验文件；`ensure_unchanged` 复查冻结证据 | 发现来源变化；不是签名认证，也不证明采集内容真实 |
| `validation`、`types` | 基本断言、有限数值、非负数和唯一 ID 检查；`PathLike`、`JsonObject` 边界别名 | 共用基础检查；JSON 的完整 schema 由领域所有者负责 |

这些函数不持有全局运行状态。回执、协议和 episode 的状态分别保存在工作流目录、冻结文件和经验数据库中。`write_json` 的原子替换避免读到半份文件；两个写入者对同一个目标的语义竞争仍需上层锁或事务处理。`resolve_ref` 验证路径解析与内容身份，但本身不限制文件必须位于 root 内；可移植资产包和任务缓存应由各自合同实施目录边界检查，不能将哈希校验当作路径沙箱。

### 几何约定模块 geometry：统一相机变换，不隐藏坐标差异

`geometry.cameras` 是纯几何函数集合，内部只计算矩阵与四元数，没有场景对象、相机设备或长期状态。`look_at_c2w(eye,target,up)` 用相机位置、目标点和上方向建立 $4\times4$ 相机到世界矩阵；输出相机轴为 OpenCV 的右、下、前，拒绝零方向以及与上方向平行的视线。`rigid_inverse` 支持批量矩阵，先检查正交旋转、行列式与齐次末行，再用旋转转置求逆，拒绝把缩放矩阵当作刚体位姿。

USD 相机采用右、上、后坐标，`usd_look_at_quaternion` 因此显式乘以轴翻转矩阵，再返回 wxyz。`reorder_quaternion(values,source,target)` 只重排 xyzw/wxyz 的元素并检查有限非零输入，不负责归一化。调用者必须区分表示顺序、坐标轴约定和尺度变换：三者是不同操作。高斯 PLY 的解码与相似变换仍归 `simulation.gaussian_asset`，RGB-D 反投影归 `simulation.panel_perception`，机械臂 FK/IK 归 `simulation.kinematics`。

新增相机或设备时，先在数据边界明确相机到世界还是世界到相机、列向量还是行向量、四元数顺序和单位，再复用这些函数。这样可在不启动 Isaac 的条件下验证几何换算，避免各录像器和感知模块各自保留一套隐含约定。
