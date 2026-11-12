import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image

from simready.ingestion.video_frames import extract_frames, hamming_distance


def test_hash_distance_counts_bits():
    a = np.zeros(256, bool)
    b = a.copy()
    b[:7] = True
    assert hamming_distance(a, b) == 7


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_real_ffmpeg_extraction_and_manifest(tmp_path):
    frames = tmp_path / "source"
    frames.mkdir()
    rng = np.random.default_rng(3)
    for index in range(6):
        Image.fromarray(rng.integers(0, 256, (48, 64, 3), dtype=np.uint8)).save(
            frames / f"{index:02d}.png"
        )
    video = tmp_path / "source.mkv"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-threads",
            "1",
            "-filter_threads",
            "1",
            "-framerate",
            "2",
            "-i",
            str(frames / "%02d.png"),
            "-c:v",
            "ffv1",
            str(video),
        ],
        check=True,
    )
    result = extract_frames(
        video, tmp_path / "out", fps=2, min_laplacian=0, dedup_bits=0, heldout_every=2
    )
    assert len(result["frames"]) == 6 and {r["split"] for r in result["frames"]} == {
        "train",
        "heldout",
    }
    assert all(len(r["sha256"]) == 64 for r in result["frames"])
