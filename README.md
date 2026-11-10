# PowerGrid Sim-Ready — 真实视频 → 自修复仿真世界 → 带臂四足分合闸

> 群核科技 Aholo 全球 3D AI 黑客松参赛作品源码 | [Aholo Lux3D](https://lux3d.aholo3d.cn) 驱动

一段 **91 秒真实配电房一镜到底视频** → **Sim-Ready 3D 世界** → **Lite3 四足 + Z1 机械臂巡检开关柜排并执行分合闸**。

## 核心论点

**Aholo alone = "好看的 3D";Aholo + RSI 层 = "能跑任务的 3D"。**

重建好不好,不由 PSNR 说了算,由下游任务说了算(呼应 Video2World, arXiv 2610.04432 "look better could work worse"):
Lite3+Z1 在重建场景里完成分合闸 **10 跑 ≥9 成功** 才算修好。

## 管线

```
mv_panel_room.mp4 (91s 配电房)
        ┌──────────┼───────────────┐
   Aholo Lux3D     本地管线        Isaac 金管线
   (云端重建主力)  (VGGT+gsplat)  (SimReady 仓库真值考场)
        └────┬─────┘               │
       RSI sim-ready 层 ←──────────┤ 评测: PSNR/深度L1/地形/语义
   (修缺陷+碰撞+可行走+语义+USD)
              ↓
        Sim-Ready 交付包 (USD)
              ↓
   Isaac Sim 6.1: Lite3+Z1 巡检 → 分合闸
```

## Sim-Ready Score (SRS)

一段普通视频 → ≤6h → 四项门槛全过计 1 分:
1. held-out 渲染 PSNR ≥ 22
2. 几何深度 L1 中位 ≤ 5cm
3. 可行走地形误差 ≤ 3cm
4. 核心交互物语义就位(按钮/柜体可寻址)

## 仓库结构

| 目录 | 内容 |
|---|---|
| `s0_ingest` … `s5_eval` | 视频→Sim-Ready 五阶段管线(抽帧/Aholo/VGGT/训练/评测) |
| `warehouse_3dgs/` | Isaac 渲→点云融合→3DGS→USD 全链脚本 |
| `rsi_3dgs/` | 3DGS 缺陷注入/检测/修复/验收闭环 |
| `scene_bench/` | 场景评测协议(rsi_loop / cost_guard / skill_store) |
| `submission_template/` | 可复用的黑客松提交自动化管线(playwright) |

## 复现

```bash
# Aholo Lux3D 云端重建(需 API key: ~/.config/aholo/key)
python s1_aholo/submit_video.py --video data/mv_panel_room.mp4

# 本地评测
python s5_eval/run_srs.py --scene runs/aholo_panel
```

## 致谢

- [Aholo Lux3D](https://lux3d.aholo3d.cn)(群核科技)— 云端 3D 重建
- [Isaac Sim 6.1](https://developer.nvidia.com/isaac/sim) + PhysicalAI SimReady 资产
- [gsplat](https://github.com/nerfstudio-project/gsplat)、[VGGT](https://github.com/facebookresearch/vggt)
- 云深处 Lite3 / 宇树 Z1 官方模型资产

## License

MIT(代码);第三方模型资产版权归各自官方所有。
