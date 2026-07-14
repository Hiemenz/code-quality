import argparse
import json
import os
import sys

from codequality import (
    __version__, ai_report, api_diff, arch_conformance, baseline as baseline_mod, churn, commit_lint,
    complexity_coverage_risk, complexity_regression_diff, complexity_trend, config_drift, config_validate, conventions,
    dead_code_confidence,
    dependency_check, dependency_risk, edit_distance, env_check, feature_flags, flakiness, hallucination_metrics,
    history_secrets, hotspots, large_files, migration_check, mutation, orphaned_config, ownership, pipeline,
    property_scaffold, todo_age,
)
from codequality.config import Config
from codequality.coverage_check import DEFAULT_TEST_COMMAND
from codequality.git_utils import GitError, get_changed_files, get_last_commit_subject, is_git_repo, resolve_default_base
from codequality.history import append_entry, read_entries, render_trend_text
from codequality.report import build_summary, render_html, render_json, render_markdown, render_sarif, render_text
from codequality.scanner import discover_files, scan_changed, scan_repo
from codequality.scorer import compute_scores


def _add_common_args(p):
    p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    p.add_argument("--format", choices=["text", "json", "markdown", "sarif", "html"], default="text")
    p.add_argument("--output", "-o", help="Write report to a file instead of stdout")
    p.add_argument("--fail-under", type=float, default=None, help="Exit non-zero if the overall score is below this")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    p.add_argument("--no-generic", action="store_true", help="Only analyze Python files (skip heuristic analyzers)")
    p.add_argument(
        "--include-generated", action="store_true",
        help="Score auto-detected generated files too (protobuf, migrations, ... -- see README; "
             "excluded by default)"
    )
    p.add_argument(
        "--baseline", metavar="FILE",
        help="Forgive issues already recorded in this baseline file (see `codequality baseline`)"
    )
    p.add_argument(
        "--check-imports", action="store_true",
        help="Flag Python imports that don't resolve in this environment (opt-in; see README)"
    )
    p.add_argument(
        "--check-types", action="store_true",
        help="Run mypy and fold its findings into the correctness category (opt-in; requires codequality[types])"
    )
    p.add_argument(
        "--check-coverage", action="store_true",
        help="Run the repo's own test suite under coverage.py (opt-in; requires codequality[coverage]; "
             "executes the repo's code -- see README)"
    )
    p.add_argument(
        "--test-command", default=None,
        help=f'Command to run under coverage, as args after "python -m" (default: "{DEFAULT_TEST_COMMAND}")'
    )
    p.add_argument(
        "--fail-on", metavar="CATEGORY",
        help="Exit non-zero if any issue in this category exists, regardless of the overall score "
             "(e.g. --fail-on security).  Repeatable (or comma-separated) to gate on multiple categories.",
        action="append", default=[],
    )


def _add_scan_diff_subparsers(sub):
    scan_p = sub.add_parser("scan", help="Score the entire repository")
    _add_common_args(scan_p)
    scan_p.add_argument(
        "--record-history", metavar="FILE",
        help="Append this run's overall/category scores as a JSON line to FILE"
    )

    diff_p = sub.add_parser("diff", help="Score only the code changed relative to a git base")
    _add_common_args(diff_p)
    diff_p.add_argument("--base", default=None, help="Git ref to diff against (default: auto-detect)")
    diff_p.add_argument("--head", default=None, help="Git ref for the 'after' state (default: working tree)")
    diff_p.add_argument(
        "--task-description", default=None,
        help="Description of the intended change, for the scope-mismatch check (default: last commit subject)"
    )


def _add_trend_subparser(sub):
    trend_p = sub.add_parser("trend", help="Show the score trend recorded by `scan --record-history`")
    trend_p.add_argument("history_file", help="Path to the JSONL file written by --record-history")
    trend_p.add_argument("--format", choices=["text", "json"], default="text")
    trend_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_baseline_subparser(sub):
    baseline_p = sub.add_parser(
        "baseline", help="Snapshot current issues so `--baseline FILE` only fails on new ones"
    )
    baseline_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to snapshot (default: .)")
    baseline_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    baseline_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    baseline_p.add_argument("--no-generic", action="store_true", help="Only analyze Python files")
    baseline_p.add_argument(
        "--output", "-o", default=".codequality-baseline.json",
        help="Baseline file to write (default: .codequality-baseline.json)"
    )


def _add_churn_subparser(sub):
    churn_p = sub.add_parser(
        "churn", help="Compare how often AI-assisted vs. human commits need rework soon after landing"
    )
    churn_p.add_argument("path", nargs="?", default=".", help="Git repo root (default: .)")
    churn_p.add_argument(
        "--marker", default=churn.DEFAULT_MARKER,
        help=f'Substring in the commit message that marks it AI-assisted (default: "{churn.DEFAULT_MARKER}")'
    )
    churn_p.add_argument(
        "--window-days", type=int, default=churn.DEFAULT_WINDOW_DAYS,
        help=f"Days after a commit to look for follow-up changes to the same files "
             f"(default: {churn.DEFAULT_WINDOW_DAYS})"
    )
    churn_p.add_argument("--since", default=None, help="Only consider commits since this date/ref (git --since syntax)")
    churn_p.add_argument("--format", choices=["text", "json"], default="text")
    churn_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_edit_distance_subparser(sub):
    edit_distance_p = sub.add_parser(
        "edit-distance", help="Compare how much of AI-assisted vs. human commits' added lines survive to HEAD"
    )
    edit_distance_p.add_argument("path", nargs="?", default=".", help="Git repo root (default: .)")
    edit_distance_p.add_argument(
        "--marker", default=edit_distance.DEFAULT_MARKER,
        help=f'Substring in the commit message that marks it AI-assisted (default: "{edit_distance.DEFAULT_MARKER}")'
    )
    edit_distance_p.add_argument(
        "--since", default=None, help="Only consider commits since this date/ref (git --since syntax)"
    )
    edit_distance_p.add_argument("--format", choices=["text", "json"], default="text")
    edit_distance_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_commit_lint_subparser(sub):
    commit_lint_p = sub.add_parser(
        "commit-lint", help="Check commit subject lines against fixed, deterministic quality rules"
    )
    commit_lint_p.add_argument("path", nargs="?", default=".", help="Git repo root (default: .)")
    commit_lint_p.add_argument(
        "--marker", default=commit_lint.DEFAULT_MARKER,
        help=f'Substring in the commit message that marks it AI-assisted (default: "{commit_lint.DEFAULT_MARKER}")'
    )
    commit_lint_p.add_argument(
        "--since", default=None, help="Only consider commits since this date/ref (git --since syntax)"
    )
    commit_lint_p.add_argument(
        "--strict", action="store_true",
        help="Also apply the trailing-period and not-capitalized rules (off by default -- more "
             "contentious style opinions)"
    )
    commit_lint_p.add_argument("--format", choices=["text", "json"], default="text")
    commit_lint_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_scaffold_subparser(sub):
    scaffold_p = sub.add_parser(
        "scaffold-properties",
        help="Report property-based test usage and generate Hypothesis test stubs (Python only)"
    )
    scaffold_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to scan (default: .)")
    scaffold_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    scaffold_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    scaffold_p.add_argument("--max", type=int, default=25, help="Maximum number of stub tests to generate")
    scaffold_p.add_argument(
        "--output", "-o", default="property_test_stubs.py",
        help="Stub file to write (default: property_test_stubs.py)"
    )


def _add_mutation_subparser(sub):
    mutation_p = sub.add_parser(
        "mutation",
        help="Run mutation testing (mutmut) -- slow; requires [tool.mutmut] config and codequality[mutation]"
    )
    mutation_p.add_argument("path", nargs="?", default=".", help="Repo root with a [tool.mutmut] config (default: .)")
    mutation_p.add_argument("--format", choices=["text", "json"], default="text")
    mutation_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_flakiness_subparser(sub):
    flakiness_p = sub.add_parser(
        "flakiness",
        help="Rerun the repo's own test suite multiple times and report tests whose pass/fail result "
             "isn't stable across runs -- executes the repo's code repeatedly and can be slow"
    )
    flakiness_p.add_argument("path", nargs="?", default=".", help="Repo root to test (default: .)")
    flakiness_p.add_argument(
        "--runs", type=int, default=flakiness.DEFAULT_RUNS,
        help=f"Number of times to run the test suite (default: {flakiness.DEFAULT_RUNS})"
    )
    flakiness_p.add_argument(
        "--test-command", default=None,
        help=f'Command to run, as args after "python -m" (default: "{flakiness.DEFAULT_TEST_COMMAND}")'
    )
    flakiness_p.add_argument("--format", choices=["text", "json"], default="text")
    flakiness_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_pipeline_subparser(sub):
    pipeline_p = sub.add_parser(
        "pipeline",
        help="Run the repo's own format/lint/test/... commands (from [pipeline] config), plus codequality's "
             "own scan, as one combined gate"
    )
    pipeline_p.add_argument("path", nargs="?", default=".", help="Repo root to run the pipeline in (default: .)")
    pipeline_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    pipeline_p.add_argument(
        "--fail-under", type=float, default=None,
        help="Exit non-zero if codequality's own score is below this, even if every external step passed"
    )
    pipeline_p.add_argument("--format", choices=["text", "json"], default="text")
    pipeline_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")
    pipeline_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    pipeline_p.add_argument("--no-generic", action="store_true", help="Only analyze Python files")
    pipeline_p.add_argument(
        "--continue-on-failure", action="store_true",
        help="Run every configured step even after one fails, instead of stopping at the first failure"
    )


def _add_complexity_trend_subparser(sub):
    ct_p = sub.add_parser(
        "complexity-trend",
        help="Track per-function cyclomatic complexity across repeated runs (own snapshot file, "
             "separate from `scan --record-history`/`trend`)"
    )
    ct_sub = ct_p.add_subparsers(dest="ct_mode", required=True)

    snap_p = ct_sub.add_parser(
        "snapshot", help="Append a snapshot of every function's current complexity to a JSONL file"
    )
    snap_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to scan (default: .)")
    snap_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    snap_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    snap_p.add_argument("--no-generic", action="store_true", help="Only analyze Python files")
    snap_p.add_argument(
        "--output", "-o", required=True, metavar="FILE",
        help="Snapshot JSONL file to append this run's snapshot to (created if missing)"
    )

    show_p = ct_sub.add_parser(
        "show",
        help="Report which functions have gotten more complex between the earliest and most recent snapshot"
    )
    show_p.add_argument("snapshot_file", help="Path to the JSONL file written by `complexity-trend snapshot`")
    show_p.add_argument("--top", type=int, default=25, help="Max number of functions to report (default: 25)")
    show_p.add_argument("--format", choices=["text", "json"], default="text")
    show_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_api_diff_subparser(sub):
    api_diff_p = sub.add_parser(
        "api-diff",
        help="Compare every Python file's public API between two arbitrary git refs (e.g. two tags)"
    )
    api_diff_p.add_argument("path", nargs="?", default=".", help="Git repo root to compare (default: .)")
    api_diff_p.add_argument(
        "--from", dest="from_ref", required=True, metavar="REF", help="Git ref for the 'before' state"
    )
    api_diff_p.add_argument(
        "--to", dest="to_ref", default="HEAD", metavar="REF", help="Git ref for the 'after' state (default: HEAD)"
    )
    api_diff_p.add_argument("--format", choices=["text", "json"], default="text")
    api_diff_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_complexity_regression_subparser(sub):
    cr_p = sub.add_parser(
        "complexity-regression",
        help="Compare every Python function's cyclomatic complexity between two arbitrary git refs "
             "(e.g. two tags), flagging functions that got significantly more complex"
    )
    cr_p.add_argument("path", nargs="?", default=".", help="Git repo root to compare (default: .)")
    cr_p.add_argument(
        "--from", dest="from_ref", required=True, metavar="REF", help="Git ref for the 'before' state"
    )
    cr_p.add_argument(
        "--to", dest="to_ref", default="HEAD", metavar="REF", help="Git ref for the 'after' state (default: HEAD)"
    )
    cr_p.add_argument(
        "--threshold", type=int, default=complexity_regression_diff.DEFAULT_THRESHOLD,
        help="Absolute complexity increase above which a function is flagged "
             f"(default: {complexity_regression_diff.DEFAULT_THRESHOLD})"
    )
    cr_p.add_argument("--format", choices=["text", "json"], default="text")
    cr_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_hallucination_rate_subparser(sub):
    hall_p = sub.add_parser(
        "hallucination-rate",
        help="Roll up --check-imports/--check-types findings by AI-assisted vs. human git-blame attribution"
    )
    hall_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    hall_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    hall_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    hall_p.add_argument(
        "--check-imports", action="store_true",
        help="Roll up unresolved-import findings (at least one of --check-imports/--check-types is required)"
    )
    hall_p.add_argument(
        "--check-types", action="store_true",
        help="Roll up mypy type-error findings (requires codequality[types])"
    )
    hall_p.add_argument(
        "--marker", default=hallucination_metrics.DEFAULT_MARKER,
        help=f'Substring marking a commit AI-assisted (default: "{hallucination_metrics.DEFAULT_MARKER}")'
    )
    hall_p.add_argument("--format", choices=["text", "json"], default="text")
    hall_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_ownership_subparser(sub):
    ownership_p = sub.add_parser(
        "ownership",
        help="Per-file author concentration (bus factor) plus the fraction of each file's lines "
             "that trace to an AI-assisted commit"
    )
    ownership_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    ownership_p.add_argument(
        "--marker", default=ownership.DEFAULT_MARKER,
        help=f'Substring in the commit message that marks it AI-assisted (default: "{ownership.DEFAULT_MARKER}")'
    )
    ownership_p.add_argument(
        "--threshold", type=float, default=ownership.DEFAULT_THRESHOLD,
        help=f"Single-identity line share at/above which a file is flagged low-bus-factor "
             f"(default: {ownership.DEFAULT_THRESHOLD})"
    )
    ownership_p.add_argument("--format", choices=["text", "json"], default="text")
    ownership_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_todo_age_subparser(sub):
    todo_age_p = sub.add_parser(
        "todo-age",
        help="Age every TODO/FIXME/XXX/HACK marker via git blame, grouped by AI-assisted vs. human commit"
    )
    todo_age_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to scan (default: .)")
    todo_age_p.add_argument(
        "--marker", default=todo_age.DEFAULT_MARKER,
        help=f'Substring in the commit message that marks it AI-assisted (default: "{todo_age.DEFAULT_MARKER}")'
    )
    todo_age_p.add_argument(
        "--stale-days", type=int, default=todo_age.DEFAULT_STALE_DAYS,
        help=f"Age in days after which a TODO/FIXME is flagged stale (default: {todo_age.DEFAULT_STALE_DAYS})"
    )
    todo_age_p.add_argument("--format", choices=["text", "json"], default="text")
    todo_age_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_dead_code_confidence_subparser(sub):
    dcc_p = sub.add_parser(
        "dead-code-confidence",
        help="Age every cross-file dead-code finding via git blame -- how long has it looked unused, "
             "and how safe is it to remove"
    )
    dcc_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    dcc_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    dcc_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    dcc_p.add_argument(
        "--stale-days", type=int, default=dead_code_confidence.DEFAULT_STALE_DAYS,
        help=f"Age in days at/above which a finding is 'high' confidence, half of which is 'medium' "
             f"(default: {dead_code_confidence.DEFAULT_STALE_DAYS})"
    )
    dcc_p.add_argument("--format", choices=["text", "json"], default="text")
    dcc_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_ai_report_subparser(sub):
    ai_report_p = sub.add_parser(
        "ai-report",
        help="One dashboard rolling up churn/edit-distance/commit-lint/hallucination-rate: AI-assisted vs. human"
    )
    ai_report_p.add_argument("path", nargs="?", default=".", help="Git repo root (default: .)")
    ai_report_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    ai_report_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    ai_report_p.add_argument(
        "--marker", default=ai_report.DEFAULT_MARKER,
        help=f'Substring in the commit message that marks it AI-assisted (default: "{ai_report.DEFAULT_MARKER}")'
    )
    ai_report_p.add_argument(
        "--since", default=None, help="Only consider commits since this date/ref (git --since syntax)"
    )
    ai_report_p.add_argument(
        "--check-imports", action="store_true",
        help="Also roll up the hallucination-rate row via unresolved-import findings (opt-in; see README)"
    )
    ai_report_p.add_argument(
        "--check-types", action="store_true",
        help="Also roll up the hallucination-rate row via mypy findings (opt-in; requires codequality[types])"
    )
    ai_report_p.add_argument("--format", choices=["text", "json"], default="text")
    ai_report_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_dependency_check_subparser(sub):
    dep_p = sub.add_parser(
        "dependency-check",
        help="Structural consistency checks on requirements.txt/pyproject.toml/package.json (pinning "
             "consistency, cross-file duplicates, unpinned deps in a lockfile repo) -- offline, no registry calls"
    )
    dep_p.add_argument("path", nargs="?", default=".", help="Repo root to check (default: .)")
    dep_p.add_argument("--format", choices=["text", "json"], default="text")
    dep_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_dependency_risk_subparser(sub):
    dep_risk_p = sub.add_parser(
        "dependency-risk",
        help="Rank declared dependencies by Python-import usage count x dependency-check's own structural "
             "risk flags -- offline, no registry calls, NOT a staleness/CVE check (see README)"
    )
    dep_risk_p.add_argument("path", nargs="?", default=".", help="Repo root to analyze (default: .)")
    dep_risk_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    dep_risk_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    dep_risk_p.add_argument("--top", type=int, default=25, help="Max number of packages to report (default: 25)")
    dep_risk_p.add_argument("--format", choices=["text", "json"], default="text")
    dep_risk_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_orphaned_config_subparser(sub):
    oc_p = sub.add_parser(
        "orphaned-config",
        help="Flag CI workflows/docker-compose files/Makefiles that reference a local script or path "
             "that no longer exists -- structural, no judgment about whether the feature is still wanted"
    )
    oc_p.add_argument("path", nargs="?", default=".", help="Repo root to check (default: .)")
    oc_p.add_argument("--format", choices=["text", "json"], default="text")
    oc_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_hotspots_subparser(sub):
    hotspots_p = sub.add_parser(
        "hotspots",
        help="Rank files by complexity x change frequency (Michael Feathers' \"hotspot\" technique) -- "
             "the files most worth a closer look first"
    )
    hotspots_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    hotspots_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    hotspots_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    hotspots_p.add_argument("--no-generic", action="store_true", help="Only analyze Python files")
    hotspots_p.add_argument(
        "--since", default=None, help="Only consider commits since this date/ref (git --since syntax)"
    )
    hotspots_p.add_argument("--top", type=int, default=25, help="Max number of files to report (default: 25)")
    hotspots_p.add_argument("--format", choices=["text", "json"], default="text")
    hotspots_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_complexity_coverage_risk_subparser(sub):
    ccr_p = sub.add_parser(
        "complexity-coverage-risk",
        help="Rank files by complexity x whether a matching test file exists at all -- what to test first"
    )
    ccr_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    ccr_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    ccr_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    ccr_p.add_argument("--no-generic", action="store_true", help="Only analyze Python files")
    ccr_p.add_argument("--top", type=int, default=25, help="Max number of files to report (default: 25)")
    ccr_p.add_argument("--format", choices=["text", "json"], default="text")
    ccr_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_conventions_subparser(sub):
    conv_p = sub.add_parser(
        "conventions",
        help="Learn the repo's own dominant conventions (type hints, quotes, docstring style, string "
             "formatting) and report files that deviate -- the repo itself is the baseline; report-only"
    )
    conv_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    conv_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    conv_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    conv_p.add_argument("--format", choices=["text", "json"], default="text")
    conv_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_env_check_subparser(sub):
    env_p = sub.add_parser(
        "env-check",
        help="Compare env vars actually referenced in code against what's documented in .env.example/README.md"
    )
    env_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to check (default: .)")
    env_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    env_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    env_p.add_argument("--format", choices=["text", "json"], default="text")
    env_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_history_secrets_subparser(sub):
    hs_p = sub.add_parser(
        "history-secrets",
        help="Find hardcoded-secret-looking lines ever committed, even if later removed from HEAD "
             "(a plain scan/diff only ever looks at the current working tree)"
    )
    hs_p.add_argument("path", nargs="?", default=".", help="Git repo root to scan (default: .)")
    hs_p.add_argument(
        "--since", default=None, metavar="REF",
        help="Only walk commits reachable from HEAD but not from this ref (git 'REF..HEAD' range). "
             "Unlike other subcommands' date-based --since, this is a git ref/tag/sha."
    )
    hs_p.add_argument(
        "--max-commits", type=int, default=history_secrets.DEFAULT_MAX_COMMITS,
        help=f"Cap on how many of the most recent commits to walk (default: {history_secrets.DEFAULT_MAX_COMMITS}) "
             "-- diffing every commit in a large repo's full history can be slow"
    )
    hs_p.add_argument(
        "--all-commits", action="store_true",
        help="Scan the entire history instead of capping at --max-commits"
    )
    hs_p.add_argument("--format", choices=["text", "json"], default="text")
    hs_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_large_files_subparser(sub):
    lf_p = sub.add_parser(
        "large-files",
        help="Flag oversized or binary files tracked in git (accidental node_modules/build-artifact/"
             "dataset commits) -- reads git ls-tree, no code parsing"
    )
    lf_p.add_argument("path", nargs="?", default=".", help="Git repo root to check (default: .)")
    lf_p.add_argument("--config", help="Path to a .codequality.toml/.json config file (only .exclude is used)")
    lf_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    lf_p.add_argument(
        "--max-size-mb", type=float, default=large_files.DEFAULT_MAX_SIZE_MB,
        help=f"Flag tracked files larger than this many MB as large-file (default: {large_files.DEFAULT_MAX_SIZE_MB})"
    )
    lf_p.add_argument("--format", choices=["text", "json"], default="text")
    lf_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_config_drift_subparser(sub):
    cd_p = sub.add_parser(
        "config-drift",
        help="Compare key sets across sibling per-environment config files (.env variants, or a "
             "config/environments/envs directory) and flag keys missing in one but present in a sibling"
    )
    cd_p.add_argument("path", nargs="?", default=".", help="Repo root to check (default: .)")
    cd_p.add_argument("--format", choices=["text", "json"], default="text")
    cd_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_migration_check_subparser(sub):
    mc_p = sub.add_parser(
        "migration-check",
        help="Flag Django/Alembic/raw-SQL migrations that can't be rolled back (missing reverse_code, "
             "an empty downgrade(), or a *.up.sql with no matching *.down.sql) -- structural only, "
             "never connects to a database"
    )
    mc_p.add_argument("path", nargs="?", default=".", help="Repo root to check (default: .)")
    mc_p.add_argument("--format", choices=["text", "json"], default="text")
    mc_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_feature_flags_subparser(sub):
    ff_p = sub.add_parser(
        "feature-flags",
        help="Age every feature-flag-looking reference/definition via git blame -- flags whose oldest "
             "reference is past --stale-days are candidates for cleanup (best-effort idiom matching, see README)"
    )
    ff_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to scan (default: .)")
    ff_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    ff_p.add_argument(
        "--stale-days", type=int, default=feature_flags.DEFAULT_STALE_DAYS,
        help=f"Age in days after which a flag's oldest reference is flagged stale "
             f"(default: {feature_flags.DEFAULT_STALE_DAYS})"
    )
    ff_p.add_argument("--format", choices=["text", "json"], default="text")
    ff_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_arch_conformance_subparser(sub):
    ac_p = sub.add_parser(
        "arch-conformance",
        help="Config-driven import-direction check: flags a declared layer importing from an earlier "
             "layer (see [architecture].layers in README) -- entirely opt-in, no-op without config"
    )
    ac_p.add_argument("path", nargs="?", default=".", help="Repo/directory root to check (default: .)")
    ac_p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    ac_p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    ac_p.add_argument("--format", choices=["text", "json"], default="text")
    ac_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def _add_config_check_subparser(sub):
    cc_p = sub.add_parser(
        "config-check",
        help="Validate a codequality config file (.codequality.toml/.json or pyproject.toml [tool.codequality]) "
             "for unknown keys, wrong value types, and contradictory settings -- purely structural, "
             "no scanning"
    )
    cc_p.add_argument("path", nargs="?", default=".", help="Repo root to find the config in (default: .)")
    cc_p.add_argument("--config", help="Explicit path to a config file")
    cc_p.add_argument("--format", choices=["text", "json"], default="text")
    cc_p.add_argument("--output", "-o", help="Write the report to a file instead of stdout")


def build_parser():
    """Construct the argparse parser for every subcommand."""
    parser = argparse.ArgumentParser(
        prog="codequality", description="Deterministic, programmatic code quality scanner."
    )
    parser.add_argument("--version", action="version", version=f"codequality {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    _add_scan_diff_subparsers(sub)
    _add_trend_subparser(sub)
    _add_baseline_subparser(sub)
    _add_churn_subparser(sub)
    _add_edit_distance_subparser(sub)
    _add_commit_lint_subparser(sub)
    _add_scaffold_subparser(sub)
    _add_mutation_subparser(sub)
    _add_flakiness_subparser(sub)
    _add_pipeline_subparser(sub)
    _add_hallucination_rate_subparser(sub)
    _add_complexity_trend_subparser(sub)
    _add_api_diff_subparser(sub)
    _add_complexity_regression_subparser(sub)
    _add_dependency_check_subparser(sub)
    _add_dependency_risk_subparser(sub)
    _add_orphaned_config_subparser(sub)
    _add_hotspots_subparser(sub)
    _add_complexity_coverage_risk_subparser(sub)
    _add_ownership_subparser(sub)
    _add_todo_age_subparser(sub)
    _add_env_check_subparser(sub)
    _add_history_secrets_subparser(sub)
    _add_large_files_subparser(sub)
    _add_config_drift_subparser(sub)
    _add_migration_check_subparser(sub)
    _add_feature_flags_subparser(sub)
    _add_arch_conformance_subparser(sub)
    _add_ai_report_subparser(sub)
    _add_dead_code_confidence_subparser(sub)
    _add_conventions_subparser(sub)
    _add_config_check_subparser(sub)

    return parser


def _load_config(args, root):
    overrides = {"exclude": args.exclude} if args.exclude else {}
    if getattr(args, "no_generic", False):
        overrides["include_generic_languages"] = False
    if getattr(args, "include_generated", False):
        overrides["include_generated"] = True
    if getattr(args, "check_imports", False):
        overrides["check_imports"] = True
    if getattr(args, "check_types", False):
        overrides["check_types"] = True
    if getattr(args, "check_coverage", False):
        overrides["check_coverage"] = True
    if getattr(args, "test_command", None):
        overrides["test_command"] = args.test_command
    config = Config.load(root, explicit_path=args.config, overrides=overrides)
    if args.exclude:
        config.exclude = list(set(config.exclude) | set(args.exclude))
    return config


def _render(summary, fmt):
    if fmt == "json":
        return render_json(summary)
    if fmt == "markdown":
        return render_markdown(summary)
    if fmt == "sarif":
        return render_sarif(summary)
    if fmt == "html":
        return render_html(summary)
    return render_text(summary, use_color=sys.stdout.isatty())


def _emit(text, output_path):
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def cmd_scan(args):
    """Handle `codequality scan`: full-repo scan, returns the process exit code."""
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    fail_under = args.fail_under if args.fail_under is not None else config.fail_under

    file_metrics = scan_repo(root, config)
    if args.baseline:
        baseline_mod.apply(file_metrics, baseline_mod.load(args.baseline))
    score_result = compute_scores(file_metrics, config)
    summary = build_summary(file_metrics, score_result, "scan", root, fail_under=fail_under, fail_on=args.fail_on)

    if args.record_history:
        append_entry(args.record_history, summary)

    _emit(_render(summary, args.format), args.output)
    return 0 if summary["threshold"]["passed"] else 1


def cmd_diff(args):
    """Handle `codequality diff`: git-diff-scoped scan, returns the process exit code."""
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    fail_under = args.fail_under if args.fail_under is not None else config.fail_under

    if not is_git_repo(root):
        print(f"error: {root} is not a git repository (diff mode requires git)", file=sys.stderr)
        return 2

    base, auto_head = args.base, None
    if base is None:
        base, auto_head = resolve_default_base(root)
    head = args.head if args.head is not None else auto_head

    try:
        changed_files = get_changed_files(base, head, root)
    except GitError as e:
        print(f"error: git diff failed: {e}", file=sys.stderr)
        return 2

    if not changed_files:
        print(f"No changed files between {base} and {head or 'working tree'}.")
        return 0

    task_description = args.task_description if args.task_description is not None else get_last_commit_subject(root)
    file_metrics = scan_changed(root, config, changed_files, base=base, task_description=task_description)
    if args.baseline:
        baseline_mod.apply(file_metrics, baseline_mod.load(args.baseline))
    score_result = compute_scores(file_metrics, config)
    diff_info = {
        "base": base,
        "head": head,
        "changed_files": sorted(changed_files.keys()),
        "changed_lines_count": sum(len(v) for v in changed_files.values()),
    }
    summary = build_summary(file_metrics, score_result, "diff", root, diff_info=diff_info, fail_under=fail_under, fail_on=args.fail_on)

    _emit(_render(summary, args.format), args.output)
    return 0 if summary["threshold"]["passed"] else 1


def cmd_trend(args):
    """Handle `codequality trend`: render the score history recorded via --record-history."""
    if not os.path.isfile(args.history_file):
        print(f"error: history file not found: {args.history_file}", file=sys.stderr)
        return 2
    entries = read_entries(args.history_file)
    text = json.dumps(entries, indent=2) if args.format == "json" else render_trend_text(entries)
    _emit(text, args.output)
    return 0


def cmd_baseline(args):
    """Handle `codequality baseline`: snapshot current issue counts per (file, symbol)."""
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    file_metrics = scan_repo(root, config)
    baseline_mod.save(args.output, file_metrics)
    total_issues = sum(len(fm.issues) for fm in file_metrics)
    print(f"Wrote baseline with {total_issues} existing issue(s) across {len(file_metrics)} file(s) to {args.output}")
    return 0


def cmd_churn(args):
    """Handle `codequality churn`: AI-assisted vs. human commit rework rates."""
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        counts = churn.compute(root, marker=args.marker, window_days=args.window_days, since=args.since)
    except GitError as e:
        print(f"error: git log failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(counts, indent=2) if args.format == "json" else churn.render_text(counts, args.window_days)
    _emit(text, args.output)
    return 0


def cmd_edit_distance(args):
    """Handle `codequality edit-distance`: how much of AI-assisted vs. human
    commits' added lines are still there at HEAD, unchanged.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        counts = edit_distance.compute(root, marker=args.marker, since=args.since)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(counts, indent=2) if args.format == "json" else edit_distance.render_text(counts)
    _emit(text, args.output)
    return 0


def cmd_commit_lint(args):
    """Handle `codequality commit-lint`: deterministic commit-subject quality rules."""
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        result = commit_lint.compute(root, marker=args.marker, since=args.since, strict=args.strict)
    except GitError as e:
        print(f"error: git log failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2) if args.format == "json" else commit_lint.render_text(result, args.strict)
    _emit(text, args.output)
    return 0


def cmd_scaffold_properties(args):
    """Handle `codequality scaffold-properties`: report + generate Hypothesis stubs."""
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    files = discover_files(root, config.exclude, include_generic=False)
    python_files = [rel for rel, lang in files if lang == "python"]

    existing = property_scaffold.scan_existing_property_tests(root, python_files)
    candidates = property_scaffold.find_candidates(root, python_files, limit=args.max)

    with open(args.output, "w", encoding="utf-8") as f:
        f.write(property_scaffold.render_stub_file(candidates))

    existing_count = sum(existing.values())
    files_note = f" (in {len(existing)} file(s))" if existing else ""
    print(f"Existing @given-decorated property tests found: {existing_count}{files_note}")
    print(f"Candidate functions without property tests: {len(candidates)}")
    print(f"Wrote {len(candidates)} stub(s) to {args.output}")
    return 0


def cmd_mutation(args):
    """Handle `codequality mutation`: mutmut kill-rate, the trust signal
    for whether a test suite actually asserts behavior.
    """
    root = os.path.abspath(args.path)
    if not mutation.AVAILABLE:
        print("error: mutmut is not installed (pip install codequality[mutation])", file=sys.stderr)
        return 2
    if not mutation.is_configured(root):
        print(mutation.SETUP_HINT, file=sys.stderr)
        return 2
    stats = mutation.run(root)
    if stats is None:
        print("error: mutmut did not produce results", file=sys.stderr)
        return 2
    if args.format == "json":
        text = json.dumps({**stats, "mutation_score": mutation.mutation_score(stats)}, indent=2)
    else:
        text = mutation.render_text(stats)
    _emit(text, args.output)
    return 0


def cmd_flakiness(args):
    """Handle `codequality flakiness`: rerun the repo's own test suite
    --runs times and report tests whose pass/fail/error result isn't
    stable across runs. Same "runs the target repo's own code" trust
    boundary as --check-coverage/mutation -- its own explicit subcommand,
    never folded into scan/diff, and never a required opt-in flag beyond
    just invoking it.
    """
    root = os.path.abspath(args.path)
    result = flakiness.run(root, test_command=args.test_command, runs=args.runs)
    text = json.dumps(result, indent=2) if args.format == "json" else flakiness.render_text(result)
    _emit(text, args.output)
    return 0


def cmd_pipeline(args):
    """Handle `codequality pipeline`: run the configured external steps
    (format/lint/test/... -- see the `[pipeline]` config table) in order,
    then codequality's own scan, as one combined report + exit code.
    """
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    fail_under = args.fail_under if args.fail_under is not None else config.fail_under

    try:
        result = pipeline.run(
            root, config, fail_under=fail_under, continue_on_failure=args.continue_on_failure
        )
    except pipeline.PipelineError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    text = json.dumps(pipeline.to_dict(result), indent=2) if args.format == "json" else pipeline.render_text(result)
    _emit(text, args.output)
    return 0 if result.passed else 1


def cmd_hallucination_rate(args):
    """Handle `codequality hallucination-rate`: AI-assisted vs. human
    attribution of --check-imports/--check-types findings, per 1,000 LOC.
    """
    if not args.check_imports and not args.check_types:
        print("error: hallucination-rate requires --check-imports and/or --check-types", file=sys.stderr)
        return 2
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    config = _load_config(args, root)
    try:
        counts = hallucination_metrics.compute(root, config, marker=args.marker)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(counts, indent=2) if args.format == "json" else hallucination_metrics.render_text(counts)
    _emit(text, args.output)
    return 0


def cmd_complexity_trend(args):
    """Handle `codequality complexity-trend`: `snapshot` appends one run's
    per-function complexity to a JSONL file; `show` reports the biggest
    complexity increases between the earliest and most recent snapshot.
    """
    if args.ct_mode == "snapshot":
        root = os.path.abspath(args.path)
        config = _load_config(args, root)
        entry = complexity_trend.snapshot(root, config)
        complexity_trend.append_snapshot(args.output, entry)
        commit = entry["commit"] or "unknown"
        print(f"Wrote snapshot of {len(entry['functions'])} function(s) at commit {commit} to {args.output}")
        return 0

    if not os.path.isfile(args.snapshot_file):
        print(f"error: snapshot file not found: {args.snapshot_file}", file=sys.stderr)
        return 2
    snapshots = complexity_trend.read_snapshots(args.snapshot_file)
    rows = complexity_trend.diff_report(snapshots, top_n=args.top)
    text = json.dumps(rows, indent=2) if args.format == "json" else complexity_trend.render_text(rows)
    _emit(text, args.output)
    return 0


def cmd_dependency_check(args):
    """Handle `codequality dependency-check`: purely structural consistency
    checks on dependency manifests -- no network access, ever, so this
    never asks "is there a newer version," only "are these declarations
    internally consistent." See codequality/dependency_check.py.
    """
    root = os.path.abspath(args.path)
    issues = dependency_check.check(root)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = dependency_check.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_dependency_risk(args):
    """Handle `codequality dependency-risk`: Python-import usage count x
    dependency_check's own structural risk flags -- purely local signals,
    NOT staleness/CVE detection. See codequality/dependency_risk.py.
    """
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    rows = dependency_risk.compute(root, config)
    if args.format == "json":
        text = json.dumps(rows[: args.top], indent=2)
    else:
        text = dependency_risk.render_text(rows, top_n=args.top)
    _emit(text, args.output)
    return 0


def cmd_orphaned_config(args):
    """Handle `codequality orphaned-config`: flags CI workflows/docker-
    compose files/Makefiles that reference a local path that no longer
    exists. Purely structural (existence check only) -- see
    codequality/orphaned_config.py.
    """
    root = os.path.abspath(args.path)
    issues = orphaned_config.check(root)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = orphaned_config.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_large_files(args):
    """Handle `codequality large-files`: flag tracked files that are
    oversized or binary -- reads `git ls-tree -r -l HEAD` only, no source
    parsing. See codequality/large_files.py.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    config = _load_config(args, root)
    issues = large_files.check(root, config, max_size_mb=args.max_size_mb)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = large_files.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_api_diff(args):
    """Handle `codequality api-diff`: public API comparison between two
    arbitrary git refs -- unlike `diff`'s breaking-signature-change check,
    not scoped to the working tree/one base folded into a scoring run.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        result = api_diff.compare(root, args.from_ref, args.to_ref)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = api_diff.render_json(result) if args.format == "json" else api_diff.render_text(result)
    _emit(text, args.output)
    return 1 if result["issues"] else 0


def cmd_complexity_regression(args):
    """Handle `codequality complexity-regression`: per-function complexity
    comparison between two arbitrary git refs -- the `api-diff`-style
    generalization of `diff`'s always-on complexity-regression check,
    usable against any two points in history, not just the current diff.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        result = complexity_regression_diff.compare(root, args.from_ref, args.to_ref, threshold=args.threshold)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = (
        complexity_regression_diff.render_json(result) if args.format == "json"
        else complexity_regression_diff.render_text(result)
    )
    _emit(text, args.output)
    return 1 if result["issues"] else 0


def cmd_hotspots(args):
    """Handle `codequality hotspots`: cross per-file complexity with git
    change frequency (see codequality/hotspots.py) to rank the files most
    worth a closer look first.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    config = _load_config(args, root)
    rows = hotspots.compute(root, config, since=args.since)[: args.top]
    text = json.dumps(rows, indent=2) if args.format == "json" else hotspots.render_text(rows, top_n=args.top)
    _emit(text, args.output)
    return 0


def cmd_complexity_coverage_risk(args):
    """Handle `codequality complexity-coverage-risk`: cross per-file
    complexity with whether a matching test file exists at all (see
    codequality/complexity_coverage_risk.py) to rank files by what's most
    worth writing a test for first.
    """
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    rows = complexity_coverage_risk.compute(root, config)
    if args.format == "json":
        text = json.dumps(rows[: args.top], indent=2)
    else:
        text = complexity_coverage_risk.render_text(rows, top_n=args.top)
    _emit(text, args.output)
    return 0


def cmd_ownership(args):
    """Handle `codequality ownership`: per-file single-identity line
    concentration (bus factor) plus AI-assisted line fraction, both via
    `git blame`.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        entries = ownership.compute(root, marker=args.marker, threshold=args.threshold)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(entries, indent=2) if args.format == "json" else ownership.render_text(entries, args.threshold)
    _emit(text, args.output)
    return 0


def cmd_todo_age(args):
    """Handle `codequality todo-age`: age every TODO/FIXME/XXX/HACK marker
    via git blame, grouped by AI-assisted vs. human introducing commit.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        todos = todo_age.compute(root, marker=args.marker, stale_days=args.stale_days)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        groups = todo_age.summarize(todos)
        text = json.dumps({"ai": groups["ai"], "human": groups["human"], "todos": todos}, indent=2)
    else:
        text = todo_age.render_text(todos, args.stale_days)
    _emit(text, args.output)
    return 0


def cmd_conventions(args):
    """Handle `codequality conventions`: learn the repo's own dominant
    conventions and report deviating files (see codequality/conventions.py).
    Report-only -- always exits 0 on success.
    """
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    result = conventions.compute(root, config)
    text = conventions.render_json(result) if args.format == "json" else conventions.render_text(result)
    _emit(text, args.output)
    return 0


def cmd_env_check(args):
    """Handle `codequality env-check`: env vars referenced in code vs. env
    vars documented in .env.example/README.md, flagged in either
    direction -- see codequality/env_check.py.
    """
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    issues = env_check.check(root, config)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = env_check.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_history_secrets(args):
    """Handle `codequality history-secrets`: hardcoded-secret-looking lines
    ever added by any commit, including ones since removed from HEAD --
    the case a plain scan/diff can never catch since both only look at the
    current working tree.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    max_commits = None if args.all_commits else args.max_commits
    try:
        hits = history_secrets.scan(root, since=args.since, max_commits=max_commits)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(hits, indent=2) if args.format == "json" else history_secrets.render_text(hits)
    _emit(text, args.output)
    return 1 if hits else 0


def cmd_ai_report(args):
    """Handle `codequality ai-report`: one dashboard rolling up churn,
    edit-distance, commit-lint, and (opt-in) hallucination-rate --
    a pure aggregation of those four existing AI-vs-human metrics, no new
    detection logic of its own (see codequality/ai_report.py).
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    config = _load_config(args, root)
    try:
        result = ai_report.compute(root, config, marker=args.marker, since=args.since)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = json.dumps(result, indent=2) if args.format == "json" else ai_report.render_text(result)
    _emit(text, args.output)
    return 0


def cmd_dead_code_confidence(args):
    """Handle `codequality dead-code-confidence`: age every cross-file
    dead-code finding (see codequality/analyzers/dead_code.py) via git
    blame, and label how safe each looks to remove.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    config = _load_config(args, root)
    try:
        results = dead_code_confidence.compute(root, config, stale_days=args.stale_days)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    text = (
        json.dumps(results, indent=2) if args.format == "json"
        else dead_code_confidence.render_text(results, args.stale_days)
    )
    _emit(text, args.output)
    return 0


def cmd_config_drift(args):
    """Handle `codequality config-drift`: flags key-set mismatches across
    sibling per-environment config files. Purely structural (key names
    only, values are never read into the report) -- see
    codequality/config_drift.py.
    """
    root = os.path.abspath(args.path)
    issues = config_drift.check(root)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = config_drift.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_migration_check(args):
    """Handle `codequality migration-check`: flags Django/Alembic/raw-SQL
    migrations that can't be rolled back. Never executes a migration or
    connects to a database -- see codequality/migration_check.py.
    """
    root = os.path.abspath(args.path)
    issues = migration_check.check(root)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = migration_check.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_feature_flags(args):
    """Handle `codequality feature-flags`: ages every flag-looking
    reference/definition via git blame -- see codequality/feature_flags.py.
    """
    root = os.path.abspath(args.path)
    if not is_git_repo(root):
        print(f"error: {root} is not a git repository", file=sys.stderr)
        return 2
    try:
        occurrences = feature_flags.compute(root, stale_days=args.stale_days, exclude=args.exclude)
    except GitError as e:
        print(f"error: git failed: {e}", file=sys.stderr)
        return 2
    if args.format == "json":
        groups = feature_flags.summarize(occurrences)
        text = json.dumps({"flags": groups, "occurrences": occurrences}, indent=2)
    else:
        text = feature_flags.render_text(occurrences, args.stale_days)
    _emit(text, args.output)
    return 0


def cmd_arch_conformance(args):
    """Handle `codequality arch-conformance`: config-driven import-direction
    check across declared layers. No-op without [architecture].layers
    configured -- see codequality/arch_conformance.py.
    """
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    issues = arch_conformance.check(root, config)
    if args.format == "json":
        text = json.dumps([i.to_dict() for i in issues], indent=2)
    else:
        text = arch_conformance.render_text(issues)
    _emit(text, args.output)
    return 0


def cmd_config_check(args):
    """Handle `codequality config-check`: validate a config file for
    unknown keys, wrong types, and contradictory settings.
    """
    import json as _json
    root = os.path.abspath(args.path)
    path, issues = config_validate.validate(root=root, explicit_path=getattr(args, "config", None))
    if args.format == "json":
        text = _json.dumps(
            {"config_file": path, "issues": [i.to_dict() for i in issues]}, indent=2
        )
    else:
        text = config_validate.render_text(path, issues)
    _emit(text, args.output)
    errors = [i for i in issues if i.severity == "error"]
    return 1 if errors else 0


_COMMANDS = {
    "scan": cmd_scan,
    "diff": cmd_diff,
    "trend": cmd_trend,
    "baseline": cmd_baseline,
    "churn": cmd_churn,
    "edit-distance": cmd_edit_distance,
    "commit-lint": cmd_commit_lint,
    "scaffold-properties": cmd_scaffold_properties,
    "mutation": cmd_mutation,
    "flakiness": cmd_flakiness,
    "pipeline": cmd_pipeline,
    "hallucination-rate": cmd_hallucination_rate,
    "complexity-trend": cmd_complexity_trend,
    "api-diff": cmd_api_diff,
    "complexity-regression": cmd_complexity_regression,
    "dependency-check": cmd_dependency_check,
    "dependency-risk": cmd_dependency_risk,
    "orphaned-config": cmd_orphaned_config,
    "hotspots": cmd_hotspots,
    "complexity-coverage-risk": cmd_complexity_coverage_risk,
    "ownership": cmd_ownership,
    "todo-age": cmd_todo_age,
    "env-check": cmd_env_check,
    "history-secrets": cmd_history_secrets,
    "large-files": cmd_large_files,
    "config-drift": cmd_config_drift,
    "migration-check": cmd_migration_check,
    "feature-flags": cmd_feature_flags,
    "arch-conformance": cmd_arch_conformance,
    "ai-report": cmd_ai_report,
    "dead-code-confidence": cmd_dead_code_confidence,
    "conventions": cmd_conventions,
    "config-check": cmd_config_check,
}


def main(argv=None):
    """CLI entrypoint; returns the process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 2
    try:
        return handler(args)
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
