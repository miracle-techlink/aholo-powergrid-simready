# 资产审查与渲染加速 Infrastructure 优化

> 2026-11-10 | 基于 Isaac 6.1 自带工具链

---

## 1. 资产审查（Validation）

### 1.1 NVIDIA 自带验证器（已内置）

Isaac 6.1 自带 `usd_validation_nvidia`，覆盖 13 类规则：

| 规则文件 | 检查内容 |
|---|---|
| `_basic.py` | 基本元数据（upAxis/metersPerUnit/defaultPrim） |
| `_units.py` | 单位一致性 |
| `_layers.py` | 引用/子层完整性 |
| `_geometry.py` | 几何数据（法线/UV/拓扑） |
| `_materials.py` | 材质/纹理路径 |
| `_physics.py` | 碰撞体/质量/关节/刚体 |
| `_hierarchy.py` | 层级结构 |
| `_layout.py` | 布局约束 |
| `_performance.py` | 性能边界（extent/precision） |
| `_gaussian_splat.py` | 3DGS 特定验证 |
| `_unicode.py` | 非 ASCII 字符 |
| `_atomic_asset.py` | 原子资产完整性 |
| `_misc.py` | 其他杂项 |

### 1.2 快速审查脚本

```python
# validate_asset.py
from pxr import Usd
import sys

def quick_check(path):
    """快速 USD 资产健康检查"""
    stage = Usd.Stage.Open(path)
    issues = []

    # 基本元数据
    up = UsdGeom.GetStageUpAxis(stage)
    mpu = UsdGeom.GetStageMetersPerUnit(stage)
    if mpu != 1.0:
        issues.append(f"units: metersPerUnit={mpu} (expect 1.0)")

    # 遍历检查
    for prim in stage.Traverse():
        # 检查空 prim
        if not prim.HasAuthoredProperties() and not prim.GetChildren():
            issues.append(f"empty prim: {prim.GetPath()}")
        # 检查缺失的引用
        for ref in prim.GetMetadata('references').value.prependedItems if prim.GetMetadata('references') else []:
            if not os.path.exists(ref.assetPath):
                issues.append(f"broken ref: {ref.assetPath}")

    return issues

# 用法
issues = quick_check("asset.usda")
for i in issues:
    print(f"  [ISSUE] {i}")
```

### 1.3 SimReady 合规检查清单

| 检查项 | 工具/方法 |
|---|---|
| 碰撞体存在 | `prim.HasAPI(UsdPhysics.CollisionAPI)` |
| 质量标注 | `UsdPhysics.MassAPI.GetMassAttr()` |
| 语义标签 | `prim.GetAttribute('semantic:label')` |
| 材质完整 | 遍历 `Looks/` 子树 |
| 纹理路径 | 检查 `file` 属性是否可解析 |
| 单位正确 | `metersPerUnit == 1.0` |
| 轴向正确 | `upAxis` 与目标一致 |

---

## 2. 渲染加速

### 2.1 核心原则：解耦物理步进和渲染

```python
# 慢：每步都渲染（实时速度）
for _ in range(1000):
    world.step(render=True)  # 受显示器刷新率限制

# 快：只渲染需要的帧
for i in range(1000):
    world.step(render=False)  # 物理全速
    if i % 10 == 0:  # 每10步渲一帧
        frame = capture_frame()
```

### 2.2 渲染模式选择

| 模式 | 速度 | 质量 | 适用 |
|---|---|---|---|
| `RTX Real-Time` | 最快 | 低 | 快速迭代/数据生成 |
| `RTX Interactive` | 中 | 中 | 调试/预览 |
| `RTX Path-Traced` | 最慢 | 高 | 最终出片 |

```python
# 切换渲染模式
omni.kit.commands.execute('ChangeSetting',
    path='rtx/renderMode', value='RealTime')  # 或 'PathTracing'
```

### 2.3 分辨率优化

```python
# 低分辨率快速预览
rp = rep.create.render_product('/World/Camera', resolution=(640, 360))

# 高分辨率最终出片
rp = rep.create.render_product('/World/Camera', resolution=(1920, 1080))
```

### 2.4 批量帧捕获（Replicator）

```python
import omni.replicator.core as rep

# 创建渲染产品
rp = rep.create.render_product('/World/Camera', resolution=(960, 540))

# 使用 BasicWriter 直接写盘（最快）
writer = rep.WriterRegistry.get("BasicWriter")
writer.initialize(
    output_dir="./frames",
    rgb=True,
    depth=True,
    semantic_segmentation=True
)
writer.attach([rp])

# 批量捕获（不受实时限制）
for i in range(100):
    rep.orchestrator.step(rt_subframes=1)  # 物理步进
    # 帧自动写入 ./frames/
```

### 2.5 后期视频编码

```python
import cv2
import os

def frames_to_video(frame_dir, output, fps=30):
    """把捕获的帧序列编码为视频"""
    frames = sorted([f for f in os.listdir(frame_dir) if f.endswith('.png')])
    h, w = cv2.imread(os.path.join(frame_dir, frames[0])).shape[:2]

    writer = cv2.VideoWriter(output, cv2.VideoWriter_fourcc(*'mp4v'), fps, (w, h))
    for f in frames:
        img = cv2.imread(os.path.join(frame_dir, f))
        writer.write(img)
    writer.release()
    print(f"saved {len(frames)} frames -> {output}")
```

---

## 3. 完整优化工作流

```
┌─────────────┐     ┌─────────────┐     ┌─────────────┐     ┌─────────────┐
│  资产审查   │────→│  场景搭建   │────→│  批量渲染   │────→│  后期编码   │
│ validate_   │     │ (低分辨率)  │     │ (无实时限制)│     │ frames_to_  │
│ asset.py    │     │ 快速迭代    │     │ 高分辨率出片│     │ video.py    │
└─────────────┘     └─────────────┘     └─────────────┘     └─────────────┘
```

### 3.1 快速迭代配置（开发用）

```python
# dev_config.py
RESOLUTION = (640, 360)
RENDER_MODE = 'RealTime'
SAMPLES_PER_PIXEL = 1
PHYSICS_DT = 1/60
RENDER_EVERY_N = 10  # 每10步渲一帧
```

### 3.2 最终出片配置

```python
# prod_config.py
RESOLUTION = (1920, 1080)
RENDER_MODE = 'PathTracing'
SAMPLES_PER_PIXEL = 64
PHYSICS_DT = 1/120
RENDER_EVERY_N = 1  # 每步都渲
```

---

## 4. 性能基准（GB10 实测）

实测场景：组合场景（3422 prims / 781 meshes / 780 materials，`fast_render.py`，2026-11-10）

| 配置 | 帧率（实测） | 适用场景 |
|---|---|---|
| 640×360 + RealTime + subframes=1 | **6.5 FPS**（修复前 1.9） | 快速迭代 |
| 960×540 + RealTime + subframes=4 | ~2 FPS（render_pc.py 口径） | 点云采集 |
| 1920×1080 + PathTracing + spp=64 | ~0.3-0.5 FPS（估） | 最终出片 |

实测踩坑（已修，在 `fast_render.py`）：
1. **每帧两次 `kit.update()` 浪费 3 倍时间** → orchestrator.step 后一次 update 即可（1.9→6.5 FPS）
2. **`simulation_app.close()` 直接终止进程** → ffmpeg 编码必须放在 close() **之前**（放后面永远不执行）
3. **dev 静态场景 subframes=1 够用**（4 没必要）

**优化建议**：
- 开发阶段用 640×360 + RealTime，快速验证逻辑
- 出片阶段用 1920×1080 + PathTracing，批量渲染 + 后期编码
- 物理步进和渲染解耦，避免实时限制

---

## 5. 工具链汇总

| 工具 | 用途 | 位置 |
|---|---|---|
| `usd_validation_nvidia` | 资产审查 | Isaac 6.1 自带 |
| `omni.replicator` | 批量渲染 | Isaac 6.1 自带 |
| `omni.kit.window.movie_capture` | 视频录制 | Isaac 6.1 自带 |
| `usd-convert-gsplat` | 3DGS→USD | Isaac 6.1 自带 |
| `usd-optimize` | USD 优化 | GitHub (isaac-sim) |
| `usd-convert-asset` | 通用格式→USD | GitHub (isaac-sim) |
