"""Suppression-debt report: age blanket (unscoped) inline suppression comments.

A *blanket* suppression silences a checker for an entire line without
specifying which rules are being suppressed:

  * ``# noqa``              -- silences every flake8/ruff warning on the line
  * ``# type: ignore``      -- silences every mypy error on the line
  * ``codequality: ignore``  -- silences every codequality finding on the line

Scoped variants (``# noqa: E501``, ``# type: ignore[attr-defined]``,
``codequality: ignore[rule-name]``) are intentional and targeted; they are NOT
flagged here.

For each blanket suppression found, ``git blame`` dates the line so you can
see how old it is and whether it was introduced by an AI-assisted or human
commit. Suppressions older than ``--stale-days`` (default 90) are flagged
stale.
"""

import os
import re
from datetime import datetime, timezone

from codequality.git_utils import GitError, _run
from codequality.scanner import discover_files

DEFAULT_MARKER = "Co-Authored-By: Claude"
DEFAULT_STALE_DAYS = 90
DEFAULT_MAX_LISTING = 25

_FS = "\x1f"
_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")

# Blanket suppression patterns. Scoped variants are excluded via negative
# lookahead: `# noqa: E501` is scoped; `# noqa` alone is blanket.
_SUPPRESSION_PATTERNS = [
    ("noqa",                 re.compile(r"#\s*noqa\b(?!\s*:)", re.IGNORECASE)),
    ("type-ignore",          re.compile(r"#\s*type:\s*ignore\b(?!\s*\[)", re.IGNORECASE)),
    ("codequality-ignore",   re.compile(r"codequality:\s*ignore\b(?!\s*\[)", re.IGNORECASE)),
]


def _blame_shas(cwd, rel_path):
    """One sha per line of `rel_path` at HEAD (index 0 = line 1). [] on error."""
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
    """Author date + AI classification for `sha`, cached across calls."""
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
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.readlines()
    except OSError:
        return None


def _match_kind(line_text):
    """Return the first matching suppression kind, or None."""
    for kind, pat in _SUPPRESSION_PATTERNS:
        if pat.search(line_text):
            return kind
    return None


def _file_suppressions(root, rel_path, marker, stale_days, now, cache):
    lines = _read_lines(root, rel_path)
    if not lines:
        return []

    matches = [
        (i, line, _match_kind(line))
        for i, line in enumerate(lines, start=1)
        if _match_kind(line) is not None
    ]
    if not matches:
        return []

    shas = _blame_shas(root, rel_path)
    entries = []
    for lineno, raw_line, kind in matches:
        sha = shas[lineno - 1] if lineno - 1 < len(shas) else None
        meta = _commit_metadata(root, sha, marker, cache) if sha else None
        if meta is None:
            continue
        age_days = (now - meta["date"]).days
        entries.append({
            "file": rel_path,
            "line": lineno,
            "kind": kind,
            "snippet": raw_line.strip()[:120],
            "sha": sha[:8],
            "commit_date": meta["date"].isoformat(),
            "age_days": age_days,
            "group": "ai" if meta["is_ai"] else "human",
            "stale": age_days > stale_days,
        })
    return entries


def compute(root, marker=DEFAULT_MARKER, stale_days=DEFAULT_STALE_DAYS, exclude=None):
    """Return a list of dicts, one per blanket suppression line found in the repo.

    Each dict has: ``file``, ``line``, ``kind``, ``snippet``, ``sha``,
    ``commit_date``, ``age_days``, ``group`` (``"ai"``/``"human"``), ``stale``.

    Files that are untracked (git can't blame them) or unreadable contribute
    nothing rather than raising.
    """
    files = discover_files(root, exclude or [], include_generic=True)
    now = datetime.now(timezone.utc)
    cache = {}
    results = []
    for rel_path, _lang in files:
        results.extend(_file_suppressions(root, rel_path, marker, stale_days, now, cache))
    return results


def summarize(entries):
    """Returns ``{"ai": {...}, "human": {...}}`` with count/avg_age_days/
    oldest_age_days/stale_count per group.
    """
    groups = {
        "ai":    {"count": 0, "total_age": 0, "oldest_age_days": 0, "stale_count": 0},
        "human": {"count": 0, "total_age": 0, "oldest_age_days": 0, "stale_count": 0},
    }
    for e in entries:
        g = groups[e["group"]]
        g["count"] += 1
        g["total_age"] += e["age_days"]
        g["oldest_age_days"] = max(g["oldest_age_days"], e["age_days"])
        if e["stale"]:
            g["stale_count"] += 1
    for g in groups.values():
        g["avg_age_days"] = g["total_age"] / g["count"] if g["count"] else None
        if g["count"] == 0:
            g["oldest_age_days"] = None
        del g["total_age"]
    return groups


def render_text(entries, stale_days=DEFAULT_STALE_DAYS, max_listing=DEFAULT_MAX_LISTING):
    groups = summarize(entries)
    lines = [f"Suppression Debt (stale threshold: {stale_days} days)", ""]
    header = f"  {'Group':<14}{'Count':>8}{'AvgAge':>10}{'Oldest':>10}{'Stale':>8}"
    lines.append(header)
    for label, key in (("AI-assisted", "ai"), ("Human", "human")):
        g = groups[key]
        avg = "n/a" if g["avg_age_days"] is None else f"{g['avg_age_days']:.1f}d"
        oldest = "n/a" if g["oldest_age_days"] is None else f"{g['oldest_age_days']}d"
        lines.append(
            f"  {label:<14}{g['count']:>8}{avg:>10}{oldest:>10}{g['stale_count']:>8}"
        )

    stale = sorted((e for e in entries if e["stale"]), key=lambda e: e["age_days"], reverse=True)
    lines.append("")
    lines.append(
        f"  Stale blanket suppressions (>{stale_days}d old, showing up to {max_listing})"
    )
    if not stale:
        lines.append("    (none)")
    else:
        for e in stale[:max_listing]:
            lines.append(
                f"    {e['file']}:{e['line']}  [{e['group']:<5}]  {e['age_days']:>5}d"
                f"  [{e['kind']}]  {e['snippet']}"
            )
        remaining = len(stale) - min(len(stale), max_listing)
        if remaining:
            lines.append(f"    ... and {remaining} more (see --format json for the full list)")
    return "\n".join(lines)
