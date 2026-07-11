"""Placeholder/stub detection: code that *looks* finished but was never
actually written. Two related checks, both aimed at a failure mode much
more common in LLM-generated code than in hand-written code -- emitting a
complete-looking file whose actual work is a stub or an ellipsis comment:

- **`stub-implementation`** -- a function whose body (after its docstring)
  is nothing but `pass`, `...`, or a single `raise NotImplementedError`.
  The `raise NotImplementedError` form is reported at `warn` (calling it
  crashes, and outside an abstract base that's almost never intentional);
  the `pass`/`...`/docstring-only forms at `info`, since an intentional
  no-op hook is a legitimate pattern this check can't distinguish from an
  unfinished one.

- **`placeholder-comment`** -- a standalone comment line matching one of a
  fixed set of "the model elided the code here" phrases: `... rest of the
  code ...`, `your logic here`, `implementation omitted`, and so on. These
  are near-unambiguous: a human rarely writes them, an LLM asked to edit a
  file writes them constantly. Only lines that *start* with a comment
  marker are scanned -- detecting trailing comments would require deciding
  whether a `#` sits inside a string literal, and a `#` in a URL fragment
  shouldn't produce a finding. Reported at `warn`.

Exemptions for `stub-implementation`, all in the direction of fewer false
positives (same philosophy as `dead_code.py`):

- any decorated function -- `@abstractmethod`, `@overload`,
  `@singledispatch` registrations and framework hooks are all
  decorator-driven, and guessing which decorators imply "stub body is
  fine" is a losing game;
- every method of an abstract-looking class: a base whose last dotted
  segment is `Protocol`/`ABC`, a `metaclass=ABCMeta` keyword, or any
  sibling method decorated with `abstractmethod`/`abstractproperty`
  (a class with one real abstractmethod often declares its other abstract
  hooks as undecorated `raise NotImplementedError` by convention);
- `test_*` functions -- an empty test body is already flagged by
  `assertion-free-test`, and one finding per defect is enough.
"""

import ast
import re

from codequality.analyzers.base import Issue

STUB_SYMBOL = "stub-implementation"
COMMENT_SYMBOL = "placeholder-comment"

_ABSTRACT_BASE_NAMES = {"Protocol", "ABC", "ABCMeta"}
_ABSTRACT_DECORATORS = {"abstractmethod", "abstractproperty"}

PLACEHOLDER_COMMENT_RE = re.compile(
    r"(?:"
    r"your\s+(?:code|logic|implementation)\s+(?:goes\s+)?here"
    r"|(?:add|insert|put)\s+(?:your\s+)?(?:code|logic|implementation)\s+here"
    r"|(?:code|logic|implementation)\s+goes\s+here"
    r"|implement\s+(?:this|me)\b"
    r"|implementation\s+(?:omitted|elided|left\s+as\s+an?\s+exercise)"
    r"|omitted\s+for\s+brevity"
    r"|rest\s+of\s+(?:the\s+)?(?:\w+\s+)?(?:code|file|function|method|class|implementation|logic)"
    r"|remaining\s+(?:code|implementation|logic)"
    r"|existing\s+(?:code|implementation)\s*(?:\.\.\.|remains|unchanged)"
    r"|\.\.\.\s*existing\s+(?:code|implementation)"
    r")",
    re.IGNORECASE,
)

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _in_scope(node_or_lineno, only_lines):
    if only_lines is None:
        return True
    if isinstance(node_or_lineno, int):
        return node_or_lineno in only_lines
    lineno = getattr(node_or_lineno, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node_or_lineno, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _last_segment(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _last_segment(node.func)
    return None


def _is_abstractish_class(node):
    for base in node.bases:
        if _last_segment(base) in _ABSTRACT_BASE_NAMES:
            return True
    for kw in node.keywords:
        if kw.arg == "metaclass" and _last_segment(kw.value) in _ABSTRACT_BASE_NAMES:
            return True
    for child in node.body:
        if isinstance(child, _FUNC_TYPES):
            if any(_last_segment(dec) in _ABSTRACT_DECORATORS for dec in child.decorator_list):
                return True
    return False


def _exempt_method_ids(tree):
    """ids of every function node that is a direct member of an
    abstract-looking class -- see the module docstring's exemption list.
    """
    exempt = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and _is_abstractish_class(node):
            for child in node.body:
                if isinstance(child, _FUNC_TYPES):
                    exempt.add(id(child))
    return exempt


def _body_after_docstring(fn_node):
    body = fn_node.body
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        return body[1:]
    return body


def _is_ellipsis_or_pass(stmt):
    if isinstance(stmt, ast.Pass):
        return True
    return isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Constant) and stmt.value.value is Ellipsis


def _is_not_implemented_raise(stmt):
    if not isinstance(stmt, ast.Raise) or stmt.exc is None:
        return False
    return _last_segment(stmt.exc) == "NotImplementedError"


def _stub_kind(fn_node):
    """None if the function has a real body, otherwise which stub shape it
    is: 'raise' for a lone `raise NotImplementedError`, 'empty' for a body
    that is only `pass`/`...`/a docstring.
    """
    body = _body_after_docstring(fn_node)
    if len(body) == 1 and _is_not_implemented_raise(body[0]):
        return "raise"
    if all(_is_ellipsis_or_pass(stmt) for stmt in body):
        return "empty"
    return None


def stub_implementation_issues(tree, path, only_lines=None):
    """Every stub-implementation issue in `tree`."""
    exempt = _exempt_method_ids(tree)
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, _FUNC_TYPES):
            continue
        if id(node) in exempt or node.decorator_list or node.name.startswith("test_"):
            continue
        if not _in_scope(node, only_lines):
            continue
        kind = _stub_kind(node)
        if kind is None:
            continue
        if kind == "raise":
            severity = "warn"
            detail = "just raises NotImplementedError -- calling it crashes"
        else:
            severity = "info"
            detail = "is empty (pass/.../docstring only) -- it silently does nothing"
        issues.append(
            Issue(path, node.lineno, "correctness", severity, STUB_SYMBOL,
                  f"Function '{node.name}' has no implementation: its body {detail}")
        )
    return issues


def placeholder_comment_issues(path, lines, only_lines=None):
    """Every placeholder-comment issue in `lines` (the file's raw source
    lines). Only standalone comment lines are scanned -- see the module
    docstring for why trailing comments are out of scope.
    """
    issues = []
    for i, raw in enumerate(lines, start=1):
        if not _in_scope(i, only_lines):
            continue
        text = raw.strip()
        if not text.startswith("#"):
            continue
        if PLACEHOLDER_COMMENT_RE.search(text):
            issues.append(
                Issue(path, i, "correctness", "warn", COMMENT_SYMBOL,
                      f"Placeholder comment where real code should be: {text[:100]}")
            )
    return issues
