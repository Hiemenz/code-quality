"""Per-function cyclomatic complexity trend over time.

`codequality complexity-trend snapshot` runs a normal scan and appends one
JSON line to a snapshot file, recording every function's current
cyclomatic complexity (the same number `scorer.py` already computes per
function -- see `codequality/analyzers/base.py`'s `FunctionMetrics.complexity`),
keyed by `path::function_name`, plus a timestamp and the current git commit
sha. `codequality complexity-trend show` reads that file back and reports,
per function present in both the earliest and the most recent snapshot,
how its complexity changed -- sorted by biggest increase first, since
that's the actionable list: "these functions have been quietly getting
more complex."

Deliberately self-contained: its own module, its own JSONL snapshot file,
its own subcommand. It does not touch `history.py`/`report.py`/`trend` --
those back the unrelated overall-score-history feature (`scan
--record-history` / `codequality trend`), which is a completely separate
timeline from complexity-per-function.
"""

import json
from datetime import datetime, timezone

from codequality.git_utils import GitError, _run
from codequality.scanner import scan_repo


def _qualified_name(file_metrics, func):
    return f"{file_metrics.path}::{func.name}"


def _git_sha(root):
    try:
        return _run(["rev-parse", "HEAD"], root).strip()
    except GitError:
        return None


def snapshot(root, config):
    """Run a scan-equivalent pass and build one snapshot entry:
    timestamp, git commit sha, and {qualified_name: complexity} for every
    function found.
    """
    file_metrics_list = scan_repo(root, config)
    functions = {}
    for fm in file_metrics_list:
        for func in fm.functions:
            functions[_qualified_name(fm, func)] = func.complexity
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "commit": _git_sha(root),
        "functions": functions,
    }


def append_snapshot(path, entry):
    """Append one snapshot entry (from `snapshot()`) as a JSON line."""
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, sort_keys=False) + "\n")
    return entry


def read_snapshots(path):
    """Every snapshot entry recorded in `path`, in file order (use
    `diff_report` to compare oldest vs. newest -- it sorts by timestamp
    itself rather than trusting file order).
    """
    snapshots = []
    with open(path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                snapshots.append(json.loads(line))
    return snapshots


def diff_report(snapshots, top_n=25):
    """Compare the earliest and the most recent snapshot (by `timestamp`,
    not file order) and report every function present in both, sorted by
    biggest complexity increase first.

    Returns a list of {name, first_complexity, last_complexity, delta}.
    A function that only appears in one of the two snapshots is excluded
    -- there's nothing to compute a delta from. Fewer than two snapshots
    produces an empty report rather than an error.
    """
    if len(snapshots) < 2:
        return []
    ordered = sorted(snapshots, key=lambda s: s["timestamp"])
    first, last = ordered[0], ordered[-1]
    rows = []
    for name, last_complexity in last["functions"].items():
        if name not in first["functions"]:
            continue
        first_complexity = first["functions"][name]
        rows.append({
            "name": name,
            "first_complexity": first_complexity,
            "last_complexity": last_complexity,
            "delta": last_complexity - first_complexity,
        })
    rows.sort(key=lambda r: r["delta"], reverse=True)
    return rows[:top_n]


def render_text(rows):
    """Render `diff_report()`'s rows as a plain-text table."""
    if not rows:
        return "No functions found in both the earliest and latest snapshot yet (need at least 2 snapshots)."
    lines = ["Complexity Trend (earliest -> latest snapshot)", ""]
    lines.append(f"  {'Function':<64}{'First':>7}{'Last':>7}{'Delta':>8}")
    for r in rows:
        lines.append(
            f"  {r['name']:<64}{r['first_complexity']:>7}{r['last_complexity']:>7}{r['delta']:>+8}"
        )
    return "\n".join(lines)
