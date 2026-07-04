#!/usr/bin/env python3
"""Clone a batch of GitHub repos and grade each with `codequality scan`.

Demo/exploration script, not part of the package. Uses the `gh` CLI (must
be authenticated) for listing/cloning so private repos work the same as
public ones, and shells out to `codequality` so this always scores with
whatever version is on PATH/importable -- no import coupling to the
package internals.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_LIMIT = 15
CLONE_TIMEOUT = 120
SCAN_TIMEOUT = 180


def _run(cmd, timeout=None, check=True):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=check)


def list_repos(owner, limit, include_forks, include_archived):
    """Returns a list of {"nameWithOwner", "diskUsage"} dicts via `gh repo list`."""
    cmd = ["gh", "repo", "list", owner, "--limit", str(limit), "--json", "nameWithOwner,isFork,isArchived,diskUsage"]
    result = _run(cmd, timeout=30)
    repos = json.loads(result.stdout)
    if not include_forks:
        repos = [r for r in repos if not r["isFork"]]
    if not include_archived:
        repos = [r for r in repos if not r["isArchived"]]
    return repos


def clone_repo(full_name, dest):
    _run(["gh", "repo", "clone", full_name, str(dest), "--", "--depth", "1", "--quiet"], timeout=CLONE_TIMEOUT)


def grade_repo(dest, codequality_cmd):
    result = subprocess.run(
        [*codequality_cmd, "scan", str(dest), "--format", "json", "--no-color"],
        capture_output=True, text=True, timeout=SCAN_TIMEOUT,
    )
    if result.returncode not in (0, 1):  # 1 == "scored but below --fail-under", not an error here
        raise RuntimeError(result.stderr.strip() or f"exit code {result.returncode}")
    return json.loads(result.stdout)


def grade_one(full_name, workdir, codequality_cmd, keep_clones):
    dest = workdir / full_name.replace("/", "__")
    try:
        clone_repo(full_name, dest)
        report = grade_repo(dest, codequality_cmd)
        overall = report["overall"]
        categories = report["categories"]
        weakest = min(categories.items(), key=lambda kv: kv[1]["score"])
        return {
            "repo": full_name,
            "status": "ok",
            "score": overall["score"],
            "grade": overall["grade"],
            "loc": report.get("summary", {}).get("loc"),
            "weakest_category": weakest[0],
            "weakest_score": weakest[1]["score"],
        }
    except subprocess.TimeoutExpired:
        return {"repo": full_name, "status": "error", "error": "timed out"}
    except subprocess.CalledProcessError as e:
        return {"repo": full_name, "status": "error", "error": (e.stderr or str(e)).strip()[:200]}
    except (json.JSONDecodeError, RuntimeError, KeyError) as e:
        return {"repo": full_name, "status": "error", "error": str(e)[:200]}
    finally:
        if not keep_clones and dest.exists():
            shutil.rmtree(dest, ignore_errors=True)


def render_table(results):
    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] != "ok"]
    ok.sort(key=lambda r: r["score"], reverse=True)

    lines = [f"{'Repo':<40}{'Grade':>6}{'Score':>8}{'LOC':>9}  Weakest category"]
    lines.append("-" * 90)
    for r in ok:
        loc = r["loc"] if r["loc"] is not None else "?"
        lines.append(
            f"{r['repo']:<40}{r['grade']:>6}{r['score']:>8.1f}{str(loc):>9}  "
            f"{r['weakest_category']} ({r['weakest_score']:.1f})"
        )
    if failed:
        lines.append("")
        lines.append("Failed to grade:")
        for r in failed:
            lines.append(f"  {r['repo']}: {r['error']}")
    return "\n".join(lines)


def parse_args():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--owner", help="GitHub user/org to list repos from (default: authenticated user)")
    p.add_argument("--repos", help="Comma-separated owner/name list, overrides --owner/--limit")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help=f"Max repos to list (default {DEFAULT_LIMIT})")
    p.add_argument("--include-forks", action="store_true")
    p.add_argument("--include-archived", action="store_true")
    p.add_argument("--keep-clones", action="store_true", help="Don't delete cloned repos after scoring")
    p.add_argument("--output", "-o", help="Write JSON results to this file in addition to printing the table")
    p.add_argument(
        "--codequality-cmd", default="codequality",
        help='How to invoke codequality, e.g. "codequality" or "python3 -m codequality" (default: "codequality")'
    )
    return p.parse_args()


def main():
    args = parse_args()
    codequality_cmd = args.codequality_cmd.split()

    if args.repos:
        repo_names = [r.strip() for r in args.repos.split(",") if r.strip()]
    else:
        owner = args.owner or _run(["gh", "api", "user", "--jq", ".login"], timeout=15).stdout.strip()
        repos = list_repos(owner, args.limit, args.include_forks, args.include_archived)
        repo_names = [r["nameWithOwner"] for r in repos]

    if not repo_names:
        print("No repos to grade.", file=sys.stderr)
        return 1

    print(f"Grading {len(repo_names)} repo(s): {', '.join(repo_names)}\n", file=sys.stderr)

    results = []
    with tempfile.TemporaryDirectory(prefix="codequality-grade-") as tmp:
        workdir = Path(tmp)
        for full_name in repo_names:
            print(f"  cloning + scanning {full_name} ...", file=sys.stderr)
            results.append(grade_one(full_name, workdir, codequality_cmd, args.keep_clones))

    print()
    print(render_table(results))

    if args.output:
        Path(args.output).write_text(json.dumps(results, indent=2))
        print(f"\nWrote {args.output}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())
