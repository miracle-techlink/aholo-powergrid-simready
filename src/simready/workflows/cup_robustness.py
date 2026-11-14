"""Freeze, prepare, execute and independently audit a cup robustness campaign."""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import sys
import time
from pathlib import Path

from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.validation import number
from simready.evaluation.cup_robustness import (
    execution_status,
    inspect_parameters,
    summarize_campaign,
)
from simready.execution.isaac_launcher import default_repository, launch_worker
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.simulation.cup_control import VERSIONS, controller_spec
from simready.simulation.cup_randomization import (
    ASSETS,
    prepare_episode,
    sample_protocol,
)
from simready.simulation.cup_recording import implementation_hashes


def runtime_configuration(
    *,
    backend="docker",
    python=None,
    image="simready-isaac:6.1.0",
    gpu="0",
    worker_timeout_s=7200,
    cleanup_timeout_s=15,
    audit_timeout_s=300,
):
    """Freeze requested execution identity without claiming a reproducible host."""
    if backend not in {"docker", "local"}:
        raise ValueError("backend must be docker or local")
    for name, value in (
        ("worker_timeout_s", worker_timeout_s),
        ("cleanup_timeout_s", cleanup_timeout_s),
        ("audit_timeout_s", audit_timeout_s),
    ):
        if number(value, name) <= 0:
            raise ValueError(f"{name} must be positive")
    if cleanup_timeout_s > 15:
        raise ValueError("cleanup_timeout_s must be <=15 for nested cancellation")
    if not isinstance(gpu, str) or not re.fullmatch(
        r"(?:[0-9]+|GPU-[a-zA-Z0-9-]+)", gpu
    ):
        raise ValueError("Select one GPU index or UUID")

    def executable(value):
        expanded = str(Path(value).expanduser())
        # Preserve invocation symlinks so .venv/bin/python retains its environment.
        path = Path(os.path.abspath(shutil.which(expanded) or expanded))
        if not path.is_file():
            raise ValueError(f"Python executable is unavailable: {value}")
        return {
            "path": str(path),
            "resolved_path": str(path.resolve()),
            "sha256": digest(path),
        }

    if backend == "docker" and (
        not isinstance(image, str)
        or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/@-]*", image)
    ):
        raise ValueError("Invalid Docker image reference")
    immutable_image = backend == "docker" and bool(
        re.search(r"(?:@|^)sha256:[a-f0-9]{64}$", image)
    )
    return {
        "backend": backend,
        "python": executable(python or sys.executable) if backend == "local" else None,
        "image": image if backend == "docker" else None,
        "image_content_addressed": immutable_image,
        "gpu_selector": gpu,
        "gpu_uuid_pinned": gpu.startswith("GPU-"),
        "resource_kind": "rtx",
        "worker_timeout_s": float(worker_timeout_s),
        "cleanup_timeout_s": float(cleanup_timeout_s),
        "audit_timeout_s": float(audit_timeout_s),
        "audit_python": executable(sys.executable),
        "environment_immutable": False,
        "limitations": [
            "Docker tags are mutable; only digest references identify image content.",
            "GPU indices can select another device; resolved UUID is recorded by the launcher.",
            "Executable hashes do not pin installed libraries, drivers or the complete runtime environment.",
        ],
    }


def run_campaign(
    protocol_path: Path,
    assets: Path,
    output: Path,
    *,
    prepare_only: bool = False,
    backend: str = "docker",
    python: str | None = None,
    image: str = "simready-isaac:6.1.0",
    gpu: str = "0",
    physics_hz: int = 240,
    controller: str = "baseline-v1",
    worker_timeout_s: float = 7200,
    cleanup_timeout_s: float = 15,
    audit_timeout_s: float = 300,
    cancel_event=None,
) -> dict:
    """Never overwrite prior runs; execute all frozen samples, including failures."""
    if type(physics_hz) is not int or physics_hz not in (240, 480):
        raise ValueError("physics_hz must be 240 or 480")
    runtime_args = dict(
        backend=backend,
        python=python,
        image=image,
        gpu=gpu,
        worker_timeout_s=worker_timeout_s,
        cleanup_timeout_s=cleanup_timeout_s,
        audit_timeout_s=audit_timeout_s,
    )
    runtime = runtime_configuration(**runtime_args)
    spec = controller_spec(controller)
    assets, output = assets.resolve(), output.resolve()
    protocol = read_json(protocol_path)
    samples = sample_protocol(protocol)
    source_hashes = {name: digest(assets / name) for name in ASSETS}
    source = Path(__file__).resolve().parents[1]
    manifest = {
        "schema_version": 1,
        "protocol": protocol,
        "protocol_sha256": object_digest(protocol),
        "samples": samples,
        "source_assets_sha256": source_hashes,
        "runtime": runtime,
        "controller": {
            "spec": spec,
            "implementation_sha256": implementation_hashes(),
            "worker_sha256": digest(source / "simulation/cup_grasp.py"),
            "kinematics_sha256": digest(
                source / "simulation/articulated_kinematics.py"
            ),
            "plan_sha256": source_hashes["plan.json"],
            "physics_hz": physics_hz,
            "gripper_close_rad": 0.70,
            "feedback": "existing placement correction uses simulator truth pose",
        },
        "audit_sha256": digest(source / "evaluation/cup_transfer.py"),
        "workflow_sha256": digest(Path(__file__)),
        "sample_distribution": "independent uniforms, each sampled once before an episode",
        "heldout_note": "Fixed controller; no optimization in this campaign; heldout split must not enter later training memory",
    }
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "manifest.json", manifest)
    manifest_sha256 = digest(output / "manifest.json")

    def check_frozen_inputs():
        if digest(output / "manifest.json") != manifest_sha256 or any(
            digest(assets / name) != sha for name, sha in source_hashes.items()
        ):
            raise ValueError("Frozen campaign manifest or source assets changed")
        if runtime_configuration(**runtime_args) != runtime:
            raise ValueError("Frozen runtime configuration changed during campaign")
        if (
            digest(source / "simulation/cup_grasp.py")
            != manifest["controller"]["worker_sha256"]
            or digest(source / "simulation/articulated_kinematics.py")
            != manifest["controller"]["kinematics_sha256"]
            or digest(source / "evaluation/cup_transfer.py") != manifest["audit_sha256"]
            or digest(Path(__file__)) != manifest["workflow_sha256"]
            or implementation_hashes()
            != manifest["controller"]["implementation_sha256"]
        ):
            raise ValueError("Frozen execution code changed during campaign")

    def inspect_frozen_parameters(prepared, sample):
        return inspect_parameters(
            prepared,
            sample,
            source_assets_sha256=source_hashes,
            protocol_sha256=manifest["protocol_sha256"],
        )

    records = []
    stopped = None
    started = time.perf_counter()
    try:
        for sample in samples:
            tick = time.perf_counter()
            directory = output / sample["id"]
            row = {"id": sample["id"], "status": "RUNNING"}
            records.append(row)
            try:
                if cancel_event is not None and cancel_event.is_set():
                    raise ProcessCancelledError(time.perf_counter() - started)
                check_frozen_inputs()
                prepared = directory / "assets"
                prepare_episode(assets, prepared, sample, manifest["protocol_sha256"])
                check_frozen_inputs()
                row["parameter_audit"] = inspect_frozen_parameters(prepared, sample)
                write_json(directory / "parameter_audit.json", row["parameter_audit"])
                if row["parameter_audit"]["status"] != "PASS":
                    raise ValueError("Composed USD parameter audit failed")
                if prepare_only:
                    row["status"] = "PREPARED"
                    continue
                artifacts = directory / "artifacts"
                worker_assets = "/assets" if backend == "docker" else str(prepared)
                worker_output = (
                    "/output/physics"
                    if backend == "docker"
                    else str(artifacts / "physics")
                )
                row["worker_exit_code"] = launch_worker(
                    [
                        "-u",
                        "-m",
                        "simready.simulation.cup_grasp",
                        "--assets",
                        worker_assets,
                        "--out",
                        worker_output,
                        "--physics-hz",
                        str(physics_hz),
                        "--gripper-close-rad",
                        "0.70",
                        "--controller",
                        controller,
                    ],
                    repo=default_repository(),
                    assets=prepared,
                    output=artifacts,
                    backend=backend,
                    python=runtime["python"]["path"] if runtime["python"] else None,
                    image=image,
                    gpu=gpu,
                    timeout_s=worker_timeout_s,
                    cleanup_timeout_s=cleanup_timeout_s,
                    cancel_event=cancel_event,
                )
                check_frozen_inputs()
                audit_path = directory / "task_audit.json"
                completed = run_process(
                    [
                        runtime["audit_python"]["path"],
                        "-m",
                        "simready.evaluation.cup_transfer",
                        "--assets",
                        str(prepared),
                        "--rollout",
                        str(artifacts / "physics"),
                        "--out",
                        str(audit_path),
                    ],
                    cwd=None,
                    timeout=audit_timeout_s,
                    cancel_event=cancel_event,
                    stdout=directory / "audit.log",
                    stderr=directory / "audit.stderr.log",
                )
                row["task_audit"] = (
                    read_json(audit_path)
                    if audit_path.exists()
                    else {"status": "FAILED", "error": "Audit did not produce a report"}
                )
                row["task_audit_exit_code"] = completed.returncode
                result_path = artifacts / "physics/result.json"
                worker_result = read_json(result_path) if result_path.exists() else {}
                row["worker_status"] = worker_result.get("status")
                row["worker_identity_matches"] = (
                    worker_result.get("controller") == spec
                    and worker_result.get("controller_code_sha256")
                    == manifest["controller"]["implementation_sha256"]
                )
                actual = worker_result.get("randomization_runtime", {})
                row["runtime_parameters_match"] = math.isclose(
                    actual.get("cup_mass_kg", -1),
                    sample["parameters"]["cup_mass_kg"],
                    rel_tol=1e-6,
                ) and all(
                    actual.get("materials", {}).get(group, {}).get("combine")
                    == "average"
                    and math.isclose(
                        actual["materials"][group]["static_friction"],
                        sample["parameters"][group + "_static_friction"],
                        rel_tol=1e-6,
                    )
                    and math.isclose(
                        actual["materials"][group]["dynamic_friction"],
                        sample["parameters"][group + "_static_friction"]
                        * sample["parameters"]["dynamic_static_ratio"],
                        rel_tol=1e-6,
                    )
                    for group in ("cup", "gripper", "table")
                )
                # Re-read after execution as well; stale preflight checks cannot
                # validate an asset modified while the worker was running.
                check_frozen_inputs()
                row["parameter_audit"] = inspect_frozen_parameters(prepared, sample)
                write_json(directory / "parameter_audit.json", row["parameter_audit"])
                row["status"] = execution_status(
                    row["worker_exit_code"], worker_result, row["task_audit"]
                )
                if completed.returncode not in (0, 2):
                    row["status"] = "AUDIT_FAILED"
                if not row["worker_identity_matches"]:
                    row["status"] = "WORKER_IDENTITY_MISMATCH"
            except ProcessCancelledError as error:
                row.update(status="CANCELLED", error=f"{type(error).__name__}: {error}")
                stopped = "CANCELLED"
                raise
            except ProcessTimeoutError as error:
                row.update(status="TIMEOUT", error=f"{type(error).__name__}: {error}")
            except Exception as error:
                row.update(status="FAILED", error=f"{type(error).__name__}: {error}")
            except BaseException as error:
                row.update(status="INTERRUPTED", error=type(error).__name__)
                stopped = "INTERRUPTED"
                raise
            finally:
                row["wall_s"] = time.perf_counter() - tick
                write_json(directory / "episode.json", row)
                write_json(output / "episodes.json", records)
    finally:
        report = summarize_campaign(manifest, records)
        if prepare_only:
            report["status"] = (
                "PREPARED"
                if all(r["status"] == "PREPARED" for r in records)
                and len(records) == len(samples)
                else "INCOMPLETE"
            )
            report["execution"] = "NOT_RUN"
            # No task trial has run, so a numerical success rate is inapplicable.
            report["all"] = report["by_split"] = None
        if stopped is not None:
            report["status"] = stopped
        report["wall_s"] = time.perf_counter() - started
        report["manifest_sha256"] = manifest_sha256
        write_json(output / "report.json", report)
    return report


def run_comparison(
    protocol_path, assets, output, *, prepare_only=False, frozen_manifest=None, **kwargs
):
    """Freeze both versions against identical samples; failures keep their pairs."""
    if "controller" in kwargs:
        raise ValueError("Paired comparison specifies both controller versions")
    output = Path(output).resolve()
    protocol = read_json(protocol_path)
    samples = sample_protocol(protocol)
    code = implementation_hashes()
    source_hashes = {name: digest(Path(assets) / name) for name in ASSETS}
    runtime_args = {
        key: kwargs[key]
        for key in (
            "backend",
            "python",
            "image",
            "gpu",
            "worker_timeout_s",
            "cleanup_timeout_s",
            "audit_timeout_s",
        )
        if key in kwargs
    }
    frozen = {
        "schema_version": 1,
        "protocol": protocol,
        "samples": samples,
        "source_assets_sha256": source_hashes,
        "runtime": runtime_configuration(**runtime_args),
        "implementation_sha256": code,
        "controllers": [controller_spec(version) for version in VERSIONS],
        "physics_hz": kwargs.get("physics_hz", 240),
        "audit_sha256": digest(
            Path(__file__).resolve().parents[1] / "evaluation/cup_transfer.py"
        ),
        "workflow_sha256": digest(Path(__file__)),
        "promotion": "No automatic promotion. New paired physical evidence is required.",
        "old_heldout_seeds_excluded": [901, 902],
    }
    if any(row["seed"] in {901, 902} for row in samples):
        raise ValueError(
            "Previously observed heldout seeds 901/902 cannot be reused for candidate evaluation"
        )
    if frozen_manifest is not None and read_json(frozen_manifest) != frozen:
        raise ValueError(
            "Requested paired run differs from the frozen comparison manifest"
        )
    output.mkdir(parents=True, exist_ok=False)
    write_json(output / "comparison_manifest.json", frozen)
    frozen_hash = digest(output / "comparison_manifest.json")
    write_json(output / "protocol.json", protocol)
    reports = {}
    comparison = {
        "status": "INCOMPLETE",
        "physical_validation": "NOT_RUN",
        "comparison_manifest_sha256": frozen_hash,
        "pairs": [
            {
                "id": row["id"],
                "split": row["split"],
                "seed": row["seed"],
                **{
                    version: {"status": "NOT_RUN", "task_passed": None}
                    for version in VERSIONS
                },
            }
            for row in samples
        ],
    }
    active_version = None

    def record_campaign(version, report):
        manifest_path = output / version / "manifest.json"
        actual = read_json(manifest_path)
        if (
            report.get("manifest_sha256") != digest(manifest_path)
            or actual.get("samples") != samples
            or actual.get("runtime") != frozen["runtime"]
            or actual.get("source_assets_sha256") != source_hashes
            or actual.get("controller", {}).get("spec") != controller_spec(version)
            or actual.get("controller", {}).get("implementation_sha256") != code
            or actual.get("audit_sha256") != frozen["audit_sha256"]
            or actual.get("workflow_sha256") != frozen["workflow_sha256"]
        ):
            raise ValueError(
                "Partial campaign report differs from frozen paired identity"
            )
        rows = summarize_campaign(actual, report["episodes"])["episodes"]
        reports[version] = report
        for pair in comparison["pairs"]:
            row = next(row for row in rows if row["id"] == pair["id"])
            pair[version] = {
                "status": row["status"],
                "task_passed": None
                if prepare_only or row["status"] == "NOT_RUN"
                else (
                    row.get("success") is True
                    and row.get("worker_identity_matches") is True
                ),
                "task_audit": row.get("task_audit"),
            }

    try:
        for version in VERSIONS:
            active_version = version
            if (
                implementation_hashes() != code
                or digest(output / "comparison_manifest.json") != frozen_hash
                or read_json(output / "protocol.json") != protocol
                or runtime_configuration(**runtime_args) != frozen["runtime"]
                or digest(Path(__file__)) != frozen["workflow_sha256"]
                or digest(
                    Path(__file__).resolve().parents[1] / "evaluation/cup_transfer.py"
                )
                != frozen["audit_sha256"]
                or any(
                    digest(Path(assets) / name) != value
                    for name, value in source_hashes.items()
                )
            ):
                raise ValueError("Frozen paired inputs changed")
            report = run_campaign(
                output / "protocol.json",
                Path(assets),
                output / version,
                controller=version,
                prepare_only=prepare_only,
                **kwargs,
            )
            record_campaign(version, report)
            write_json(output / "comparison.json", comparison)
        expected = "PREPARED" if prepare_only else "COMPLETE"
        if all(report["status"] == expected for report in reports.values()):
            comparison["status"] = expected
        comparison["physical_validation"] = (
            "NOT_RUN" if prepare_only else "SEE_PAIRED_AUDITS"
        )
    except ProcessCancelledError as error:
        comparison.update(status="CANCELLED", error=str(error))
        raise
    except BaseException as error:
        if not isinstance(error, Exception):
            comparison["status"] = "INTERRUPTED"
        comparison["error"] = f"{type(error).__name__}: {error}"
        raise
    finally:
        if active_version is not None and active_version not in reports:
            partial_report = output / active_version / "report.json"
            if partial_report.is_file():
                try:
                    record_campaign(active_version, read_json(partial_report))
                except Exception as error:
                    comparison["partial_report_error"] = str(error)
        if (
            not prepare_only
            and any(
                pair[version]["status"] != "NOT_RUN"
                for pair in comparison["pairs"]
                for version in VERSIONS
            )
            and comparison["status"] != "COMPLETE"
        ):
            comparison["physical_validation"] = "INCOMPLETE"
        comparison["campaigns"] = {
            version: report["status"] for version, report in reports.items()
        }
        write_json(output / "comparison.json", comparison)
    return comparison


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--protocol", type=Path, required=True)
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--backend", choices=["docker", "local"], default="docker")
    parser.add_argument("--python")
    parser.add_argument("--image", default="simready-isaac:6.1.0")
    parser.add_argument("--gpu", default="0")
    parser.add_argument("--physics-hz", type=int, choices=[240, 480], default=240)
    parser.add_argument("--worker-timeout-s", type=float, default=7200)
    parser.add_argument("--cleanup-timeout-s", type=float, default=15)
    parser.add_argument("--audit-timeout-s", type=float, default=300)
    parser.add_argument("--controller", choices=VERSIONS, default="baseline-v1")
    parser.add_argument(
        "--paired",
        action="store_true",
        help="Run both versions on the same frozen samples; never promote automatically",
    )
    parser.add_argument(
        "--comparison-manifest",
        type=Path,
        help="Require a prior frozen paired code/config/asset/sample identity",
    )
    parser.add_argument("--accept-eula", action="store_true")
    args = parser.parse_args(argv)
    if not args.prepare_only and not args.accept_eula:
        parser.error("Use --accept-eula after accepting the Isaac Sim license")
    try:
        if args.paired and args.controller != "baseline-v1":
            parser.error("--paired selects both versions; omit --controller")
        if args.comparison_manifest is not None and not args.paired:
            parser.error("--comparison-manifest requires --paired")
        run = run_comparison if args.paired else run_campaign
        report = run(
            args.protocol,
            args.assets,
            args.out,
            prepare_only=args.prepare_only,
            backend=args.backend,
            python=args.python,
            image=args.image,
            gpu=args.gpu,
            physics_hz=args.physics_hz,
            worker_timeout_s=args.worker_timeout_s,
            cleanup_timeout_s=args.cleanup_timeout_s,
            audit_timeout_s=args.audit_timeout_s,
            **(
                {"frozen_manifest": args.comparison_manifest}
                if args.paired
                else {"controller": args.controller}
            ),
        )
    except (ValueError, OSError, KeyError, TypeError) as error:
        print(json.dumps({"status": "INVALID", "error": str(error)}))
        return 3
    print(json.dumps({k: v for k, v in report.items() if k != "episodes"}, indent=2))
    return 0 if report["status"] in {"COMPLETE", "PREPARED"} else 2


if __name__ == "__main__":
    raise SystemExit(main())
