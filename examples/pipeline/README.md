# 规范 pipeline CPU 示例

先按根目录 README 安装依赖。生成器提供测试视频、两个合成高斯、显式动态 box 和完整配置：

```bash
python examples/pipeline/create_fixture.py --out /tmp/pipeline-inputs
simready run --pipeline /tmp/pipeline-inputs/pipeline.json --out /tmp/pipeline-run --dry-run
simready run --pipeline /tmp/pipeline-inputs/pipeline.json --out /tmp/pipeline-run --until reconstruction
simready run --pipeline /tmp/pipeline-inputs/pipeline.json --out /tmp/pipeline-run --resume
```

修改配置或 scene spec 后，用 `--upgrade` 替换 `--resume`，显式接受新计划并重新验证阶段复用。配置路径以配置文件目录为基准；操作语义见[工程指南](../../docs/README.md#规范流水线)。

例子仅验证真实 CPU 文件转换、USD 参数写入及恢复。没有进行视觉模型推断、几何重建质量或机器人任务测试。
