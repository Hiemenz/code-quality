"""Auto-fix engine for the twelve deterministic style/correctness rules with a
single correct rewrite.

Text-level rules (pure line manipulation, no parsing):
  trailing-whitespace      -- strip trailing whitespace from the flagged line
  f-string-no-placeholder  -- remove the leading f/F prefix from the string literal
  comparison-to-none       -- == None → is None,  != None → is not None
  comparison-to-true       -- == True/False → truthiness (simple names/attributes only)
  redundant-else           -- drop the else: line and dedent its body by one level
  bare-except              -- except: → except Exception:
  tab-indent               -- expand leading tabs to 4 spaces
  unused-import            -- delete a top-level, single-name import statement

AST-guided rules (the file is parsed once, and each rewrite is a splice at
exact node offsets -- so a match inside a string literal or comment can never
be hit):
  mutable-default-arg      -- def f(x=[]) → def f(x=None) plus an `if x is None:`
                              guard at the top of the body
  lost-exception-context   -- raise NewError(...) → raise NewError(...) from err
  unsafe-yaml-load         -- yaml.load(x[, Loader=...]) → yaml.safe_load(x)
  future-import-order      -- move `from __future__ import ...` above the other
                              imports (just after the module docstring)

No LLM, no network: every rewrite is derived from the source text alone.

Fixes are applied bottom-to-top within each file so that line-number shifts
from rules that delete or insert lines do not invalidate earlier issue line
numbers. Deletions blank a slot in the line list rather than removing it, and
insertions go into a separate before/after map, so every issue's line number
keeps indexing the slot the AST said it would.

Limitations, all deliberately conservative -- see --dry-run to review changes
before writing:
- comparison-to-none/true fixes use a text-level regex and may incorrectly
  modify `== None` / `== True` that appear inside string literals or comments
  on the same line as a real comparison. This is rare in practice.
- tab-indent only expands tabs found in a line's *leading* whitespace; a tab
  elsewhere on the line (e.g. inside a string literal) is left untouched
  rather than risk altering the string's content.
- unused-import only deletes import statements that (a) have zero leading
  indentation -- an indented import may be the sole statement in a
  try/if/function body, and deleting it would leave an empty, invalid suite
  -- and (b) bind exactly one name -- `import a, b` where only `b` is unused
  is skipped rather than guessing which comma-separated segment to remove.
- mutable-default-arg skips one-line defs (`def f(x=[]): return x`) and
  defaults that span more than one line, because both need a rewrite of the
  surrounding layout rather than a splice. On an annotated parameter it
  produces `def f(x: list = None)`, which a strict type checker will want
  widened to `Optional[list]` by hand -- the runtime bug is fixed either way.
- unsafe-yaml-load only rewrites a single-argument `yaml.load(...)` call that
  fits on one line; anything with extra positional args or non-`Loader`
  keywords is left alone rather than guessing which arguments `safe_load`
  should keep.
"""

import ast
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
    "bare-except",
    "tab-indent",
    "unused-import",
    "mutable-default-arg",
    "lost-exception-context",
    "unsafe-yaml-load",
    "future-import-order",
})

#: Rules whose fix needs the file's AST. If the file doesn't parse, these are
#: skipped (the text-level rules still apply).
AST_RULES = frozenset({
    "mutable-default-arg",
    "lost-exception-context",
    "unsafe-yaml-load",
    "future-import-order",
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
class Pending:
    """Lines to splice in around an existing line slot.

    Insertions are held here rather than pushed into the line list so that a
    slot's index keeps matching the line number the AST reported, no matter how
    many lines earlier fixes added.
    """
    before: dict = field(default_factory=dict)
    after: dict = field(default_factory=dict)

    def insert_before(self, idx, text):
        self.before.setdefault(idx, []).append(text)

    def insert_after(self, idx, text):
        self.after.setdefault(idx, []).append(text)

    def assemble(self, lines):
        out = []
        for idx, line in enumerate(lines):
            out.extend(self.before.get(idx, ()))
            if line is not None:
                out.append(line)
            out.extend(self.after.get(idx, ()))
        return "".join(out)


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

    def _remove_if_no_placeholder(m):
        pos = m.end()
        if pos >= len(line):
            return m.group(0)
        q = line[pos]
        if q not in ('"', "'"):
            return m.group(0)
        triple = line[pos:pos + 3] in ('"""', "'''")
        cq = line[pos:pos + 3] if triple else q
        cs = pos + (3 if triple else 1)
        ce = line.find(cq, cs)
        if ce == -1:
            return m.group(0)  # multiline string — skip conservatively
        content = line[cs:ce]
        # Strip escaped braces {{ / }} then check for a real { placeholder.
        if "{" in content.replace("{{", "").replace("}}", ""):
            return m.group(0)
        return m.group(1) + m.group(3)

    new_line = _FSTR_RE.sub(_remove_if_no_placeholder, line)
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


_BARE_EXCEPT_RE = re.compile(r"^(\s*)except\s*:")


def _fix_bare_except(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False
    line = lines[idx]
    new_line, n = _BARE_EXCEPT_RE.subn(r"\1except Exception:", line, count=1)
    if n == 0:
        return False
    lines[idx] = new_line
    return True


_LEADING_WS_RE = re.compile(r"^[ \t]*")


def _fix_tab_indent(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False
    line = lines[idx]
    leading = _LEADING_WS_RE.match(line).group(0)
    if "\t" not in leading:
        return False  # tab is elsewhere on the line (e.g. inside a string); don't touch content
    lines[idx] = leading.replace("\t", "    ") + line[len(leading):]
    return True


def _import_alias_count(line):
    """Number of comma-separated aliases bound by the import statement on
    `line`, or None if `line` isn't a single-line import statement this
    fixer can safely parse (spans multiple lines, or doesn't start with
    `import `/`from `).
    """
    stmt = line.rstrip("\r\n")
    if "#" in stmt:
        stmt = stmt.split("#", 1)[0]
    stmt = stmt.rstrip()
    if stmt.endswith("\\"):
        return None  # explicit line continuation
    if stmt.startswith("from "):
        marker = " import "
        pos = stmt.find(marker)
        if pos == -1:
            return None
        names_part = stmt[pos + len(marker):].strip()
    elif stmt.startswith("import "):
        names_part = stmt[len("import "):].strip()
    else:
        return None
    if names_part.startswith("("):
        if not names_part.endswith(")"):
            return None  # multi-line parenthesized import
        names_part = names_part[1:-1].strip()
    if not names_part:
        return None
    names = [n for n in names_part.split(",") if n.strip()]
    return len(names) or None


def _fix_unused_import(lines, lineno):
    idx = lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False, "line not found"
    line = lines[idx]
    if line[:1] in (" ", "\t"):
        return False, "indented import (may be the sole statement in a try/if/function body); skipping"
    count = _import_alias_count(line)
    if count is None:
        return False, "could not parse as a single-line import statement"
    if count != 1:
        return False, "line imports multiple names; skipping to avoid removing a used import"
    lines[idx] = None
    return True, None


# ---------------------------------------------------------------------------
# AST-guided fixes
#
# `ast` column offsets are UTF-8 *byte* offsets, not character indices, so
# every splice round-trips through bytes. Before writing, each fix re-reads the
# span it is about to replace and compares it to what the AST said was there;
# if an earlier fix on the same line already moved things, the span won't match
# and the fix is skipped instead of corrupting the line.
# ---------------------------------------------------------------------------

class _AstContext:
    """Parsed view of a file, shared by every AST-guided fix on it."""

    def __init__(self, source):
        self.source = source
        self.tree = ast.parse(source)
        self._functions = {}          # def lineno    -> FunctionDef
        self._raises = {}             # raise lineno  -> (handler name, Raise)
        self._yaml_loads = {}         # call lineno   -> Call
        self._future_imports = {}     # import lineno -> ImportFrom
        self._index()

    def _index(self):
        # ast.walk is breadth-first, so an outer `except ... as e` is seen
        # before a handler nested inside it. Assigning (rather than
        # setdefault-ing) therefore lets the innermost handler -- the one whose
        # name is actually the right thing to chain from -- win.
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._functions.setdefault(node.lineno, node)
            elif isinstance(node, ast.ExceptHandler) and node.name:
                for raise_node in _raises_in_scope(node):
                    self._raises[raise_node.lineno] = (node.name, raise_node)
            elif isinstance(node, ast.Call) and _call_dotted_name(node.func) == "yaml.load":
                self._yaml_loads.setdefault(node.lineno, node)
            elif isinstance(node, ast.ImportFrom) and node.module == "__future__" and not node.level:
                self._future_imports.setdefault(node.lineno, node)

    def function_at(self, lineno):
        return self._functions.get(lineno)

    def unchained_raise_at(self, lineno):
        return self._raises.get(lineno)

    def yaml_load_at(self, lineno):
        return self._yaml_loads.get(lineno)

    def future_import_at(self, lineno):
        return self._future_imports.get(lineno)

    def segment(self, node):
        return ast.get_source_segment(self.source, node)

    def first_module_stmt_after_docstring(self, skip):
        """The module-level statement that `skip` should be moved above, or None.

        Returns the line the statement's source starts on, which is the first
        decorator line for a decorated def -- inserting between a decorator and
        its def would be a syntax error.
        """
        seen_docstring = False
        for node in ast.iter_child_nodes(self.tree):
            if node is skip:
                continue
            if (not seen_docstring and isinstance(node, ast.Expr)
                    and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)):
                seen_docstring = True
                continue
            return min([node.lineno] + [d.lineno for d in getattr(node, "decorator_list", [])])
        return None


_NESTED_SCOPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)


def _raises_in_scope(handler):
    """Unchained `raise X` statements directly inside `handler`'s body.

    Nested function/class bodies are not descended into: the handler's bound
    exception name isn't in scope there, so `raise ... from err` would be a
    NameError. This mirrors what the lost-exception-context check itself walks.
    """
    found = []

    def visit(node):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _NESTED_SCOPES):
                continue
            if isinstance(child, ast.Raise) and child.exc is not None and child.cause is None:
                found.append(child)
            visit(child)

    visit(handler)
    return found


def _call_dotted_name(node):
    """Dotted source name of a call target (`yaml.load`), or None if it isn't
    a plain name/attribute chain."""
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return ".".join(reversed(parts))


def _byte_span(line, start_col, end_col):
    """The [start_col, end_col) byte range of `line`, decoded back to text."""
    return line.encode("utf-8")[start_col:end_col].decode("utf-8")


def _splice(line, start_col, end_col, replacement):
    """`line` with its [start_col, end_col) byte range replaced by `replacement`."""
    raw = line.encode("utf-8")
    return raw[:start_col].decode("utf-8") + replacement + raw[end_col:].decode("utf-8")


def _line_ending(line):
    for eol in ("\r\n", "\n", "\r"):
        if line.endswith(eol):
            return eol
    return "\n"


def _splice_node(lines, node, replacement, ctx):
    """Replace a single-line node's source span with `replacement`.

    False if the node spans lines, its slot is gone, or the text sitting at the
    node's offsets is no longer what the AST parsed.
    """
    if node.lineno != node.end_lineno:
        return False
    idx = node.lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False
    expected = ctx.segment(node)
    if expected is None or _byte_span(lines[idx], node.col_offset, node.end_col_offset) != expected:
        return False
    lines[idx] = _splice(lines[idx], node.col_offset, node.end_col_offset, replacement)
    return True


_MUTABLE_LITERALS = (ast.List, ast.Dict, ast.Set)


def _mutable_defaults(fn):
    """[(param name, default node)] for every mutable-literal default, in
    declaration order."""
    out = []
    positional = list(fn.args.posonlyargs) + list(fn.args.args)
    defaults = list(fn.args.defaults)
    if defaults:
        for arg, default in zip(positional[len(positional) - len(defaults):], defaults):
            if isinstance(default, _MUTABLE_LITERALS):
                out.append((arg.arg, default))
    for arg, default in zip(fn.args.kwonlyargs, fn.args.kw_defaults):
        if default is not None and isinstance(default, _MUTABLE_LITERALS):
            out.append((arg.arg, default))
    return out


def _fix_mutable_default_arg(lines, lineno, ctx, pending):
    """`def f(x=[])` → `def f(x=None)` plus an `if x is None: x = []` guard.

    The guard goes at the very top of the body (after the docstring, if there
    is one), which preserves the original semantics for every caller that
    passes the argument and fixes the shared-state bug for every caller that
    doesn't.
    """
    fn = ctx.function_at(lineno)
    if fn is None:
        return False, "no function definition starts on this line"
    targets = _mutable_defaults(fn)
    if not targets:
        return False, "no mutable-literal default in the signature"
    if any(d.lineno != d.end_lineno for _, d in targets):
        return False, "default value spans multiple lines; rewrite it by hand"

    first = fn.body[0]
    body_idx = first.lineno - 1
    if body_idx >= len(lines) or lines[body_idx] is None:
        return False, "function body line not found"
    body_prefix = _byte_span(lines[body_idx], 0, first.col_offset)
    if body_prefix.strip():
        return False, "one-line def; put the body on its own line first"

    def_line = lines[fn.lineno - 1]
    if def_line is None:
        return False, "def line not found"
    def_indent = _LEADING_WS_RE.match(def_line).group(0)
    step = body_prefix[len(def_indent):] or "    "

    # Rewrite the defaults right-to-left within each line so that splicing one
    # doesn't shift the offsets of the ones before it.
    for _, default in sorted(targets, key=lambda t: (t[1].lineno, t[1].col_offset), reverse=True):
        if not _splice_node(lines, default, "None", ctx):
            return False, "signature no longer matches what was parsed"

    is_docstring = (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str))
    anchor_idx = (first.end_lineno - 1) if is_docstring else body_idx
    eol = _line_ending(lines[anchor_idx] or lines[body_idx])

    guard = "".join(
        f"{body_prefix}if {name} is None:{eol}{body_prefix}{step}{name} = {ctx.segment(default)}{eol}"
        for name, default in targets
    )
    if is_docstring:
        pending.insert_after(anchor_idx, guard)
    else:
        pending.insert_before(anchor_idx, guard)
    return True, None


def _fix_lost_exception_context(lines, lineno, ctx, _pending):
    """Append ` from <err>` to a raise that discards the handler's exception."""
    found = ctx.unchained_raise_at(lineno)
    if found is None:
        return False, "no unchained raise starts on this line"
    name, raise_node = found
    idx = raise_node.end_lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False, "raise statement line not found"
    segment = ctx.segment(raise_node)
    if segment is None:
        return False, "could not read the raise statement's source"
    tail = segment.splitlines()[-1]
    if not lines[idx].encode("utf-8")[:raise_node.end_col_offset].endswith(tail.encode("utf-8")):
        return False, "raise statement no longer matches what was parsed"
    lines[idx] = _splice(lines[idx], raise_node.end_col_offset, raise_node.end_col_offset, f" from {name}")
    return True, None


def _fix_unsafe_yaml_load(lines, lineno, ctx, _pending):
    """`yaml.load(x)` / `yaml.load(x, Loader=...)` → `yaml.safe_load(x)`."""
    call = ctx.yaml_load_at(lineno)
    if call is None:
        return False, "no yaml.load() call starts on this line"
    if len(call.args) != 1 or isinstance(call.args[0], ast.Starred):
        return False, "call doesn't take exactly one positional argument"
    if any(kw.arg != "Loader" for kw in call.keywords):
        return False, "call passes keywords other than Loader; rewrite it by hand"
    func_src = ctx.segment(call.func)
    arg_src = ctx.segment(call.args[0])
    if func_src is None or arg_src is None or not func_src.endswith("load"):
        return False, "could not read the call's source"
    replacement = f"{func_src[:-len('load')]}safe_load({arg_src})"
    if not _splice_node(lines, call, replacement, ctx):
        return False, "call spans multiple lines or no longer matches what was parsed"
    return True, None


def _fix_future_import_order(lines, lineno, ctx, pending):
    """Move a misplaced `from __future__ import ...` above the other imports."""
    node = ctx.future_import_at(lineno)
    if node is None:
        return False, "no __future__ import starts on this line"
    if node.lineno != node.end_lineno:
        return False, "import spans multiple lines; move it by hand"
    idx = node.lineno - 1
    if idx >= len(lines) or lines[idx] is None:
        return False, "import line not found"
    if node.col_offset != 0:
        return False, "import is indented, so it isn't at module scope"
    target_lineno = ctx.first_module_stmt_after_docstring(node)
    if target_lineno is None or target_lineno >= node.lineno:
        return False, "no earlier module-level statement to move the import above"
    target_idx = target_lineno - 1
    if lines[target_idx] is None:
        return False, "destination line was removed by another fix"

    moved = lines[idx]
    if not moved.endswith(("\n", "\r")):
        moved += _line_ending(lines[target_idx])
    lines[idx] = None
    pending.insert_before(target_idx, moved)
    return True, None


# ---------------------------------------------------------------------------
# Dispatch table
#
# Every entry is normalized to (lines, lineno, ctx, pending) -> (ok, reason) so
# the driver doesn't need to know which rules parse and which don't.
# ---------------------------------------------------------------------------

def _plain(fn, default_reason):
    """Adapt a text-level fix returning a bare bool."""
    def run(lines, lineno, _ctx, _pending):
        return (True, None) if fn(lines, lineno) else (False, default_reason)
    return run


def _with_reason(fn, default_reason):
    """Adapt a text-level fix that already explains its own refusals."""
    def run(lines, lineno, _ctx, _pending):
        ok, reason = fn(lines, lineno)
        return ok, (None if ok else (reason or default_reason))
    return run


_FIXES = {
    "trailing-whitespace": _plain(_fix_trailing_whitespace, "line not found"),
    "f-string-no-placeholder": _plain(_fix_fstring_no_placeholder, "f-string prefix not found on line"),
    "comparison-to-none": _plain(_fix_comparison_to_none, "pattern not found on line"),
    "comparison-to-true": _with_reason(_fix_comparison_to_true, "pattern not found on line"),
    "redundant-else": _plain(_fix_redundant_else, "else: line not found"),
    "bare-except": _plain(_fix_bare_except, "bare 'except:' not found on line"),
    "tab-indent": _plain(_fix_tab_indent, "no tab in leading indentation (tab may be inside a string/comment)"),
    "unused-import": _with_reason(_fix_unused_import, "could not fix"),
    "mutable-default-arg": _fix_mutable_default_arg,
    "lost-exception-context": _fix_lost_exception_context,
    "unsafe-yaml-load": _fix_unsafe_yaml_load,
    "future-import-order": _fix_future_import_order,
}


# ---------------------------------------------------------------------------
# File-level fix driver
# ---------------------------------------------------------------------------

def _fix_file(abs_path, root, issues, dry_run=False):
    rel = str(Path(abs_path).relative_to(root))
    result = FixResult(path=rel)
    try:
        with open(abs_path, encoding="utf-8", newline="") as fh:
            original = fh.read()
    except (OSError, UnicodeDecodeError) as exc:
        result.error = str(exc)
        result.original_text = result.new_text = ""
        for iss in issues:
            result.skipped.append(SkippedFix(iss["line"], iss["symbol"], str(exc)))
        return result

    result.original_text = original

    lines = original.splitlines(keepends=True)
    pending = Pending()

    ctx, ctx_error = None, None
    if any(iss["symbol"] in AST_RULES for iss in issues):
        try:
            ctx = _AstContext(original)
        except SyntaxError as exc:
            ctx_error = f"file doesn't parse ({exc.msg}); AST-guided fixes skipped"

    # Process issues bottom-to-top so that line removals/insertions from one
    # fix do not shift the line numbers of issues earlier in the same file.
    sorted_issues = sorted(issues, key=lambda i: i["line"], reverse=True)

    for iss in sorted_issues:
        rule = iss["symbol"]
        lineno = iss["line"]

        fix = _FIXES.get(rule)
        if fix is None:
            continue
        if rule in AST_RULES and ctx is None:
            result.skipped.append(SkippedFix(lineno, rule, ctx_error or "file could not be parsed"))
            continue

        ok, reason = fix(lines, lineno, ctx, pending)
        if ok:
            result.applied.append(AppliedFix(lineno, rule))
        else:
            result.skipped.append(SkippedFix(lineno, rule, reason or "could not fix"))

    new_text = pending.assemble(lines)
    result.new_text = new_text

    if not dry_run and result.changed:
        with open(abs_path, "w", encoding="utf-8", newline="") as fh:
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
