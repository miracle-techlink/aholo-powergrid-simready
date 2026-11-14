"""Import-safe CPU extraction with explicit inputs and full provenance hashes."""

import shutil
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from simready.core.artifacts import digest, write_json
from simready.core.types import JsonObject, PathLike
from simready.core.validation import number
from simready.execution.processes.local import run_process


def gradient_hash(image):
    gray = np.asarray(
        image.convert("L").resize((32, 32), Image.Resampling.LANCZOS), dtype=np.float32
    )
    gradient = gray[1::2, 1::2] - gray[::2, ::2]
    return (gradient > gradient.mean()).flatten()


def hamming_distance(left, right):
    left, right = np.asarray(left, dtype=bool), np.asarray(right, dtype=bool)
    if left.shape != right.shape:
        raise ValueError("Hash shapes differ")
    return int(np.count_nonzero(left != right))


def extract_frames(
    video: PathLike,
    out: PathLike,
    *,
    fps: float = 2.0,
    min_laplacian: float = 60.0,
    dedup_bits: int = 6,
    heldout_every: int = 10,
    timeout: float = 3600,
) -> JsonObject:
    """Extract filtered video frames into a fresh directory and return provenance."""
    video, out = Path(video).resolve(), Path(out).resolve()
    number(fps, "fps", 0.001)
    number(min_laplacian, "min_laplacian")
    number(timeout, "timeout", 0.001)
    if type(dedup_bits) is not int or not 0 <= dedup_bits <= 256:
        raise ValueError("dedup_bits must be between 0 and 256")
    if type(heldout_every) is not int or heldout_every < 2:
        raise ValueError("heldout_every must be at least 2")
    source_hash = digest(video)
    out.mkdir(parents=True, exist_ok=False)
    rows, previous = [], None
    with tempfile.TemporaryDirectory(prefix="simready-frames-") as directory:
        raw = Path(directory)
        process = run_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-i",
                str(video),
                "-vf",
                f"fps={fps}",
                "-q:v",
                "2",
                str(raw / "f%08d.jpg"),
            ],
            cwd=out,
            timeout=timeout,
            stdout=out / "ffmpeg.stdout.log",
            stderr=out / "ffmpeg.stderr.log",
        )
        if process.returncode:
            raise ValueError("ffmpeg extraction failed; inspect stderr")
        for sample_index, frame in enumerate(sorted(raw.glob("f*.jpg"))):
            with Image.open(frame) as image:
                gray = np.asarray(image.convert("L"), dtype=np.float32)
                if min(gray.shape) < 5:
                    raise ValueError("Frame too small for sharpness measurement")
                center = gray[1:-1, 1:-1]
                lap = (
                    gray[:-2, 1:-1]
                    + gray[2:, 1:-1]
                    + gray[1:-1, :-2]
                    + gray[1:-1, 2:]
                    - 4 * center
                )
                sharpness = float(lap.var())
                if sharpness < min_laplacian:
                    continue
                signature = gradient_hash(image)
                if (
                    previous is not None
                    and hamming_distance(previous, signature) <= dedup_bits
                ):
                    continue
                previous = signature
                index = len(rows)
                split = (
                    "heldout"
                    if index % heldout_every == heldout_every // 2
                    else "train"
                )
                target = out / split / f"{index:06d}.jpg"
                target.parent.mkdir(exist_ok=True)
                shutil.copyfile(frame, target)
                rows.append(
                    {
                        "index": index,
                        "sample_index": sample_index,
                        "nominal_time_s": sample_index / fps,
                        "split": split,
                        "path": str(target.relative_to(out)),
                        "sha256": digest(target),
                        "laplacian_variance": sharpness,
                        "mean_brightness": float(gray.mean()),
                    }
                )
    if digest(video) != source_hash:
        raise ValueError("Input video changed during extraction")
    if not rows:
        raise ValueError(
            "No frames survived extraction; no successful manifest published"
        )
    manifest = {
        "schema_version": 1,
        "video_sha256": source_hash,
        "fps": fps,
        "frames": rows,
        "dedup_metric": "bit Hamming distance on 256-bit gradient hash",
        "dedup_bits": dedup_bits,
        "min_laplacian": min_laplacian,
        "heldout_every": heldout_every,
        "time_basis": "Nominal fps-filter sample grid; not measured source-frame PTS",
        "split_scope": "Same-video split; not an independent capture or cross-scene benchmark",
    }
    write_json(out / "manifest.json", manifest)
    return manifest
