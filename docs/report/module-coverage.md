# 工程模块与白皮书主题核查索引

本索引面向工程实现核查，保留十类模块与图示、代码的对应关系。技术白皮书正文按“问题→完整技术路线→关键方法→系统运行→实验与验证”组织；下表不是正文目录。命令与字段约定集中在附录和使用说明，图号以最终 PDF 为准。

| 模块 | 必须解释的原理与数据 | 对应图 | 实现与证据边界 |
|---|---|---|---|
| 1 输入与观测 | 哈希、fps采样、Laplacian、梯度哈希、同视频split与时间戳 | module_input_gaussians | `ingestion.video_frames`；新入口2fps、阈值0/0，测锐度但不按锐度删帧，完全相同梯度哈希仍去重 |
| 2 三维底稿 | Aholo任务/receipt与恢复；Gaussian中心、协方差、opacity、SH；VGGT独立支路 | module_input_gaussians、new_video_pipeline | `ingestion.reconstruction_stage`、`gaussian_asset`；不把供应商训练细节说成本项目实现 |
| 3 坐标尺度 | 列向量Sim3、正尺度/旋转、Z-up、平面估计；单目尺度不可辨识 | module_registration_geometry | `gaussian_asset.validate_sim3`、`scene_preparation`；外部尺度声明不等于独立测量验证 |
| 4 资产生成 | 可见外观、占据体素、碰撞上限、显式box/button及资产ID | module_registration_geometry | `scene_preparation`；不等于全资产语义分割、纹理网格或缺失面真实性 |
| 5 物理交互 | kinematic base、dynamic cap、prismatic joint、弹簧阻尼；成对接触与防抖 | module_button_contact | `scene_preparation`与`button.ContactButton`分别负责结构和测量状态；生成原语不会自动绑定事件或机器人 |
| 6 观测缺陷 | 固定RGB/深度/实例输入、PSNR/SSIM、缺失/错类/位置偏差、缺陷记录 | module_observation_acceptance | `evaluation.observations/scoring/asset_graph`；相机真实性和场景覆盖不由图像计分自动证明 |
| 7 修复与RSI | 受限proposal、可信worker、固定协议重算、回归接收、Memory/Skill晋升 | repair_closed_loop | `repair`、`agents`、`experience`、`workflows.repair_loop`；当前为可组合支路，非每次新视频默认调用 |
| 8 机器人runtime | 策略状态/动作、RGB-D反投影、FK/IK、路径、统一设备端口和驱动 | robot_control_runtime | `substation_mission/robot_interface`；G2编排和Lite3+Z1物理路径不同，新场景未自动迁移任务 |
| 9 独立验收 | USD结构、开发回归、固定机器人任务三类验收分别测什么 | module_observation_acceptance | `evaluation.scene_package`、`regression`、`mission_audit`；三种pass不可互相替代 |
| 10 渲染交付 | 实测轨迹回放/新巡览相机、RTX、编码、帧数与hash、分级完成状态 | module_render_evidence | `scene_video/mission_video/cafe_video`；视频生成不等于视觉保真度通过，首轮关闭顺序失败及重跑均保留 |

当前新视频默认只调用观测、重建、参数化准备、USD结构检查与巡览；修复框架和机器人任务是另外的受约束路径。报告总图与模块目录不应被解读成这些路径已经对任意新视频无条件自动运行。

实测分别查阅 [v0.7](new-video-validation.json)、[v0.6](one-command-validation.json)、[v0.5](packaging-validation.json) 和 [历史实验](evidence.json)。
