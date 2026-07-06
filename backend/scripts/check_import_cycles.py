from __future__ import annotations

import ast
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1] / "app"
PACKAGE = "app"


def module_name(path: Path) -> str:
    relative = path.relative_to(ROOT).with_suffix("")
    parts = [PACKAGE, *relative.parts]
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def imported_app_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text())
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == PACKAGE or alias.name.startswith(f"{PACKAGE}."):
                    imports.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and (node.module == PACKAGE or node.module.startswith(f"{PACKAGE}.")):
                imports.add(node.module)
    return imports


def collapse_to_known_module(name: str, known: set[str]) -> str | None:
    current = name
    while current:
        if current in known:
            return current
        if "." not in current:
            return None
        current = current.rsplit(".", 1)[0]
    return None


def build_graph() -> dict[str, set[str]]:
    files = [path for path in ROOT.rglob("*.py") if "__pycache__" not in path.parts]
    known = {module_name(path) for path in files}
    graph: dict[str, set[str]] = {module_name(path): set() for path in files}
    for path in files:
        source = module_name(path)
        for imported in imported_app_modules(path):
            target = collapse_to_known_module(imported, known)
            if target and target != source:
                graph[source].add(target)
    return graph


def find_cycles(graph: dict[str, set[str]]) -> list[list[str]]:
    cycles: list[list[str]] = []
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            index = stack.index(node)
            cycles.append([*stack[index:], node])
            return
        visiting.add(node)
        stack.append(node)
        for target in sorted(graph.get(node, ())):
            visit(target)
        stack.pop()
        visiting.remove(node)
        visited.add(node)

    for node in sorted(graph):
        visit(node)
    return cycles


def main() -> int:
    graph = build_graph()
    cycles = find_cycles(graph)
    if cycles:
        print("Import cycles detected:")
        for cycle in cycles:
            print(" -> ".join(cycle))
        return 1
    print(f"No import cycles detected across {len(graph)} app modules.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
