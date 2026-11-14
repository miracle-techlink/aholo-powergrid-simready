# 规范 pipeline CPU 示例

`python examples/pipeline/create_fixture.py --out /tmp/pipeline-inputs` 生成测试视频、两个合成高斯和显式动态 box。按[规范入口说明](../../docs/pipeline.md)执行 `--dry-run`、`--until reconstruction`、`--resume` 和 `--upgrade`。

例子仅验证真实 CPU 文件转换、USD 参数写入及恢复。没有进行视觉模型推断、几何重建质量或机器人任务测试。
