"""Unused dependency check: packages listed in requirements.txt / pyproject.toml
that are never imported anywhere in the Python source files.

This is a full-scan-only check (like dead-code and internal-refs): a diff has
no view of every other file's imports, so it cannot determine whether a package
is "unused" across the whole repo.

Package-name → import-name matching uses PEP-503 normalisation plus a small
hardcoded alias table for the most common mismatches (Pillow→PIL, pyyaml→yaml,
etc.). Packages not in the alias table AND whose install name differs from the
import name will produce false positives; the check is `info` severity for this
reason. CLI/tool-only packages (black, flake8, mypy, ...) that are never
directly imported in source code are excluded automatically.

Findings are attached to the requirements/pyproject file that declared the
unused package.
"""

import ast
import os
import re

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None

from codequality.analyzers.base import FileMetrics, Issue

# PEP 503 canonical normalisation: collapse any run of [-_.] → single '-', lowercase.
_NORM_RE = re.compile(r"[-_.]+")


def _normalize(name):
    return _NORM_RE.sub("-", name).lower()


# Known install-name → import-name mismatches.
_ALIASES = {
    "pillow": "pil",
    "pyyaml": "yaml",
    "python-dateutil": "dateutil",
    "scikit-learn": "sklearn",
    "scikit-image": "skimage",
    "beautifulsoup4": "bs4",
    "opencv-python": "cv2",
    "opencv-python-headless": "cv2",
    "opencv-contrib-python": "cv2",
    "typing-extensions": "typing_extensions",
    "python-dotenv": "dotenv",
    "attrs": "attr",
    "psycopg2-binary": "psycopg2",
    "mysqlclient": "mysqldb",
    "mysql-connector-python": "mysql",
    "google-cloud-storage": "google",
    "google-auth": "google",
    "google-api-python-client": "googleapiclient",
    "azure-storage-blob": "azure",
    "python-multipart": "multipart",
    "email-validator": "email_validator",
    "python-jose": "jose",
    "python-magic": "magic",
}

# Packages that are CLI / build tools and are never `import`ed in source code.
_TOOL_PACKAGES = frozenset(_normalize(n) for n in {
    "black", "isort", "autopep8", "yapf", "ruff",
    "flake8", "pylint", "pycodestyle", "pydocstyle", "mypy", "pyright", "pyflakes",
    "bandit", "safety",
    "build", "wheel", "twine", "setuptools", "pip", "pip-tools",
    "pre-commit", "tox", "nox", "commitizen",
    "pytest-cov", "pytest-mock", "pytest-asyncio", "pytest-xdist",
    "pytest-timeout", "pytest-randomly", "pytest-benchmark",
    "codequality",
})


def _parse_requirements_file(text):
    """(lineno, raw_name) pairs from a requirements.txt-format file."""
    entries = []
    for i, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("-") or "://" in line:
            continue
        line = line.split("#")[0].strip()   # strip inline comment
        line = line.split(";")[0].strip()   # strip env markers
        name = re.split(r"[\[><=!~^@ ]", line)[0].strip()
        if name:
            entries.append((i, name))
    return entries


def _parse_pyproject(path):
    """(1, raw_name) pairs from [project].dependencies + optional-dependencies."""
    if tomllib is None:
        return []
    try:
        with open(path, "rb") as f:
            data = tomllib.load(f)
    except Exception:
        return []
    entries = []
    project = data.get("project", {})
    for dep_str in project.get("dependencies", []):
        name = re.split(r"[\[><=!~^@ ;]", dep_str.strip())[0].strip()
        if name:
            entries.append((1, name))
    for group in project.get("optional-dependencies", {}).values():
        for dep_str in (group or []):
            name = re.split(r"[\[><=!~^@ ;]", dep_str.strip())[0].strip()
            if name:
                entries.append((1, name))
    return entries


def _collect_imported_modules(file_sources):
    """Set of lowercased top-level module names imported anywhere in file_sources."""
    modules = set()
    for source in file_sources.values():
        try:
            tree = ast.parse(source)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    modules.add(alias.name.split(".")[0].lower())
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.level == 0:
                    modules.add(node.module.split(".")[0].lower())
    return modules


def _is_imported(pkg_name, imported_lower):
    norm = _normalize(pkg_name)
    if norm in _TOOL_PACKAGES:
        return True  # treat as "used" so it's never flagged
    alias = _ALIASES.get(norm)
    if alias is not None:
        return alias.lower() in imported_lower
    return norm.replace("-", "_") in imported_lower


def unused_dependency_issues(root, file_sources):
    """Return {rel_path: [Issue, ...]} for every requirements/pyproject file
    that lists packages never imported in `file_sources`.

    `file_sources` is the same {rel_path: source} map dead_code uses --
    every Python file's source, keyed by its repo-relative path.
    """
    imported = _collect_imported_modules(file_sources)
    results = {}

    def _check(req_rel, entries):
        issues = []
        for lineno, pkg_name in entries:
            if not _is_imported(pkg_name, imported):
                issues.append(Issue(
                    req_rel, lineno, "correctness", "info", "unused-dependency",
                    f"'{pkg_name}' is listed as a dependency but never imported "
                    f"(if the import name differs from the package name this may be a false positive)"
                ))
        if issues:
            results[req_rel] = issues

    # Scan requirements*.txt files at root and in a requirements/ subdirectory.
    _SKIP_DIRS = {"node_modules", "venv", ".venv", "env", "__pycache__", ".git"}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if not (fn == "requirements.txt" or
                    (fn.startswith("requirements") and fn.endswith(".txt"))):
                continue
            full = os.path.join(dirpath, fn)
            rel = os.path.relpath(full, root)
            try:
                with open(full, "r", encoding="utf-8", errors="replace") as f:
                    text = f.read()
            except OSError:
                continue
            _check(rel, _parse_requirements_file(text))

    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject):
        _check("pyproject.toml", _parse_pyproject(pyproject))

    return results
