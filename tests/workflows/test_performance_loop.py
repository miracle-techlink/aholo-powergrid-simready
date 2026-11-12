"""A bad runtime must roll back, enter memory, and never be retried unchanged."""

import shutil
import sys
from copy import deepcopy
from pathlib import Path

import pytest

from simready.core.artifacts import digest, read_json, write_json
from simready.core.evidence import reference
from simready.experience.performance import PerformanceMemory
from simready.workflows.performance_loop import run_performance_workflow
from tests.paths import REPOSITORY_ROOT


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg required")
def test_failure_then_improvement_and_scoped_memory_reuse(tmp_path):
    worker = tmp_path / "synthetic_video.py"
    worker.write_text("""import hashlib,json,subprocess,sys,time
from pathlib import Path
out=Path(sys.argv[1]);mode=sys.argv[2];out.mkdir()
time.sleep(0.6 if mode=='baseline' else 0.02)
video=out/'navigation_vision_z1_stop.mp4'
color='white' if mode=='bad' else '0x808080'
subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c={color}:s=64x48:r=25','-frames:v','4','-c:v','libx264','-threads','1','-pix_fmt','yuv420p',str(video)],check=True)
sha=lambda p:hashlib.sha256(p.read_bytes()).hexdigest()
(out/'render.json').write_text(json.dumps(dict(frames=4,fps=25,width=64,height=48,native_render=True,new_physics=False,usd_pose_checks_passed=True,video_sha256=sha(video),renderer_sha256=sha(Path(__file__)))))
""")

    def variant(name, priority):
        return {
            "id": name,
            "priority": priority,
            "description": name,
            "worker_sha256": digest(worker),
            "argv": [sys.executable, str(worker), "{out}", name],
        }

    names = [
        "mean_mae",
        "p95_frame_mae",
        "max_frame_mae",
        "p99_tile_mae",
        "max_tile_mae",
        "mean_temporal_error_mae",
    ]
    protocol = {
        "schema_version": 1,
        "scope": "render_replay",
        "cwd": str(tmp_path),
        "verifier_root": str(REPOSITORY_ROOT / "src"),
        "verifier_files": [
            reference(path)
            for path in sorted((REPOSITORY_ROOT / "src/simready").rglob("*.py"))
        ],
        "frozen_inputs": [reference(worker)],
        "workload": {
            "width": 64,
            "height": 48,
            "frames": 4,
            "fps": 25,
            "tile_grid": [2, 4],
            "ssim_frames": [0, 3],
        },
        "quality_policy": {
            "absolute_max": dict.fromkeys(names, 5.0),
            "noise_multiplier": 1.5,
            "noise_floor": dict.fromkeys(names, 0.1),
            "absolute_min_ssim": 0.97,
            "ssim_noise_floor": 0.002,
            "sharpness_ratio": [0.97, 1.03],
        },
        "speed_policy": {
            "repetitions": 2,
            "minimum_gain_fraction": 0.08,
            "max_baseline_ratio": 2.0,
            "max_candidate_tail_ratio": 1.1,
        },
        "baseline": variant("baseline", 0),
        "candidates": [variant("bad", 1), variant("fast", 2)],
        "provider": {"kind": "rules"},
        "max_rounds": 2,
        "trial_timeout_s": 30,
        "verification_timeout_s": 30,
        "environment": {},
        "required_render_metadata": {"native_render": True, "new_physics": False},
    }
    path = tmp_path / "protocol.json"
    write_json(path, protocol)
    memory_path = tmp_path / "memory.sqlite"
    result = run_performance_workflow(path, tmp_path / "first", memory_path)
    assert result["status"] == "COMPLETE", result
    assert [x["outcome"] for x in result["episodes"]] == ["REJECTED", "ACCEPTED"]
    assert result["selected_runtime"] == "fast"
    assert not (tmp_path / "first/round_1/trial_1").exists()
    records = PerformanceMemory(memory_path).retrieve(result["benchmark_key"])
    assert len(records) == 2 and all(not r["cross_scene_skill_active"] for r in records)
    altered = deepcopy(records[0])
    altered["recipe"]["argv"] = ["unregistered-command"]
    with pytest.raises(ValueError, match="recipe differs"):
        PerformanceMemory(memory_path).append(altered)
    duplicated = deepcopy(records[0])
    original_profile = Path(duplicated["baseline_profiles"][0])
    duplicated["baseline_profiles"][1] = (
        str(original_profile.parent) + "/./" + original_profile.name
    )
    with pytest.raises(ValueError, match="independent trial"):
        PerformanceMemory(memory_path).append(duplicated)
    second = run_performance_workflow(path, tmp_path / "second", memory_path)
    assert second["episodes"] == [] and second["memory_reused_records"] == 2
    assert second["selected_runtime"] == "fast"
    # Corrupting evidence invalidates only that experience, not the whole database.
    changed = Path(records[0]["quality_reports"][0])
    changed.write_text("{}")
    assert len(PerformanceMemory(memory_path).retrieve(result["benchmark_key"])) == 1
    assert read_json(tmp_path / "first/selected_runtime.json")["id"] == "fast"
