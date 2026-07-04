"""Wraps `mypy` for a real type-checking pass, via its Python API rather
than shelling out -- this way it runs in the exact same environment/venv
codequality itself is installed in, which matters because meaningful type
checking requires the target repo's own dependencies to be importable.

Optional (`pip install codequality[types]`) so the base install doesn't
pull in mypy. Opt-in via --check-types for the same reason --check-imports
is opt-in: the result depends on this environment and on type annotations
existing at all, not on parsing the source alone -- see README.

Unlike the per-file analyzers, mypy needs to see the whole project at once
to do real cross-file type inference, so this runs once over the repo root
rather than file-by-file; `scanner.py` distributes the resulting issues
back onto each file's FileMetrics afterward.
"""

import os
import re

from codequality.analyzers.base import Issue

try:
    from mypy import api as _mypy_api
    AVAILABLE = True
except ImportError:  # pragma: no cover - exercised when the extra isn't installed
    _mypy_api = None
    AVAILABLE = False

_LINE_RE = re.compile(
    r"^(?P<file>.+?):(?P<line>\d+):(?:\d+:)?\s*(?P<severity>error|warning|note):\s*"
    r"(?P<message>.*?)\s*(?:\[(?P<code>[\w-]+)\])?$"
)

_SEVERITY_MAP = {"error": "error", "warning": "warn", "note": "info"}


def _parse_line(line, root):
    m = _LINE_RE.match(line)
    if not m:
        return None
    rel_path = os.path.relpath(m.group("file"), root)
    severity = _SEVERITY_MAP.get(m.group("severity"), "info")
    symbol = m.group("code") or "type-error"
    return Issue(rel_path, int(m.group("line")), "correctness", severity, symbol, m.group("message"))


def run(root):
    """Run mypy once over `root`. Returns dict[relative_path] -> list[Issue]."""
    if not AVAILABLE:
        return {}
    stdout, _stderr, _exit_status = _mypy_api.run([
        root, "--ignore-missing-imports", "--no-error-summary", "--no-color-output", "--show-absolute-path",
        # codequality runs mypy fresh, often against different repos/paths
        # from the same process; mypy's incremental cache keys on module
        # name, not full path, and can otherwise mix up results across
        # unrelated directories that happen to share a filename.
        "--no-incremental", "--cache-dir=/dev/null",
    ])
    by_file = {}
    for line in stdout.splitlines():
        issue = _parse_line(line, root)
        if issue is not None:
            by_file.setdefault(issue.file, []).append(issue)
    return by_file
