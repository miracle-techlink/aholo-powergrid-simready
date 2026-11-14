"""Check module ownership, import direction and public API contracts without imports."""

import argparse
import ast
import json
from pathlib import Path


def check_architecture(source: Path, rules: dict) -> list[str]:
    """Return structural violations; parsing never starts a runtime or loads a model."""
    errors = []
    domains = rules["domains"]
    actual = {
        p.name for p in source.iterdir() if p.is_dir() and not p.name.startswith("_")
    }
    if actual != set(domains):
        errors.append(
            f"Domain inventory mismatch: actual={sorted(actual)}, declared={sorted(domains)}"
        )
    parsed = {}
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        module = ".".join([rules["package"], *relative.with_suffix("").parts])
        tree = ast.parse(path.read_text(), filename=str(path))
        parsed[module] = tree
        domain = relative.parts[0] if len(relative.parts) > 1 else None
        importlib_names = {"importlib"}
        dynamic_loaders = {"__import__"}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                importlib_names.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "importlib"
                )
            elif isinstance(node, ast.ImportFrom) and node.module == "importlib":
                dynamic_loaders.update(
                    alias.asname or alias.name
                    for alias in node.names
                    if alias.name == "import_module"
                )
        if path.stem in rules["forbidden_module_names"]:
            errors.append(f"{relative}: ambiguous module name")
        if path.name == "__main__.py" and len(path.read_text().splitlines()) > 8:
            errors.append(f"{relative}: entry point must only delegate to cli.main")
        for node in ast.walk(tree):
            imports = []
            if isinstance(node, ast.ImportFrom):
                if node.level:
                    errors.append(
                        f"{relative}:{node.lineno}: use explicit absolute imports"
                    )
                imports = [node.module or ""]
            elif isinstance(node, ast.Import):
                imports = [a.name for a in node.names]
            elif isinstance(node, ast.Call):
                function = node.func
                dynamic = (
                    isinstance(function, ast.Name) and function.id in dynamic_loaders
                ) or (
                    isinstance(function, ast.Attribute)
                    and isinstance(function.value, ast.Name)
                    and function.value.id in importlib_names
                    and function.attr == "import_module"
                )
                if dynamic:
                    target = (
                        node.args[0]
                        if node.args
                        else next(
                            (kw.value for kw in node.keywords if kw.arg == "name"), None
                        )
                    )
                    if isinstance(target, ast.Constant) and isinstance(
                        target.value, str
                    ):
                        imports = [target.value]
                        if target.value.startswith("."):
                            errors.append(
                                f"{relative}:{node.lineno}: use explicit absolute imports"
                            )
                    elif str(relative) not in rules.get("dynamic_import_routers", {}):
                        errors.append(
                            f"{relative}:{node.lineno}: dynamic import requires a declared CLI router"
                        )
            for target in imports:
                parts = target.split(".")
                if parts[0] in rules["forbidden_runtime_roots"]:
                    errors.append(
                        f"{relative}:{node.lineno}: forbidden runtime import {target}"
                    )
                if parts[0] != rules["package"] or domain is None:
                    continue
                dependency = parts[1] if len(parts) > 1 else None
                if dependency == domain:
                    continue
                if dependency not in domains.get(domain, {}).get(
                    "allowed_dependencies", []
                ):
                    errors.append(
                        f"{relative}:{node.lineno}: forbidden dependency {domain} -> {dependency}"
                    )
    for domain in domains:
        if not (source / domain / "__init__.py").is_file():
            errors.append(f"{domain}: missing package marker")

    # The declared dependency graph must be acyclic, including edges not imported yet.
    def visit(domain, visiting, visited):
        if domain in visiting:
            errors.append(f"Domain dependency cycle at {domain}")
            return
        if domain in visited:
            return
        visiting.add(domain)
        for dependency in domains[domain]["allowed_dependencies"]:
            if dependency not in domains:
                errors.append(f"{domain}: unknown dependency {dependency}")
            else:
                visit(dependency, visiting, visited)
        visiting.remove(domain)
        visited.add(domain)

    visited = set()
    for domain in domains:
        visit(domain, set(), visited)
    for api in rules["public_functions"]:
        module, name = api.rsplit(".", 1)
        tree = parsed.get(module)
        functions = (
            []
            if tree is None
            else [
                n
                for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == name
            ]
        )
        if len(functions) != 1:
            errors.append(f"{api}: public implementation missing")
            continue
        function = functions[0]
        arguments = [
            *function.args.posonlyargs,
            *function.args.args,
            *function.args.kwonlyargs,
        ]
        if function.returns is None or any(arg.annotation is None for arg in arguments):
            errors.append(
                f"{api}: public signature must annotate parameters and result"
            )
        if not ast.get_docstring(function):
            errors.append(f"{api}: public API needs a responsibility docstring")
    return sorted(set(errors))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root", type=Path, default=Path(__file__).resolve().parents[1]
    )
    args = parser.parse_args()
    rules = json.loads(
        (args.root / "configs/architecture/module_boundaries.json").read_text()
    )
    errors = check_architecture(args.root / rules["source_directory"], rules)
    for error in errors:
        print(error)
    if not errors:
        print(
            f"Architecture PASS: {len(rules['domains'])} domains, {len(rules['public_functions'])} public functions"
        )
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
