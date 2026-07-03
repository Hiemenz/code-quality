import argparse
import os
import sys

from codequality import __version__
from codequality.config import Config
from codequality.git_utils import GitError, get_changed_files, is_git_repo, resolve_default_base
from codequality.report import build_summary, render_json, render_markdown, render_text
from codequality.scanner import scan_changed, scan_repo
from codequality.scorer import compute_scores


def _add_common_args(p):
    p.add_argument("path", nargs="?", default=".", help="Repo/directory root to analyze (default: .)")
    p.add_argument("--config", help="Path to a .codequality.toml/.json config file")
    p.add_argument("--format", choices=["text", "json", "markdown"], default="text")
    p.add_argument("--output", "-o", help="Write report to a file instead of stdout")
    p.add_argument("--fail-under", type=float, default=None, help="Exit non-zero if the overall score is below this")
    p.add_argument("--no-color", action="store_true")
    p.add_argument("--exclude", action="append", default=[], help="Glob pattern to exclude (repeatable)")
    p.add_argument("--no-generic", action="store_true", help="Only analyze Python files (skip heuristic analyzers)")


def build_parser():
    parser = argparse.ArgumentParser(prog="codequality", description="Deterministic, programmatic code quality scanner.")
    parser.add_argument("--version", action="version", version=f"codequality {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    scan_p = sub.add_parser("scan", help="Score the entire repository")
    _add_common_args(scan_p)

    diff_p = sub.add_parser("diff", help="Score only the code changed relative to a git base")
    _add_common_args(diff_p)
    diff_p.add_argument("--base", default=None, help="Git ref to diff against (default: auto-detect)")
    diff_p.add_argument("--head", default=None, help="Git ref for the 'after' state (default: working tree)")

    return parser


def _load_config(args, root):
    overrides = {"exclude": args.exclude} if args.exclude else {}
    if args.no_generic:
        overrides["include_generic_languages"] = False
    config = Config.load(root, explicit_path=args.config, overrides=overrides)
    if args.exclude:
        config.exclude = list(set(config.exclude) | set(args.exclude))
    return config


def _render(summary, fmt):
    if fmt == "json":
        return render_json(summary)
    if fmt == "markdown":
        return render_markdown(summary)
    return render_text(summary, use_color=sys.stdout.isatty())


def _emit(text, output_path):
    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)


def cmd_scan(args):
    root = os.path.abspath(args.path)
    config = _load_config(args, root)
    fail_under = args.fail_under if args.fail_under is not None else config.fail_under

    file_metrics = scan_repo(root, config)
    score_result = compute_scores(file_metrics, config)
    summary = build_summary(file_metrics, score_result, "scan", root, fail_under=fail_under)

    _emit(_render(summary, args.format), args.output)
    return 0 if summary["threshold"]["passed"] else 1


def cmd_diff(args):
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

    file_metrics = scan_changed(root, config, changed_files)
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


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "scan":
            return cmd_scan(args)
        if args.command == "diff":
            return cmd_diff(args)
    except KeyboardInterrupt:
        return 130
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
