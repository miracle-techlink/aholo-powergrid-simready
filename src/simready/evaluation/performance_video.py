"""Independent, aligned video comparison for runtime performance experiments."""

import argparse
import subprocess
import tempfile
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

from simready.core.artifacts import digest, loads_json, read_json, write_json


def video_info(path: Path) -> dict:
    """Read stream identity without trusting renderer-supplied dimensions."""
    result = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=width,height,r_frame_rate",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
    )
    stream = loads_json(result.stdout)["streams"][0]
    return {
        "width": stream["width"],
        "height": stream["height"],
        "fps": float(Fraction(stream["r_frame_rate"])),
    }


@contextmanager
def _decoder(path: Path):
    with tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(
            [
                "ffmpeg",
                "-v",
                "error",
                "-threads",
                "1",
                "-i",
                str(path),
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-threads",
                "1",
                "pipe:1",
            ],
            stdout=subprocess.PIPE,
            stderr=errors,
        )
        try:
            yield process.stdout
            if process.wait(timeout=30):
                errors.seek(0)
                raise ValueError(
                    "Video decoder failed: "
                    + errors.read(2000).decode(errors="replace")
                )
        finally:
            process.stdout.close()
            if process.poll() is None:
                process.kill()
                process.wait()


def _read_frame(stream, size):
    chunks = bytearray()
    while len(chunks) < size:
        part = stream.read(size - len(chunks))
        if not part:
            break
        chunks.extend(part)
    if chunks and len(chunks) != size:
        raise ValueError("Truncated decoded frame")
    return chunks


def compare_videos(before: Path, after: Path, workload: dict) -> dict:
    """Measure every aligned frame; no resizing, alignment fitting or error masks."""
    import numpy as np

    from simready.evaluation.observations import image_metrics

    expected = {key: workload[key] for key in ("width", "height", "fps")}
    if video_info(before) != expected or video_info(after) != expected:
        raise ValueError("Video resolution or frame rate changed")
    height, width = expected["height"], expected["width"]
    gy, gx = workload["tile_grid"]
    if height % gy or width % gx:
        raise ValueError("Tile grid must divide the native image")
    byte_count = height * width * 3
    rows, tile_values, previous_error = [], [], None
    sharp_before = sharp_after = 0.0
    with _decoder(before) as left, _decoder(after) as right:
        while True:
            a, b = _read_frame(left, byte_count), _read_frame(right, byte_count)
            if bool(a) != bool(b):
                raise ValueError("Video frame count mismatch")
            if not a:
                break
            index = len(rows)
            if index >= workload["frames"]:
                raise ValueError("Video has extra frames")
            a = np.frombuffer(a, np.uint8).reshape(height, width, 3).astype(np.float32)
            b = np.frombuffer(b, np.uint8).reshape(height, width, 3).astype(np.float32)
            error = b - a
            absolute = np.abs(error)
            tiles = absolute.reshape(gy, height // gy, gx, width // gx, 3).mean(
                axis=(1, 3, 4)
            )
            tile_values.extend(tiles.ravel().tolist())
            row = {
                "frame": index,
                "mae": float(absolute.mean()),
                "max_tile_mae": float(tiles.max()),
                "temporal_error_mae": 0.0
                if previous_error is None
                else float(np.abs(error - previous_error).mean()),
            }
            if index in workload["ssim_frames"]:
                row["ssim"] = image_metrics(
                    a.astype(np.float64) / 255, b.astype(np.float64) / 255
                )["ssim"]
            sharp_before += float(
                np.abs(np.diff(a, axis=0)).mean() + np.abs(np.diff(a, axis=1)).mean()
            )
            sharp_after += float(
                np.abs(np.diff(b, axis=0)).mean() + np.abs(np.diff(b, axis=1)).mean()
            )
            previous_error = error
            rows.append(row)
    if len(rows) != workload["frames"]:
        raise ValueError("Video has missing frames")
    return {
        "frames": len(rows),
        "mean_mae": float(np.mean([r["mae"] for r in rows])),
        "p95_frame_mae": float(np.quantile([r["mae"] for r in rows], 0.95)),
        "max_frame_mae": max(r["mae"] for r in rows),
        "p99_tile_mae": float(np.quantile(tile_values, 0.99)),
        "max_tile_mae": max(tile_values),
        "mean_temporal_error_mae": float(
            np.mean([r["temporal_error_mae"] for r in rows[1:]])
        )
        if len(rows) > 1
        else 0.0,
        "min_ssim": min(r["ssim"] for r in rows if "ssim" in r),
        "sharpness_ratio": sharp_after / sharp_before
        if sharp_before
        else 1.0 + sharp_after,
        "per_frame": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--expected-protocol", required=True)
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if digest(args.protocol) != args.expected_protocol:
        raise ValueError("Frozen performance protocol changed")
    before_hash, after_hash = digest(args.before), digest(args.after)
    metrics = compare_videos(
        args.before, args.after, read_json(args.protocol)["workload"]
    )
    if before_hash != digest(args.before) or after_hash != digest(args.after):
        raise ValueError("Video changed during verification")
    write_json(
        args.out,
        {
            "protocol_sha256": args.expected_protocol,
            "before_sha256": before_hash,
            "after_sha256": after_hash,
            "metrics": metrics,
            "release_authorized": False,
        },
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
