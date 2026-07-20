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


# Functions below both floors are never worth listing in the complexity
# table, no matter how clean the rest of the repo is -- without a floor,
# a tidy repo's report would "top out" with complexity-3 functions and
# imply they're a problem. Fixed values rather than config limits on
# purpose: the table is a repo-wide "these are your hardest functions"
# ranking meant to stay visible even when everything passes the
# per-function limits, not another pass/fail gate.
_COMPLEXITY_TABLE_FLOOR_COGNITIVE = 8
_COMPLEXITY_TABLE_FLOOR_MCCABE = 8


def _complex_functions(file_metrics_list, limit=10):
    """Top `limit` functions repo-wide by cognitive complexity (McCabe as
    tie-break) -- the always-visible "known complexity debt" ranking; see
    the floor comment above.
    """
    ranked = [
        fn
        for fm in file_metrics_list
        for fn in fm.functions
        if fn.cognitive >= _COMPLEXITY_TABLE_FLOOR_COGNITIVE or fn.complexity >= _COMPLEXITY_TABLE_FLOOR_MCCABE
    ]
    ranked.sort(key=lambda fn: (fn.cognitive, fn.complexity), reverse=True)
    return [
        {
            "file": fn.file,
            "line": fn.lineno,
            "name": fn.name,
            "cognitive": fn.cognitive,
            "complexity": fn.complexity,
            "length": fn.length,
            "nesting": fn.nesting,
        }
        for fn in ranked[:limit]
    ]


def _totals(file_metrics_list, issues):
    """Whole-scan counters for the report's `summary` block."""
    total_loc = sum(fm.loc for fm in file_metrics_list)
    test_loc = sum(fm.loc for fm in file_metrics_list if is_test_file(fm.path))
    source_loc = total_loc - test_loc
    return {
        "files_analyzed": len(file_metrics_list),
        "loc": total_loc,
        "functions": sum(len(fm.functions) for fm in file_metrics_list),
        "issues": len(issues),
        "suppressed": sum(fm.suppressed_count for fm in file_metrics_list),
        "test_loc": test_loc,
        "source_loc": source_loc,
        "test_ratio": (test_loc / source_loc) if source_loc else None,
    }


def _threshold(issues, score_result, fail_under, fail_on):
    """Evaluate --fail-under/--fail-on for the report's `threshold` block."""
    score_passed = score_result.overall >= fail_under if fail_under is not None else True
    fail_on_categories = [c.strip() for cs in (fail_on or []) for c in cs.split(",") if c.strip()]
    flagged = {issue.category for issue in issues}
    fail_on_triggered = [cat for cat in fail_on_categories if cat in flagged]
    return {
        "fail_under": fail_under,
        "fail_on": fail_on_categories or None,
        "fail_on_triggered": fail_on_triggered or None,
        "passed": score_passed and not fail_on_triggered,
    }


def build_summary(file_metrics_list, score_result, mode, root, diff_info=None, fail_under=None, fail_on=None, use_color=True):
    """Assemble the format-agnostic report dict consumed by render_json/text/markdown."""
    issues = _all_issues(file_metrics_list)
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
        "summary": _totals(file_metrics_list, issues),
        "worst_files": [{"path": p, "score": s, "lines": n} for p, s, n in _worst_files(file_metrics_list)],
        "complex_functions": _complex_functions(file_metrics_list),
        "issues": [i.to_dict() for i in issues],
        "diff": diff_info,
        "threshold": _threshold(issues, score_result, fail_under, fail_on),
    }


def _version():
    from codequality import __version__

    return __version__


def render_json(summary):
    return json.dumps(summary, indent=2, sort_keys=False)


def _badge_color(score):
    """Map a 0-100 score to a shields.io named color."""
    if score >= 90:
        return "brightgreen"
    if score >= 80:
        return "green"
    if score >= 70:
        return "yellowgreen"
    if score >= 60:
        return "yellow"
    if score >= 50:
        return "orange"
    return "red"


def render_badge(summary):
    """Render `summary` as shields.io endpoint JSON.

    Serve the output over HTTPS (e.g. as a gist or pages artifact) and
    point https://img.shields.io/endpoint?url=... at it to get a live
    score badge in a README.
    """
    score = summary["overall"]["score"]
    grade = summary["overall"]["grade"]
    return json.dumps(
        {
            "schemaVersion": 1,
            "label": "code quality",
            "message": f"{score:.1f} ({grade})",
            "color": _badge_color(score),
        },
        indent=2,
    )


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

    if summary.get("complex_functions"):
        lines.append("")
        lines.append(f"{c('bold')}Most complex functions{c('reset')} (cognitive/McCabe -- known debt, not a gate)")
        for cf in summary["complex_functions"]:
            lines.append(
                f"  cog {cf['cognitive']:>3}  mccabe {cf['complexity']:>3}  "
                f"{cf['file']}:{cf['line']}  {cf['name']} ({cf['length']} lines)"
            )

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
    if threshold["fail_under"] is not None or threshold.get("fail_on"):
        status = "PASS" if threshold["passed"] else "FAIL"
        color = c("A") if threshold["passed"] else c("F")
        parts = []
        if threshold["fail_under"] is not None:
            parts.append(f"fail_under={threshold['fail_under']} actual={score}")
        if threshold.get("fail_on"):
            triggered = threshold.get("fail_on_triggered") or []
            parts.append(
                f"fail_on={','.join(threshold['fail_on'])}"
                + (f" (triggered: {','.join(triggered)})" if triggered else "")
            )
        lines.append(f"{color}{c('bold')}{status}{c('reset')} - {'; '.join(parts)}")

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

    if summary.get("complex_functions"):
        lines.append("")
        lines.append("### Most complex functions (known debt, not a gate)")
        lines.append("| Cognitive | McCabe | Function | Lines |")
        lines.append("|---|---|---|---|")
        for cf in summary["complex_functions"]:
            lines.append(
                f"| {cf['cognitive']} | {cf['complexity']} | `{cf['file']}:{cf['line']}` {cf['name']} "
                f"| {cf['length']} |"
            )

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
    if threshold["fail_under"] is not None or threshold.get("fail_on"):
        status = "PASS" if threshold["passed"] else "FAIL"
        parts = []
        if threshold["fail_under"] is not None:
            parts.append(f"fail_under={threshold['fail_under']}, actual={score}")
        if threshold.get("fail_on"):
            triggered = threshold.get("fail_on_triggered") or []
            parts.append(
                f"fail_on={','.join(threshold['fail_on'])}"
                + (f" (triggered: {','.join(triggered)})" if triggered else "")
            )
        lines.append("")
        lines.append(f"**{status}** — {'; '.join(parts)}")

    return "\n".join(lines)


def _h(text):
    """Escape HTML special characters in `text`."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def render_html(summary):
    """Render `summary` as a self-contained HTML page (no external dependencies)."""
    grade = summary["overall"]["grade"]
    score = summary["overall"]["score"]
    mode = summary["mode"]
    s = summary["summary"]

    grade_color = {"A": "#22c55e", "B": "#84cc16", "C": "#eab308", "D": "#f97316", "F": "#ef4444"}.get(grade, "#6b7280")
    sev_color = {"error": "#ef4444", "warn": "#f97316", "info": "#3b82f6"}

    # Category rows
    cat_rows = ""
    for name, cat in summary["categories"].items():
        pct = cat["score"]
        bar_color = "#22c55e" if pct >= 80 else ("#eab308" if pct >= 60 else "#ef4444")
        cat_rows += (
            f'<tr><td>{_h(name)}</td>'
            f'<td><div class="bar-bg"><div class="bar-fg" style="width:{pct}%;background:{bar_color}"></div></div></td>'
            f'<td class="num">{pct:.1f}</td>'
            f'<td class="num">{cat["weight"]}</td></tr>\n'
        )

    # Worst files rows
    wf_rows = "".join(
        f'<tr><td class="num">{wf["score"]:.1f}</td><td class="mono">{_h(wf["path"])}</td>'
        f'<td class="num">{wf["lines"]}</td></tr>\n'
        for wf in summary["worst_files"]
    )

    # Issues rows
    issue_rows = ""
    for issue in summary["issues"]:
        sc = sev_color.get(issue["severity"], "#6b7280")
        issue_rows += (
            f'<tr><td><span class="badge" style="background:{sc}">{_h(issue["severity"])}</span></td>'
            f'<td class="mono">{_h(issue["file"])}:{issue["line"]}</td>'
            f'<td class="mono">{_h(issue["symbol"])}</td>'
            f'<td>{_h(issue["message"])}</td></tr>\n'
        )

    diff_note = ""
    if summary.get("diff"):
        d = summary["diff"]
        head = d.get("head") or "working tree"
        diff_note = f'<p class="dim">Diff: {_h(d["base"])} → {_h(head)} ({len(d["changed_files"])} files changed)</p>'

    threshold = summary["threshold"]
    gate_html = ""
    if threshold["fail_under"] is not None:
        status = "PASS" if threshold["passed"] else "FAIL"
        gate_color = "#22c55e" if threshold["passed"] else "#ef4444"
        gate_html = (
            f'<p style="font-weight:bold;color:{gate_color}">{status} — '
            f'threshold: fail_under={threshold["fail_under"]}, actual={score}</p>'
        )

    issues_section = ""
    if summary["issues"]:
        issues_section = f"""
<h2>Issues ({s['issues']})</h2>
<input id="filter" type="text" placeholder="Filter issues..." oninput="filterIssues(this.value)"
       style="margin-bottom:8px;padding:4px 8px;width:300px;border:1px solid #d1d5db;border-radius:4px">
<div style="overflow-x:auto">
<table id="issue-table">
<thead><tr><th>Severity</th><th>Location</th><th>Rule</th><th>Message</th></tr></thead>
<tbody>{issue_rows}</tbody>
</table>
</div>
<script>
function filterIssues(q) {{
  q = q.toLowerCase();
  var rows = document.querySelectorAll('#issue-table tbody tr');
  rows.forEach(function(r) {{
    r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none';
  }});
}}
</script>
"""

    wf_section = ""
    if summary["worst_files"]:
        wf_section = f"""
<h2>Lowest-scoring files</h2>
<div style="overflow-x:auto">
<table>
<thead><tr><th>Score</th><th>File</th><th>Lines</th></tr></thead>
<tbody>{wf_rows}</tbody>
</table>
</div>
"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Quality Report</title>
<style>
:root {{ font-family: system-ui, sans-serif; font-size: 15px; color: #111; background: #fff; }}
@media (prefers-color-scheme: dark) {{
  :root {{ color: #e5e7eb; background: #111827; }}
  table {{ border-color: #374151; }}
  th {{ background: #1f2937; }}
  tr:nth-child(even) {{ background: #1f2937; }}
  input {{ background: #1f2937; color: #e5e7eb; border-color: #374151; }}
}}
body {{ max-width: 1100px; margin: 0 auto; padding: 24px 16px; }}
h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
h2 {{ font-size: 1.1rem; margin-top: 32px; border-bottom: 1px solid #e5e7eb; padding-bottom: 4px; }}
.score-badge {{ display:inline-block; padding: 8px 20px; border-radius: 8px;
               font-size: 2rem; font-weight: bold; color: #fff;
               background: {grade_color}; margin: 12px 0; }}
.dim {{ color: #6b7280; font-size: 0.9rem; }}
table {{ width: 100%; border-collapse: collapse; font-size: 0.9rem; }}
th, td {{ text-align: left; padding: 6px 10px; border-bottom: 1px solid #e5e7eb; }}
th {{ font-weight: 600; background: #f9fafb; }}
tr:nth-child(even) {{ background: #f9fafb; }}
.num {{ text-align: right; }}
.mono {{ font-family: monospace; font-size: 0.85rem; word-break: break-all; }}
.badge {{ display:inline-block; padding:1px 7px; border-radius:4px; font-size:0.78rem;
          font-weight:600; color:#fff; }}
.bar-bg {{ background: #e5e7eb; border-radius: 4px; height: 10px; width: 180px; }}
.bar-fg {{ height: 10px; border-radius: 4px; }}
.stats {{ display: flex; gap: 24px; flex-wrap: wrap; margin: 16px 0; }}
.stat {{ text-align: center; }}
.stat-val {{ font-size: 1.4rem; font-weight: bold; }}
.stat-lbl {{ font-size: 0.8rem; color: #6b7280; }}
</style>
</head>
<body>
<h1>Code Quality Report <small class="dim">({_h(mode)} mode)</small></h1>
{diff_note}
<div class="score-badge">{score:.1f} / 100 &nbsp; {_h(grade)}</div>
{gate_html}
<div class="stats">
  <div class="stat"><div class="stat-val">{s['files_analyzed']}</div><div class="stat-lbl">files</div></div>
  <div class="stat"><div class="stat-val">{s['loc']}</div><div class="stat-lbl">LOC</div></div>
  <div class="stat"><div class="stat-val">{s['functions']}</div><div class="stat-lbl">functions</div></div>
  <div class="stat"><div class="stat-val">{s['issues']}</div><div class="stat-lbl">issues</div></div>
</div>

<h2>Category Scores</h2>
<table>
<thead><tr><th>Category</th><th>Score</th><th>Score/100</th><th>Weight</th></tr></thead>
<tbody>{cat_rows}</tbody>
</table>

{wf_section}
{issues_section}

<p class="dim">Generated {_h(summary['generated_at'])} · codequality {_h(summary['version'])}</p>
</body>
</html>"""
