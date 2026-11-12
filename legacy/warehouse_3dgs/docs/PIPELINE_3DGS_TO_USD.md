# 3DGS → USD → Isaac Sim 标准管线

> 版本: 2026-11-10 | 基于 NVIDIA `usd-convert-gsplat` v0.1.16（随 Isaac 6.1 自带）
> 目标：把 3D 重建资产（3DGS）标准化为 USD，进入 Isaac Sim 仿真场景

---

## 管线总览

```
┌─────────────┐     ┌─────────────┐     ┌─────────────────┐     ┌─────────────┐
│  3D 重建    │────→│  标准 PLY   │────→│  USD (Particle  │────→│  Isaac Sim  │
│  (model.npz)│     │  (3DGS格式) │     │  Field3DGSplat) │     │  场景组合   │
└─────────────┘     └─────────────┘     └─────────────────┘     └─────────────┘
     Stage 1             Stage 2              Stage 3               Stage 4
   npz_to_ply.py    usd-convert-gsplat    compose_isaac_scene.py   渲染/仿真
```

---

## Stage 1: npz → PLY

**输入**: `model.npz` — 我们的 3DGS 格式（means/scales/quats/rgb/opacity/K/w2cs/H/W）

**输出**: 标准 3DGS PLY（binary little-endian），字段：

| 字段 | 说明 | 转换 |
|---|---|---|
| x y z | 高斯中心位置 | 直接拷贝 |
| nx ny nz | 法线（占位） | 全 0 |
| f_dc_0/1/2 | SH DC 系数 | `(rgb-0.5)/0.28209`，clamp 到 [-1.7, 1.7] |
| opacity | 不透明度 | logit 变换：`log(p/(1-p))` |
| scale_0/1/2 | 缩放 | log 变换：`log(s)` |
| rot_0/1/2/3 | 旋转四元数 | wxyz 顺序（与我们的格式一致） |

**用法**:
```bash
python npz_to_ply.py model.npz output.ply
```

---

## Stage 2: PLY → USD

**工具**: NVIDIA `usd-convert-gsplat`（已随 Isaac 6.1 自带，无需安装）

**位置**:
```
isaaclab/.venv/lib/python3.12/site-packages/isaacsim/extscache/
  omni.kit.converter.gsplat-0.1.16+110.1.0.la64.r.cp312.u7f4/
  pip_prebundle/bin/usd-convert-gsplat
```

**输出**: `ParticleField3DGaussianSplat` prim（UsdVol schema，OpenUSD 26.03+）

**用法**:
```bash
PYTHONPATH=<gsplat_pkg>:$PYTHONPATH \
python -m usd_convert_gsplat.cli \
  -i input.ply -o output.usda --up-axis Z
```

**参数**:
| 参数 | 说明 |
|---|---|
| `--up-axis Z` | 目标 up-axis（Isaac Sim 用 Z） |
| `--rotate-x/y/z` | 欧拉角修正（度） |
| `--generateSh` | 从 RGB 生成 DC SH（如果 PLY 没有 f_dc） |
| `--generateScales` | 从邻域间距生成 scales |

---

## Stage 3: USD → Isaac Sim 场景

**脚本**: `compose_isaac_scene.py`

**功能**: 把 3DGS USD + SimReady 资产 + 机器人组合成最终场景

**层结构**:
```
/World
├── GaussianSplat/     ← 3DGS 视觉层（无碰撞，只渲染）
├── Assets/            ← SimReady 碰撞资产（柜子/按钮等）
├── Robot/             ← Lite3+Z1（或 Go2）
├── PhysicsScene       ← PhysX 场景
├── Ground             ← 地面
└── DomeLight          ← 环境光
```

**用法**:
```bash
python compose_isaac_scene.py gsplat.usda scene.usda --simready /path/to/simready
```

---

## 一键管线

```bash
python pipeline_3dgs_to_isaac.py \
  --input 3dgs_v1/model.npz \
  --output scene.usda \
  --simready /path/to/simready/assets
```

**选项**:
| 选项 | 说明 |
|---|---|
| `--ply-only` | 只到 Stage 1（生成 PLY） |
| `--usd-only` | 只到 Stage 2（生成 USD） |
| `--ply input.ply` | 跳过 Stage 1，直接用 PLY |
| `--rotate-x 180` | 修正 COLMAP Y-down 坐标系 |

---

## 关键坑

1. **SH DC 系数溢出**：RGB=0 或 1 时 f_dc=±1.77，导致渲染全白/全黑。必须 clamp 到 [-1.7, 1.7]。
2. **scales 是 log 空间**：`usd-convert-gsplat` 内部做 `exp(scale)`，所以 PLY 里存的是 `log(实际scale)`。
3. **opacity 是 logit 空间**：同上，PLY 里存 `log(p/(1-p))`。
4. **quat 顺序**：PLY 标准 = wxyz，与我们的 npz 格式一致（不需要转换）。
5. **up-axis**：Isaac Sim 用 Z-up，COLMAP 是 Y-down，必要时加 `--rotate-x 180`。
6. **文件大小**：120K splats → PLY 8MB → USDA 22MB。100 万 splats → PLY 70MB → USDA 180MB。

---

## 与 SimReady 资产对接

NVIDIA SimReady 资产（HF `nvidia/simready-assets`，CC-BY-4.0）提供：
- 带碰撞体的 USD 资产
- 语义标签 + 质量标注
- 配电房可用类别：`cabinet_file_*` / `equipment_rolling_tool_cabinet_*` / `electrical_power_shutoff_a01`

**组合原则**：3DGS 做视觉背景（无碰撞），SimReady 做碰撞层（有物理）。

---

## 验证

```python
from pxr import Usd

stage = Usd.Stage.Open("scene.usda")
prim = stage.GetPrimAtPath("/World/GaussianSplat")
print(prim.GetTypeName())  # ParticleField3DGaussianSplat
```
