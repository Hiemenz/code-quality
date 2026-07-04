"""Commit message quality: deterministic, lexical rules run over each
non-merge commit's subject line -- no LLM call, no "does this message make
sense" judgment, just length/casing/punctuation checks a formula can decide.

`codequality commit-lint` walks the commit log the same way `churn.py` and
`edit_distance.py` do (see those modules), classifies each commit as
AI-assisted or not by the same marker-substring convention (default
`"Co-Authored-By: Claude"`), and runs a fixed set of rule functions against
the subject line. Each rule is a small, independent function returning
`(rule_name, passed, detail)` -- easy to read, easy to add to, and
impossible to disagree with on "what it measured" even if you disagree with
the rule itself.

Two rules are on by default (`too-short`, `generic-subject`) because they
catch messages that are almost never intentional. Two more
(`trailing-period`, `not-capitalized`) are opt-in behind `--strict`, since
those are house-style conventions plenty of teams don't follow -- flagging
them unconditionally would make this feel like a style cop instead of a
quality signal.
"""

from collections import Counter

from codequality.git_utils import _run

DEFAULT_MARKER = "Co-Authored-By: Claude"
DEFAULT_MIN_LENGTH = 10
DEFAULT_MAX_FAILURES = 25

_FS = "\x1f"  # field separator
_RS = "\x1e"  # record separator

# Case-insensitive, exact-match only (after stripping trailing punctuation)
# -- deliberately narrow so "Fix the null pointer in auth" is never flagged
# just because it starts with "fix".
_GENERIC_SUBJECTS = {
    "fix", "wip", "stuff", "update", "updates", "misc", "changes", "fixes",
    "asdf", "test", "tmp", "more changes", "fix bug", "bug fix",
}


def _commit_subjects(cwd, marker, since=None):
    args = ["log", "--no-merges", f"--format=%H{_FS}%s{_FS}%B{_RS}"]
    if since:
        args.append(f"--since={since}")
    raw = _run(args, cwd)
    commits = []
    for record in raw.split(_RS):
        record = record.strip("\n")
        if not record:
            continue
        sha, subject, body = record.split(_FS, 2)
        commits.append({"sha": sha, "subject": subject, "is_ai": marker.lower() in body.lower()})
    return commits


def rule_too_short(subject, min_length=DEFAULT_MIN_LENGTH):
    passed = len(subject) >= min_length
    detail = f"{len(subject)} char(s), minimum is {min_length}"
    return "too-short", passed, detail


def rule_generic_subject(subject):
    normalized = subject.strip().rstrip(".!?").strip().lower()
    passed = normalized not in _GENERIC_SUBJECTS
    detail = "not a banned placeholder subject" if passed else f'subject is exactly "{normalized}"'
    return "generic-subject", passed, detail


def rule_trailing_period(subject):
    passed = not subject.rstrip().endswith(".")
    detail = "no trailing period" if passed else "subject ends with a period"
    return "trailing-period", passed, detail


def rule_not_capitalized(subject):
    stripped = subject.strip()
    first = stripped[:1]
    passed = not first.isalpha() or first.isupper()
    detail = "first letter is uppercase" if passed else "first letter is not uppercase"
    return "not-capitalized", passed, detail


def _run_rules(subject, min_length, strict):
    results = [rule_too_short(subject, min_length), rule_generic_subject(subject)]
    if strict:
        results.append(rule_trailing_period(subject))
        results.append(rule_not_capitalized(subject))
    return results


def _new_group():
    return {"commits": 0, "failed": 0, "by_rule": Counter()}


def compute(
    cwd, marker=DEFAULT_MARKER, since=None, strict=False,
    min_length=DEFAULT_MIN_LENGTH, max_failures=DEFAULT_MAX_FAILURES,
):
    """Returns {"ai": {...}, "human": {...}, "failures": [...], "failures_total": N}.

    Each group dict has commits/failed/by_rule/rate. `failures` is a list of
    (up to `max_failures`) commits that failed at least one rule, each with
    sha/group/subject/failed_rules; `failures_total` is the true count before
    truncation, same "cap the listing, keep the true total" pattern as the
    scan report's issue list.
    """
    commits = _commit_subjects(cwd, marker, since)
    counts = {"ai": _new_group(), "human": _new_group()}
    failures = []

    for commit in commits:
        group_key = "ai" if commit["is_ai"] else "human"
        group = counts[group_key]
        group["commits"] += 1

        results = _run_rules(commit["subject"], min_length, strict)
        failed_rules = [name for name, passed, _detail in results if not passed]
        if failed_rules:
            group["failed"] += 1
            for name in failed_rules:
                group["by_rule"][name] += 1
            failures.append({
                "sha": commit["sha"][:8],
                "group": group_key,
                "subject": commit["subject"],
                "failed_rules": failed_rules,
            })

    for group in counts.values():
        group["by_rule"] = dict(group["by_rule"])
        group["rate"] = group["failed"] / group["commits"] if group["commits"] else None

    return {
        "ai": counts["ai"],
        "human": counts["human"],
        "failures": failures[:max_failures],
        "failures_total": len(failures),
    }


def render_text(result, strict=False, max_failures=DEFAULT_MAX_FAILURES):
    """Render the `compute()` result as a human-readable terminal report."""
    title = "Commit Message Lint" + (" (strict mode)" if strict else "")
    lines = [title, ""]
    lines.append(f"  {'Group':<14}{'Commits':>9}{'Failed':>9}{'Rate':>9}")
    for label, key in (("AI-assisted", "ai"), ("Human", "human")):
        g = result[key]
        rate = "n/a" if g["rate"] is None else f"{g['rate'] * 100:.1f}%"
        lines.append(f"  {label:<14}{g['commits']:>9}{g['failed']:>9}{rate:>9}")

    rule_names = sorted(set(result["ai"]["by_rule"]) | set(result["human"]["by_rule"]))
    lines.append("")
    lines.append("  Failures by rule")
    if not rule_names:
        lines.append("    (none)")
    else:
        lines.append(f"    {'Rule':<20}{'AI':>6}{'Human':>8}")
        for name in rule_names:
            ai_n = result["ai"]["by_rule"].get(name, 0)
            human_n = result["human"]["by_rule"].get(name, 0)
            lines.append(f"    {name:<20}{ai_n:>6}{human_n:>8}")

    if result["failures"]:
        lines.append("")
        lines.append(f"  Failing commits (showing up to {max_failures})")
        for f in result["failures"]:
            rules = ", ".join(f["failed_rules"])
            lines.append(f"    {f['sha']}  [{f['group']:<5}]  {f['subject']}  ({rules})")
        remaining = result["failures_total"] - len(result["failures"])
        if remaining > 0:
            lines.append(f"    ... and {remaining} more (see --format json for the full list)")
    return "\n".join(lines)
