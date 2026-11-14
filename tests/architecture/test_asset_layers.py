"""Asset subpackages have one-way dependencies, independent of runtime workers."""

import ast

from tests.paths import REPOSITORY_ROOT

LAYERS = {
    "appearance": set(),
    "physics": set(),
    "association": {"appearance", "physics"},
    "compilation": {"appearance", "physics"},
}


def test_asset_layers_keep_declarations_below_association_and_compilation():
    root = REPOSITORY_ROOT / "src/simready/assets"
    assert {
        path.name
        for path in root.iterdir()
        if path.is_dir() and not path.name.startswith("_")
    } == set(LAYERS)
    for path in root.rglob("*.py"):
        relative = path.relative_to(root)
        if len(relative.parts) < 2:
            continue
        layer = relative.parts[0]
        for node in ast.walk(ast.parse(path.read_text())):
            imports = (
                [node.module or ""]
                if isinstance(node, ast.ImportFrom)
                else [item.name for item in node.names]
                if isinstance(node, ast.Import)
                else []
            )
            for module in imports:
                parts = module.split(".")
                if parts[:2] == ["simready", "assets"] and len(parts) > 2:
                    assert parts[2] == layer or parts[2] in LAYERS[layer], (
                        relative,
                        module,
                        "reverse asset-layer dependency",
                    )
