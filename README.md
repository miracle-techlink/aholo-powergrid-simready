# SimReady Pipeline

**将来源视频、可交互资产、机器人任务与独立验收连接起来。**

v0.7.0 增加新视频入口：观测 → 可恢复重建 → 坐标/资产准备 → 独立结构检查 → 原生巡览。已有变电站仍执行 Lite3+Z1 物理任务，星巴克仍执行 G2 编排演示。新场景的尺度、语义和机器人任务分别保留验证状态。

[中文竖版技术白皮书 PDF](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/download/technical-report-2026-11-13-v4/simready-technical-report-zh.pdf) · [视频到 Demo 流程](docs/report/pipeline-overview.md) · [白皮书正文](docs/report/technical-report.md) · [新视频说明](examples/new_video/README.md) · [已有场景](examples/scenes/README.md) · [部署](docs/deployment.md) · [方法与模块](docs/architecture.md)

## 从新视频启动

准备本仓库、宿主 ffmpeg、RTX GPU 和 [Isaac Docker镜像](docs/deployment.md)，并通过环境提供 `AHOLO_API_KEY`：

```bash
python -m pip install -e '.[ingest,aholo]'
simready run --video /path/to/input.mp4 --out runs/new-scene \
  --submit-reconstruction --accept-eula
```

![新视频的执行、配置与验收边界](docs/report/figures/new_video_pipeline.svg)

入口自动保存重建凭据并生成可查看的USD、估计碰撞层与巡览视频。`--scene-spec` 接入坐标/尺度证据、显式柜体/按钮及物理参数；`--reconstruction-manifest` 离线复用已有重建；`--resume` 校验产物后恢复；`--prepare-only`只构建和检查USD。配置、产物目录、失败恢复和服务器命令见[新视频完整示例](examples/new_video/README.md)。

单目输入无测量锚点时，真实尺度与材料参数仍未知；新入口不会自动完成全资产语义分割、完整纹理网格或机器人任务。`SCENE_SURVEY_READY`与已有变电站的物理任务通过是不同交付等级。

## 一条命令运行已准备场景

先在源码目录安装编排包：

```bash
python -m pip install -e '.[ingest,aholo]'
```

准备条件：本机可用的 Isaac Docker 镜像、支持 RTX 的 GPU，以及按 [资产合同](examples/scenes/README.md) 导出的 `data/substation/bundle.json`。原视频、机器人模型和策略权重由使用者按许可提供；[部署文档](docs/deployment.md) 给出镜像构建方法。

```bash
simready run --scene substation --accept-eula
```

程序核验包与运行环境，创建唯一输出目录，串接来源检查 → 物理任务 → 独立审计 → 轨迹回放；任一必选阶段失败即停止。`summary.json` 给出最终状态和产物位置，`run-plan.json` 记录输入和执行计划。变电站的视频与物理证据位于 `execution/delivery/`。

将 `--scene` 改为 `starbucks`，并提供对应资产包，可执行 G2 视频流程。它是运动编排演示，不是物理导航。统一入口也支持本机 Isaac Python 与 SSH；该模式完整选项用 `simready run --scene substation --help` 查看。

```bash
# 只读计划：不建输出、不启动进程/GPU、不导出资产
simready run --scene substation --dry-run

# 已有资产规格：先用带 pxr 的 Python 导出，再运行默认 Docker 流程
simready run --scene substation --bundle-spec /path/to/substation.bundle.json \
  --bundle-python /path/to/pxr/python --accept-eula
```

`--bundle-spec` 只打包已经准备好的资产，不执行新视频重建或自动资产补建。SDK、镜像和数据准备与任务运行分开；只有新视频模式显式提供 `--submit-reconstruction` 时才提交新 Aholo 任务。

## 方法总览

![M1–M6：来源观测、经检查的资产准备、任务与独立验收](docs/report/figures/architecture_overview.svg)

高斯底稿保留外观；独立几何、碰撞和关节使指定任务可执行；测量与独立审计决定动作是否成立。外观、碰撞、交互是三个运行层，来源证据链贯穿所有层。

| 阶段 | 输入 → 方法 → 输出 | 当前状态 |
|---|---|---|
| M1 观测 | 视频 → 哈希、抽帧、清晰度和去重 → 帧清单 | 可执行 |
| M2 底稿 | 视频 → Aholo → PLY/SPZ | 可执行客户端；VGGT→gsplat 为独立研究支路 |
| M3 资产 | 底稿与参数 → Sim3、可见高斯、体素碰撞、显式盒子/按钮 → USD | 通用准备可执行；完整纹理补建、语义标定仍需专用处理 |
| M4 任务 | 已准备场景 → 感知、策略、规划、驱动 → 测量日志 | 两个场景专用运行时 |
| M5 验收 | 传感器与固定合同 → 事件、定位和 FK 重算 → audit | 变电站通过后才录制 |
| M6 回放 | 实测轨迹 → 独立回放层与 RTX → 视频 | 与物理成功分开验收 |

Agent 修复循环以固定观测生成缺陷，只从已注册插件中选择提案；可信 worker 执行，独立 verifier 决定接受或保留旧候选。Memory/Skill 复用通过回归的参数配方，不更新基础模型权重。真实插件尚未覆盖任意新视频，详见 [闭环方法](docs/architecture.md) 与 [使用手册](docs/runtime.md)。

## 机器人能力边界

| 接入 | 统一端口提供的能力 | 已知范围 |
|---|---|---|
| G2 | `root_pose`，读取/写入 USD authored 变换 | 编排展示，无物理导航驱动 |
| Lite3+Z1 | `joint_drive`，读取 articulation 实测状态 | 已有步态、RGB-D 与 Z1 控制，禁止端口改写物理根位姿 |
| 独立 Lite3 / Z1 | 同一工厂的绑定模板 | CPU 合同验证，无独立 GPU 任务成功证据 |

端口统一 `describe`、`observe`、`command`，显式约定米/弧度、关节顺序与 `xyzw` 四元数。新构型仍需策略、安装/TCP 和传感器标定；导入 USD 不会自动获得导航或操作能力。见 [机器人接入示例](examples/robots/README.md)。

## 结果与复现范围

v0.7 新入口用真实历史原片与操作者声明关联的已有 PLY 重新生成场景：CPU构建约7.951秒，完整恢复约0.246秒；Docker巡览为640×360、12帧，命令约231.874秒，包含冷启动。独立结构检查通过，但画面仍有原高斯的模糊/空洞，未通过视觉保真度或机器人任务验收。新云任务未执行，不能把这组耗时当成完整重建E2E。详见[新入口证据](docs/report/new-video-validation.json)。

历史 v0.6.0 在 NVIDIA GB10 上使用 Docker 核验统一入口，详见 [一键运行证据](docs/report/one-command-validation.json)：

| v0.6 核验 | 结果 | 范围 |
|---|---|---|
| 变电站完整流程 | 20/20 审计；28.508 s 模拟、1.294082 m 位移、一次按压；713 帧 1080p / 25 fps / 28.52 s | 入口总 wall 1095.164 s，包含冷 shader；不含新重建或此前资产准备 |
| 星巴克短预览 | 640×360、30 fps、8 帧；入口总 wall 281.670 s | G2 编排功能验证，非完整 4K 重跑或物理导航 |
| CPU 与静态检查 | 297 passed、1 skipped；Ruff、格式、编译与架构检查通过 | 包含实际 bundle spec 导出及缺少 Isaac 环境时的拒绝检查 |

以上耗时用于功能验证，不作加速比较。变电站整轮源码绑定 `10c250b`；其后两处失败分支保护由 5 个 CPU 负例验证，控制/物理/渲染代码未变，最终源码另完成星巴克预览。完整来源与差异哈希见证据清单。

以下为保留的历史结果：

| 证据 | 已有结果 | 解释 |
|---|---|---|
| 历史变电站任务 | 28.528 s 模拟、1.2945 m 位移、一次按压、20 项审计通过；714 帧原生 1080p 视频 | 单个选定模拟任务，非真实电气认证 |
| 历史星巴克视频 | 4K、30 fps、900 帧、30 s | G2 编排，非自主导航 |
| VGGT→gsplat v3 | PSNR 14.2357 dB、SSIM 0.6205、LPIPS 0.6117 | 未达 22 dB 门槛，非交付底稿 |
| v0.5 包装核验 | 已有资产完整本机复现 756.632 s；接口 Docker 物理另通过 20/20 审计 | 不含云重建/资产再处理；镜像与资产版本分开记录 |

历史数据和代码身份见 [历史证据](docs/report/evidence.json) 与 [v0.5 核验](docs/report/packaging-validation.json)，与本次新入口实测分开记录。

未观测柜面、尺度和物理参数仍有估计。121 个按钮和 49 个旋钮只是当前低压资产候选，不证明整场景完备。SSH 尚无远端完整任务证据。历史约 56 分钟是多轮工程窗口；15 分钟全工作量目标未达，性能优化保持暂停。

## 开发与许可

[模块/API](docs/architecture.md) · [Agent / Memory / Skill](docs/runtime.md) · [测试与开发](docs/development.md) · [性能口径](docs/benchmarks/substation_goal15.md) · [首版报告](https://github.com/miracle-techlink/aholo-powergrid-simready/releases/tag/technical-report-2026-11-13)

原创代码采用 MIT。Isaac SDK、机器人资产、权重、场景视频和云服务按各自许可使用；本仓库不重新授权第三方材料。参见 [第三方说明](THIRD_PARTY_NOTICES.md)。
