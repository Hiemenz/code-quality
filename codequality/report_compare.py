"""Delta report between two ``codequality scan --format json`` outputs.

``codequality compare before.json after.json`` loads both reports and
computes the overall-score delta, per-category deltas, and new/resolved issue
counts. Exit code is non-zero when the overall score drops by more than
``--tolerance`` (default 0 -- any regression fails).
"""

import json


def load_report(path):
    """Load and return a scan JSON report from `path`."""
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def compare(before, after):
    """Compute the delta between two scan JSON reports.

    `before` and `after` are dicts as returned by ``load_report``.

    Returns a dict with:
      - ``overall_delta``  -- after.score - before.score (positive = improvement)
      - ``overall_before`` / ``overall_after``
      - ``category_deltas`` -- {category: {"before", "after", "delta"}}
      - ``new_issues``     -- list of issue dicts present in after but not before
      - ``resolved_issues``-- list of issue dicts present in before but not after
      - ``new_issue_count`` / ``resolved_issue_count``
    """
    before_score = before.get("overall", {}).get("score", 0.0)
    after_score = after.get("overall", {}).get("score", 0.0)

    before_cats = before.get("categories", {})
    after_cats = after.get("categories", {})
    all_cats = sorted(set(before_cats) | set(after_cats))

    category_deltas = {}
    for cat in all_cats:
        b = before_cats.get(cat, {}).get("score", 0.0)
        a = after_cats.get(cat, {}).get("score", 0.0)
        category_deltas[cat] = {"before": b, "after": a, "delta": a - b}

    def _issue_key(iss):
        return (iss.get("file", ""), iss.get("line", 0), iss.get("symbol", iss.get("rule", "")))

    before_keys = {_issue_key(i): i for i in before.get("issues", [])}
    after_keys = {_issue_key(i): i for i in after.get("issues", [])}

    new_issues = [after_keys[k] for k in after_keys if k not in before_keys]
    resolved_issues = [before_keys[k] for k in before_keys if k not in after_keys]

    return {
        "overall_before": before_score,
        "overall_after": after_score,
        "overall_delta": after_score - before_score,
        "category_deltas": category_deltas,
        "new_issues": sorted(new_issues, key=lambda i: (i.get("file", ""), i.get("line", 0))),
        "resolved_issues": sorted(resolved_issues, key=lambda i: (i.get("file", ""), i.get("line", 0))),
        "new_issue_count": len(new_issues),
        "resolved_issue_count": len(resolved_issues),
    }


def is_regression(delta_result, tolerance=0.0):
    """True if the overall score dropped by more than `tolerance` points."""
    return delta_result["overall_delta"] < -tolerance


def render_text(delta, tolerance=0.0):
    lines = ["Code Quality Comparison", ""]

    # Overall score row
    b, a, d = delta["overall_before"], delta["overall_after"], delta["overall_delta"]
    sign = "+" if d >= 0 else ""
    lines.append(f"  Overall score:  {b:.1f}  →  {a:.1f}  ({sign}{d:.1f})")
    lines.append("")

    # Per-category table
    lines.append(f"  {'Category':<16}{'Before':>8}{'After':>8}{'Delta':>8}")
    lines.append(f"  {'-'*16}{'-'*8}{'-'*8}{'-'*8}")
    for cat, row in sorted(delta["category_deltas"].items()):
        sign = "+" if row["delta"] >= 0 else ""
        delta_str = f"{sign}{row['delta']:.1f}"
        lines.append(
            f"  {cat:<16}{row['before']:>8.1f}{row['after']:>8.1f}{delta_str:>8}"
        )

    lines.append("")
    lines.append(f"  New issues:      {delta['new_issue_count']}")
    lines.append(f"  Resolved issues: {delta['resolved_issue_count']}")

    if delta["new_issues"]:
        lines.append("")
        lines.append("  New issues:")
        for iss in delta["new_issues"][:25]:
            sym = iss.get("symbol") or iss.get("rule", "?")
            lines.append(f"    {iss.get('file', '?')}:{iss.get('line', '?')}  [{sym}]  {iss.get('message', '')}")
        if delta["new_issue_count"] > 25:
            lines.append(f"    ... and {delta['new_issue_count'] - 25} more")

    if delta["resolved_issues"]:
        lines.append("")
        lines.append("  Resolved issues:")
        for iss in delta["resolved_issues"][:10]:
            sym = iss.get("symbol") or iss.get("rule", "?")
            lines.append(f"    {iss.get('file', '?')}:{iss.get('line', '?')}  [{sym}]  {iss.get('message', '')}")
        if delta["resolved_issue_count"] > 10:
            lines.append(f"    ... and {delta['resolved_issue_count'] - 10} more")

    lines.append("")
    if is_regression(delta, tolerance):
        lines.append(
            f"  REGRESSION: score dropped {abs(delta['overall_delta']):.1f} points "
            f"(tolerance: {tolerance:.1f})"
        )
    else:
        lines.append("  No regression detected.")
    return "\n".join(lines)
