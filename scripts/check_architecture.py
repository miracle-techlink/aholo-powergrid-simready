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
    module_paths = {
        ".".join(
            [rules["package"], *p.relative_to(source).with_suffix("").parts]
        ).removesuffix(".__init__"): p
        for p in source.rglob("*.py")
    }
    module_edges = {name: set() for name in module_paths}
    for path in sorted(source.rglob("*.py")):
        relative = path.relative_to(source)
        module = ".".join(
            [rules["package"], *relative.with_suffix("").parts]
        ).removesuffix(".__init__")
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
                base = node.module or ""
                imports = [
                    f"{base}.{alias.name}"
                    if f"{base}.{alias.name}" in module_paths
                    else base
                    for alias in node.names
                ]
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
                if target in module_edges:
                    module_edges[module].add(target)
                parts = target.split(".")
                if parts[0] in rules["forbidden_runtime_roots"]:
                    errors.append(
                        f"{relative}:{node.lineno}: forbidden runtime import {target}"
                    )
                if parts[0] != rules["package"] or domain is None:
                    continue
                dependency = parts[1] if len(parts) > 1 else None
                if dependency == domain:
                    layers = domains[domain].get("layers")
                    if layers:
                        layer = relative.parts[1] if len(relative.parts) > 2 else "."
                        target_layer = (
                            parts[2] if len(parts) > 2 and parts[2] in layers else "."
                        )
                        if target_layer != layer and target_layer not in layers.get(
                            layer, []
                        ):
                            errors.append(
                                f"{relative}:{node.lineno}: forbidden submodule dependency "
                                f"{domain}.{layer} -> {domain}.{target_layer}"
                            )
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
        specification = domains[domain]
        layers = specification.get("layers")
        if layers:
            actual_layers = {
                p.name
                for p in (source / domain).iterdir()
                if p.is_dir() and not p.name.startswith("_")
            }
            if actual_layers != set(layers) - {"."}:
                errors.append(f"{domain}: submodule inventory mismatch")
            for layer in set(layers) - {"."}:
                if not (source / domain / layer / "__init__.py").is_file():
                    errors.append(f"{domain}.{layer}: missing package marker")
            if "root_modules" in specification:
                actual_roots = {p.stem for p in (source / domain).glob("*.py")}
                if actual_roots != set(specification["root_modules"]):
                    errors.append(f"{domain}: unclassified root modules")
            errors.extend(_cycles(layers, f"Submodule dependency in {domain}"))

    errors.extend(_cycles(module_edges, "Module dependency"))

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


def _cycles(graph: dict, label: str) -> list[str]:
    """Check declared sublayers and actual imports, including function-local imports."""
    errors, visited, stack = [], set(), []

    def visit(node):
        if node in stack:
            cycle = " -> ".join([*stack[stack.index(node) :], node])
            errors.append(f"{label} cycle: {cycle}")
            return
        if node in visited:
            return
        stack.append(node)
        for target in sorted(graph[node]):
            if target not in graph:
                errors.append(f"{label}: unknown dependency {target}")
            else:
                visit(target)
        stack.pop()
        visited.add(node)

    for node in sorted(graph):
        visit(node)
    return errors


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
