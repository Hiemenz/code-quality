"""Baseline mode: let a messy repo turn on CI gating today instead of after
a cleanup sprint.

`codequality baseline` snapshots the current issue count per (file, symbol)
pair. A later `scan --baseline FILE` forgives up to that many issues of
each pair -- so only issues *beyond* what was already there count as new,
and those are what fail the build. Existing debt stays visible in `scan`
without a baseline; it just stops being a blocker.

Forgiveness reuses the exact mechanism inline suppression comments use
(`suppress.py`): the oldest N issues per (file, symbol), ordered by line,
are marked suppressed, both in the printed report and in the score. Which
means a baseline is, mechanically, just a bulk auto-generated suppression
list instead of one written by hand.
"""

import json
from collections import Counter, defaultdict

# Per-function checks that are scored from a continuous metric rather than
# purely from the issues list -- same set scorer.py consults via
# `suppress.is_suppressed`. Forgiving one of these needs to also update the
# function's `.suppressed` set, not just remove the issue from the report.
_METRIC_SYMBOLS = {"high-complexity", "long-function", "deep-nesting", "missing-docstring"}


def _key(file, symbol):
    return f"{file}::{symbol}"


def build_counts(file_metrics_list):
    counts = Counter()
    for fm in file_metrics_list:
        for issue in fm.issues:
            counts[_key(issue.file, issue.symbol)] += 1
    return dict(counts)


def save(path, file_metrics_list):
    data = {"version": 1, "counts": build_counts(file_metrics_list)}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, sort_keys=True)
        f.write("\n")


def load(path):
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return data.get("counts", {})


def _forgiven_by_file(file_metrics_list, counts):
    """dict[file] -> set of (line, symbol) forgiven at that file, choosing
    the oldest-by-line issues first for each (file, symbol) pair.
    """
    by_key = defaultdict(list)
    for fm in file_metrics_list:
        for issue in fm.issues:
            by_key[_key(issue.file, issue.symbol)].append(issue)

    forgiven = defaultdict(set)
    for key, allowed in counts.items():
        if allowed <= 0:
            continue
        issues = sorted(by_key.get(key, []), key=lambda i: i.line)
        for issue in issues[:allowed]:
            forgiven[issue.file].add((issue.line, issue.symbol))
    return forgiven


def _apply_to_file(fm, file_forgiven):
    kept = [i for i in fm.issues if (i.line, i.symbol) not in file_forgiven]
    fm.suppressed_count += len(fm.issues) - len(kept)
    fm.issues = kept
    for fn in fm.functions:
        extra = {sym for (line, sym) in file_forgiven if line == fn.lineno and sym in _METRIC_SYMBOLS}
        if extra:
            fn.suppressed = fn.suppressed | extra


def apply(file_metrics_list, counts):
    """Forgive issues already recorded in `counts` (as returned by `load`),
    in place, across `file_metrics_list`.
    """
    if not counts:
        return
    forgiven = _forgiven_by_file(file_metrics_list, counts)
    for fm in file_metrics_list:
        file_forgiven = forgiven.get(fm.path)
        if file_forgiven:
            _apply_to_file(fm, file_forgiven)
