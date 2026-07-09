"""Async/await misuse check: a call to a locally-defined `async def`
function/method whose result is never awaited, scheduled, or handed off --
meaning the coroutine object is created but its body never actually runs.
This is a purely syntactic, very common real-world bug (forgetting an
`await`), not a style preference.

Scoped per function (and separately per module-level top code), stopping at
nested function/class boundaries -- same convention as
`resource_lifecycle.py`. `async def` names are collected once per module
(any `async def`, at any nesting level, anywhere in the file) and matched
against a call's *last* dotted segment only (`foo` for both `foo()` and
`self.foo()`/`obj.foo()`) -- there is no type information here, so this
can't know which class `self`/`obj` actually is; it only knows a same-named
`async def` exists somewhere in this file. That's a deliberate, documented
tradeoff: a same-named sync method elsewhere could cause a false positive,
but the alternative (full type resolution) is out of scope for this tool.

A coroutine call is only flagged when there is truly no visible sign its
result is ever consumed:

- Directly `await`ed -- never flagged, that's the correct idiom.
- Passed directly to a scheduling/entry-point call (`asyncio.create_task`/
  `ensure_future`/`gather`/`wait`/`shield`/`run`/`run_until_complete`, by
  bare method/function name so both `asyncio.gather(...)` and
  `loop.run_until_complete(...)` match) -- ownership of actually running it
  transfers to that call.
- Returned directly (`return foo()`) -- ownership transfers to the caller,
  who is expected to await it.
- Assigned to a plain local name (`coro = foo()`) -- flagged unless that
  name is later awaited, returned, or passed to one of the scheduling
  calls above, anywhere in the same scope.

Assignment targets other than a plain `Name` are out of scope, same as
`resource_lifecycle.py`'s reasoning for `self.f = open(path)`.
"""

import ast

from codequality.analyzers.base import Issue

SYMBOL = "unawaited-coroutine"

_SCHEDULING_CALLS = {"create_task", "ensure_future", "gather", "wait", "shield", "run", "run_until_complete"}


def _called_name(node):
    """Last dotted segment of a Call's callee: 'foo' for both `foo()` and
    `self.foo()`/`obj.foo()`.
    """
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _collect_async_def_names(tree):
    return {node.name for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef)}


class _ScopeVisitor(ast.NodeVisitor):
    """Collects async-call facts for a single function/module scope,
    stopping at nested function/class boundaries.
    """

    def __init__(self, async_names):
        self.async_names = async_names
        self.calls = []  # list[(call_node, assigned_name_or_None)]
        self._recorded_ids = set()
        self.safe_ids = set()
        self.consumed_names = set()  # a bare Name awaited/returned/scheduled elsewhere

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def _is_target_call(self, node):
        return isinstance(node, ast.Call) and _called_name(node) in self.async_names

    def _record(self, call_node, name):
        if id(call_node) not in self._recorded_ids:
            self._recorded_ids.add(id(call_node))
            self.calls.append((call_node, name))

    def _mark_consumed(self, value_node):
        if isinstance(value_node, ast.Call):
            self.safe_ids.add(id(value_node))
        elif isinstance(value_node, ast.Name):
            self.consumed_names.add(value_node.id)

    def visit_Await(self, node):
        self._mark_consumed(node.value)
        self.generic_visit(node)

    def visit_Return(self, node):
        self._mark_consumed(node.value)
        self.generic_visit(node)

    def visit_Assign(self, node):
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and self._is_target_call(node.value):
            self._record(node.value, node.targets[0].id)
        self.generic_visit(node)

    def visit_Call(self, node):
        if self._is_target_call(node):
            self._record(node, None)
        if _called_name(node) in _SCHEDULING_CALLS:
            for arg in node.args:
                self._mark_consumed(arg)
        self.generic_visit(node)


def _scope_issues(scope_node, path, async_names, only_lines):
    visitor = _ScopeVisitor(async_names)
    visitor.generic_visit(scope_node)
    issues = []
    for call_node, name in visitor.calls:
        if id(call_node) in visitor.safe_ids:
            continue
        if not _in_scope(call_node, only_lines):
            continue
        if name is not None and name in visitor.consumed_names:
            continue
        called = _called_name(call_node)
        issues.append(
            Issue(path, call_node.lineno, "correctness", "warn", SYMBOL,
                  f"'{called}(...)' calls a local async function but the result is never awaited or "
                  f"scheduled -- its body never runs")
        )
    return issues


def unawaited_coroutine_issues(tree, path, only_lines=None):
    """Every unawaited-coroutine issue in `tree`. Returns [] immediately if
    the file defines no `async def` at all -- nothing to match against.
    """
    async_names = _collect_async_def_names(tree)
    if not async_names:
        return []
    issues = _scope_issues(tree, path, async_names, only_lines)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(_scope_issues(node, path, async_names, only_lines))
    return issues
