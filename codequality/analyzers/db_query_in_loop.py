"""N+1-shaped query check: a database-looking call sitting inside a
`for`/`async for`/`while` loop body, where it would run once per iteration
instead of once total.

Deliberately narrow on which calls count as "a query" -- this tool has no
type information, so it can't know that some arbitrary `.get(...)` call is
actually hitting a database. Flagging every `.get`/`.filter`/`.all` call
anywhere would mostly catch dict/list methods and be pure noise. Instead,
only receiver shapes that are strongly, unambiguously DB-flavored are
matched (see `_query_call_label`):

- Django-style manager access: `<Model>.objects.get/filter/exclude/all/
  count/first/values/values_list(...)`.
- SQLAlchemy-style session: `<...>.session.query/execute(...)`.
- DB-API cursor: `<...>.cursor.execute/executemany(...)`.
- A raw connection variable named `conn`/`connection`/`db`:
  `<name>.execute/query(...)`.

Only the *first* call in a chain is inspected (e.g. for
`Model.objects.filter(x=1).exclude(y=2)`, only the `.filter(...)` hop
resolves to a dotted name at all -- `.exclude(...)`'s receiver is a Call,
not a Name/Attribute chain) so a multi-hop queryset chain is reported once,
not once per hop.

Scoped like `resource_lifecycle.py`: a query call is only attributed to the
loop if it's reachable without crossing into a nested function/class
definition -- a query inside a locally-defined helper isn't necessarily run
once per iteration (it might be called once, stored, or called elsewhere),
so that's out of scope here.
"""

import ast

from codequality.analyzers.base import Issue

_LOOP_TYPES = (ast.For, ast.AsyncFor, ast.While)

_OBJECTS_METHODS = {"get", "filter", "exclude", "all", "count", "first", "values", "values_list"}
_SESSION_METHODS = {"query", "execute"}
_CURSOR_METHODS = {"execute", "executemany"}
_CONN_BASE_NAMES = {"conn", "connection", "db"}
_CONN_METHODS = {"execute", "query"}


def _call_full_name(node):
    """Best-effort dotted name for a Call's callee, e.g. 'Model.objects.get'.
    None if the callee isn't a plain Name/Attribute chain (e.g. a chained
    call like `qs.filter(...).exclude(...)`).
    """
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


def _query_call_label(node):
    """Returns a human-readable label if `node` looks like a DB query call,
    else None. See module docstring for exactly which shapes count.
    """
    full_name = _call_full_name(node)
    if full_name is None or "." not in full_name:
        return None
    parts = full_name.split(".")
    method = parts[-1]
    receiver_tail = parts[-2]

    if receiver_tail == "objects" and method in _OBJECTS_METHODS:
        return full_name
    if receiver_tail == "session" and method in _SESSION_METHODS:
        return full_name
    if receiver_tail == "cursor" and method in _CURSOR_METHODS:
        return full_name
    if parts[0] in _CONN_BASE_NAMES and method in _CONN_METHODS:
        return full_name
    return None


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


class _LoopBodyCallCollector(ast.NodeVisitor):
    """Every Call reachable within a loop's body, stopping at nested
    function/class boundaries (see module docstring).
    """

    def __init__(self):
        self.calls = []

    def _stop(self, node):
        return

    visit_FunctionDef = _stop
    visit_AsyncFunctionDef = _stop
    visit_Lambda = _stop
    visit_ClassDef = _stop

    def visit_Call(self, node):
        self.calls.append(node)
        self.generic_visit(node)


def query_in_loop_issues(tree, path, only_lines=None):
    """Every query-in-loop issue in `tree`. A query call nested inside
    several loops is reported once, against whichever loop's scan reaches
    it first -- not once per enclosing loop.
    """
    issues = []
    seen_ids = set()
    for node in ast.walk(tree):
        if not isinstance(node, _LOOP_TYPES):
            continue
        collector = _LoopBodyCallCollector()
        for stmt in node.body:
            collector.visit(stmt)
        for call in collector.calls:
            if id(call) in seen_ids or not _in_scope(call, only_lines):
                continue
            label = _query_call_label(call)
            if label is None:
                continue
            seen_ids.add(id(call))
            issues.append(
                Issue(path, call.lineno, "correctness", "warn", "query-in-loop",
                      f"{label}(...) looks like a query executed on every loop iteration -- consider batching")
            )
    return issues
