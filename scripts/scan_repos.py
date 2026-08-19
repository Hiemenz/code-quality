#!/usr/bin/env python3
"""
Scan a set of GitHub repos with codequality and compare rule hit rates.

Usage:
    python scripts/scan_repos.py [--repos repo1 repo2 ...] [--workdir /tmp/scan] [--output report.html]

Repos can be:
  - GitHub slugs:  psf/black  pallets/flask
  - Local paths:   /home/pi/git/myproject

The script clones GitHub slugs into --workdir, runs `codequality scan` on each,
then produces a comparative report showing which rules fire and how often.
"""

import argparse
import datetime
import json
import os
import subprocess
import sys
import tempfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


DEFAULT_REPOS = [
    "psf/black",
    "pallets/flask",
    "encode/httpx",
    "tiangolo/fastapi",
    "cookiecutter/cookiecutter",
]


def clone_or_update(slug: str, workdir: Path) -> Path:
    dest = workdir / slug.replace("/", "__")
    if dest.exists():
        print(f"  [skip clone] {dest} already exists")
    else:
        url = f"https://github.com/{slug}.git"
        print(f"  [clone] {url}")
        subprocess.run(
            ["git", "clone", "--depth=1", "--quiet", url, str(dest)],
            check=True,
        )
    return dest


def scan(repo_path: Path) -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "codequality", "scan", str(repo_path), "--format", "json"],
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):  # 1 = findings present but not a crash
        print(f"  [warn] scan exited {result.returncode}: {result.stderr[:200]}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        print(f"  [error] could not parse JSON output for {repo_path}")
        return {}


def rule_hits(scan_data: dict) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for issue in scan_data.get("issues", []):
        rule = issue.get("rule") or issue.get("symbol") or "unknown"
        counts[rule] += 1
    return dict(counts)


def severity_hits(scan_data: dict) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for issue in scan_data.get("issues", []):
        counts[issue.get("severity", "unknown")] += 1
    return dict(counts)


def build_table(results: list[dict]) -> tuple[list[str], list[dict]]:
    """Return (sorted rule list, per-repo hit-rate rows)."""
    all_rules: set[str] = set()
    for r in results:
        all_rules.update(r["rule_hits"].keys())

    # Sort rules by total hits descending
    rule_totals = {
        rule: sum(r["rule_hits"].get(rule, 0) for r in results)
        for rule in all_rules
    }
    sorted_rules = sorted(all_rules, key=lambda r: rule_totals[r], reverse=True)

    rows = []
    for r in results:
        loc = r["summary"].get("loc", 1) or 1
        row = {
            "name": r["name"],
            "score": r["overall"].get("score", 0),
            "grade": r["overall"].get("grade", "?"),
            "loc": loc,
            "issues": r["summary"].get("issues", 0),
            "issues_per_kloc": round(r["summary"].get("issues", 0) / loc * 1000, 1),
            "severities": r["severity_hits"],
        }
        for rule in sorted_rules:
            count = r["rule_hits"].get(rule, 0)
            row[rule] = count
            row[f"{rule}_per_kloc"] = round(count / loc * 1000, 2)
        rows.append(row)

    return sorted_rules, rows


def render_html(results: list[dict], sorted_rules: list[str], rows: list[dict]) -> str:
    repo_names = [r["name"] for r in results]

    # Build rules table rows
    rule_rows_html = ""
    for rule in sorted_rules:
        cells = f"<td class='rule-name'>{rule}</td>"
        for row in rows:
            count = row.get(rule, 0)
            per_kloc = row.get(f"{rule}_per_kloc", 0)
            cls = "hit" if count > 0 else "miss"
            cells += f"<td class='{cls}'>{count}<br><span class='small'>{per_kloc}/kloc</span></td>"
        rule_rows_html += f"<tr>{cells}</tr>\n"

    # Build summary rows
    summary_rows = ""
    metrics = [
        ("Score", lambda r: f"{r['score']:.1f} ({r['grade']})"),
        ("LOC", lambda r: f"{r['loc']:,}"),
        ("Total issues", lambda r: str(r["issues"])),
        ("Issues / kloc", lambda r: str(r["issues_per_kloc"])),
        ("Errors", lambda r: str(r["severities"].get("error", 0))),
        ("Warnings", lambda r: str(r["severities"].get("warn", 0))),
    ]
    for label, fn in metrics:
        cells = f"<td class='rule-name'><strong>{label}</strong></td>"
        for row in rows:
            cells += f"<td>{fn(row)}</td>"
        summary_rows += f"<tr>{cells}</tr>\n"

    header_cells = "<th>Rule</th>" + "".join(f"<th>{n}</th>" for n in repo_names)

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>codequality — repo comparison</title>
<style>
  body {{ font-family: system-ui, sans-serif; font-size: 13px; margin: 1rem 2rem; }}
  h1 {{ font-size: 1.2rem; margin-bottom: 0.25rem; }}
  p.sub {{ color: #666; margin-top: 0; }}
  table {{ border-collapse: collapse; width: 100%; margin-top: 1rem; }}
  th, td {{ border: 1px solid #ddd; padding: 5px 8px; text-align: center; white-space: nowrap; }}
  th {{ background: #f4f4f4; position: sticky; top: 0; }}
  td.rule-name {{ text-align: left; font-family: monospace; font-size: 12px; }}
  tr:nth-child(even) {{ background: #fafafa; }}
  .hit {{ background: #fff3cd; }}
  .miss {{ color: #aaa; }}
  .small {{ font-size: 10px; color: #888; }}
  .section-header td {{ background: #e8e8e8; font-weight: bold; text-align: left; }}
  @media (prefers-color-scheme: dark) {{
    body {{ background: #1e1e1e; color: #ddd; }}
    th {{ background: #2a2a2a; }}
    td {{ border-color: #444; }}
    tr:nth-child(even) {{ background: #252525; }}
    .hit {{ background: #3a3000; }}
    .miss {{ color: #555; }}
    .small {{ color: #666; }}
    .section-header td {{ background: #333; }}
    p.sub {{ color: #999; }}
  }}
</style>
</head>
<body>
<h1>codequality — cross-repo comparison</h1>
<p class="sub">Generated {datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')} &nbsp;·&nbsp; {len(repo_names)} repos &nbsp;·&nbsp; {len(sorted_rules)} rules fired</p>
<div style="overflow-x:auto">
<table>
  <thead><tr>{header_cells}</tr></thead>
  <tbody>
    <tr class="section-header"><td colspan="{len(repo_names)+1}">Summary</td></tr>
    {summary_rows}
    <tr class="section-header"><td colspan="{len(repo_names)+1}">Rule hits (raw count + per-kloc)</td></tr>
    {rule_rows_html}
  </tbody>
</table>
</div>
</body>
</html>"""


def render_text(results: list[dict], sorted_rules: list[str], rows: list[dict]) -> str:
    lines = []
    col_w = 20
    header = f"{'Rule':<35}" + "".join(f"{r['name'][:col_w]:>{col_w}}" for r in results)
    lines.append(header)
    lines.append("-" * len(header))

    for metric_label, key in [("Score", "score"), ("LOC", "loc"), ("Issues", "issues"), ("Issues/kloc", "issues_per_kloc")]:
        row_line = f"{metric_label:<35}"
        for row in rows:
            val = str(row.get(key, ""))
            row_line += f"{val:>{col_w}}"
        lines.append(row_line)

    lines.append("")
    lines.append(f"{'--- Rules ---':<35}")
    for rule in sorted_rules:
        row_line = f"{rule:<35}"
        for row in rows:
            count = row.get(rule, 0)
            per_kloc = row.get(f"{rule}_per_kloc", 0)
            cell = f"{count} ({per_kloc}/k)" if count else "-"
            row_line += f"{cell:>{col_w}}"
        lines.append(row_line)

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repos", nargs="+", default=DEFAULT_REPOS,
                        help="GitHub slugs (org/repo) or local paths to scan")
    parser.add_argument("--workdir", default=None,
                        help="Directory to clone repos into (default: temp dir)")
    parser.add_argument("--output", "-o", default="repo_comparison.html",
                        help="Output file (default: repo_comparison.html). Use - for stdout.")
    parser.add_argument("--format", choices=["html", "text", "json"], default="html")
    parser.add_argument("--keep", action="store_true",
                        help="Keep cloned repos after the run (only relevant when --workdir is not set)")
    args = parser.parse_args()

    tmp_dir = None
    if args.workdir:
        workdir = Path(args.workdir)
        workdir.mkdir(parents=True, exist_ok=True)
    else:
        tmp_dir = tempfile.mkdtemp(prefix="cq_scan_")
        workdir = Path(tmp_dir)

    def _process_repo(repo):
        if Path(repo).exists():
            repo_path = Path(repo)
            name = repo_path.name
        else:
            print(f"\n[{repo}]")
            repo_path = clone_or_update(repo, workdir)
            name = repo
        print(f"\n[{repo}]  [scan] {repo_path}")
        data = scan(repo_path)
        if not data:
            print(f"  [skip] no usable output for {repo}")
            return None
        return {
            "name": name,
            "path": str(repo_path),
            "overall": data.get("overall", {}),
            "summary": data.get("summary", {}),
            "rule_hits": rule_hits(data),
            "severity_hits": severity_hits(data),
        }

    results = []
    with ThreadPoolExecutor(max_workers=len(args.repos)) as pool:
        futures = {pool.submit(_process_repo, repo): repo for repo in args.repos}
        for future in as_completed(futures):
            result = future.result()
            if result is not None:
                results.append(result)

    # Restore deterministic order matching the original repo list.
    repo_order = {repo: i for i, repo in enumerate(args.repos)}
    results.sort(key=lambda r: repo_order.get(r["name"], repo_order.get(r["path"], 999)))

    if not results:
        print("No results to report.")
        sys.exit(1)

    sorted_rules, rows = build_table(results)

    if args.format == "json":
        output = json.dumps({"repos": results, "rules": sorted_rules}, indent=2)
    elif args.format == "text":
        output = render_text(results, sorted_rules, rows)
    else:
        output = render_html(results, sorted_rules, rows)

    if args.output == "-":
        print(output)
    else:
        Path(args.output).write_text(output)
        print(f"\n[done] report written to {args.output}")

    if tmp_dir and not args.keep:
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
