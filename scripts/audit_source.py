"""Inventory every maintained/historical Python source without importing it."""

import argparse
import ast
from pathlib import Path

from simready.core.artifacts import digest, write_json

ROOTS = ("src/simready", "legacy", "examples", "tests", "scripts")


def inventory(root):
    records = []
    for folder in ROOTS:
        for path in sorted((root / folder).rglob("*.py")):
            source = path.read_text()
            tree = ast.parse(source, filename=str(path))
            compile(tree, str(path), "exec")
            records.append(
                {
                    "path": str(path.relative_to(root)),
                    "sha256": digest(path),
                    "lines": len(source.splitlines()),
                    "syntax": "PASS",
                    "scope": "historical_experiment" if folder == "legacy" else folder,
                    "top_level_definitions": [
                        n.name
                        for n in tree.body
                        if isinstance(n, (ast.FunctionDef, ast.ClassDef))
                    ],
                    "absolute_local_path_literals": sum(
                        isinstance(n, ast.Constant)
                        and isinstance(n.value, str)
                        and n.value.startswith(("/home/", "/root/", "~/"))
                        for n in ast.walk(tree)
                    ),
                    "integration_execution_certified": False,
                }
            )
    return {
        "schema_version": 1,
        "method": "AST parse, compile, source identity and module inventory; no GPU/network execution",
        "file_count": len(records),
        "files": records,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = inventory(args.root.resolve())
    write_json(args.out, report)
    print(f"Audited {report['file_count']} Python sources; syntax passed")


if __name__ == "__main__":
    main()
