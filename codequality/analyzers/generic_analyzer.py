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

TODO_RE = re.compile(r"(//|#|/\*)\s*(TODO|FIXME|XXX|HACK)\b", re.IGNORECASE)

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
        issues.append(
            Issue(path, i, "style", "info", "long-line", f"Line is {len(stripped)} characters (limit {limits.max_line_length})")
        )
    if stripped != stripped.rstrip():
        issues.append(Issue(path, i, "style", "info", "trailing-whitespace", "Trailing whitespace"))
    if TODO_RE.search(stripped):
        issues.append(Issue(path, i, "style", "info", "todo-marker", stripped.strip()[:120]))

    return issues, is_comment, _indent_level(raw), len(DECISION_KEYWORDS.findall(stripped))


def analyze(path, source, language, limits, only_lines=None):
    lines = source.splitlines(keepends=True)
    total_lines = len(lines)
    loc = sum(1 for l in lines if l.strip())
    comment_prefix = LINE_COMMENT_PREFIXES.get(language, "//")

    fm = FileMetrics(path=path, language=language, total_lines=total_lines, loc=loc)

    decision_hits = 0
    comment_lines = 0
    max_indent_level = 0
    considered_lines = 0

    for i, raw in enumerate(lines, start=1):
        if only_lines is not None and i not in only_lines:
            if raw.rstrip("\n").lstrip().startswith(comment_prefix):
                comment_lines += 1
            continue
        considered_lines += 1

        issues, is_comment, indent_level, hits = _scan_line(path, i, raw, comment_prefix, limits)
        fm.issues.extend(issues)
        comment_lines += 1 if is_comment else 0
        max_indent_level = max(max_indent_level, indent_level)
        decision_hits += hits

    if total_lines > limits.max_file_lines and only_lines is None:
        fm.issues.append(
            Issue(
                path,
                1,
                "structure",
                "info",
                "long-file",
                f"File is {total_lines} lines long (limit {limits.max_file_lines})",
            )
        )

    # Treat the whole (considered) file as one unit: no real function
    # boundaries are detected, so this feeds the complexity/structure
    # categories as a single pseudo-function rather than claiming
    # per-function granularity we can't actually back up.
    if considered_lines > 0:
        density_per_100 = decision_hits / max(considered_lines, 1) * 100
        approx_complexity = 1 + round(density_per_100 / 3)
        fm.functions.append(
            FunctionMetrics(
                file=path,
                name="<file>",
                lineno=1,
                end_lineno=total_lines,
                complexity=approx_complexity,
                length=considered_lines,
                nesting=min(max_indent_level, 12),
                params=0,
                has_docstring=comment_lines > 0,
                is_public=True,
            )
        )
        if approx_complexity > limits.max_complexity * 2:
            fm.issues.append(
                Issue(
                    path,
                    1,
                    "complexity",
                    "warn",
                    "high-complexity-density",
                    f"High density of branching keywords (~{approx_complexity} approx. complexity) "
                    "for a file without per-function analysis support",
                )
            )

    fm.comment_lines = comment_lines
    fm.has_module_docstring = comment_lines > 0 and total_lines > 0 and (comment_lines / max(loc, 1)) > 0.03
    return fm
