"""Lightweight heuristic analyzer for non-Python source files.

There is no real parser here (no tree-sitter dependency in v1), so this
deliberately does less than the Python analyzer: file-level line checks
plus a rough decision-keyword density used as a stand-in for cyclomatic
complexity, and indentation depth as a stand-in for nesting. Treat the
scores from this analyzer as directional, not as precise as the Python
path -- see README for the roadmap on closing this gap.
"""

import re

from codequality.analyzers.base import FileMetrics, FunctionMetrics, Issue
from codequality.analyzers.placeholder_code import PLACEHOLDER_COMMENT_RE
from codequality.analyzers.secrets import SECRET_ASSIGN_RE, is_placeholder

TODO_RE = re.compile(r"(//|#|/\*)\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

# Standalone comment line in any supported language: //, #, /* ... */, a
# mid-block-comment continuation `*`, or SQL/Haskell-style `--`.
_COMMENT_START_RE = re.compile(r"^\s*(//|#|/\*|\*|--)")

# "This is a stub" idioms, per language, matched line-level (there's no
# parser here to see function bodies): C#'s NotImplementedException, Java's
# no-message UnsupportedOperationException, a JS `throw new Error('not
# implemented')`, Go's panic("not implemented"), Ruby's raise
# NotImplementedError. Rust's `unimplemented!()`/`todo!()` and Kotlin's
# `TODO()` are matched case-sensitively in a second pattern so a
# user-defined `todo()` helper in a case-insensitive world doesn't trip it.
_NOT_IMPLEMENTED_RE = re.compile(
    r"throw\s+new\s+NotImplementedException"
    r"|throw\s+new\s+UnsupportedOperationException\s*\(\s*\)"
    r"|throw\s+new\s+Error\s*\(\s*['\"][^'\"]*not\s+implemented"
    r"|panic\s*\(\s*\"[^\"]*not\s+implemented"
    r"|\braise\s+NotImplementedError\b",
    re.IGNORECASE,
)
_NOT_IMPLEMENTED_CS_RE = re.compile(r"\b(?:unimplemented|todo)!\s*\(\)|\bTODO\s*\(\s*\)")

LINE_COMMENT_PREFIXES = {
    "javascript": "//",
    "typescript": "//",
    "java": "//",
    "go": "//",
    "c": "//",
    "cpp": "//",
    "csharp": "//",
    "rust": "//",
    "kotlin": "//",
    "swift": "//",
    "scala": "//",
    "php": "//",
    "ruby": "#",
}

DECISION_KEYWORDS = re.compile(
    r"\b(if|else if|elif|for|foreach|while|case|catch|except)\b|(&&|\|\||\?)"
)

_EVAL_RE = re.compile(r"\b(eval|exec)\s*\(")


def _security_line_issues(path, i, stripped):
    issues = []
    if _EVAL_RE.search(stripped):
        issues.append(
            Issue(path, i, "security", "warn", "dangerous-eval", "Use of eval()/exec() can execute arbitrary code")
        )
    m = SECRET_ASSIGN_RE.search(stripped)
    if m and not is_placeholder(m.group(3)):
        issues.append(
            Issue(path, i, "security", "error", "hardcoded-secret", f"'{m.group(1)}' looks like a hardcoded secret")
        )
    return issues


def _indent_width(line):
    stripped = line.lstrip(" \t")
    return len(line) - len(stripped)


def _indent_level(raw):
    text_only = raw.strip()
    if not text_only:
        return 0
    width = _indent_width(raw)
    unit = 4 if "\t" not in raw[: width or 1] else 1
    return width // unit if unit else 0


def _scan_line(path, i, raw, comment_prefix, limits):
    """Returns (issues, is_comment_line, indent_level, decision_hits) for one line."""
    stripped = raw.rstrip("\n")
    is_comment = stripped.lstrip().startswith(comment_prefix)

    issues = []
    if len(stripped) > limits.max_line_length:
        msg = f"Line is {len(stripped)} characters (limit {limits.max_line_length})"
        issues.append(Issue(path, i, "style", "info", "long-line", msg))
    if stripped != stripped.rstrip():
        issues.append(Issue(path, i, "style", "info", "trailing-whitespace", "Trailing whitespace"))
    if TODO_RE.search(stripped):
        issues.append(Issue(path, i, "style", "info", "todo-marker", stripped.strip()[:120]))
    if _COMMENT_START_RE.match(stripped) and PLACEHOLDER_COMMENT_RE.search(stripped):
        issues.append(
            Issue(path, i, "correctness", "warn", "placeholder-comment",
                  f"Placeholder comment where real code should be: {stripped.strip()[:100]}")
        )
    elif not is_comment and (_NOT_IMPLEMENTED_RE.search(stripped) or _NOT_IMPLEMENTED_CS_RE.search(stripped)):
        issues.append(
            Issue(path, i, "correctness", "info", "stub-implementation",
                  "Not-implemented stub -- this code path crashes if reached "
                  "(info: without a parser, an intentionally-abstract member can't be told apart)")
        )
    issues.extend(_security_line_issues(path, i, stripped))

    return issues, is_comment, _indent_level(raw), len(DECISION_KEYWORDS.findall(stripped))


class _ScanTotals:
    """Accumulated stats from scanning a file's lines, one pass."""

    def __init__(self):
        self.issues = []
        self.decision_hits = 0
        self.comment_lines = 0
        self.max_indent_level = 0
        self.considered_lines = 0


def _scan_lines(path, lines, comment_prefix, limits, only_lines):
    totals = _ScanTotals()
    for i, raw in enumerate(lines, start=1):
        in_scope = only_lines is None or i in only_lines
        is_comment_prefix = raw.rstrip("\n").lstrip().startswith(comment_prefix)
        if not in_scope:
            totals.comment_lines += 1 if is_comment_prefix else 0
            continue

        totals.considered_lines += 1
        issues, is_comment, indent_level, hits = _scan_line(path, i, raw, comment_prefix, limits)
        totals.issues.extend(issues)
        totals.comment_lines += 1 if is_comment else 0
        totals.max_indent_level = max(totals.max_indent_level, indent_level)
        totals.decision_hits += hits
    return totals


def _pseudo_function(path, total_lines, limits, totals):
    """A whole considered file, treated as one unit of complexity/nesting
    since no real function boundaries are detected for this language.
    """
    density_per_100 = totals.decision_hits / max(totals.considered_lines, 1) * 100
    approx_complexity = 1 + round(density_per_100 / 3)
    fn = FunctionMetrics(
        file=path,
        name="<file>",
        lineno=1,
        end_lineno=total_lines,
        complexity=approx_complexity,
        length=totals.considered_lines,
        nesting=min(totals.max_indent_level, 12),
        params=0,
        has_docstring=totals.comment_lines > 0,
        is_public=True,
    )
    issue = None
    if approx_complexity > limits.max_complexity * 2:
        issue = Issue(
            path,
            1,
            "complexity",
            "warn",
            "high-complexity-density",
            f"High density of branching keywords (~{approx_complexity} approx. complexity) "
            "for a file without per-function analysis support",
        )
    return fn, issue


def analyze(path, source, language, limits, only_lines=None):
    """Heuristic file-level analysis for a non-Python source file."""
    lines = source.splitlines(keepends=True)
    total_lines = len(lines)
    loc = sum(1 for l in lines if l.strip())
    comment_prefix = LINE_COMMENT_PREFIXES.get(language, "//")

    fm = FileMetrics(path=path, language=language, total_lines=total_lines, loc=loc)
    totals = _scan_lines(path, lines, comment_prefix, limits, only_lines)
    fm.issues.extend(totals.issues)

    if total_lines > limits.max_file_lines and only_lines is None:
        msg = f"File is {total_lines} lines long (limit {limits.max_file_lines})"
        fm.issues.append(Issue(path, 1, "structure", "info", "long-file", msg))

    if totals.considered_lines > 0:
        fn, complexity_issue = _pseudo_function(path, total_lines, limits, totals)
        fm.functions.append(fn)
        if complexity_issue:
            fm.issues.append(complexity_issue)

    fm.comment_lines = totals.comment_lines
    comment_ratio = totals.comment_lines / max(loc, 1)
    fm.has_module_docstring = totals.comment_lines > 0 and total_lines > 0 and comment_ratio > 0.03
    return fm
