"""Impact-weighted dependency risk: usage count x structural risk flags.

The original ask behind this feature was "which dependencies are both
outdated and heavily imported" -- true staleness (installed version vs.
latest available) needs a package-registry lookup (PyPI/npm), which this
tool categorically refuses to make (see the top of README.md and
`codequality/dependency_check.py`'s module docstring: no network access,
ever). This is **not** that. It's a narrower, honest question built only
from signals this tool can already compute offline:

- **usage_count** -- how many times each declared dependency is actually
  imported across the codebase's Python files, a proxy for "how much of
  the codebase would be affected if this package broke or needed
  migrating." Computed with a real `ast` walk per file (`Import`/
  `ImportFrom` nodes), matched on the *top-level* module name -- e.g. both
  `import requests.sessions` and `from requests.auth import
  HTTPBasicAuth` count as one use of `requests`. Matching a declared
  package name to the module name Python code actually imports is done
  with a simple, deterministic normalization (lowercase, `-`/`.`
  collapsed to `_`) -- the same "no cleverness, just reproducibility"
  tradeoff every heuristic in this tool makes. This does **not** handle
  packages whose PyPI distribution name differs from their import name
  (`PyYAML` -> `yaml`, `beautifulsoup4` -> `bs4`, `python-dateutil` ->
  `dateutil`, ...); resolving that in general would require either a
  hardcoded alias table (which goes stale) or querying installed-package
  metadata for the *current* environment (which silently produces
  different answers depending on what's installed where `codequality`
  happens to run) -- both are worse than the plain, honest limitation
  documented here. Non-Python usage counting (JS `require`/`import`, Go,
  Ruby, ...) is out of scope for this first version entirely, rather than
  half-implemented via a per-ecosystem regex guess -- so this only ever
  meaningfully scores `pip`-ecosystem packages; npm-declared packages
  always report `usage_count = 0`.
- **structural risk flags** -- reuses `dependency_check.check()`'s own
  findings (`inconsistent-pinning`, `duplicate-dependency`,
  `unpinned-in-lockfile-repo`) directly rather than re-deriving them; this
  module never reimplements those rules, only reads their output back.

`risk_score` is `usage_count` if the package has at least one structural
issue from `dependency_check`, else `0`: heavy use of a *correctly*
pinned/consistent dependency isn't the problem this feature targets --
only heavy use *combined with* an existing structural inconsistency is.
Results are sorted by `risk_score` descending (ties broken by
`usage_count` descending, then package name, for determinism) -- the top
of the list is the highest-priority dependency to fix first.

This is explicitly **not** a vulnerability or staleness scanner, same as
`dependency-check` itself: it never asks a registry what the latest
version is, so it can't tell you a pin is old or has a CVE.
"""

import ast
import os
from collections import defaultdict

from codequality import dependency_check
from codequality.scanner import discover_files


def _ecosystem(dep):
    return "npm" if dep.manifest.endswith(".json") else "pip"


def _module_form(package_name):
    """Best-effort mapping from a declared package name to the module name
    Python code would import it as: lowercase, `-`/`.` collapsed to `_`.
    Correct for the common case (e.g. `requests`, `click`, `flask-cors` ->
    `flask_cors`); wrong for the well-known PyPI-name-vs-import-name
    mismatches (`PyYAML` -> `yaml`, ...) -- see module docstring.
    """
    return package_name.strip().lower().replace("-", "_").replace(".", "_")


def _read_source(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _top_level_module(dotted_name):
    return dotted_name.split(".", 1)[0]


class _ImportVisitor(ast.NodeVisitor):
    """Collects the top-level module name of every `import x[.y]`/`from
    x[.y] import z` in one file's AST.
    """

    def __init__(self):
        self.modules = []

    def visit_Import(self, node):
        for alias in node.names:
            self.modules.append(_top_level_module(alias.name))
        self.generic_visit(node)

    def visit_ImportFrom(self, node):
        # node.level > 0 is a relative import (`from . import x`/`from ..pkg import y`)
        # -- no external module name to count.
        if node.module and node.level == 0:
            self.modules.append(_top_level_module(node.module))
        self.generic_visit(node)


def count_python_imports(root, config=None):
    """{lowercased_top_level_module_name: usage_count} across every scanned
    Python file. A file that fails to parse is skipped, not a crash --
    same convention every other `ast`-based check in this tool follows.
    """
    counts = defaultdict(int)
    exclude = config.exclude if config is not None else []
    files = discover_files(root, exclude, include_generic=False)
    for rel_path, lang in files:
        if lang != "python":
            continue
        source = _read_source(root, rel_path)
        if source is None:
            continue
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        visitor = _ImportVisitor()
        visitor.visit(tree)
        for mod in visitor.modules:
            counts[mod.lower()] += 1
    return counts


def _group_declared_packages(deps_by_section):
    """{(ecosystem, normalized_name): {"raw_names": {str, ...}}} across
    every manifest section `dependency_check.parse_manifests()` found.
    """
    packages = {}
    for deps in deps_by_section.values():
        for d in deps:
            key = (_ecosystem(d), d.name)
            entry = packages.setdefault(key, {"raw_names": set()})
            entry["raw_names"].add(d.raw_name)
    return packages


def _match_issue_to_package(issue, deps_by_section):
    """Attributes one `dependency_check.check()` Issue back to the
    declared package it names.

    `Issue` is a shape shared by every check in this tool (file, line,
    category, severity, symbol, message) and carries no structured
    package-name field of its own. But every one of `dependency_check`'s
    three structural-issue messages is built starting with the exact
    declared `raw_name` followed by a space (see `_pinning_issue`/
    `_duplicate_issue`/`_check_unpinned_in_lockfile_repo` in
    `dependency_check.py`) -- so the declared dependency, in the same
    manifest file, whose `raw_name` is that leading prefix is the one the
    issue is about. Package names never contain spaces, so this is
    unambiguous. Returns None if no declared dependency in that manifest
    matches (shouldn't happen for an issue `dependency_check.check()`
    itself produced from the same manifests, but never raises either way).
    """
    for deps in deps_by_section.values():
        for d in deps:
            if d.manifest == issue.file and issue.message.startswith(d.raw_name + " "):
                return (_ecosystem(d), d.name)
    return None


def compute(root, config=None):
    """Runs `dependency_check`'s own structural checks plus a Python-import
    usage count and combines them into one impact-weighted, sorted list:

        [{"package": str, "ecosystem": "pip"|"npm", "usage_count": int,
          "issue_types": [str, ...], "risk_score": int}, ...]

    sorted by `risk_score` descending (ties: `usage_count` descending, then
    `package` for determinism). Returns [] if no manifest is found (same
    as `dependency_check.check()`).
    """
    issues = dependency_check.check(root)
    deps_by_section = dependency_check.parse_manifests(root)
    import_counts = count_python_imports(root, config)

    packages = _group_declared_packages(deps_by_section)
    issue_symbols = defaultdict(set)
    for issue in issues:
        key = _match_issue_to_package(issue, deps_by_section)
        if key is not None:
            issue_symbols[key].add(issue.symbol)

    rows = []
    for (ecosystem, name), entry in packages.items():
        display_name = sorted(entry["raw_names"])[0]
        usage_count = import_counts.get(_module_form(display_name), 0) if ecosystem == "pip" else 0
        symbols = sorted(issue_symbols.get((ecosystem, name), ()))
        risk_score = usage_count if symbols else 0
        rows.append({
            "package": display_name,
            "ecosystem": ecosystem,
            "usage_count": usage_count,
            "issue_types": symbols,
            "risk_score": risk_score,
        })

    rows.sort(key=lambda r: (-r["risk_score"], -r["usage_count"], r["package"]))
    return rows


def render_text(rows, top_n=25):
    """Rank / package / ecosystem / usage count / issue types / risk score
    table for the top `top_n` rows from `compute()`.

    NOT a staleness or CVE detector -- see module docstring. `risk_score`
    is 0 for any package without a `dependency_check` structural issue,
    regardless of how heavily it's imported.
    """
    shown = rows[:top_n]
    if not shown:
        return "Dependency Risk (usage count x structural risk flags -- not staleness/CVE detection)\n\nNo declared dependencies found."
    lines = [
        "Dependency Risk (usage count x structural risk flags -- not staleness/CVE detection)",
        "",
        f"  {'#':>4}  {'Package':<30}{'Ecosystem':>11}{'Usage':>8}  {'Issue types':<50}{'Risk':>6}",
    ]
    for i, r in enumerate(shown, start=1):
        issue_types = ", ".join(r["issue_types"]) if r["issue_types"] else "-"
        lines.append(
            f"  {i:>4}  {r['package']:<30}{r['ecosystem']:>11}{r['usage_count']:>8}  "
            f"{issue_types:<50}{r['risk_score']:>6}"
        )
    return "\n".join(lines)
