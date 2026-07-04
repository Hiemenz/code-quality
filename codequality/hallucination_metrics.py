"""Hallucination rate: how often the specific lines a hallucination-style
check flags (`--check-imports`, `--check-types`) trace back to AI-assisted
vs. human commits, per 1,000 lines of code attributed to each.

`codequality churn` answers "does AI-assisted code get reworked sooner"
from commit history alone. This answers a narrower, sharper question using
the *content* of the correctness checks this tool already runs: of the
code a given source (AI vs. human, via `git blame`) is responsible for,
how much of it trips an unresolved-import or real-type-error finding? That
needs both a scan (to know which lines are flagged) and blame (to know who
last touched them) -- neither alone answers it.

This module doesn't invent new detection: `--check-imports` and
`--check-types` already do that (see `python_analyzer.py` and
`typecheck.py`). It only attributes their findings to a commit and rolls
them up. Deliberately line-level, unlike `churn.py`'s file-level rework
tracking, since a hallucinated import/type error is a property of one
specific line, not the whole file it lives in.

The blame-vs-marker classification here is intentionally a small, local
duplicate of the same idea in `churn.py` rather than a shared import --
the two modules answer different questions (commit rework vs. line
attribution) and are kept decoupled on purpose.
"""

import re

from codequality.git_utils import GitError, _run
from codequality.scanner import discover_files, scan_repo

DEFAULT_MARKER = "Co-Authored-By: Claude"

_ALWAYS_ON_CORRECTNESS_SYMBOLS = {"assertion-free-test", "unreachable-code"}
_UNRESOLVED_IMPORT_SYMBOL = "unresolved-import"

_BLAME_HEADER_RE = re.compile(r"^([0-9a-f]{40}) \d+ \d+")


class UsageError(ValueError):
    """Raised when the feature is invoked without enough to roll up."""


def _is_hallucination_issue(issue, config):
    if issue.symbol == _UNRESOLVED_IMPORT_SYMBOL:
        return config.check_imports
    if issue.category == "correctness" and issue.symbol not in _ALWAYS_ON_CORRECTNESS_SYMBOLS:
        return config.check_types
    return False


def _flagged_lines(file_metrics, config):
    """dict[path] -> set of 1-based line numbers with a hallucination-style
    finding.
    """
    flagged = {}
    for fm in file_metrics:
        lines = {issue.line for issue in fm.issues if _is_hallucination_issue(issue, config)}
        if lines:
            flagged[fm.path] = lines
    return flagged


def _blame_shas(root, rel_path):
    """List of commit shas, one per line of `rel_path` at HEAD (index 0 is
    line 1). Returns [] for paths git can't blame at HEAD (untracked, or
    deleted since -- the scan reads the working tree, blame reads HEAD, so
    an uncommitted file is a gap this can't attribute).
    """
    try:
        raw = _run(["blame", "-w", "--line-porcelain", "HEAD", "--", rel_path], root)
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


def _classify_commit(root, sha, marker, cache):
    if sha not in cache:
        try:
            body = _run(["show", "-s", "--format=%B", sha], root)
        except GitError:
            body = ""
        cache[sha] = "ai" if marker.lower() in body.lower() else "human"
    return cache[sha]


def _new_group():
    return {"loc": 0, "flagged": 0}


def _accumulate_file(root, rel_path, flagged_lines, marker, cache, counts):
    for lineno, sha in enumerate(_blame_shas(root, rel_path), start=1):
        group = _classify_commit(root, sha, marker, cache)
        counts[group]["loc"] += 1
        if lineno in flagged_lines:
            counts[group]["flagged"] += 1


def compute(root, config, marker=DEFAULT_MARKER):
    """Returns {"ai": {...}, "human": {...}}, each with loc/flagged/rate_per_1000.

    Requires `config.check_imports` or `config.check_types` (or both) --
    without at least one there is nothing for this rollup to attribute.
    """
    if not config.check_imports and not config.check_types:
        raise UsageError("hallucination-rate requires --check-imports and/or --check-types")

    file_metrics = scan_repo(root, config)
    flagged_by_file = _flagged_lines(file_metrics, config)
    files = discover_files(root, config.exclude, config.include_generic_languages)

    counts = {"ai": _new_group(), "human": _new_group()}
    cache = {}
    for rel_path, _lang in files:
        _accumulate_file(root, rel_path, flagged_by_file.get(rel_path, set()), marker, cache, counts)

    for group in counts.values():
        group["rate_per_1000"] = (group["flagged"] / group["loc"] * 1000) if group["loc"] else 0.0
    return counts


def render_text(counts):
    lines = ["Hallucination Rate (per 1,000 lines, by git-blame attribution)", ""]
    lines.append(f"  {'Group':<14}{'LOC':>10}{'Flagged':>10}{'Per 1k':>9}")
    for label, key in (("AI-assisted", "ai"), ("Human", "human")):
        g = counts[key]
        lines.append(f"  {label:<14}{g['loc']:>10}{g['flagged']:>10}{g['rate_per_1000']:>9.2f}")
    return "\n".join(lines)
