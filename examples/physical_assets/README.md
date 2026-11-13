# 多类物性与合成动力学复现

这个公开夹具含柜体、桌、椅、地面和按钮五类对象。它验证软件配置和数值响应，没有真实场景、实测材料或云端重建依赖。

## 1. CPU：逐物体参数写入与独立 USD 重读

```bash
python -m pip install -e '.[physical-assets]'
python examples/physical_assets/create_fixture.py --output runs/physical-input
python -m simready.simulation.scene_preparation \
  --ply runs/physical-input/source.ply \
  --spec runs/physical-input/scene_spec.json \
  --output runs/physical-scene
simready evaluation physical-assets \
  --scene-directory runs/physical-scene \
  --scene-spec runs/physical-input/scene_spec.json \
  --out runs/physical-audit.json
```

输出 `PASS` 表示组合后的 USD 与输入声明一致。检查涵盖逐形状材质绑定、碰撞、刚体模式、质量、质心、惯量、按钮关节与来源元数据。缺少输入 spec 时不能用 catalog 自证通过。真实材料准确率、语义准确率和动态响应另列状态。

## 2. Isaac：摩擦与质量的合成响应

先按[部署指南](../../docs/deployment.md)准备 Isaac 镜像，再运行：

```bash
python examples/physical_assets/run_probe.py \
  --out runs/physical-response --accept-eula
```

本机 Isaac Python 可使用 `--backend local --python /path/to/isaac-sim/python.sh`。每次指定新目录。

固定协议使用 CPU PhysX/TGS、`dt=1/120 s`、240 步；比较两组摩擦滑块，以及无重力、相同 1 N 力下的 1 kg 和 4 kg 刚体。运行仍需启动 Isaac/Kit；协议不进行视频渲染。

`probe/trajectories.csv` 保存 964 条采样；`probe/probe.json` 保存协议、SDK 版本、源码哈希、9 项响应检查和分段时间；`launcher_timing.json` 记录完整进程时间。已验证一次完整运行约 25.13 秒，低／高摩擦位移约 3.015／0.251 米，质量对照位移比为 4.0。这是固定数值协议的结果，不能推广为真实材质或机器人任务已验证。

语义模型与相机投影的公开夹具见[语义资产示例](../semantic_assets/README.md)。
