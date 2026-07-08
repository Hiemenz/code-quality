"""Architecture conformance: config-driven import-direction check across
named layers.

Entirely opt-in and does nothing unless `[architecture] layers` is
declared in `.codequality.toml`/`.codequality.json` (see `config.py`'s
`DEFAULT_CONFIG` and README) -- this tool never assumes a repo's layering.
A layer is a name plus a list of dotted Python module prefixes it owns,
e.g.:

    [[architecture.layers]]
    name = "api"
    modules = ["myapp.api"]

    [[architecture.layers]]
    name = "service"
    modules = ["myapp.service"]

    [[architecture.layers]]
    name = "data"
    modules = ["myapp.data", "myapp.models"]

The declared *order* is the rule: a layer may import from itself or from
any layer declared *after* it, never one declared before it. Above, `api`
may import `service`/`data`, `service` may import `data`, but `data`
importing anything from `service`/`api` is a violation, and so is
`service` importing `api`.

Deliberately does not try to resolve imports to actual files on disk --
that would need real package-resolution logic (`sys.path`, namespace
packages, editable installs, ...). Instead, both a file's own layer and
each import's layer are decided purely by dotted-name prefix matching
(a file at `myapp/service/orders.py` is module `myapp.service.orders`;
an `import myapp.data.models` statement is checked against that same
`myapp.data` prefix) -- the same "structural pattern match, not real
understanding" tradeoff every check in this tool makes. Only absolute
imports are resolvable this way; a relative import (`from . import x`)
has no dotted name to check against configured layer modules, so it's
silently skipped rather than guessed at. A file whose own module name
doesn't match any configured layer is skipped entirely -- this check only
ever judges files that were explicitly placed into a layer.
"""

import ast
import os

from codequality.analyzers.base import Issue
from codequality.scanner import discover_files

SYMBOL = "layering-violation"


def _module_name_for_path(rel_path):
    """Dotted module name for a Python file's relative path, e.g.
    'myapp/service/orders.py' -> 'myapp.service.orders', and
    'myapp/service/__init__.py' -> 'myapp.service'.
    """
    without_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    parts = without_ext.split(os.sep)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _layer_index_for_module(dotted, layers):
    """Index into `layers` of the first layer whose module prefix matches
    `dotted` at a dotted-component boundary, else None.
    """
    for i, layer in enumerate(layers):
        for prefix in layer.get("modules", []):
            if dotted == prefix or dotted.startswith(prefix + "."):
                return i
    return None


def _imported_module_names(node):
    """Every absolute dotted module name a single Import/ImportFrom node
    references. A relative import has no dotted name to resolve, so it
    contributes nothing.
    """
    if isinstance(node, ast.Import):
        return [alias.name for alias in node.names]
    if isinstance(node, ast.ImportFrom):
        if node.level or not node.module:
            return []
        return [node.module]
    return []


def _violation_issue(rel_path, lineno, file_layer, imported, imported_layer, layers):
    return Issue(
        rel_path, lineno, "structure", "warn", SYMBOL,
        f"'{layers[file_layer]['name']}' layer imports '{imported}' (layer "
        f"'{layers[imported_layer]['name']}'), which is declared earlier -- a layer may only import "
        f"itself or a later layer",
    )


def _file_violations(root, rel_path, layers):
    dotted = _module_name_for_path(rel_path)
    file_layer = _layer_index_for_module(dotted, layers)
    if file_layer is None:
        return []

    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            source = f.read()
    except OSError:
        return []
    try:
        tree = ast.parse(source, filename=full)
    except SyntaxError:
        return []

    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        for imported in _imported_module_names(node):
            imported_layer = _layer_index_for_module(imported, layers)
            if imported_layer is not None and imported_layer < file_layer:
                issues.append(_violation_issue(rel_path, node.lineno, file_layer, imported, imported_layer, layers))
    return issues


def check(root, config):
    """Runs the architecture conformance check against `root`. Returns []
    (never raises) if `config.architecture_layers` is empty.
    """
    layers = config.architecture_layers
    if not layers:
        return []
    issues = []
    for rel_path, _lang in discover_files(root, config.exclude, include_generic=False):
        issues.extend(_file_violations(root, rel_path, layers))
    return issues


def render_text(issues):
    if not issues:
        return "Architecture Conformance Check\n\nNo issues found."
    lines = [f"Architecture Conformance Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.symbol}] {issue.message}")
    return "\n".join(lines)
