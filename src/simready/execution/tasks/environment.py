"""Freeze a task's declared environment without persisting credential values.

Literal ``env`` and ``environment.inherit`` are explicitly nonsecret settings.
Credentials belong in ``environment.secrets`` and disable artifact reuse. Old
plans retain full host inheritance, but cannot read or publish artifact caches.
"""

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from simready.core.artifacts import object_digest
from simready.execution.processes.cancellation import RESERVED as PROCESS_RESERVED
from simready.execution.resources.gpu_capability import LEASE_FD

RESERVED = (
    frozenset(
        {"CUDA_VISIBLE_DEVICES", "SIMREADY_GPU_UUID", "SIMREADY_OUT", "SIMREADY_SEED"}
    )
    | PROCESS_RESERVED
    | {LEASE_FD}
)


def _names(values, label):
    if not isinstance(values, list) or any(
        not isinstance(name, str) or not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_]*", name)
        for name in values
    ):
        raise ValueError(f"{label} must be a list of environment variable names")
    if len(set(values)) != len(values):
        raise ValueError(f"{label} must contain unique names")
    if set(values) & RESERVED:
        raise ValueError("Task cannot override scheduler device/output/seed bindings")


def validate_environment(task):
    """Validate names and disjoint credential references without reading the host."""
    overrides = task.get("env", {})
    if not isinstance(overrides, dict):
        raise ValueError("env must be an object of nonsecret string values")
    _names(list(overrides), "env keys")
    if any(
        not isinstance(value, str) or "\x00" in value for value in overrides.values()
    ):
        raise ValueError("env values must be strings without NUL characters")
    if "environment" not in task:
        return
    contract = task["environment"]
    if not isinstance(contract, dict) or set(contract) - {"inherit", "secrets"}:
        raise ValueError("environment must be an object with only inherit and secrets")
    inherit, secrets = contract.get("inherit", []), contract.get("secrets", [])
    _names(inherit, "environment.inherit")
    _names(secrets, "environment.secrets")
    if set(secrets) & (set(inherit) | set(overrides)):
        raise ValueError("environment.secrets cannot overlap inherit or env")


@dataclass(frozen=True)
class TaskEnvironment:
    """Private process values plus safe metadata; repr never contains values."""

    values: Mapping[str, str] = field(repr=False)
    mode: str
    inherited_names: tuple[str, ...]
    missing_inherited_names: tuple[str, ...]
    secret_names: tuple[str, ...]
    override_names: tuple[str, ...]
    nonsecret_sha256: str | None
    cache_requested: bool
    cache_disabled_reason: str | None

    @property
    def cache_enabled(self):
        return self.cache_disabled_reason is None

    def identity(self):
        # No raw environment values, secret value hashes, or legacy host hashes.
        return {
            "contract_version": 1,
            "mode": self.mode,
            "nonsecret_sha256": self.nonsecret_sha256,
            "secret_names": list(self.secret_names),
        }

    def receipt(self):
        return {
            "environment": dict(
                self.identity(),
                inherited_names=list(self.inherited_names),
                missing_inherited_names=list(self.missing_inherited_names),
                override_names=list(self.override_names),
            ),
            "cache_requested": self.cache_requested,
            "cache_enabled": self.cache_enabled,
            "cache_disabled_reason": self.cache_disabled_reason,
        }


def freeze_environment(task, host):
    """Resolve one contract against the graph's single, private host snapshot."""
    validate_environment(task)
    overrides = task.get("env", {})
    explicit = "environment" in task
    contract = task.get("environment", {})
    inherit = (
        tuple(sorted(contract.get("inherit", [])))
        if explicit
        else tuple(sorted(set(host) - RESERVED))
    )
    secrets = tuple(sorted(contract.get("secrets", [])))
    missing_secrets = [name for name in secrets if name not in host]
    if missing_secrets:
        raise ValueError(
            "Missing required environment secret: " + ", ".join(missing_secrets)
        )
    values = {
        name: host[name] for name in inherit if name in host and name not in RESERVED
    }
    values.update(overrides)
    # Hash only the final effective nonsecret values, after literal overrides.
    # Absent keys and present empty strings have distinct JSON representations.
    fingerprint = object_digest(values) if explicit else None
    values.update({name: host[name] for name in secrets})
    if not explicit:
        reason = "legacy_environment_untracked"
    elif secrets:
        reason = "secret_environment"
    elif not task.get("cache", True):
        reason = "cache_disabled_by_plan"
    else:
        reason = None
    return TaskEnvironment(
        values=MappingProxyType(values),
        mode="explicit" if explicit else "legacy_inherit_all",
        inherited_names=inherit,
        missing_inherited_names=tuple(name for name in inherit if name not in host),
        secret_names=secrets,
        override_names=tuple(sorted(overrides)),
        nonsecret_sha256=fingerprint,
        cache_requested=task.get("cache", True),
        cache_disabled_reason=reason,
    )


def plan_receipt(plan):
    """Store literal variable names without their values or a replayable stub."""
    result = copy.deepcopy(plan)
    for task in result["tasks"]:
        if task.get("env"):
            task["env"] = {name: "<redacted>" for name in task["env"]}
            result["environment_values_redacted"] = True
    return result
