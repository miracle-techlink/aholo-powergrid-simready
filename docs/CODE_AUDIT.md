# 代码整理与审查记录：0.3.0

2026-11-12。本次覆盖仓库的全部 Python 源文件：逐模块检查职责、调用与数据流，全部文件执行 AST/编译检查和静态错误扫描，可移植代码执行完整 lint、统一格式和 CPU 回归。逐文件路径、哈希、定义、硬编码本地路径数量见 [机器可读清单](code_audit.json)。清单覆盖不等于每个 GPU/云服务分支已执行；无法保证代码永远没有 bug。

## 结构整理

安装包仅暴露 `simready_bridge`，依赖方向为 pipeline → agent / plugins / infra / memory / skills / benchmark → 公共 IO、进程与几何工具；运行时不依赖 `legacy`。Memory 的成功状态由 scorecard 重新计算，不接受 Agent 自报成功。插件执行和 benchmark 计划分别使用不同注册表，避免将尚未接入的方案误当成可运行代码。

旧实验集中到 `legacy/`，不再占据顶层，也不随 wheel 自动安装。原来的三份工程总文档和交付资产在原工作目录中，本次没有修改它们。

| 原路径 | 新路径 / 入口 | 兼容性说明 |
|---|---|---|
| `s0_ingest/extract_frames.py` | `simready-ingest`；旧入口移到 `legacy/s0_ingest/` | 改为显式 `--video --out`，新清单有 SHA256 与抽帧参数 |
| `s1_aholo/submit_panel.py` | `legacy/s1_aholo/submit_panel.py` | 显式 `--video --out --client`；历史 worldId.txt 不自动当作可信回执 |
| `rsi_3dgs/` | `legacy/rsi_3dgs/` | NPZ 统一 xyzw、线性 scales、[0,1] opacity |
| `scene_bench/` | `legacy/scene_bench/` | 保留历史指标；不混入新协议分数 |
| `warehouse_3dgs/` | `legacy/warehouse_3dgs/` | entry-only 脚本导入不启动 Isaac/CUDA；深度训练需显式实测焦距 |
| `submission_template/` | `legacy/submission_template/` | 非场景运行时；默认预览不再上传文件 |

历史模块在仓库根目录以 `python -m legacy.<目录>.<模块>` 运行，须准备其专用依赖。旧绝对路径仍出现在部分历史实验中，清单中明确列出，未宣称它们已经全部迁移为生产 worker。旧模型若不符合新约定，必须显式转换，不能悄悄当作新格式加载。

## 修复与验证

| 发现的问题 | 修正 | 验证范围 |
|---|---|---|
| 抽帧去重把字节计数当 bit Hamming 距离 | 256 位逐位比较；帧清晰度计算使用内部像素 | 单元测试 + 实际 ffmpeg 视频抽帧 |
| 相机 OpenGL/OpenCV 轴混用，c2w 反变换缺转置 | 公共 `geometry` 明确 right/down/forward；训练入口使用正确逆变换 | CPU 投影中心、前向、往返、USD 四元数测试；GPU 图像未复测 |
| NPZ/gsplat/PLY 四元数顺序不一致 | NPZ xyzw，gsplat/PLY wxyz；显式转换 | CPU 几何与序列化测试 |
| PLY DC 系数夹断导致黑白颜色偏差 | 保留完整 RGB→SH DC 映射 | RGB 端点往返测试 |
| 融合脚本重复 exp/sigmoid，未优化模型存零相机 | log/logit 正确初始化；未 polish 也写真实位姿 | 静态审查；实际 CUDA polish 未复测 |
| 半径/颜色邻域修复构造巨大 N×N 距离矩阵 | cKDTree 查询与有界查询批次 | 小样本精确结果和修复测试；无大型 GPU 性能结论 |
| 补洞忽略 max_fill、浮点颜色再次除 255、体素哈希冲突 | 上限强制执行，按颜色 dtype 归一，完整整数体素键 | 数值回归 |
| 模型 shape 仅 assert；None 相机被写成无效零矩阵 | 显式数据校验，缺失相机不写占位；内容哈希含相机 | roundtrip、坏相机和 identity 测试 |
| RSI 保存 gate 早于最终覆盖，回退后报告修复分数 | 先确定接受/回退，再保存 gate 与最终指标 | 代码路径审查；GPU episode 未复跑 |
| RANSAC 先构造所有 N choose 3 组合 | 最多抽取 2,000 个唯一三元组 | 250 相机、带离群值的数值回归 |
| GPU 脚本导入即解析参数、启动应用；名为 test 的渲染基准被误收集 | main 入口隔离；更名 benchmark_render_variants | 无 torch/Isaac/网络导入测试 |
| USD 转换器在导入时退出，`--ply-only` 仍要求安装转换器 | 仅转换时解析显式环境变量/PATH | import/CLI 检查；真实转换器未运行 |
| 两个旧渲染器重复相机与 IO 工具 | 抽出 render_common；复用公共原子 JSON 与几何 | 静态/编译与纯数学测试 |
| USD 验证器按根目录猜嵌套引用、Points 计数错误 | 依赖解析交给 USD resolver；标记为结构检查 | 静态审查；目标 OpenUSD 环境集成待执行 |
| Aholo 导入即提交，下载没检查 HTTP 状态 | 显式 CLI、互斥锁、提交意图/回执、有限轮询、流式下载和哈希 | 导入测试；未发起新的付费 Aholo 任务 |
| 提交模板日志含认证头/载荷，配置根目录错位，预览也上传 | 去掉敏感头和请求/响应正文，URL 去 query/fragment，修正根目录，预览禁上传 | 静态审查；未访问提交网站 |
| 租约重复 close 归还两次资源；缓存复制/排队存在证据变化窗口 | 幂等释放、复制后校验、缓存复用前重新校验输入/依赖 | infra 回归 |
| Agent 输出、memory 和 skill 易与执行混淆 | 强类型候选选择、独立验收、证据绑定、负经验、版本失效与退役 | 闭环与异常回归、真实 Codex 合成调用 |

## 可复现检查

```bash
python -m pip install -e '.[test,dev]'
ruff check simready_bridge tests examples scripts
ruff check legacy --select E9,F63,F7,F82
ruff format --check simready_bridge tests examples scripts legacy
python -m compileall -q simready_bridge legacy examples scripts
python -m pytest -q
python -m build
python scripts/audit_source.py --out docs/code_audit.json
```

CI 在 Python 3.10、3.12 上执行 lint、格式、测试、编译、构建与 CLI 检查。真实 ffmpeg 测试在没有 ffmpeg 的环境会明确 skip。当前本机为 Python 3.14；真实 Codex 账号测试只使用合成场景，详情见 [测试汇总](benchmarks/agent_runtime_20261112.json)。

## 尚未认证的边界

- CUDA/gsplat/Isaac、Difix、USD 扩展和 Aholo 服务的集成分支没有在本次全量重跑。修改过坐标约定的历史训练/采集脚本，正式使用前需要在专用环境重新采集和比对，不能沿用旧截图当新证据。
- 旧渲染器仍有场景路径、全局 GPU 锁、进程环境等实验假设；它们不在已发布运行时的插件注册表中。渲染速度基准的亮度非零检查不等于图像等价。
- 新 verifier 是独立进程，不是独立 OS 权限域。SHA256 能证明字节一致，不能证明观测来源真实或阻止同权限恶意修改。
- Skills 是受测插件参数的复用，没有自动生成/晋升新算法代码、基础模型训练或 Codex 原生 Skill 包导出。
- 完整视角、真实资产提取、物理/机器人任务、生产阈值和密封发布集仍未完成。`DEVELOPMENT_PASS` 仅指所给开发协议通过；发布授权始终关闭。

相机约定依据：[gsplat 数据约定](https://docs.gsplat.studio/main/conventions/data_conventions.html)、[rasterization 参数](https://docs.gsplat.studio/main/apis/rasterization.html)。
