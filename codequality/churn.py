"""Git-history churn: an empirical trust signal computed from what actually
happened after code landed, instead of from re-reading the code.

`codequality churn` walks the commit log and classifies each commit as
AI-assisted or not, by a marker string in the commit message/trailers
(default `"Co-Authored-By: Claude"`, matching the trailer this very tool's
own commits use). For each commit, it checks whether any file it touched
was modified again by a later commit within a configurable window -- a
proxy for "did this need a second look soon after landing." Reported per
group (AI-assisted vs. not) so the two rework rates can be compared.

This is file-level, not line-level: precise line-level "was this exact
change reverted" tracking would need to survive renames and diff-hunk
overlap across history, which is a lot of machinery for a heuristic
signal. File-level granularity is coarser but simple, deterministic, and
still answers the real question -- does code from this source tend to
need rework soon after landing. Merge commits are excluded since they
touch many files without representing new authored content.
"""

from collections import defaultdict
from datetime import datetime, timedelta

from codequality.git_utils import _run

DEFAULT_MARKER = "Co-Authored-By: Claude"
DEFAULT_WINDOW_DAYS = 14

_FS = "\x1f"  # field separator
_RS = "\x1e"  # record separator


def _commit_metadata(cwd, marker, since=None):
    args = ["log", "--no-merges", f"--format=%H{_FS}%aI{_FS}%B{_RS}"]
    if since:
        args.append(f"--since={since}")
    raw = _run(args, cwd)
    commits = {}
    for record in raw.split(_RS):
        record = record.strip("\n")
        if not record:
            continue
        sha, date_str, body = record.split(_FS, 2)
        commits[sha] = {"date": datetime.fromisoformat(date_str.replace("Z", "+00:00")), "is_ai": marker.lower() in body.lower()}
    return commits


def _commit_files(cwd, since=None):
    """dict[sha] -> list of files touched by that commit."""
    args = ["log", "--no-merges", f"--format={_RS}%H", "--name-only"]
    if since:
        args.append(f"--since={since}")
    raw = _run(args, cwd)
    files_by_sha = {}
    # Split on the record separator *before* splitting into lines --
    # str.splitlines() treats \x1e as a line boundary in its own right, so
    # doing it in the other order silently eats the delimiter.
    for chunk in raw.split(_RS):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        sha, _, rest = chunk.partition("\n")
        files_by_sha[sha] = [f.strip() for f in rest.splitlines() if f.strip()]
    return files_by_sha


def _touches_by_file(ordered_commits, files_by_sha):
    """dict[file] -> [(sequence_index, date, sha), ...], where
    sequence_index reflects commit order (oldest first).
    """
    touches = defaultdict(list)
    for index, (sha, meta) in enumerate(ordered_commits):
        for f in files_by_sha.get(sha, []):
            touches[f].append((index, meta["date"], sha))
    return touches


def _was_reworked(index, sha, meta, files_by_sha, touches, window_days):
    window_end = meta["date"] + timedelta(days=window_days)
    for f in files_by_sha.get(sha, []):
        for other_index, other_date, other_sha in touches[f]:
            if other_sha != sha and other_index > index and other_date <= window_end:
                return True
    return False


def compute(cwd, marker=DEFAULT_MARKER, window_days=DEFAULT_WINDOW_DAYS, since=None):
    """Returns {"ai": {...}, "human": {...}}, each with commits/reworked/rate."""
    metadata = _commit_metadata(cwd, marker, since)
    files_by_sha = _commit_files(cwd, since)
    # git log's own order is newest-first and topological (a child always
    # precedes its parent); reversing it is a more reliable oldest-first
    # ordering than sorting by author date, since git's timestamps only
    # have one-second resolution and rapid successive commits can tie.
    ordered = list(reversed(list(metadata.items())))
    touches = _touches_by_file(ordered, files_by_sha)

    counts = {"ai": {"commits": 0, "reworked": 0}, "human": {"commits": 0, "reworked": 0}}
    for index, (sha, meta) in enumerate(ordered):
        group = "ai" if meta["is_ai"] else "human"
        counts[group]["commits"] += 1
        if _was_reworked(index, sha, meta, files_by_sha, touches, window_days):
            counts[group]["reworked"] += 1

    for group in counts.values():
        group["rate"] = group["reworked"] / group["commits"] if group["commits"] else None
    return counts


def render_text(counts, window_days):
    lines = [f"Git History Churn (rework window: {window_days} days)", ""]
    lines.append(f"  {'Group':<14}{'Commits':>9}{'Reworked':>10}{'Rate':>9}")
    for label, key in (("AI-assisted", "ai"), ("Human", "human")):
        g = counts[key]
        rate = "n/a" if g["rate"] is None else f"{g['rate'] * 100:.1f}%"
        lines.append(f"  {label:<14}{g['commits']:>9}{g['reworked']:>10}{rate:>9}")
    return "\n".join(lines)
