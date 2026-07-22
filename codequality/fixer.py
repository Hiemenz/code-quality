"""Auto-fix engine for the five deterministic style rules with a single correct rewrite.

Rules handled (pure text or simple line-count manipulation, no LLM, no network):
  trailing-whitespace      -- strip trailing whitespace from the flagged line
  f-string-no-placeholder  -- remove the leading f/F prefix from the string literal
  comparison-to-none       -- == None → is None,  != None → is not None
  comparison-to-true       -- == True/False → truthiness (simple names/attributes only)
  redundant-else           -- drop the else: line and dedent its body by one level

Fixes are applied bottom-to-top within each file so that line-number shifts from
redundant-else (which removes a line) do not invalidate earlier issue line numbers.

Limitation: comparison-to-none/true fixes use a text-level regex and may
incorrectly modify `== None` / `== True` that appear inside string literals or
comments on the same line as a real comparison. This is rare in practice; use
--dry-run to review changes before writing.
"""

import difflib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

FIXABLE_RULES = frozenset({
    "trailing-whitespace",
    "f-string-no-placeholder",
    "comparison-to-none",
    "comparison-to-true",
    "redundant-else",
})


@dataclass
class AppliedFix:
    lineno: int
    rule: str


@dataclass
class SkippedFix:
    lineno: int
    rule: str
    reason: str


@dataclass
class FixResult:
    path: str
    applied: list = field(default_factory=list)
    skipped: list = field(default_factory=list)
    original_text: str = ""
    new_text: str = ""
    error: Optional[str] = None

    @property
    def changed(self):
        return self.new_text != self.original_text

    @property
    def diff(self):
        return "".join(difflib.unified_diff(
            self.original_text.splitlines(keepends=True),
            self.new_text.splitlines(keepends=True),
            fromfile=f"a/{self.path}",
            tofile=f"b/{self.path}",
        ))


# ---------------------------------------------------------------------------
# Per-rule fix functions (operate on `lines: list[str | None]` in-place)
# ---------------------------------------------------------------------------

def _fix_trailing_whitespace(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False
    line = lines[idx]
    if line.endswith("\r\n"):
        eol, body = "\r\n", line[:-2]
    elif line.endswith("\n"):
        eol, body = "\n", line[:-1]
    elif line.endswith("\r"):
        eol, body = "\r", line[:-1]
    else:
        eol, body = "", line
    stripped = body.rstrip()
    if stripped == body:
        return False
    lines[idx] = stripped + eol
    return True


# Matches the f/F part of an f-string prefix, surrounded by optional r/b/u chars.
# Groups: (preceding prefix chars)(f or F)(following prefix chars)
_FSTR_RE = re.compile(r"([rRbBuU]*)([fF])([rRbBuU]*)(?=[\"'])")


def _fix_fstring_no_placeholder(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False
    line = lines[idx]
    # Remove the first f/F from the prefix (keep any r/b/u chars).
    new_line = _FSTR_RE.sub(lambda m: m.group(1) + m.group(3), line, count=1)
    if new_line == line:
        return False
    lines[idx] = new_line
    return True


_NONE_EQ_RE = re.compile(r"==\s*None\b")
_NONE_NEQ_RE = re.compile(r"!=\s*None\b")
_EQ_NONE_RE = re.compile(r"\bNone\s*==")
_NEQ_NONE_RE = re.compile(r"\bNone\s*!=")


def _fix_comparison_to_none(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False
    line = lines[idx]
    new_line = _NONE_EQ_RE.sub("is None", line)
    new_line = _NONE_NEQ_RE.sub("is not None", new_line)
    # None on the left side: `None == x` → `x is None` is trickier; convert
    # to `is None ==` form which Python won't accept, so instead rewrite those.
    # Simplest correct rewrite: `None == x` → `x is None` requires knowing x,
    # so we leave `None ==` alone (the == None form is far more common).
    if new_line == line:
        return False
    lines[idx] = new_line
    return True


# Simple Python name or dotted-attribute chain (no calls, no subscripts).
_ID = r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*"

_TRUE_FALSE_FIXES = [
    # Order matters: try longer/more-specific patterns first.
    (re.compile(rf"\b({_ID})\s*==\s*True\b"),  r"\1"),       # x == True  → x
    (re.compile(rf"\b({_ID})\s*!=\s*True\b"),  r"not \1"),   # x != True  → not x
    (re.compile(rf"\b({_ID})\s*==\s*False\b"), r"not \1"),   # x == False → not x
    (re.compile(rf"\b({_ID})\s*!=\s*False\b"), r"\1"),       # x != False → x
    (re.compile(rf"\bTrue\s*==\s*({_ID})\b"),  r"\1"),       # True == x  → x
    (re.compile(rf"\bTrue\s*!=\s*({_ID})\b"),  r"not \1"),   # True != x  → not x
    (re.compile(rf"\bFalse\s*==\s*({_ID})\b"), r"not \1"),   # False == x → not x
    (re.compile(rf"\bFalse\s*!=\s*({_ID})\b"), r"\1"),       # False != x → x
]


def _fix_comparison_to_true(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False, None
    line = lines[idx]
    new_line = line
    for pat, repl in _TRUE_FALSE_FIXES:
        new_line = pat.sub(repl, new_line)
    if new_line == line:
        return False, "expression too complex to safely transform (not a simple name/attribute)"
    lines[idx] = new_line
    return True, None


def _fix_redundant_else(lines, else_lineno):
    """Remove else: at else_lineno (1-indexed) and dedent the body by one level."""
    idx = else_lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False

    else_raw = lines[idx]
    else_stripped = else_raw.rstrip("\r\n").rstrip()
    else_indent = len(else_raw) - len(else_raw.lstrip())

    # Identify the body: contiguous lines after else: with indentation > else_indent.
    body_start = idx + 1
    body_end = body_start
    body_first_indent = None

    for i in range(body_start, len(lines)):
        raw = lines[i]
        if raw is None:
            continue
        content = raw.rstrip("\r\n")
        if not content or content.isspace():
            # Blank line — tentatively part of the body.
            body_end = i + 1
            continue
        curr_indent = len(content) - len(content.lstrip())
        if curr_indent <= else_indent:
            break
        if body_first_indent is None:
            body_first_indent = curr_indent
        body_end = i + 1

    if body_first_indent is None:
        return False  # empty else body; leave alone

    dedent = body_first_indent - else_indent

    # Remove the else: line.
    lines[idx] = None

    # Dedent each body line by `dedent` characters.
    for i in range(body_start, body_end):
        raw = lines[i]
        if raw is None:
            continue
        content = raw.rstrip("\r\n")
        if not content or content.isspace():
            continue  # blank lines unchanged
        lines[i] = raw[dedent:]

    return True


# ---------------------------------------------------------------------------
# File-level fix driver
# ---------------------------------------------------------------------------

def _fix_file(abs_path, root, issues, dry_run=False):
    rel = str(Path(abs_path).relative_to(root))
    result = FixResult(path=rel)
    try:
        with open(abs_path, encoding="utf-8") as fh:
            original = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        result.error = str(exc)
        result.original_text = result.new_text = ""
        for iss in issues:
            result.skipped.append(SkippedFix(iss["line"], iss["symbol"], str(exc)))
        return result

    result.original_text = original

    lines = original.splitlines(keepends=True)
    # Ensure last line has a newline in the list for consistent indexing.
    if lines and not lines[-1].endswith(("\n", "\r")):
        pass  # keep as-is

    # Process issues bottom-to-top so that redundant-else line removal
    # does not shift the line numbers of earlier issues in the same file.
    sorted_issues = sorted(issues, key=lambda i: i["line"], reverse=True)

    for iss in sorted_issues:
        rule = iss["symbol"]
        lineno = iss["line"]

        if rule == "trailing-whitespace":
            ok = _fix_trailing_whitespace(lines, lineno)
            (result.applied if ok else result.skipped).append(
                AppliedFix(lineno, rule) if ok else SkippedFix(lineno, rule, "line not found")
            )

        elif rule == "f-string-no-placeholder":
            ok = _fix_fstring_no_placeholder(lines, lineno)
            (result.applied if ok else result.skipped).append(
                AppliedFix(lineno, rule) if ok else SkippedFix(lineno, rule, "f-string prefix not found on line")
            )

        elif rule == "comparison-to-none":
            ok = _fix_comparison_to_none(lines, lineno)
            (result.applied if ok else result.skipped).append(
                AppliedFix(lineno, rule) if ok else SkippedFix(lineno, rule, "pattern not found on line")
            )

        elif rule == "comparison-to-true":
            ok, reason = _fix_comparison_to_true(lines, lineno)
            if ok:
                result.applied.append(AppliedFix(lineno, rule))
            else:
                result.skipped.append(SkippedFix(lineno, rule, reason or "pattern not found on line"))

        elif rule == "redundant-else":
            ok = _fix_redundant_else(lines, lineno)
            (result.applied if ok else result.skipped).append(
                AppliedFix(lineno, rule) if ok else SkippedFix(lineno, rule, "else: line not found")
            )

    new_text = "".join(line for line in lines if line is not None)
    result.new_text = new_text

    if not dry_run and result.changed:
        with open(abs_path, "w", encoding="utf-8") as fh:
            fh.write(new_text)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def fix_issues(root, issues, dry_run=False):
    """Apply fixable issues grouped by file.

    `root` is the absolute repo root (used to resolve relative file paths).
    `issues` is a list of issue dicts as returned by ``build_summary``
    (keys: file, line, symbol, ...).

    Returns a list of FixResult, one per file that had at least one fixable issue.
    """
    from collections import defaultdict
    by_file = defaultdict(list)
    for iss in issues:
        if iss.get("symbol") in FIXABLE_RULES:
            by_file[iss["file"]].append(iss)

    root_path = Path(root).resolve()
    results = []
    for rel_path in sorted(by_file):
        abs_path = root_path / rel_path
        file_issues = by_file[rel_path]
        result = _fix_file(str(abs_path), str(root_path), file_issues, dry_run=dry_run)
        results.append(result)
    return results


def render_text(results, dry_run=False):
    """Human-readable summary of fix results."""
    if not results:
        return "No fixable issues found."

    lines = []
    total_applied = sum(len(r.applied) for r in results)
    total_skipped = sum(len(r.skipped) for r in results)
    total_changed = sum(1 for r in results if r.changed)
    total_errors = sum(1 for r in results if r.error)

    action = "Would fix" if dry_run else "Fixed"

    if dry_run:
        for r in results:
            if r.diff:
                lines.append(r.diff)

    for r in results:
        if r.error:
            lines.append(f"  ERROR {r.path}: {r.error}")
            continue
        for fix in r.applied:
            verb = "would fix" if dry_run else "fixed"
            lines.append(f"  {verb}  {r.path}:{fix.lineno}  [{fix.rule}]")
        for skip in r.skipped:
            lines.append(f"  skip   {r.path}:{skip.lineno}  [{skip.rule}]  {skip.reason}")

    summary_parts = [f"{action} {total_applied} issue(s) in {total_changed} file(s)"]
    if total_skipped:
        summary_parts.append(f"{total_skipped} skipped")
    if total_errors:
        summary_parts.append(f"{total_errors} file error(s)")
    lines.append(", ".join(summary_parts) + ".")
    return "\n".join(lines)
