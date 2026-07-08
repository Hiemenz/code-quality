"""Resource lifecycle check: a well-known resource-opening call (`open()`,
`socket.socket()`, `urllib.request.urlopen()`) that is neither used as a
`with`/`async with` context manager nor explicitly `.close()`d anywhere in
the same scope.

Scoped per function (and separately per module-level top code), stopping at
nested function/class boundaries -- same "each scope judged on its own"
convention as `python_analyzer.py`'s `_AssignCollector`. To keep false
positives low, a resource is only flagged when there is truly no visible
way it gets released:

- Wrapped in `with`/`async with` -- never flagged, that's the correct idiom.
- Returned directly (`return open(path)`) or passed directly as an argument
  to another call (`contextlib.closing(open(path))`) -- ownership is
  assumed to transfer to the caller/wrapper, which this tool has no way to
  verify further.
- Assigned to a plain local name (`f = open(path)`) -- flagged unless that
  name later has `.close()` called on it, is returned, or is passed as an
  argument to some other call anywhere in the same scope (again, assumed
  ownership transfer -- e.g. `return f` or `json.load(f)` some lines later).
- Not assigned to a name at all and not returned/passed directly (a chained
  one-liner like `open(path).read()`) -- flagged, since there is no way for
  this code to ever close it, *unless* the chain itself is `.close()`
  (`open(path).close()`), which is closed inline and never flagged.

Assignment targets other than a plain `Name` (tuple-unpacking, attribute,
subscript) are out of scope entirely, same as `_AssignCollector` -- an
attribute target like `self.f = open(path)` usually means the handle's
lifetime is managed by a different method (e.g. closed in `__exit__`/
`close()`), which this per-scope check cannot see, so guessing there would
mostly produce noise.
"""

import ast

from codequality.analyzers.base import Issue

_RESOURCE_CALL_NAMES = {"open", "socket.socket", "urllib.request.urlopen"}


def _call_full_name(node):
    """Best-effort dotted name for a Call's callee, e.g. 'socket.socket' or 'open'."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _resource_call_name(node):
    if not isinstance(node, ast.Call):
        return None
    name = _call_full_name(node)
    return name if name in _RESOURCE_CALL_NAMES else None


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


class _ScopeVisitor(ast.NodeVisitor):
    """Collects resource-lifecycle facts for a single function/module scope,
    stopping at nested function/class boundaries.
    """

    def __init__(self):
        self.resource_calls = []  # list[(call_node, assigned_name_or_None)]
        self._recorded_ids = set()
        self.with_call_ids = set()
        self.direct_safe_ids = set()  # call id returned or passed directly to another call
        self.closed_names = set()
        self.used_names = set()  # a bare Name returned or passed as an argument elsewhere

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def _record(self, call_node, name):
        if id(call_node) not in self._recorded_ids:
            self._recorded_ids.add(id(call_node))
            self.resource_calls.append((call_node, name))

    def visit_With(self, node):
        for item in node.items:
            if isinstance(item.context_expr, ast.Call):
                self.with_call_ids.add(id(item.context_expr))
        self.generic_visit(node)

    visit_AsyncWith = visit_With

    def visit_Return(self, node):
        if isinstance(node.value, ast.Call):
            self.direct_safe_ids.add(id(node.value))
        elif isinstance(node.value, ast.Name):
            self.used_names.add(node.value.id)
        self.generic_visit(node)

    def visit_Assign(self, node):
        if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Name)
                and _resource_call_name(node.value) is not None):
            self._record(node.value, node.targets[0].id)
        self.generic_visit(node)

    def visit_Call(self, node):
        if _resource_call_name(node) is not None:
            self._record(node, None)
        if isinstance(node.func, ast.Attribute) and node.func.attr == "close":
            if isinstance(node.func.value, ast.Name):
                self.closed_names.add(node.func.value.id)
            elif isinstance(node.func.value, ast.Call):
                # chained `open(path).close()` -- closed inline, no name involved
                self.direct_safe_ids.add(id(node.func.value))
        for arg in list(node.args) + [kw.value for kw in node.keywords]:
            if isinstance(arg, ast.Call):
                self.direct_safe_ids.add(id(arg))
            elif isinstance(arg, ast.Name):
                self.used_names.add(arg.id)
        self.generic_visit(node)


def _scope_issues(scope_node, path, only_lines):
    visitor = _ScopeVisitor()
    visitor.generic_visit(scope_node)
    issues = []
    for call_node, name in visitor.resource_calls:
        if id(call_node) in visitor.with_call_ids or id(call_node) in visitor.direct_safe_ids:
            continue
        if not _in_scope(call_node, only_lines):
            continue
        if name is not None and (name in visitor.closed_names or name in visitor.used_names):
            continue
        call_name = _resource_call_name(call_node)
        issues.append(
            Issue(path, call_node.lineno, "correctness", "warn", "unclosed-resource",
                  f"{call_name}(...) is never used as a context manager or explicitly closed")
        )
    return issues


def resource_lifecycle_issues(tree, path, only_lines=None):
    """Every unclosed-resource issue in `tree`: module-level top code plus
    each function/method body, each judged as its own scope.
    """
    issues = _scope_issues(tree, path, only_lines)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(_scope_issues(node, path, only_lines))
    return issues
