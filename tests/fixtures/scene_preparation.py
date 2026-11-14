"""Shared synthetic inputs; no test collection or real device initialization."""

import numpy as np


def write_ply(path, points, *, gaussian=True, degree=1):
    properties = {
        name: np.asarray(points, dtype=np.float32)[:, i] for i, name in enumerate("xyz")
    }
    count = len(points)
    if gaussian:
        properties.update(
            {
                f"rot_{i}": np.full(count, 2 if i == 0 else 0, dtype=np.float32)
                for i in range(4)
            }
        )
        properties.update(
            {
                f"scale_{i}": np.full(count, np.log(0.02), dtype=np.float32)
                for i in range(3)
            }
        )
        properties["opacity"] = np.zeros(count, dtype=np.float32)
        properties.update(
            {f"f_dc_{i}": np.full(count, i / 10, dtype=np.float32) for i in range(3)}
        )
        properties.update(
            {
                f"f_rest_{i}": np.full(count, i, dtype=np.float32)
                for i in range(3 * ((degree + 1) ** 2 - 1))
            }
        )
    else:
        properties.update(
            {
                name: np.full(count, color, dtype=np.uint8)
                for name, color in zip(
                    ["red", "green", "blue"], [255, 128, 0], strict=True
                )
            }
        )
    dtype = np.dtype([(name, value.dtype) for name, value in properties.items()])
    table = np.empty(count, dtype=dtype)
    for name, values in properties.items():
        table[name] = values
    header = "ply\nformat binary_little_endian 1.0\nelement vertex " + str(count) + "\n"
    header += (
        "".join(
            f"property {'uchar' if value.dtype == np.uint8 else 'float'} {name}\n"
            for name, value in properties.items()
        )
        + "end_header\n"
    )
    path.write_bytes(header.encode() + table.tobytes())
    return path


def cloud():
    x, y = np.meshgrid(np.linspace(-2, 2, 25), np.linspace(-2, 2, 25))
    floor = np.column_stack([x.ravel(), y.ravel(), np.zeros(x.size)])
    obstacle = np.random.default_rng(4).uniform([0.7, 0.7, 0.2], [1, 1, 1], (200, 3))
    return np.concatenate([floor, obstacle])
