"""Groups security-relevant findings by CWE/OWASP for `codequality compliance`.

Purely a re-grouping of issues the scanner already produced -- no new
detection logic. Every issue's `rule` is looked up in `codequality.rules`
for its `cwe`/`owasp` metadata (see rules.py); issues on rules without a
mapping are counted separately as "unmapped" rather than dropped, so the
report total always reconciles with the number of security-relevant
findings found.

"Security-relevant" is broader than `category == "security"`: a few rules
outside that category (e.g. `unclosed-resource` -> CWE-404, `deprecated-api`
-> CWE-477) still carry a `cwe`, and those should show up in an audit
report too. An issue is included if it's `category == "security"` *or* its
rule carries a `cwe`/`owasp` tag -- a correctness-category issue with no
such tag is just not what this report is for, so it's excluded entirely
rather than padding "unmapped" with noise.
"""

from collections import defaultdict

from codequality.rules import get_rule


def build_report(issues):
    """issues: list of Issue.to_dict()-shaped dicts (needs `rule`, `category`).

    Returns {"cwe": {id: [issue, ...]}, "owasp": {id: [issue, ...]},
    "unmapped": [issue, ...], "total": int}.
    """
    report = {"cwe": defaultdict(list), "owasp": defaultdict(list), "unmapped": [], "total": 0}
    for issue in issues:
        rule = get_rule(issue.get("rule"))
        cwe = rule.get("cwe") if rule else None
        owasp = rule.get("owasp") if rule else None
        if issue.get("category") != "security" and not cwe and not owasp:
            continue
        report["total"] += 1
        if cwe:
            report["cwe"][cwe].append(issue)
        if owasp:
            report["owasp"][owasp].append(issue)
        if not cwe and not owasp:
            report["unmapped"].append(issue)
    report["cwe"] = dict(report["cwe"])
    report["owasp"] = dict(report["owasp"])
    return report


def render_text(report):
    if report["total"] == 0:
        return "No security-relevant findings."

    lines = [f"Compliance Report ({report['total']} security-relevant finding(s))", ""]

    lines.append(f"By CWE ({len(report['cwe'])} categories):")
    for cwe in sorted(report["cwe"], key=lambda c: -len(report["cwe"][c])):
        findings = report["cwe"][cwe]
        lines.append(f"  {cwe:<10} {len(findings):>4}  {sorted({f['rule'] for f in findings})}")
    lines.append("")

    lines.append(f"By OWASP Top 10 2021 ({len(report['owasp'])} categories):")
    for owasp in sorted(report["owasp"]):
        findings = report["owasp"][owasp]
        lines.append(f"  {owasp:<10} {len(findings):>4}  {sorted({f['rule'] for f in findings})}")
    lines.append("")

    if report["unmapped"]:
        lines.append(f"Unmapped ({len(report['unmapped'])}): {sorted({f['rule'] for f in report['unmapped']})}")

    return "\n".join(lines)
