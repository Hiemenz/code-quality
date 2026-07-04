import argparse
import json
import os
import sys

from codequality import (
    __version__, baseline as baseline_mod, churn, edit_distance, hallucination_metrics, mutation, pipeline,
    property_scaffold,
)
from codequality.config import Config
from codequality.coverage_check import DEFAULT_TEST_COMMAND
from codequality.git_utils import GitError, get_changed_files, get_last_commit_subject, is_git_repo, resolve_default_base
from codequality.history import append_entry, read_entries, render_trend_text
from codequality.report import build_summary, render_json, render_markdown, render_sarif, render_text
from codequality.scanner import discover_files, scan_changed, scan_repo
from codequality.scorer import compute_scores


def _add_common_args(p):
    p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    p.add_argument("--format", choices=["text", "json", "markdown", "sarif"], default="text")
    p.add_argument("--output", "-o", help="Write report to a file instead of stdout")
    p.add_argument("--fail-under", type=float, default=None, help="Exit non-zero if the overall score is below this")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    p.add_argument("--no-generic", action="store_true", help="Only analyze Python files (skip heuristic analyzers)")
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
    _add_scaffold_subparser(sub)
    _add_mutation_subparser(sub)
    _add_pipeline_subparser(sub)
    _add_hallucination_rate_subparser(sub)

    return parser


def _load_config(args, root):
    overrides = {"exclude": args.exclude} if args.exclude else {}
    if getattr(args, "no_generic", False):
        overrides["include_generic_languages"] = False
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
    summary = build_summary(file_metrics, score_result, "scan", root, fail_under=fail_under)

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
    summary = build_summary(file_metrics, score_result, "diff", root, diff_info=diff_info, fail_under=fail_under)

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


_COMMANDS = {
    "scan": cmd_scan,
    "diff": cmd_diff,
    "trend": cmd_trend,
    "baseline": cmd_baseline,
    "churn": cmd_churn,
    "edit-distance": cmd_edit_distance,
    "scaffold-properties": cmd_scaffold_properties,
    "mutation": cmd_mutation,
    "pipeline": cmd_pipeline,
    "hallucination-rate": cmd_hallucination_rate,
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
