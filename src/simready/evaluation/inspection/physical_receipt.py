"""Accept a USD readback only when its checks and current input hashes agree."""

from pathlib import Path
from typing import Any

from simready.core.artifacts import digest, read_json


def verify_physical_receipt(
    report: dict[str, Any], *, directory: str | Path, scene_spec: str | Path
) -> None:
    """Validate a passing inspector receipt without importing USD or the writer.

    Paths in input records can belong to a container; hashes bind their content.
    This validates a trusted independent worker's receipt, not prediction accuracy
    or an adversarial worker. The caller must also require a successful exit.
    """
    root = Path(directory).resolve()
    if (
        report.get("schema_version") != 1
        or report.get("artifact_type") != "physical_asset_inspection"
        or report.get("status") != "PASS"
        or report.get("passed") is not True
    ):
        raise ValueError("Physical audit did not pass")
    checks = report.get("checks")
    if (
        not isinstance(checks, list)
        or not checks
        or any(
            not isinstance(row, dict)
            or row.get("status") != "PASS"
            or row.get("passed") is not True
            for row in checks
        )
    ):
        raise ValueError("Physical audit requires nonempty passing checks")
    inputs = report.get("inputs", {})
    for key, path in (
        ("scene", root / "scene.usda"),
        ("catalog", root / "asset_catalog.json"),
        ("scene_spec", Path(scene_spec)),
    ):
        if inputs.get(key, {}).get("sha256") != digest(path):
            raise ValueError(f"Physical audit input changed: {key}")
    expected = sorted(row["id"] for row in read_json(scene_spec).get("objects", []))
    assets = report.get("assets", [])
    if (
        not expected
        or not isinstance(assets, list)
        or any(not isinstance(row, dict) for row in assets)
        or sorted(row.get("id", "") for row in assets) != expected
        or any(
            row.get("passed") is not True or row.get("status") != "PASS"
            for row in assets
        )
    ):
        raise ValueError(
            "Physical audit asset inventory does not match the specification"
        )
    dependencies = report.get("dependencies")
    if not isinstance(dependencies, list) or not dependencies:
        raise ValueError("Physical audit requires composed USD dependencies")
    seen = set()
    for item in dependencies:
        name = item.get("path") if isinstance(item, dict) else None
        if not isinstance(name, str) or Path(name).is_absolute() or name in seen:
            raise ValueError("Physical audit dependency path is invalid or repeated")
        path = (root / name).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError(
                "Physical audit dependency is missing or outside the scene"
            )
        if digest(path) != item.get("sha256"):
            raise ValueError(f"Physical audit dependency changed: {name}")
        seen.add(name)
    if "scene.usda" not in seen:
        raise ValueError("Physical audit does not include the root USD layer")
