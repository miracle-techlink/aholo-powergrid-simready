"""Explicit reconstruction backends sharing one source-bound artifact contract.

External commands are trusted user code, not sandboxed plugins. They receive an
explicit environment and the existing process deadline/device lease. Importing
artifacts never claims to have rerun their model. No model runs during planning.
"""

import copy
import fcntl
import math
import os
import sys
import time
from pathlib import Path

from simready.assets.appearance.gaussian_asset import decode_vertices, read_ply
from simready.core.artifacts import digest, object_digest, read_json, write_json
from simready.core.reconstruction_contract import (
    checked_ref,
    declarations,
    fields,
    verify_bundle,
)
from simready.execution.device_lease import device_lease
from simready.execution.processes import (
    ProcessCancelledError,
    ProcessTimeoutError,
    run_process,
)
from simready.execution.task_environment import freeze_environment
from simready.ingestion.reconstruction_stage import prepare_reconstruction

COMMON = {
    "provider",
    "base_dir",
    "representation",
    "algorithm",
    "frames",
    "coordinates",
}
COMMAND = {
    "argv",
    "input_files",
    "runtime_identity",
    "environment",
    "env",
    "environment_identity",
    "resources",
    "executable_identity",
}


def _manifest_dependencies(path, source_hash):
    manifest = read_json(path)
    if (
        not isinstance(manifest, dict)
        or type(manifest.get("schema_version")) is not int
        or manifest["schema_version"] != 1
        or manifest.get("status") != "COMPLETE"
        or manifest.get("source_video_sha256") != source_hash
    ):
        raise ValueError(
            "Imported reconstruction manifest must be complete and source-bound"
        )
    if not isinstance(manifest.get("artifacts"), list):
        raise ValueError("Reconstruction manifest needs artifacts")
    refs, kinds = [], set()
    for item in manifest["artifacts"]:
        fields(
            item,
            {"kind", "path", "sha256", "bytes"},
            "manifest artifact",
            {"kind", "path", "sha256", "bytes"},
        )
        if item["kind"] not in {"ply", "spz"} or item["kind"] in kinds:
            raise ValueError("Manifest supports one PLY and optional SPZ")
        ref = checked_ref({k: item[k] for k in ("path", "sha256")}, path.parent)
        artifact = Path(ref["path"])
        if (
            Path(item["path"]).is_absolute()
            or not artifact.is_relative_to(path.parent)
            or type(item["bytes"]) is not int
            or item["bytes"] <= 0
            or artifact.stat().st_size != item["bytes"]
        ):
            raise ValueError(
                "Manifest artifact must have a matching size and stay inside its directory"
            )
        kinds.add(item["kind"])
        refs.append(ref)
    if "ply" not in kinds:
        raise ValueError("Manifest needs a PLY")
    return refs


def plan_provider(config: dict, video: Path) -> dict:
    """Freeze absolute inputs and nonsecret environment identity without writes."""
    if not isinstance(config, dict) or config.get("provider") not in {
        "artifact",
        "aholo",
        "command",
    }:
        raise ValueError("provider must explicitly select artifact, aholo or command")
    provider = config["provider"]
    fields(
        config,
        COMMON
        | (
            {"manifest"}
            if provider == "artifact"
            else COMMAND
            if provider == "command"
            else set()
        ),
        "provider",
    )
    config = copy.deepcopy(config)
    base = Path(config.get("base_dir", ".")).expanduser().resolve()
    if not base.is_dir():
        raise ValueError("Provider base_dir must exist")
    config["base_dir"] = str(base)
    video = Path(video).expanduser().resolve()
    if not video.is_file() or not video.stat().st_size:
        raise ValueError("Provider needs a nonempty source video")
    source_hash = digest(video)
    refs = [{"path": str(video), "sha256": source_hash}]
    config.setdefault("representation", "gaussian_ply")
    if config["representation"] != "gaussian_ply":
        raise ValueError("Current USD route requires gaussian_ply representation")
    algorithm = config.get(
        "algorithm",
        {
            "name": "aholo" if provider == "aholo" else "unknown",
            "version": None,
            "implementation_sha256": None,
        },
    )
    frames = config.get(
        "frames", {"status": "unknown", "training": [], "heldout": [], "evidence": None}
    )
    coordinates = config.get(
        "coordinates",
        {
            "frame": "unknown",
            "unit": "unknown",
            "scale_status": "unknown",
            "evidence": None,
        },
    )
    config["algorithm"], config["frames"], config["coordinates"] = declarations(
        algorithm, frames, coordinates, base
    )
    for value in (config["frames"], config["coordinates"]):
        if value["evidence"] is not None:
            refs.append(value["evidence"])
    if provider == "artifact":
        config["manifest"] = checked_ref(config.get("manifest"), base)
        refs += [
            config["manifest"],
            *_manifest_dependencies(Path(config["manifest"]["path"]), source_hash),
        ]
    elif provider == "command":
        if (
            not config["algorithm"]["version"]
            or not config["algorithm"]["implementation_sha256"]
        ):
            raise ValueError(
                "Command provider needs an immutable algorithm version and implementation hash"
            )
        argv = config.get("argv")
        if (
            not isinstance(argv, list)
            or not argv
            or any(not isinstance(arg, str) or not arg or "\x00" in arg for arg in argv)
        ):
            raise ValueError("Command argv must be a nonempty list of explicit strings")
        inputs = config.get("input_files")
        if (
            not isinstance(inputs, dict)
            or not inputs
            or any(
                not isinstance(name, str) or not name.isidentifier() for name in inputs
            )
        ):
            raise ValueError(
                "Command requires named, hashed input_files (including implementation)"
            )
        config["input_files"] = {
            name: checked_ref(ref, base) for name, ref in inputs.items()
        }
        refs += list(config["input_files"].values())
        if config["algorithm"]["implementation_sha256"] not in {
            ref["sha256"] for ref in config["input_files"].values()
        }:
            raise ValueError(
                "Algorithm implementation hash must identify a declared input file"
            )
        config["runtime_identity"] = checked_ref(config.get("runtime_identity"), base)
        refs.append(config["runtime_identity"])
        resources = config.get("resources")
        fields(
            resources,
            {"kind", "gpu", "device_inventory", "lock_dir"},
            "resources",
            {"kind"},
        )
        if resources["kind"] not in {"cpu", "cuda"}:
            raise ValueError(
                "Reconstruction command resources.kind must be cpu or cuda"
            )
        if "device_inventory" in resources:
            resources["device_inventory"] = checked_ref(
                resources["device_inventory"], base
            )
            refs.append(resources["device_inventory"])
        if "lock_dir" in resources:
            resources["lock_dir"] = str((base / resources["lock_dir"]).resolve())
        if "environment" not in config:
            raise ValueError(
                "Command provider requires an explicit environment declaration"
            )
        environment = freeze_environment({**config, "cache": False}, dict(os.environ))
        if (
            "environment_identity" in config
            and config["environment_identity"] != environment.identity()
        ):
            raise ValueError("Declared runtime environment changed after planning")
        config["environment_identity"] = environment.identity()
        # Validate substitutions before any directory, process or device is created.
        executable = Path(_argv(config, video, Path("/provider-output"))[0]).resolve()
        if not executable.is_file():
            raise ValueError("Command executable is unavailable")
        executable_ref = {"path": str(executable), "sha256": digest(executable)}
        if config.get("executable_identity", executable_ref) != executable_ref:
            raise ValueError("Command executable changed after planning")
        config["executable_identity"] = executable_ref
        refs.append(executable_ref)
    dependencies = list({(ref["path"], ref["sha256"]): ref for ref in refs}.values())
    plan = {
        "config": config,
        "dependencies": dependencies,
        "source_video_sha256": source_hash,
    }
    return {**plan, "identity": object_digest(plan)}


def _argv(config, video, out):
    substitutions = {
        "{python}": sys.executable,
        "{video}": str(video),
        "{out}": str(out),
    }
    substitutions.update(
        {
            "{input:" + name + "}": ref["path"]
            for name, ref in config["input_files"].items()
        }
    )
    result = []
    for argument in config["argv"]:
        for name, value in substitutions.items():
            argument = argument.replace(name, value)
        if "{" in argument or "}" in argument:
            raise ValueError(
                "Unknown command placeholder; use python, video, out or input:NAME"
            )
        result.append(argument)
    if not Path(result[0]).is_absolute():
        raise ValueError("Command executable must be absolute or {python}")
    return result


def verify_provider_bundle(
    path: Path, *, source_video_sha256: str | None = None
) -> dict:
    """Verify declarations and independently decode all primary Gaussian values."""
    bundle = verify_bundle(
        path, source_video_sha256=source_video_sha256, require_gaussian=True
    )
    primary = next(item for item in bundle["artifacts"] if item["role"] == "primary")
    decoded = decode_vertices(read_ply(Path(path).parent / primary["path"]))
    if decoded["kind"] != "gaussian_splats":
        raise ValueError("Declared gaussian_ply decoded as a point cloud")
    return bundle


def _command(config, video, out, deadline, source_hash):
    environment = freeze_environment({**config, "cache": False}, dict(os.environ))
    if environment.identity() != config["environment_identity"]:
        raise ValueError("Declared runtime environment changed after planning")
    worker = out / "command"
    if worker.exists():
        receipt_path = out / "command-execution.json"
        if receipt_path.is_file() and not environment.secret_names:
            receipt = read_json(receipt_path)
            if receipt.get("status") == "COMPLETE":
                ref = checked_ref(receipt.get("manifest"), out)
                manifest = Path(ref["path"])
                if manifest != worker / "reconstruction.json":
                    raise ValueError("Command receipt references a different output")
                _manifest_dependencies(manifest, source_hash)
                return manifest
        raise ValueError(
            "Command output already exists; inspect failed attempt and use a fresh output"
        )
    worker.mkdir()
    resource = config["resources"]
    options = {"resource_kind": resource["kind"], "gpu": str(resource.get("gpu", "0"))}
    if "device_inventory" in resource:
        options["device_inventory"] = read_json(resource["device_inventory"]["path"])
    if "lock_dir" in resource:
        options["lock_dir"] = resource["lock_dir"]
    started = time.monotonic()
    status = "FAILED"
    manifest_ref = None
    try:
        with device_lease(**options) as lease:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProcessTimeoutError(timeout=0, seconds=time.monotonic() - started)
            result = run_process(
                _argv(config, video, worker),
                cwd=config["base_dir"],
                timeout=remaining,
                stdout=out / "command.stdout.log",
                stderr=out / "command.stderr.log",
                env={**environment.values, **lease.environment},
                pass_fds=lease.pass_fds,
            )
            if result.returncode:
                raise RuntimeError(
                    f"Reconstruction command exited {result.returncode}; see command.stderr.log"
                )
            manifest = worker / "reconstruction.json"
            _manifest_dependencies(manifest, source_hash)
            manifest_ref = {"path": str(manifest), "sha256": digest(manifest)}
            status = "COMPLETE"
    except ProcessTimeoutError:
        status = "TIMEOUT"
        raise
    except ProcessCancelledError:
        status = "CANCELLED"
        raise
    except KeyboardInterrupt:
        status = "INTERRUPTED"
        raise
    finally:
        write_json(
            out / "command-execution.json",
            {
                "status": status,
                "wall_s": time.monotonic() - started,
                "environment": environment.identity(),
                "resource_kind": resource["kind"],
                "manifest": manifest_ref,
            },
        )
    return worker / "reconstruction.json"


def prepare_provider(
    config: dict,
    video: Path,
    out: Path,
    timeout_s: float,
    authorize_cloud: bool = False,
) -> dict:
    """Run/import one explicit backend and return the legacy result plus bundle_path."""
    if (
        isinstance(timeout_s, bool)
        or not isinstance(timeout_s, (int, float))
        or not math.isfinite(timeout_s)
        or timeout_s <= 0
    ):
        raise ValueError("Provider timeout_s must be positive and finite")
    if type(authorize_cloud) is not bool:
        raise ValueError("authorize_cloud must be boolean")
    deadline = time.monotonic() + timeout_s
    plan = plan_provider(config, video)
    config, video, out = plan["config"], Path(video).resolve(), Path(out).resolve()
    if any(Path(ref["path"]).is_relative_to(out) for ref in plan["dependencies"]):
        raise ValueError("Provider output must not contain its frozen inputs")
    if out.exists() and (
        not out.is_dir() or any(child.is_symlink() for child in out.iterdir())
    ):
        raise ValueError("Provider output must be a directory without symlinked state")
    if (
        config["provider"] == "aholo"
        and not authorize_cloud
        and not (out / "receipt.json").exists()
        and not (out / "reconstruction.json").exists()
    ):
        raise PermissionError("A new Aholo job requires authorize_cloud=True")
    out.mkdir(parents=True, exist_ok=True)
    with (out / "provider.lock").open("a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        frozen = out / "provider-plan.json"
        if (
            not frozen.exists()
            and config["provider"] == "command"
            and any(
                (out / name).exists()
                for name in (
                    "command",
                    "reconstruction.json",
                    "reconstruction-bundle.json",
                )
            )
        ):
            raise ValueError(
                "Existing command outputs have no frozen provider identity"
            )
        if frozen.exists() and read_json(frozen) != plan:
            raise ValueError(
                "Provider configuration or frozen inputs changed; use a fresh output"
            )
        write_json(frozen, plan)
        bundle_path = out / "reconstruction-bundle.json"
        imported = None
        mode = "cached"
        if config["provider"] == "artifact":
            imported, mode = Path(config["manifest"]["path"]), "import"
        elif config["provider"] == "command":
            if (
                config["environment_identity"]["secret_names"]
                and (out / "reconstruction.json").exists()
            ):
                raise ValueError(
                    "Secret-dependent commands require a fresh output; artifact cache is disabled"
                )
            if not (out / "reconstruction.json").exists():
                imported, mode = (
                    _command(config, video, out, deadline, plan["source_video_sha256"]),
                    "fresh_command",
                )
                _manifest_dependencies(imported, plan["source_video_sha256"])
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProcessTimeoutError(timeout=timeout_s, seconds=timeout_s)
        result = prepare_reconstruction(
            video,
            out,
            artifact_manifest=imported,
            submit=authorize_cloud if config["provider"] == "aholo" else False,
            timeout=remaining,
        )
        if config["provider"] == "aholo":
            mode = (
                "cloud_new"
                if result["new_submission"]
                else "cloud_resume"
                if result["resumed"]
                else "cached"
            )
        for ref in plan["dependencies"]:
            checked_ref(ref, out)
        if bundle_path.exists():
            bundle = verify_provider_bundle(
                bundle_path, source_video_sha256=plan["source_video_sha256"]
            )
            if bundle["provenance"]["config_sha256"] != plan["identity"]:
                raise ValueError("Bundle does not identify this provider plan")
        else:
            bundle = {
                "schema_version": 1,
                "artifact_type": "reconstruction_bundle",
                "status": "COMPLETE",
                "representation": config["representation"],
                "source_video_sha256": plan["source_video_sha256"],
                "algorithm": config["algorithm"],
                "frames": config["frames"],
                "coordinates": config["coordinates"],
                "declaration_verification": "operator_evidence_not_independent_certification",
                "artifacts": [
                    {
                        **item,
                        "role": "primary" if item["kind"] == "ply" else "supporting",
                    }
                    for item in result["artifacts"]
                ],
                "provenance": {
                    "provider": config["provider"],
                    "execution_mode": mode,
                    "config_sha256": plan["identity"],
                    "dependencies": plan["dependencies"],
                },
            }
            pending = out / ".reconstruction-bundle.pending.json"
            write_json(pending, bundle)
            verify_provider_bundle(
                pending, source_video_sha256=plan["source_video_sha256"]
            )
            pending.replace(bundle_path)
        return {**result, "bundle_path": str(bundle_path), "execution_mode": mode}
