"""Cross-file circular-import detection: builds a module import graph over
every scanned Python file (`ast`-parsed, absolute *and* relative imports
resolved to repo-relative module paths) and flags any cycle in it.

Only internal imports matter here -- an `import os` or `import requests`
simply won't resolve to any file in the scanned set, so it's naturally
skipped without needing a stdlib/third-party denylist.
"""

import ast
import os

from codequality.analyzers.base import Issue


def _module_name_for_path(rel_path):
    """codequality/analyzers/foo.py -> codequality.analyzers.foo
    codequality/analyzers/__init__.py -> codequality.analyzers (a package's
    own dotted name, same as its `__package__`).
    """
    parts = rel_path.replace(os.sep, "/").split("/")
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    elif parts[-1].endswith(".py"):
        parts[-1] = parts[-1][: -len(".py")]
    return ".".join(p for p in parts if p)


def _base_package(rel_path, module_name):
    """The dotted package a file's relative imports are resolved against --
    equivalent to that module's `__package__` at runtime.
    """
    basename = os.path.basename(rel_path.replace(os.sep, "/"))
    if basename == "__init__.py":
        return module_name
    return module_name.rsplit(".", 1)[0] if "." in module_name else ""


def _resolve_relative_base(base_package, module, level):
    """Mirrors `importlib._bootstrap._resolve_name`: turn a relative
    `from`-import's (module, level) into an absolute dotted base, relative
    to the importing file's own package. Returns None if the import climbs
    above the top-level package (nothing sane to resolve to).
    """
    bits = base_package.rsplit(".", level - 1)
    if len(bits) < level:
        return None
    base = bits[0]
    if module:
        return f"{base}.{module}" if base else module
    return base or None


def _candidates_for_import_from(node, base_package):
    """Every dotted module name that `from X import a, b` could plausibly
    refer to: the imported names as submodules of X (`X.a`, `X.b`), and X
    itself (covers `from module import name_defined_in_module`).
    """
    if node.level > 0:
        resolved_base = _resolve_relative_base(base_package, node.module, node.level)
    else:
        resolved_base = node.module
    if not resolved_base:
        return set()
    candidates = {resolved_base}
    for alias in node.names:
        if alias.name != "*":
            candidates.add(f"{resolved_base}.{alias.name}")
    return candidates


def _imported_module_candidates(tree, base_package):
    modules = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
        elif isinstance(node, ast.ImportFrom):
            modules.update(_candidates_for_import_from(node, base_package))
    return modules


def build_import_graph(file_sources):
    """file_sources: dict[rel_path] -> source text, for every scanned Python
    file. Returns dict[rel_path] -> set of other rel_paths (from the same
    set) it directly imports. Anything that doesn't resolve to a file in
    `file_sources` (stdlib, third-party, or just not part of this scan) is
    silently skipped -- that's the point, only internal cycles are in scope.
    """
    module_to_path = {_module_name_for_path(rel_path): rel_path for rel_path in file_sources}

    graph = {rel_path: set() for rel_path in file_sources}
    for rel_path, source in file_sources.items():
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            continue
        base_package = _base_package(rel_path, _module_name_for_path(rel_path))
        for candidate in _imported_module_candidates(tree, base_package):
            target = module_to_path.get(candidate)
            if target is not None:
                graph[rel_path].add(target)
    return graph


def find_cycles(graph):
    """Three-color DFS cycle detection. Returns a list of cycles, each a
    list of rel_paths in traversal order (the cycle implicitly closes back
    from the last element to the first). Each distinct set of nodes is
    reported at most once, even if multiple back-edges within one DFS walk
    would otherwise surface it repeatedly.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {node: WHITE for node in graph}
    path = []
    path_set = set()
    cycles = []
    seen_node_sets = set()

    def visit(node):
        color[node] = GRAY
        path.append(node)
        path_set.add(node)
        for neighbor in sorted(graph.get(node, ())):
            if color.get(neighbor, WHITE) == WHITE:
                visit(neighbor)
            elif neighbor in path_set:
                idx = path.index(neighbor)
                cycle = path[idx:]
                key = frozenset(cycle)
                if key not in seen_node_sets:
                    seen_node_sets.add(key)
                    cycles.append(list(cycle))
        path.pop()
        path_set.discard(node)
        color[node] = BLACK

    for node in sorted(graph):
        if color[node] == WHITE:
            visit(node)
    return cycles


def _canonical_cycle(cycle):
    """Rotate the cycle to start from its lexicographically-first file, so
    the same logical cycle always prints identically regardless of which
    node the DFS happened to start from.
    """
    start = cycle.index(min(cycle))
    return cycle[start:] + cycle[:start]


def _cycle_message(cycle):
    canonical = _canonical_cycle(cycle)
    chain = canonical + [canonical[0]]
    return "Circular import: " + " -> ".join(chain)


def circular_import_issues(file_sources):
    """file_sources: dict[rel_path] -> source text, for every scanned Python
    file. Returns a list[Issue], one per file participating in a cycle. All
    issues for the same cycle carry an identical, normalized message (see
    `_canonical_cycle`) -- a 3-file cycle is one logical finding, not three.
    """
    graph = build_import_graph(file_sources)
    issues = []
    for cycle in find_cycles(graph):
        message = _cycle_message(cycle)
        for rel_path in cycle:
            issues.append(Issue(rel_path, 1, "structure", "warn", "circular-import", message))
    return issues
