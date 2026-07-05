"""Renders a ScoreResult + file metrics into text (terminal), JSON
(machine-readable, for other pipeline steps), or markdown (for posting
as a PR comment).
"""

import json
from datetime import datetime, timezone

from codequality.property_scaffold import is_test_file
from codequality.scorer import score_single_file

_SEVERITY_ORDER = {"error": 0, "warn": 1, "info": 2}

_COLORS = {
    "A": "\033[92m",
    "B": "\033[92m",
    "C": "\033[93m",
    "D": "\033[93m",
    "F": "\033[91m",
    "reset": "\033[0m",
    "bold": "\033[1m",
    "dim": "\033[2m",
}


def _all_issues(file_metrics_list):
    issues = []
    for fm in file_metrics_list:
        issues.extend(fm.issues)
    issues.sort(key=lambda i: (_SEVERITY_ORDER.get(i.severity, 9), i.file, i.line))
    return issues


def _worst_files(file_metrics_list, limit=10):
    scored = [(fm.path, score_single_file(fm), fm.total_lines) for fm in file_metrics_list]
    scored.sort(key=lambda t: t[1])
    return scored[:limit]


def build_summary(file_metrics_list, score_result, mode, root, diff_info=None, fail_under=None, use_color=True):
    """Assemble the format-agnostic report dict consumed by render_json/text/markdown."""
    issues = _all_issues(file_metrics_list)
    total_functions = sum(len(fm.functions) for fm in file_metrics_list)
    total_loc = sum(fm.loc for fm in file_metrics_list)
    total_suppressed = sum(fm.suppressed_count for fm in file_metrics_list)
    test_loc = sum(fm.loc for fm in file_metrics_list if is_test_file(fm.path))
    source_loc = total_loc - test_loc
    test_ratio = (test_loc / source_loc) if source_loc else None
    passed = score_result.overall >= fail_under if fail_under is not None else True

    return {
        "tool": "codequality",
        "version": _version(),
        "mode": mode,
        "root": root,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "overall": {"score": score_result.overall, "grade": score_result.grade},
        "categories": {
            name: {"score": cat.score, "weight": cat.weight} for name, cat in score_result.categories.items()
        },
        "summary": {
            "files_analyzed": len(file_metrics_list),
            "loc": total_loc,
            "functions": total_functions,
            "issues": len(issues),
            "suppressed": total_suppressed,
            "test_loc": test_loc,
            "source_loc": source_loc,
            "test_ratio": test_ratio,
        },
        "worst_files": [{"path": p, "score": s, "lines": n} for p, s, n in _worst_files(file_metrics_list)],
        "issues": [i.to_dict() for i in issues],
        "diff": diff_info,
        "threshold": {"fail_under": fail_under, "passed": passed},
    }


def _version():
    from codequality import __version__

    return __version__


def render_json(summary):
    return json.dumps(summary, indent=2, sort_keys=False)


def render_text(summary, use_color=True, max_issues=25):
    """Render `summary` as a colored, human-readable terminal report."""
    def c(name):
        return _COLORS[name] if use_color else ""

    lines = []
    grade = summary["overall"]["grade"]
    score = summary["overall"]["score"]
    mode = summary["mode"]

    lines.append(f"{c('bold')}Code Quality Report{c('reset')} ({mode} mode)")
    lines.append(f"Root: {summary['root']}")
    if summary.get("diff"):
        d = summary["diff"]
        head = d.get("head") or "working tree"
        lines.append(f"Diff: {d['base']} -> {head}  ({len(d['changed_files'])} files changed)")
    lines.append("")
    lines.append(f"{c(grade)}{c('bold')}Overall score: {score}/100  (grade {grade}){c('reset')}")
    lines.append("")
    lines.append(f"{c('bold')}Categories{c('reset')}")
    for name, cat in summary["categories"].items():
        bar_len = int(cat["score"] / 5)
        bar = "#" * bar_len + "-" * (20 - bar_len)
        lines.append(f"  {name:<14} [{bar}] {cat['score']:>5.1f}/100  (weight {cat['weight']})")
    lines.append("")
    s = summary["summary"]
    suppressed_note = f"   Suppressed: {s['suppressed']}" if s["suppressed"] else ""
    lines.append(
        f"{c('dim')}Files analyzed: {s['files_analyzed']}   LOC: {s['loc']}   "
        f"Functions: {s['functions']}   Issues: {s['issues']}{suppressed_note}{c('reset')}"
    )

    if summary["worst_files"]:
        lines.append("")
        lines.append(f"{c('bold')}Lowest-scoring files{c('reset')}")
        for wf in summary["worst_files"]:
            lines.append(f"  {wf['score']:>5.1f}  {wf['path']} ({wf['lines']} lines)")

    if summary["issues"]:
        lines.append("")
        lines.append(f"{c('bold')}Issues{c('reset')} (showing up to {max_issues}, sorted by severity)")
        for issue in summary["issues"][:max_issues]:
            sev = issue["severity"].upper()
            lines.append(f"  [{sev:<5}] {issue['file']}:{issue['line']}  {issue['symbol']} - {issue['message']}")
        remaining = len(summary["issues"]) - max_issues
        if remaining > 0:
            lines.append(f"  ... and {remaining} more (see --format json for the full list)")

    threshold = summary["threshold"]
    lines.append("")
    if threshold["fail_under"] is not None:
        status = "PASS" if threshold["passed"] else "FAIL"
        color = c("A") if threshold["passed"] else c("F")
        lines.append(
            f"{color}{c('bold')}{status}{c('reset')} - threshold: fail_under={threshold['fail_under']}, "
            f"actual={score}"
        )

    return "\n".join(lines)


_SARIF_LEVEL = {"error": "error", "warn": "warning", "info": "note"}


def _sarif_rule(symbol):
    return {
        "id": symbol,
        "shortDescription": {"text": symbol.replace("-", " ").capitalize()},
    }


def render_sarif(summary):
    """Render `summary` as SARIF 2.1.0, for GitHub code scanning / other SARIF consumers."""
    issues = summary["issues"]
    rules = {i["symbol"]: _sarif_rule(i["symbol"]) for i in issues}
    results = [
        {
            "ruleId": i["symbol"],
            "level": _SARIF_LEVEL.get(i["severity"], "warning"),
            "message": {"text": i["message"]},
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": i["file"]},
                        "region": {"startLine": max(1, i["line"])},
                    }
                }
            ],
        }
        for i in issues
    ]
    sarif = {
        "$schema": "https://raw.githubusercontent.com/oasis-tcs/sarif-spec/master/Schemata/sarif-schema-2.1.0.json",
        "version": "2.1.0",
        "runs": [
            {
                "tool": {
                    "driver": {
                        "name": "codequality",
                        "version": summary["version"],
                        "rules": sorted(rules.values(), key=lambda r: r["id"]),
                    }
                },
                "results": results,
            }
        ],
    }
    return json.dumps(sarif, indent=2, sort_keys=False)


def render_markdown(summary):
    """Render `summary` as markdown, suitable for posting as a PR comment."""
    grade = summary["overall"]["grade"]
    score = summary["overall"]["score"]
    lines = [f"## Code Quality Report ({summary['mode']} mode)", ""]
    if summary.get("diff"):
        d = summary["diff"]
        head = d.get("head") or "working tree"
        lines.append(f"Diff: `{d['base']}` -> `{head}` ({len(d['changed_files'])} files changed)")
        lines.append("")
    lines.append(f"### Overall score: **{score}/100** (grade **{grade}**)")
    lines.append("")
    lines.append("| Category | Score | Weight |")
    lines.append("|---|---|---|")
    for name, cat in summary["categories"].items():
        lines.append(f"| {name} | {cat['score']}/100 | {cat['weight']} |")
    lines.append("")
    s = summary["summary"]
    suppressed_note = f" · Suppressed: {s['suppressed']}" if s["suppressed"] else ""
    lines.append(
        f"Files analyzed: {s['files_analyzed']} · LOC: {s['loc']} · "
        f"Functions: {s['functions']} · Issues: {s['issues']}{suppressed_note}"
    )

    if summary["worst_files"]:
        lines.append("")
        lines.append("### Lowest-scoring files")
        lines.append("| Score | File | Lines |")
        lines.append("|---|---|---|")
        for wf in summary["worst_files"]:
            lines.append(f"| {wf['score']:.1f} | `{wf['path']}` | {wf['lines']} |")

    if summary["issues"]:
        lines.append("")
        lines.append("<details><summary>Issues (" + str(len(summary["issues"])) + ")</summary>")
        lines.append("")
        lines.append("| Severity | Location | Rule | Message |")
        lines.append("|---|---|---|---|")
        for issue in summary["issues"][:200]:
            lines.append(
                f"| {issue['severity']} | `{issue['file']}:{issue['line']}` | {issue['symbol']} | {issue['message']} |"
            )
        lines.append("")
        lines.append("</details>")

    threshold = summary["threshold"]
    if threshold["fail_under"] is not None:
        status = "PASS" if threshold["passed"] else "FAIL"
        lines.append("")
        lines.append(f"**{status}** — threshold: fail_under={threshold['fail_under']}, actual={score}")

    return "\n".join(lines)
