"""AI code quality report: one dashboard aggregating every existing
AI-vs-human metric this tool already computes, instead of running four
separate subcommands and combining the numbers by hand.

`codequality ai-report` is a pure aggregation/reshaping layer -- it never
computes anything new. It calls straight into `churn.compute`,
`edit_distance.compute`, `commit_lint.compute`, and (only when
`--check-imports`/`--check-types` is set, same gating as
`hallucination-rate` itself) `hallucination_metrics.compute`. All four
already classify the same git history into AI-assisted vs. human by the
same marker-substring convention (default `"Co-Authored-By: Claude"`,
case-insensitive -- see those modules' docstrings), so this module just
lays their results side by side: one row per metric, one column per group.

Deliberately no single fabricated "AI code quality score": rework rate (a
fraction of commits), edit distance (a fraction of lines), commit-lint
pass rate (a fraction of commits, by an unrelated rule), and hallucination
rate (findings per 1,000 lines) are four different units measuring four
different things. Averaging them into one number would invent a
weighting that doesn't exist and imply a precision this data doesn't
have -- so this report always shows the four numbers next to each other,
clearly labeled, and leaves interpretation to the reader.

Every "how many commits/lines does this rest on" question a reader would
need to judge whether the numbers mean anything is itself a real number
already produced by the underlying modules (`churn`'s commit counts,
`edit_distance`'s lines-added, `hallucination_metrics`'s LOC) -- surfaced
here, not recomputed.
"""

from codequality import churn, commit_lint, edit_distance, hallucination_metrics

DEFAULT_MARKER = "Co-Authored-By: Claude"

_HALLUCINATION_SKIP_NOTE = (
    "hallucination-rate skipped: requires --check-imports and/or --check-types "
    "(see `codequality hallucination-rate`)"
)
_HALLUCINATION_SINCE_NOTE = (
    "hallucination-rate does not support --since (it has no notion of a time "
    "window -- see codequality/hallucination_metrics.py); its numbers below "
    "cover the full history, unlike the other three rows"
)

_METRIC_ROWS = (
    # (label, group_key, formatter_kind)
    ("Rework rate (churn)", "rework_rate", "pct"),
    ("Edit distance", "edit_distance", "pct"),
    ("Commit-lint pass rate", "commit_lint_pass_rate", "pct"),
    ("Hallucination rate /1k loc", "hallucination_rate_per_1000", "rate"),
)


def _new_group(commits_classified):
    return {
        "commits_classified": commits_classified,
        "rework_rate": None,
        "edit_distance": None,
        "commit_lint_pass_rate": None,
        "hallucination_rate_per_1000": None,
    }


def _run_hallucination(root, config, marker, since):
    """Returns (counts_or_None, notes). Only runs hallucination_metrics
    when config opts into it, same gating as `hallucination-rate` itself;
    `since` isn't supported by that module (see its docstring), so using it
    alongside --check-imports/--check-types earns a caveat note instead of
    silently ignoring the mismatch.
    """
    if not (config.check_imports or config.check_types):
        return None, [_HALLUCINATION_SKIP_NOTE]
    counts = hallucination_metrics.compute(root, config, marker=marker)
    notes = [_HALLUCINATION_SINCE_NOTE] if since else []
    return counts, notes


def _build_group(key, churn_counts, edit_distance_counts, commit_lint_result, hallucination_counts):
    commits_classified = churn_counts[key]["commits"]
    group = _new_group(commits_classified)
    if not commits_classified:
        return group  # no AI/human-marked history at all here -- leave every rate as n/a, not a fake 0

    group["rework_rate"] = churn_counts[key]["rate"]
    group["edit_distance"] = edit_distance_counts[key]["mean_edit_distance"]
    cl_rate = commit_lint_result[key]["rate"]
    group["commit_lint_pass_rate"] = None if cl_rate is None else 1 - cl_rate
    if hallucination_counts is not None and hallucination_counts[key]["loc"]:
        group["hallucination_rate_per_1000"] = hallucination_counts[key]["rate_per_1000"]
    return group


def compute(root, config, marker=DEFAULT_MARKER, since=None):
    """Roll up churn/edit-distance/commit-lint/(opt-in) hallucination-rate
    into one {"ai": {...}, "human": {...}} structure, plus enough metadata
    for a reader to judge whether there's enough AI-marked history here
    for the numbers to mean anything.

    Returns:
    {
        "marker": str, "since": str|None,
        "hallucination_notes": [str, ...],   # why it ran/was skipped, if notable
        "ai": {commits_classified, rework_rate, edit_distance,
               commit_lint_pass_rate, hallucination_rate_per_1000},
        "human": {...same shape...},
        "raw": {"churn": ..., "edit_distance": ..., "commit_lint": ...,
                "hallucination": ... or None},
    }

    `commits_classified` (from `churn`, which walks every non-merge commit)
    is the number a reader should check first: a group with 0 commits
    means every other field for that group is `None`/n/a, not a real zero
    -- see the module docstring for why `churn.py` already treats an empty
    group this way, and why this rollup preserves that instead of letting
    a 0-LOC group's rate collapse to a misleading `0.0`.
    """
    churn_counts = churn.compute(root, marker=marker, since=since)
    edit_distance_counts = edit_distance.compute(root, marker=marker, since=since)
    commit_lint_result = commit_lint.compute(root, marker=marker, since=since)
    hallucination_counts, notes = _run_hallucination(root, config, marker, since)

    groups = {
        key: _build_group(key, churn_counts, edit_distance_counts, commit_lint_result, hallucination_counts)
        for key in ("ai", "human")
    }

    return {
        "marker": marker,
        "since": since,
        "hallucination_notes": notes,
        "ai": groups["ai"],
        "human": groups["human"],
        "raw": {
            "churn": churn_counts,
            "edit_distance": edit_distance_counts,
            "commit_lint": commit_lint_result,
            "hallucination": hallucination_counts,
        },
    }


def _fmt(kind, value):
    if value is None:
        return "n/a"
    if kind == "pct":
        return f"{value * 100:.1f}%"
    return f"{value:.2f}"


def render_text(result):
    lines = ["AI Code Quality Report", ""]
    lines.append(f'  Marker: "{result["marker"]}" (case-insensitive substring match in commit message/trailers)')
    if result["since"]:
        lines.append(f"  Since: {result['since']}")
    lines.append(
        f"  Commits classified: {result['ai']['commits_classified']} AI-assisted, "
        f"{result['human']['commits_classified']} human"
    )
    lines.append("")
    lines.append(f"  {'Metric':<28}{'AI-assisted':>14}{'Human':>14}")
    for label, key, kind in _METRIC_ROWS:
        ai_v = _fmt(kind, result["ai"][key])
        human_v = _fmt(kind, result["human"][key])
        lines.append(f"  {label:<28}{ai_v:>14}{human_v:>14}")

    if result["hallucination_notes"]:
        lines.append("")
        for note in result["hallucination_notes"]:
            lines.append(f"  Note: {note}")

    return "\n".join(lines)
