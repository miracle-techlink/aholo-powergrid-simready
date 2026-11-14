# 执行、取消与 GPU 租约

核心 pipeline 只编排阶段，execution 统一管理所属进程、有限期限、设备、容器和缓存。它不判定场景质量，也不代替独立验收。

## 单机取消、设备与恢复合同

`run_process` 为每个子进程建立所属进程组，同时通过继承管道向嵌套 owner 传递取消。父进程关闭管道或死亡后，合作 worker 逐层退出并记录终态；收到合作确认后最多等待 20 秒，再回收所属进程组。Docker 清理预算最多 15 秒。刻意绕开协议的 detached 进程、不可中断内核 I/O 和远端 SSH 作业不保证被本地取消回收。

宿主按物理 GPU UUID 获取同目录 `.lease` 锁；已持有租约的嵌套本地 worker 通过密封 capability 和锁 FD 借用，不用环境变量布尔开关跳过检查。DAG 的 MIG inventory 必须声明 `physical_uuid`，同卡 MIG 归并到父设备锁。GPU 发现失败时拒绝执行；`resource_kind="cpu"` 不发现／租用 GPU，CPU Docker 不传 `--gpus`。

公开场景入口和语义 CUDA 推断持租约。容器由宿主 launcher 在整个生命周期持租约；`scene_execution`、`video_execution` 和仿真 worker 是内部执行器，不在容器内重复发现或获取宿主租约。直接调用内部 SDK 不属于调度保证。RTX 不能仅靠 CUDA_VISIBLE_DEVICES 隔离，本地多卡需已审查的 renderer 隔离 inventory。

容器清理未确认时在同一租约文件内隔离设备；后续任务拒绝占用，不默默释放为“可用”。恢复入口：

```bash
.venv/bin/python -m simready execution recover-device \
  --gpu-uuid GPU-实际物理UUID --lock-dir /tmp/simready-device-leases \
  --out runs/device_recovery_01 --timeout-s 15
```

恢复必须验证本次容器 ownership 后移除，或确认已知完整容器 ID 不存在；否则保留隔离并返回 `NEEDS_REVIEW`。输出目录不可复用。命令不删除其他任务的锁／容器。当前真实 CPU 生命周期和受控设备锁测试已通过，真实 Docker daemon 清理与 GPU 行为仍需目标环境回归。

## 任务环境与缓存

`SIMREADY_SEED` 和 `SIMREADY_OUT` 被传入 worker。CUDA worker 通过 `CUDA_VISIBLE_DEVICES=<UUID>` 看到所选设备（内部通常是逻辑卡 0）。RTX inventory 必须声明外部已完成隔离 `rtx_isolated=true`，例如专用单卡渲染容器；这不是自动 Vulkan 隔离或兼容性检测。

任务是可信代码，当前执行器不是安全沙箱。显式声明 `environment` 后，仅继承 `inherit` / `secrets` 所列变量，再应用非秘密 `env` 字面量和调度器设备 / 输出 / 种子绑定；不会自动继承 PATH、HOME 或 PYTHONPATH。`{"environment":{"inherit":[]}}` 适用于使用绝对解释器路径且无宿主环境依赖的 CPU 夹具。普通部署可按实际需要列出 PATH、LANG、动态库路径等，并固定这些路径指向的运行环境与输入闭包；环境字符串的摘要不能证明磁盘依赖内容未变。

未声明 `environment` 的旧 v1 计划保持完整宿主继承，在图启动时冻结快照，但不读取或发布缓存，回执给出 `legacy_environment_untracked`。显式 `secrets` 同样关闭缓存，原因是 `secret_environment`；既有 `cache=false` 仍有效。普通变量缺失与空字符串有不同身份；`env` 覆盖同名普通继承值，以最终有效值计算身份。秘密变量必须存在，且不能与 `inherit` / `env` 重复；所有合同都不能覆盖调度器保留键。

计划中的 `env` 仅允许非秘密设置。保存到运行目录的 `plan.json` 会脱敏这些字面量并标记 `environment_values_redacted`，标记后的回执不能作为可重放计划提交；应保留原始配置并重新提供凭据环境。秘密值不进入环境元数据或摘要；可信 worker 自己输出的日志不属于自动脱敏范围。密钥不要放在计划、`argv` 或仓库中。

每次执行使用新的 `--out`。重启时使用相同 `--cache` 复用已完成阶段；失败阶段重新执行。云端提交不自动重试，生产适配器还必须实现提供商任务 ID 的持久化及幂等去重。阶段成功表示命令与产物合同成立，**不表示场景验收通过**。


通用任务图与资源清单的可运行示例在 `examples/task_execution/`；模型和 Isaac worker 各自声明环境。CPU、CUDA 和 RTX 资源不能互换，CUDA_VISIBLE_DEVICES 不等于 Vulkan 隔离。多机调度和常驻模型池尚未实现；SSH 仅调用预先准备好的远端环境，见[部署](deployment.md)。

设备 UUID 可用但 `memory.total=[N/A]` 时，显存容量保持未知；自动清单用 0 作为保守容量，仅允许不声明显存额度的独占租约。正显存请求被拒绝，需另提供已核查容量，不能把宿主总内存当成 GPU 可用显存。
