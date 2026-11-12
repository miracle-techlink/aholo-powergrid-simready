"""Bounded performance RSI: fixed workloads, independent gates, scoped memory."""

import os
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path

from simready.agents.provider_factory import create_decision_provider
from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.evidence import ensure_unchanged, reference
from simready.evaluation.performance_gate import calibrate_quality, check_quality
from simready.execution.profiling import profile_command, summarize_profiles
from simready.experience.performance import PerformanceMemory


def _validate(protocol):
    if protocol.get("schema_version") != 1 or protocol.get("scope") != "render_replay":
        raise ValueError("Unsupported performance benchmark protocol")
    workload = protocol["workload"]
    for name in ("width", "height", "frames", "fps"):
        if type(workload[name]) is not int or workload[name] <= 0:
            raise ValueError("Workload dimensions and timing must be positive integers")
    if not workload["ssim_frames"] or any(
        type(x) is not int or not 0 <= x < workload["frames"]
        for x in workload["ssim_frames"]
    ):
        raise ValueError("SSIM observations must be fixed and nonempty")
    if protocol["speed_policy"]["repetitions"] < 2 or protocol["max_rounds"] < 1:
        raise ValueError("Require repeated trials and a positive round budget")
    ids = [x["id"] for x in protocol["candidates"]]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate runtime candidate")
    ensure_unchanged(protocol["frozen_inputs"] + protocol["verifier_files"])
    package = Path(__file__).resolve().parents[1]
    frozen_package = Path(protocol["verifier_root"]) / "simready"
    required = [
        "evaluation/performance_video.py",
        "evaluation/performance_gate.py",
        "evaluation/observations.py",
        "core/artifacts.py",
        "core/evidence.py",
        "execution/profiling.py",
        "execution/processes.py",
        "experience/performance.py",
        "workflows/performance_loop.py",
    ]
    bound = {str(Path(ref["path"]).resolve()) for ref in protocol["verifier_files"]}
    for name in required:
        frozen = frozen_package / name
        if str(frozen.resolve()) not in bound or digest(frozen) != digest(
            package / name
        ):
            raise ValueError(
                "Runtime/checker code differs from the frozen benchmark: " + name
            )
    if protocol.get("hardware_identity"):
        hardware = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,driver_version", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        ).stdout.strip()
        if hardware != protocol["hardware_identity"]:
            raise ValueError("GPU or driver differs from the frozen benchmark")


def _trial(protocol, variant, directory, round_index):
    directory.mkdir(parents=True, exist_ok=False)
    output = directory / "output"
    argv = [arg.replace("{out}", str(output)) for arg in variant["argv"]]
    report = profile_command(
        argv,
        name="render_replay",
        directory=directory / "timing",
        cwd=protocol["cwd"],
        timeout_s=protocol["trial_timeout_s"],
        env={**os.environ, **protocol["environment"]},
        round_index=round_index,
        token_mode="no_llm",
        metadata={"benchmark_key": object_digest(protocol), "variant": variant["id"]},
    )
    ensure_unchanged(protocol["frozen_inputs"] + protocol["verifier_files"])
    if report["status"] != "SUCCESS":
        raise ValueError(
            "Runtime trial failed; timing and stderr retained at " + str(directory)
        )
    metadata = read_json(output / "render.json")
    if metadata["renderer_sha256"] != variant["worker_sha256"]:
        raise ValueError("Renderer implementation identity changed")
    for key in ("frames", "fps", "width", "height"):
        if metadata[key] != protocol["workload"][key]:
            raise ValueError("Renderer workload changed: " + key)
    for key, expected in protocol["required_render_metadata"].items():
        if metadata.get(key) != expected:
            raise ValueError("Renderer evidence changed: " + key)
    video = output / "navigation_vision_z1_stop.mp4"
    if digest(video) != metadata["video_sha256"]:
        raise ValueError("Renderer video hash mismatch")
    value = {
        "directory": str(directory),
        "video": reference(video),
        "metadata": reference(output / "render.json"),
        "profile": reference(directory / "timing/profile.json"),
        "seconds": report["wall_seconds"],
    }
    write_json(directory / "trial.json", value)
    return value


def _verify(protocol_path, protocol, before, after, directory):
    if read_json(protocol_path) != protocol:
        raise ValueError("Performance protocol changed during the experiment")
    argv = [
        sys.executable,
        "-m",
        "simready.evaluation.performance_video",
        "--protocol",
        str(protocol_path),
        "--expected-protocol",
        digest(protocol_path),
        "--before",
        before["video"]["path"],
        "--after",
        after["video"]["path"],
        "--out",
        str(directory / "quality.json"),
    ]
    env = {
        **os.environ,
        "PYTHONPATH": protocol["verifier_root"],
        "OPENBLAS_NUM_THREADS": "1",
        "OMP_NUM_THREADS": "1",
    }
    report = profile_command(
        argv,
        name="independent_video_verification",
        directory=directory / "timing",
        cwd=protocol["verifier_root"],
        timeout_s=protocol["verification_timeout_s"],
        env=env,
        token_mode="no_llm",
    )
    if report["status"] != "SUCCESS":
        raise ValueError("Independent video verification failed: " + str(directory))
    ensure_unchanged(protocol["verifier_files"])
    if read_json(protocol_path) != protocol:
        raise ValueError("Performance protocol changed during verification")
    quality = read_json(directory / "quality.json")
    if (
        quality["before_sha256"] != before["video"]["sha256"]
        or quality["after_sha256"] != after["video"]["sha256"]
    ):
        raise ValueError("Verifier video binding mismatch")
    return directory / "quality.json"


def run_performance_workflow(
    protocol_path: Path, output_dir: Path, memory_path: Path
) -> dict:
    """Run A/A calibration before candidates; retain all failures and rollback."""
    protocol_path = Path(protocol_path).resolve()
    protocol = read_json(protocol_path)
    _validate(protocol)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    snapshot = output / "protocol.json"
    write_json(snapshot, protocol)
    benchmark_key = object_digest(protocol)
    memory = PerformanceMemory(memory_path)
    history = memory.retrieve(benchmark_key)
    provider = create_decision_provider(protocol["provider"])
    selected = protocol["baseline"]
    started = time.time()
    status = {
        "status": "RUNNING",
        "benchmark_key": benchmark_key,
        "started_unix_s": started,
        "selected_runtime": selected["id"],
        "episodes": [],
        "release_authorized": False,
    }
    write_json(output / "selected_runtime.json", selected)
    write_json(output / "status.json", status)
    evidence = [snapshot]
    try:
        baselines = []
        for index in range(protocol["speed_policy"]["repetitions"]):
            trial = _trial(protocol, selected, output / f"baseline_{index}", 0)
            baselines.append(trial)
            print(f"BASELINE {index} complete: {trial['seconds']:.3f}s", flush=True)
        calibration_report = _verify(
            snapshot,
            protocol,
            baselines[0],
            baselines[1],
            output / "calibration_observation",
        )
        metrics = read_json(calibration_report)["metrics"]
        calibration = calibrate_quality(metrics, protocol["quality_policy"])
        calibration_path = output / "calibration.json"
        write_json(calibration_path, calibration)
        evidence += [calibration_report, calibration_path]
        for trial in baselines:
            evidence += [
                Path(trial[name]["path"]) for name in ("profile", "metadata", "video")
            ]
        if not calibration["stable"]:
            status.update(
                status="BASELINE_UNSTABLE",
                failed_checks=calibration["unstable_metrics"],
            )
            return status
        baseline_seconds = [x["seconds"] for x in baselines]
        if (
            max(baseline_seconds) / min(baseline_seconds)
            > protocol["speed_policy"]["max_baseline_ratio"]
        ):
            status.update(
                status="BASELINE_UNSTABLE", failed_checks=["runtime_variability"]
            )
            return status
        accepted_history = [row for row in history if row["outcome"] == "ACCEPTED"]
        if accepted_history:
            selected = min(
                accepted_history, key=lambda row: row["runtime"]["candidate_median_s"]
            )["recipe"]
            status["selected_runtime"] = selected["id"]
            write_json(output / "selected_runtime.json", selected)
        completed = {row["candidate_id"] for row in history}
        for round_index in range(1, protocol["max_rounds"] + 1):
            candidates = [x for x in protocol["candidates"] if x["id"] not in completed]
            if not candidates:
                break
            context = {
                "proposals": [
                    {
                        "proposal_id": x["id"],
                        "priority": x["priority"],
                        "description": x["description"],
                    }
                    for x in candidates
                ],
                "experiences": [
                    {"candidate_id": x["candidate_id"], "outcome": x["outcome"]}
                    for x in history
                ],
                "scope": "Frozen runtime-only benchmark; preserve quality and workload",
            }
            decision = provider.decide(
                context, output / f"decision_{round_index}", timeout=120
            )
            write_json(output / f"decision_{round_index}.json", asdict(decision))
            if decision.decision.action != "repair":
                break
            candidate = next(
                x for x in candidates if x["id"] == decision.decision.proposal_id
            )
            completed.add(candidate["id"])
            trials, quality_paths, errors = [], [], []
            round_dir = output / f"round_{round_index}"
            for index in range(protocol["speed_policy"]["repetitions"]):
                try:
                    trial = _trial(
                        protocol, candidate, round_dir / f"trial_{index}", round_index
                    )
                    trials.append(trial)
                    quality = _verify(
                        snapshot,
                        protocol,
                        baselines[0],
                        trial,
                        round_dir / f"verification_{index}",
                    )
                    quality_paths.append(quality)
                    gate = check_quality(read_json(quality)["metrics"], calibration)
                    print(
                        f"CANDIDATE {candidate['id']} {index}: {trial['seconds']:.3f}s quality={gate['passed']}",
                        flush=True,
                    )
                    if not gate["passed"]:
                        break
                except (OSError, ValueError, KeyError) as error:
                    errors.append(str(error))
                    break
            trial_evidence = list(evidence) + quality_paths
            for trial in trials:
                trial_evidence += [
                    Path(trial[name]["path"])
                    for name in ("profile", "metadata", "video")
                ]
            # Failed attempts and verifier stderr remain part of the episode evidence.
            trial_evidence += list(round_dir.rglob("profile.json")) + list(
                round_dir.rglob("stderr.log")
            )
            record = {
                "benchmark_key": benchmark_key,
                "candidate_id": candidate["id"],
                "recipe": candidate,
                "protocol": str(snapshot),
                "calibration": str(calibration_path),
                "quality_reports": list(map(str, quality_paths)),
                "baseline_profiles": [x["profile"]["path"] for x in baselines],
                "candidate_profiles": [x["profile"]["path"] for x in trials],
                "baseline_videos": [x["video"] for x in baselines],
                "candidate_videos": [x["video"] for x in trials],
                "baseline_seconds": [x["seconds"] for x in baselines],
                "candidate_seconds": [x["seconds"] for x in trials],
                "errors": errors,
                "evidence": memory.bind_evidence(trial_evidence),
            }
            episode = memory.append(record)
            write_json(round_dir / "episode.json", episode)
            history.append(episode)
            status["episodes"].append(
                {
                    "id": episode["experiment_id"],
                    "candidate": candidate["id"],
                    "outcome": episode["outcome"],
                }
            )
            if episode["outcome"] == "ACCEPTED":
                selected = min(
                    (row for row in history if row["outcome"] == "ACCEPTED"),
                    key=lambda row: row["runtime"]["candidate_median_s"],
                )["recipe"]
                write_json(output / "selected_runtime.json", selected)
            status["selected_runtime"] = selected["id"]
            write_json(output / "status.json", status)
        status["status"] = "COMPLETE"
        status["memory_reused_records"] = len(history) - len(status["episodes"])
        return status
    except (
        OSError,
        ValueError,
        KeyError,
        RuntimeError,
        subprocess.SubprocessError,
    ) as error:
        status.update(status="ERROR", error=str(error))
        return status
    finally:
        status.update(ended_unix_s=time.time(), wall_s=time.time() - started)
        status["worker_costs"] = summarize_profiles(
            [read_json(path) for path in sorted(output.rglob("profile.json"))]
        )
        status["engineering_agent_tokens"] = None
        write_json(output / "status.json", status)
