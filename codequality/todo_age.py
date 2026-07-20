"""TODO/FIXME aging: how long has each marker been sitting in the repo,
and did an AI-assisted or human commit introduce it?

The style check (see `analyzers/generic_analyzer.py`/`analyzers/
python_analyzer.py`, symbol `todo-marker`) flags every TODO/FIXME/XXX/HACK
line as a point-in-time snapshot -- it has no notion of *when* a marker
landed or whether it has been sitting there for three years. `codequality
todo-age` adds that missing time dimension: for every marker line in the
repo, `git blame` finds the commit that introduced that exact line (same
technique as `edit_distance.py`'s `_blame_counts`/`hallucination_metrics.
py`'s `_blame_shas`), then that commit's author date and AI-assisted/human
classification (same marker-substring convention as `churn.py`, default
`"Co-Authored-By: Claude"`) tell you how old the marker is and who left it
there.

Anything older than `stale_days` (default 90) is flagged stale. Results
are reported grouped by ai/human, the same two-group shape as `churn.py`/
`edit_distance.py`/`commit_lint.py`, so you can compare whether
AI-introduced TODOs linger longer than human ones or get cleaned up
sooner.

The regex is imported from `generic_analyzer` (a superset of
`python_analyzer`'s -- it matches `#`, `//`, and `/*` comment openers
rather than just `#`) so every scanned file is checked with one shared
pattern instead of re-implementing per-language detection here.

The blame-based line attribution is intentionally a small, local duplicate
of the same idea in `edit_distance.py`/`hallucination_metrics.py` rather
than a shared import -- each of those modules answers a different
question and is kept decoupled on purpose (see their module docstrings).
"""

import os
import re
from datetime import datetime, timezone

from codequality.analyzers.generic_analyzer import TODO_RE
from codequality.git_utils import GitError, _run
from codequality.scanner import discover_files

DEFAULT_MARKER = "Co-Authored-By: Claude"
DEFAULT_STALE_DAYS = 90
DEFAULT_MAX_LISTING = 25

_FS = "\x1f"  # field separator

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def _blame_shas(cwd, rel_path):
    """List of commit shas, one per line of `rel_path` at HEAD (index 0 is
    line 1). Returns [] for paths git can't blame at HEAD (untracked, or
    deleted since).
    """
    try:
        raw = _run(["blame", "-w", "--line-porcelain", "HEAD", "--", rel_path], cwd)
    except GitError:
        return []
    shas = []
    current_sha = None
    for line in raw.splitlines():
        m = _BLAME_HEADER_RE.match(line)
        if m:
            current_sha = m.group(1)
        elif line.startswith("\t"):
            shas.append(current_sha)
    return shas


def _commit_metadata(cwd, sha, marker, cache):
    """Author date + AI-marker classification for `sha`, cached across
    calls so a commit that introduced several TODO lines is only looked
    up once.
    """
    if sha not in cache:
        try:
            raw = _run(["show", "-s", f"--format=%aI{_FS}%B", sha], cwd)
        except GitError:
            cache[sha] = None
        else:
            date_str, _, body = raw.partition(_FS)
            cache[sha] = {
                "date": datetime.fromisoformat(date_str.strip().replace("Z", "+00:00")),
                "is_ai": marker.lower() in body.lower(),
            }
    return cache[sha]


def _read_lines(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except OSError:
        return None


def _todo_entry(rel_path, lineno, raw_line, sha, meta, now, stale_days):
    age_days = (now - meta["date"]).days
    return {
        "file": rel_path,
        "line": lineno,
        "snippet": raw_line.strip()[:120],
        "sha": sha[:8],
        "commit_date": meta["date"].isoformat(),
        "age_days": age_days,
        "group": "ai" if meta["is_ai"] else "human",
        "stale": age_days > stale_days,
    }


def _file_todos(root, rel_path, marker, stale_days, now, cache):
    """List of todo entries for one file's TODO/FIXME/XXX/HACK lines, or []
    if the file has none / can't be read / can't be blamed.
    """
    lines = _read_lines(root, rel_path)
    if not lines:
        return []
    matches = [(i, line) for i, line in enumerate(lines, start=1) if TODO_RE.search(line.rstrip("\n"))]
    if not matches:
        return []

    shas = _blame_shas(root, rel_path)
    entries = []
    for lineno, raw_line in matches:
        sha = shas[lineno - 1] if lineno - 1 < len(shas) else None
        meta = _commit_metadata(root, sha, marker, cache) if sha else None
        if meta is None:
            continue
        entries.append(_todo_entry(rel_path, lineno, raw_line, sha, meta, now, stale_days))
    return entries


def compute(root, marker=DEFAULT_MARKER, stale_days=DEFAULT_STALE_DAYS, exclude=None):
    """Find every TODO/FIXME/XXX/HACK marker line across scanned files and
    return one dict per marker:

        {"file", "line", "snippet", "sha", "commit_date", "age_days",
         "group" ("ai"/"human"), "stale"}

    Files git can't blame (untracked/uncommitted) or that don't parse
    contribute nothing rather than raising -- a marker that isn't in git
    history yet has no age to report.
    """
    files = discover_files(root, exclude or [], include_generic=True)
    now = datetime.now(timezone.utc)
    cache = {}
    todos = []
    for rel_path, _lang in files:
        todos.extend(_file_todos(root, rel_path, marker, stale_days, now, cache))
    return todos


def _new_group():
    return {"count": 0, "total_age_days": 0, "oldest_age_days": 0, "stale_count": 0}


def summarize(todos):
    """Returns {"ai": {...}, "human": {...}}, each with count/avg_age_days/
    oldest_age_days/stale_count -- avg/oldest are None for an empty group,
    same "empty group reports None, not a crash" convention as churn.py.
    """
    groups = {"ai": _new_group(), "human": _new_group()}
    for t in todos:
        g = groups[t["group"]]
        g["count"] += 1
        g["total_age_days"] += t["age_days"]
        g["oldest_age_days"] = max(g["oldest_age_days"], t["age_days"])
        if t["stale"]:
            g["stale_count"] += 1

    for g in groups.values():
        g["avg_age_days"] = g["total_age_days"] / g["count"] if g["count"] else None
        if g["count"] == 0:
            g["oldest_age_days"] = None
        del g["total_age_days"]
    return groups


def render_text(todos, stale_days=DEFAULT_STALE_DAYS, max_listing=DEFAULT_MAX_LISTING):
    groups = summarize(todos)
    lines = [f"TODO Aging (stale threshold: {stale_days} days)", ""]
    lines.append(f"  {'Group':<14}{'Count':>8}{'AvgAge':>10}{'Oldest':>10}{'Stale':>8}")
    for label, key in (("AI-assisted", "ai"), ("Human", "human")):
        g = groups[key]
        avg = "n/a" if g["avg_age_days"] is None else f"{g['avg_age_days']:.1f}d"
        oldest = "n/a" if g["oldest_age_days"] is None else f"{g['oldest_age_days']}d"
        lines.append(f"  {label:<14}{g['count']:>8}{avg:>10}{oldest:>10}{g['stale_count']:>8}")

    stale = sorted((t for t in todos if t["stale"]), key=lambda t: t["age_days"], reverse=True)
    lines.append("")
    lines.append(f"  Stale TODOs (>{stale_days}d old, showing up to {max_listing})")
    if not stale:
        lines.append("    (none)")
    else:
        for t in stale[:max_listing]:
            lines.append(f"    {t['file']}:{t['line']}  [{t['group']:<5}]  {t['age_days']:>5}d  {t['snippet']}")
        remaining = len(stale) - min(len(stale), max_listing)
        if remaining > 0:
            lines.append(f"    ... and {remaining} more (see --format json for the full list)")
    return "\n".join(lines)
