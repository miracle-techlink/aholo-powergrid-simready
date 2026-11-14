"""Shared synthetic inputs; no test collection or real device initialization."""

import struct

from simready.core.artifacts import digest, write_json


def gaussian_ply(path):
    names = [
        "x",
        "y",
        "z",
        "rot_0",
        "rot_1",
        "rot_2",
        "rot_3",
        "scale_0",
        "scale_1",
        "scale_2",
        "opacity",
        "f_dc_0",
        "f_dc_1",
        "f_dc_2",
    ]
    header = "ply\nformat binary_little_endian 1.0\nelement vertex 2\n"
    header += "".join(f"property float {name}\n" for name in names) + "end_header\n"
    path.write_bytes(
        header.encode()
        + b"".join(
            struct.pack("<14f", *([xyz] * 3 + [1, 0, 0, 0, -3, -3, -3, 0, 0, 0, 0]))
            for xyz in [0, 1]
        )
    )
    return path


def worker_inputs(tmp_path, *, prepare_only=True):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    ply = gaussian_ply(inputs / "splats.ply")
    write_json(inputs / "scene-spec.json", {"schema_version": 1})
    write_json(
        inputs / "input.json",
        {
            "source_video_sha256": "a" * 64,
            "ply_sha256": digest(ply),
            "spec_sha256": digest(inputs / "scene-spec.json"),
            "prepare_only": prepare_only,
        },
    )
    return inputs
