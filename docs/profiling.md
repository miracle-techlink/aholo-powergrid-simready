# 真实场景的时间与 token 记账

性能测量分三份账：场景 worker 的运行成本、Agent 决策调用的成本、本次开发排查的成本。确定没有 LLM 调用的 worker 可以记录 0 token；缺少 usage 的调用必须保留未知。图像模型、物理和渲染的 GPU 开销不能用 LLM token 表示。

## 阶段记录

`execution.profiling.profile_command` 为外部 worker 保留开始/结束时间、完整进程墙钟时间、退出状态、stdout、stderr 和 token 来源。输出目录必须是新目录；超时与失败也记录已经发生的成本。

```python
import sys
from simready.execution.profiling import profile_command, summarize_profiles

record = profile_command(
    [sys.executable, "/deployment/observe.py", "--scene", "/data/scene.usdc"],
    name="native_observation",
    directory="runs/profile/round_01/observation_attempt_01",
    cwd="/deployment",
    timeout_s=900,
    round_index=1,
    token_mode="no_llm",  # 只在确认该 worker 不调用 LLM 时声明
    metadata={"candidate": "candidate_01", "attempt": 1},
)
summary = summarize_profiles([record])
```

调用模型时改用 `token_mode="provider"`，并提供新的绝对路径 `usage_file`。worker 从提供商真实 usage 导出该文件，至少包含 `input_tokens`、`output_tokens`、`total_tokens`；可选 `cached_input_tokens` 是输入的子集。总数必须等于输入加输出，不能再次加上缓存输入。文件不存在或格式错误时，不补零。禁止把上一轮的 usage 文件重复计费。默认 `token_mode="unknown"` 不假定 worker 无模型调用。

`summarize_profiles` 分别输出阶段墙钟之和与最早开始到最晚结束的跨度。前者可能含并发重叠，后者可能含排队、人工修复或空闲；两者都不能无条件称为关键路径时间。为每次失败重试分配独立目录，不覆盖原记录。

任务 DAG 另有 `queue_seconds`、`process_seconds`、`worker_wall_seconds` 和 UNIX 开始/结束时间。`worker_wall_seconds` 包含准备、执行与校验；DAG 总耗时还包含调度。原任务结果中的未知 `token_usage` 不会被计时工具擅自改写。需要基于 worker 是否调用模型、提供商 usage 或原始日志生成独立的成本账本。

## 旧部署接入

`examples/deployment_profiling/bridge_worker.py` 是本地变电站验证过的适配示例：物理运行 → 复制产物后独立审计 → 从实测轨迹渲染。通过参数提供外部脚本、解释器、配置和任务输出路径。审计先复制输入，因为旧审计脚本会向输入目录写文件；这样保留上游产物的不可变性。

这个适配器依赖部署中已有的场景、权重和脚本，不能独立从任意视频生成场景。任务的代码、配置、资产依赖及解释器身份仍需显式声明。最终物理验证和验收关闭缓存；不能以缓存命中代替新验证。

## 工程 Agent 用量

`scripts/profile_codex_session.py` 从显式指定的本地会话日志导出请求时间边界之后的累计计数差分：

```bash
python scripts/profile_codex_session.py \
  --session-log /private/session.jsonl \
  --after 2026-11-12T06:39:54.011Z \
  --out runs/profile/engineering
```

只导出数值、时间和快照哈希，不导出消息、认证信息或原始日志。工具需要边界之前的一条累计计数；缺少基线、计数回退或字段不完整会标记连续性不足。重复计数事件不会重复累计。CSV 的每行是一次**用量计数更新**，不是一次场景修复，也不保证等于一次模型调用。行间时间包含工具执行和等待，不能当作模型延迟。报告截至采集时刻，之后的调用及最终答复不包含在快照内。

## 可比较的实验

1. 固定源视频、模型、参考帧、坐标、机器人、物理步长、渲染分辨率、帧数和质量参数，并保存哈希。
2. 分开记录云端重建复用、资产重新生成、实际仿真、实际渲染、候选观测与最终验收。手工确认过的输入必须披露。
3. 原脚本和模块化运行各做新执行，禁用缓存。细粒度内部计时另做一轮，避免将插桩版本直接作为严格的速度对照。
4. 比较物理事件、完整轨迹与图像；质量门槛未通过时不能因更快而晋升。
5. 记录其他负载、失败、重试和等待。单次前后比较只报告观测差异，不声称 p50/p95、普遍加速或商业交付已经通过。

内部 host timer 包含嵌套、同步和等待，不能相加后当作独立 GPU kernel 时间。降低画质、减少物理步数或跳过完整覆盖的运行必须单列，不能作为等价加速。
