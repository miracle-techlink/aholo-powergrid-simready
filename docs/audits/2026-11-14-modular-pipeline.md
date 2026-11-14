# 模块化 pipeline 与 baseline 接入验收

本轮把已有算法和交付链归入明确职责，以配置、产物合同、阶段身份连接；没有另建调度服务。三名 agent 分别处理资产迁移、重建合同、阶段回执／矩阵，主 agent 完成规范入口、语义／物性接线、交叉审查和全仓回归。此前嵌套取消、GPU 租约、NeRF2Physics 和杯控制候选工作保留，历史证据没有改写。

## 实现与兼容

| 交付 | 实际实现与验收 |
|---|---|
| 资产分层 | assets.appearance / association / physics / compilation；纯坐标数学归 geometry。旧 simulation 导入为同一模块的薄别名，旧／新资产 CLI 导出的四份 USD 哈希相同，独立物性读回通过。 |
| 规范入口 | `simready run --pipeline` 与旧入口使用同一 video_pipeline；配置未知项、未实现 provider、缺失证据提前拒绝。支持停止在指定阶段、严格恢复和显式局部升级。 |
| 重建后端 | artifact / aholo / command 统一 bundle，绑定视频、算法、坐标尺度、文件和环境声明。command 复用现有进程监督及设备租约。当前只接受可解码 Gaussian PLY。 |
| 阶段回执 | 成功回执按内容身份保存，兼容索引引用不可变回执；失败另记，输入篡改拒绝。实际 worker checkout 和外部环境清单进入身份。 |
| 语义与物性 | 可选已提供 mask 的语义投影；视频与重建关联必须匹配。NeRF2Physics 数值估计或导入候选接入同一次 USD 编译；语义 REVIEW_REQUIRED 不被结构通过覆盖。 |
| baseline 矩阵 | 固定输入、逐成员执行，保留失败与未执行；支持重建阶段或完整链。缺真实留出证据时为 NOT_EVALUATED，无默认算法排名或自动晋升。 |

配置和模块责任见[规范 pipeline](../pipeline.md)、[架构](../architecture.md)、[迁移表](../migration.md)。

## 完成的验证

- 全仓 **1055 passed，1 skipped，113.97 秒**；Ruff、格式、严重语法检查、compileall 和 diff 检查通过。
- 架构检查 **11 个领域、19 个公开函数**通过；资产子层另有依赖方向检查。
- 构建 sdist / wheel 通过，wheel 包含新资产、重建、阶段和矩阵模块；隔离解释器从 wheel 加载 CLI 帮助成功。
- 实际 CPU 小视频＋合成高斯：dry-run 无输出副作用，停止在 reconstruction 返回 INTERMEDIATE；恢复到完整 USD，随后全部阶段核验复用。
- 将场景杯质量从 0.1 kg 改为 0.2 kg：旧 `--resume` 拒绝，`--upgrade` 复用 observations / reconstruction，重跑 worker_inputs / scene_delivery；旧成功回执哈希不变，USD 实际读回 **0.20000000298023224 kg**。
- 实际 CPU 两成员矩阵：合成 command 和 artifact 导入均 COMPLETE；两者质量均 **NOT_EVALUATED**，不是新模型质量评测。
- 真实 CPU 语义投影／USD、重建 PLY 变更拒绝旧语义关联、证据缺失拒绝、不同 worker 源码只使交付失效，均有回归覆盖。

最终本地证据索引：`runs/modular_pipeline_20261114/validation.json`；全仓日志：同目录 `pytest-full.log`。受检运行时源码身份：`528bad7ac7443e41e3f3d65ce93ca52f5a4e978432d701559443fe09ac73a827`。运行目录不提交到仓库，CPU 示例可按文档在新目录重新生成。

## 交叉审查修复

- 整体源码和实际 worker 源码在运行及发布前重验；阶段执行中代码改变不会写入可复用成功回执。
- 已成功 command 在外层发布前中断可恢复，避免重新执行；依赖 secret 的 command 禁止通过外层恢复／升级间接复用。
- 观测 manifest 必须绑定当前视频及完整帧清单；语义点分配文件必须存在且匹配报告；公共物性规范化代码进入相应阶段身份。
- 外部解释器和 Docker 必须有冻结环境清单；该清单明确是操作者声明，不冒充已独立核验实际依赖。
- sealed 验收只返回允许公开的分数，诊断为 WITHHELD，不把密封视角缺陷送给修复器。

## 尚未验证与后续边界

本轮未执行新 VGGT / AnySplat / TRELLIS 等重建模型、真实 NeRF2Physics 视觉模型、GPU / Docker 生命周期或机器人动力学。此前搬杯候选仍不能宣称已消除倾倒，默认保持原控制器。已有已知相机姿态的 gsplat 研究脚本不等于完整 VGGT→gsplat 可运行链。

完整 mesh／关节生成、任意多输出 DAG、自动多视图对象关联、背景补洞、跨节点调度尚未实现。当前语义代理不等于完整封闭几何，物性先验或模型推断不等于实物测量；训练／留出声明不证明算法训练进程未访问留出帧。后续算法应逐个补 converter、真实运行和独立质量协议后再考虑默认采用。
