"""Feature flag aging: finds flag-looking references/definitions across the
codebase and, like `todo_age.py`, uses `git blame` to say how long each has
been sitting there -- a flag whose oldest reference predates `stale_days`
(default 180 -- flags are meant to be temporary, and a longer runway than
the 90-day TODO default reflects that a flag typically needs a full
release/rollout cycle before it's safe to remove) is a candidate cleanup
target: either it should have been fully rolled out and deleted by now, or
it's effectively permanent configuration masquerading as a flag.

Detection is deliberately a family of narrow, best-effort regexes (the same
"idiom matching, not real understanding" tradeoff `env_check.py`'s generic
fallback makes), not a real parse of every flagging SDK's API -- there is
no single standard shape for "check a feature flag" across
LaunchDarkly/Split/Unleash/Django-waffle/home-grown dict lookups. Three
shapes are recognized, each capturing the flag's name as a plain string:

- **A flag-check call**: `is_enabled("x")`, `flag_enabled("x")`,
  `feature_enabled("x")`, `flag_is_active(request, "x")`,
  `switch_is_active("x")`, `is_active("x")` -- the first quoted string
  literal anywhere in the parens is taken as the flag name, so this works
  regardless of exactly which argument position the name is in for a given
  SDK.
- **A dict-like flag lookup**: `FEATURE_FLAGS["x"]`, `flags.get("x")`,
  `self.feature_flags["x"]` -- any identifier containing "flag(s)"
  subscripted or `.get()`-ed with a string key.
- **A boolean flag constant**: `ENABLE_NEW_CHECKOUT = True` or
  `NEW_CHECKOUT_ENABLED = False` at the start of a line -- the constant's
  own name is the flag name.

Expect noise, especially from the dict-lookup shape (a variable that merely
contains "flag" in its name, unrelated to feature flagging). This is an
opt-in, best-effort signal, not a hard rule -- same posture as
`env_check.py`.
"""

import os
import re
from datetime import datetime, timezone

from codequality.git_utils import GitError, _run
from codequality.scanner import discover_files

DEFAULT_STALE_DAYS = 180
DEFAULT_MAX_LISTING = 25

_FS = "\x1f"  # field separator

_FLAG_CALL_RE = re.compile(
    r"\b(?:is_enabled|is_flag_enabled|flag_enabled|feature_enabled|flag_is_active|switch_is_active|is_active)"
    r"\s*\([^)]*?['\"]([A-Za-z_][\w.-]*)['\"]"
)
_FLAG_LOOKUP_RE = re.compile(
    r"\b\w*flags?\w*\s*(?:\[\s*|\.get\(\s*)['\"]([A-Za-z_][\w.-]*)['\"]", re.IGNORECASE
)
_FLAG_CONST_RE = re.compile(
    r"^\s*(ENABLE_[A-Z0-9_]+|[A-Z][A-Z0-9_]*_ENABLED)\s*[:=]\s*(?:True|False|true|false)\b"
)

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


def _blame_shas(cwd, rel_path):
    """List of commit shas, one per line of `rel_path` at HEAD (index 0 is
    line 1). Returns [] for paths git can't blame at HEAD (untracked, or
    deleted since) -- same technique as `todo_age.py`/`edit_distance.py`.
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


def _commit_date(cwd, sha, cache):
    if sha not in cache:
        try:
            raw = _run(["show", "-s", "--format=%aI", sha], cwd)
        except GitError:
            cache[sha] = None
        else:
            cache[sha] = datetime.fromisoformat(raw.strip())
    return cache[sha]


def _read_lines(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except OSError:
        return None


def _flag_matches(line):
    """Every flag name referenced/defined on one line of source, from
    whichever of the three recognized shapes matches.
    """
    names = []
    for pattern in (_FLAG_CALL_RE, _FLAG_LOOKUP_RE, _FLAG_CONST_RE):
        m = pattern.search(line)
        if m:
            names.append(m.group(1))
    return names


def _file_flag_occurrences(root, rel_path, stale_days, now, cache):
    lines = _read_lines(root, rel_path)
    if not lines:
        return []
    matches = []
    for lineno, raw_line in enumerate(lines, start=1):
        for name in _flag_matches(raw_line.rstrip("\n")):
            matches.append((lineno, name, raw_line))
    if not matches:
        return []

    shas = _blame_shas(root, rel_path)
    occurrences = []
    for lineno, name, raw_line in matches:
        sha = shas[lineno - 1] if lineno - 1 < len(shas) else None
        date = _commit_date(root, sha, cache) if sha else None
        if date is None:
            continue
        age_days = (now - date).days
        occurrences.append({
            "flag": name,
            "file": rel_path,
            "line": lineno,
            "snippet": raw_line.strip()[:120],
            "sha": sha[:8],
            "commit_date": date.isoformat(),
            "age_days": age_days,
            "stale": age_days > stale_days,
        })
    return occurrences


def compute(root, stale_days=DEFAULT_STALE_DAYS, exclude=None):
    """Every flag reference/definition found across scanned files, one
    dict per occurrence:

        {"flag", "file", "line", "snippet", "sha", "commit_date",
         "age_days", "stale"}

    Files git can't blame (untracked/uncommitted) contribute nothing
    rather than raising.
    """
    files = discover_files(root, exclude or [], include_generic=True)
    now = datetime.now(timezone.utc)
    cache = {}
    occurrences = []
    for rel_path, _lang in files:
        occurrences.extend(_file_flag_occurrences(root, rel_path, stale_days, now, cache))
    return occurrences


def summarize(occurrences):
    """Groups occurrences by flag name: {name: {"count", "oldest_age_days",
    "stale", "files"}}. A flag is "stale" if its single oldest reference
    exceeds the threshold -- a flag with even one very old touchpoint has
    been around at least that long.
    """
    groups = {}
    for occ in occurrences:
        g = groups.setdefault(occ["flag"], {"count": 0, "oldest_age_days": 0, "stale": False, "files": set()})
        g["count"] += 1
        g["oldest_age_days"] = max(g["oldest_age_days"], occ["age_days"])
        g["files"].add(occ["file"])
        if occ["stale"]:
            g["stale"] = True
    for g in groups.values():
        g["files"] = sorted(g["files"])
    return groups


def render_text(occurrences, stale_days=DEFAULT_STALE_DAYS, max_listing=DEFAULT_MAX_LISTING):
    groups = summarize(occurrences)
    lines = [f"Feature Flag Aging (stale threshold: {stale_days} days)", ""]
    if not groups:
        lines.append("  No flag-looking references found.")
        return "\n".join(lines)

    lines.append(f"  {'Flag':<40}{'Refs':>6}{'Oldest':>10}{'Files':>7}  Stale")
    stale_names = sorted(
        (name for name, g in groups.items() if g["stale"]),
        key=lambda n: groups[n]["oldest_age_days"], reverse=True,
    )
    fresh_names = sorted(n for n in groups if n not in stale_names)
    for name in stale_names + fresh_names:
        g = groups[name]
        lines.append(
            f"  {name:<40}{g['count']:>6}{g['oldest_age_days']:>9}d{len(g['files']):>7}  {'yes' if g['stale'] else ''}"
        )

    lines.append("")
    lines.append(f"  Stale flags (>{stale_days}d old, showing up to {max_listing})")
    if not stale_names:
        lines.append("    (none)")
    else:
        for name in stale_names[:max_listing]:
            g = groups[name]
            lines.append(f"    {name}  ({g['oldest_age_days']}d old, {g['count']} ref(s) in {len(g['files'])} file(s))")
        remaining = len(stale_names) - min(len(stale_names), max_listing)
        if remaining > 0:
            lines.append(f"    ... and {remaining} more (see --format json for the full list)")
    return "\n".join(lines)
