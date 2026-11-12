"""GaussianModel 数据模型与 npz 读写.

约定(与 fuse_pc.py 产出对齐):
- npz 存储: quats 为 xyzw 顺序; scales 为线性值(非 log)
- 内存(GaussianModel): 同样 xyzw + 线性 scales —— 与 npz 一致, 避免双向转换错误
- 仅 rasterization 入口转换为 wxyz(见 render.py)
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

MODEL_KEYS = ("means", "scales", "quats", "rgb", "opacity")


@dataclass
class GaussianModel:
    """N 个高斯的场景模型. 所有数组 float32, quats 为 xyzw."""

    means: np.ndarray  # (N,3)
    scales: np.ndarray  # (N,3) 线性
    quats: np.ndarray  # (N,4) xyzw
    rgb: np.ndarray  # (N,3) in [0,1]
    opacity: np.ndarray  # (N,)   in [0,1]
    K: Optional[np.ndarray] = None  # (3,3) 相机内参
    w2cs: Optional[np.ndarray] = None  # (M,4,4) 训练/冻结机位
    H: int = 540
    W: int = 960
    meta: dict = field(default_factory=dict)

    def __post_init__(self):
        n = len(self.means)
        for name, width in (
            ("means", 3),
            ("scales", 3),
            ("quats", 4),
            ("rgb", 3),
            ("opacity", None),
        ):
            arr = getattr(self, name)
            if (
                arr.shape != ((n, width) if width else (n,))
                or not np.isfinite(arr).all()
            ):
                raise ValueError(f"Invalid {name} shape or nonfinite data")
        if (self.scales <= 0).any() or (np.linalg.norm(self.quats, axis=1) <= 0).any():
            raise ValueError("Positive scales and nonzero quaternions required")
        if ((self.rgb < 0) | (self.rgb > 1)).any() or (
            (self.opacity < 0) | (self.opacity > 1)
        ).any():
            raise ValueError("RGB and opacity must be in [0,1]")
        if self.H < 1 or self.W < 1:
            raise ValueError("Positive image dimensions required")

        if self.K is not None and (
            self.K.shape != (3, 3)
            or not np.isfinite(self.K).all()
            or self.K[0, 0] <= 0
            or self.K[1, 1] <= 0
        ):
            raise ValueError("Invalid camera intrinsics")
        if self.w2cs is not None:
            from simready.geometry.cameras import rigid_inverse

            if self.w2cs.ndim != 3:
                raise ValueError("Camera poses must be Mx4x4")
            rigid_inverse(self.w2cs)

    @property
    def n(self) -> int:
        return len(self.means)

    def subset(self, idx: np.ndarray) -> "GaussianModel":
        """按索引取子集(共享相机字段)."""
        idx = np.asarray(idx)
        return GaussianModel(
            means=self.means[idx],
            scales=self.scales[idx],
            quats=self.quats[idx],
            rgb=self.rgb[idx],
            opacity=self.opacity[idx],
            K=self.K,
            w2cs=self.w2cs,
            H=self.H,
            W=self.W,
            meta=dict(self.meta),
        )

    def with_arrays(self, **kw) -> "GaussianModel":
        """替换部分字段生成新模型(不可变式更新), 支持 K/w2cs/meta."""
        state = {k: getattr(self, k) for k in MODEL_KEYS}
        extra = {k: kw.pop(k) for k in ("K", "w2cs", "meta", "H", "W") if k in kw}
        state.update(kw)
        base = dict(K=self.K, w2cs=self.w2cs, H=self.H, W=self.W, meta=dict(self.meta))
        base.update(extra)
        return GaussianModel(**base, **state)

    def delete(self, idx: np.ndarray) -> "GaussianModel":
        keep = np.ones(self.n, dtype=bool)
        keep[np.asarray(idx)] = False
        return self.subset(np.nonzero(keep)[0])

    def append(self, other: "GaussianModel") -> "GaussianModel":
        kw = {
            k: np.concatenate([getattr(self, k), getattr(other, k)], 0)
            for k in MODEL_KEYS
        }
        return self.with_arrays(**kw)


def load_model(path) -> GaussianModel:
    with np.load(Path(path), allow_pickle=False) as d:
        missing = [k for k in MODEL_KEYS if k not in d]
        if missing:
            raise ValueError(f"{path} 缺字段: {missing}")
        return GaussianModel(
            means=d["means"].astype(np.float32),
            scales=d["scales"].astype(np.float32),
            quats=d["quats"].astype(np.float32),
            rgb=d["rgb"].astype(np.float32),
            opacity=d["opacity"].astype(np.float32),
            K=d["K"].astype(np.float32) if "K" in d else None,
            w2cs=d["w2cs"].astype(np.float32) if "w2cs" in d else None,
            H=int(d["H"]) if "H" in d else 540,
            W=int(d["W"]) if "W" in d else 960,
            meta=json.loads(str(d["meta"])) if "meta" in d else {},
        )


def save_model(model: GaussianModel, path) -> str:
    """落盘并返回文件 sha256."""
    path = Path(path)
    if path.suffix != ".npz":
        raise ValueError("Gaussian model output must use .npz")
    path.parent.mkdir(parents=True, exist_ok=True)
    optional = {
        k: getattr(model, k) for k in ("K", "w2cs") if getattr(model, k) is not None
    }
    np.savez(
        path,
        **{k: getattr(model, k) for k in MODEL_KEYS},
        **optional,
        H=model.H,
        W=model.W,
        meta=json.dumps(model.meta, ensure_ascii=False),
    )
    return sha256(path)


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def model_digest(model: GaussianModel) -> str:
    """内容级 digest(与落盘字节序无关), 用于缺陷/修复记录锚定."""
    h = hashlib.sha256()
    for k in (*MODEL_KEYS, "K", "w2cs"):
        if getattr(model, k) is None:
            h.update((k + ":none").encode())
            continue
        arr = np.ascontiguousarray(getattr(model, k))
        h.update(k.encode())
        h.update(arr.shape.__repr__().encode())
        h.update(str(arr.dtype).encode())
        h.update(arr.tobytes())
    h.update(
        json.dumps(
            {"H": model.H, "W": model.W, "meta": model.meta},
            sort_keys=True,
            allow_nan=False,
        ).encode()
    )
    return h.hexdigest()
