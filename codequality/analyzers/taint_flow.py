"""Best-effort taint tracking for SQL execute calls.

Deliberately narrow in one way that's easy to widen later, and bounded in
another way that's deliberate and permanent:

- **One sink family, on purpose.** SQL execute methods (`_SQL_EXEC_METHODS`)
  are the only sink checked here. `python_security.py`'s existing checks
  for eval/exec/os.system/subprocess shell=True/yaml.load already fire
  *unconditionally* on any call to those (regardless of where the argument
  came from), so tracking taint into them would only ever produce a
  second, redundant issue on a line `python_security.py` has already
  flagged. `python_security._sql_injection_issue` is the one check that
  only looks at the argument *expression* (f-string/%/+/.format() built
  inline at the call site) -- a query string assembled a few lines
  earlier and passed in as a bare variable slips past it entirely.
  That gap is what this module fills.

- **Exactly one hop of interprocedural tracking, permanently.** Tainting a
  local variable and passing it to a helper function defined in the same
  module is followed one level deep (see `_Analyzer._summary_for`): if the
  helper passes that parameter to a SQL sink internally, or returns
  something derived from it, the caller is flagged too. Going further
  (the helper's helper, or anything across files) would need a real call
  graph and risks false positives compounding with each hop -- one hop
  covers the common "thin repository/service wrapper" shape without that
  risk, and summaries are computed with hop resolution turned off, which
  also means a self-recursive or mutually-recursive pair of functions
  can't cause unbounded recursion here.

Two kinds of taint sources are recognized:

- **Expressions** -- `input()`, `sys.argv[...]`, `os.environ.get(...)`, and
  common web-framework request-data accessors (`request.args.get`,
  `request.json`, `request.headers.get`, Django's `request.META.get`,
  Starlette/FastAPI's `request.query_params.get`, ...). See
  `_SOURCE_CALL_SUFFIXES`/`_SOURCE_ATTR_NAMES`/`_SOURCE_SUBSCRIPT_BASES`.
- **Route handler parameters** -- a function decorated with a recognized
  web-framework route decorator (Flask's `@app.route(...)`, FastAPI/
  Starlette's `@app.get/post/put/delete/patch(...)`) has *all* of its
  parameters treated as tainted at entry, since in these frameworks path
  params, query params, and request bodies commonly arrive as plain
  function parameters, not only via `request.*`. A parameter whose
  default is `Depends(...)` (FastAPI dependency injection -- typically a
  DB session or auth object, not user input) is excluded, as is a
  leading `self`/`cls`. See `_route_handler_seed`.

Branches (`if`/`for`/`while`/`with`/`try`) are unioned rather than
intersected: if either side of a branch taints a name, the merged state
after the branch treats it as tainted. This over-approximates (a false
positive is possible if only the untainted branch could actually run) in
favor of not missing a real one -- the same bias this tool's other
best-effort checks (e.g. `broad-except-swallow`) already take.
"""

import ast

from codequality.analyzers.base import Issue

_SQL_EXEC_METHODS = {"execute", "executemany", "raw"}

_SOURCE_CALL_SUFFIXES = (
    "environ.get",
    "request.args.get", "request.form.get", "request.values.get",
    "request.GET.get", "request.POST.get", "request.json.get",
    "request.cookies.get", "request.headers.get", "request.query_params.get",
    "request.META.get",
)
_SOURCE_ATTR_NAMES = (
    "request.json", "request.args", "request.form", "request.values",
    "request.GET", "request.POST", "request.data", "request.body",
    "request.cookies", "request.headers", "request.query_params", "request.META",
)
_SOURCE_SUBSCRIPT_BASES = ("sys.argv", "os.environ")

_ROUTE_DECORATOR_METHODS = {"get", "post", "put", "delete", "patch", "route", "websocket"}


def _dotted_name(node):
    """Best-effort dotted name for a Name/Attribute chain, e.g. 'os.environ'."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted_name(node.value)
        return f"{base}.{node.attr}" if base is not None else None
    return None


def _suffix_match(name, candidates):
    return name is not None and any(name == c or name.endswith("." + c) for c in candidates)


def _source_provenance(node):
    """Short description of `node` if it's a known untrusted-input
    expression (a taint *source*), else None.
    """
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id == "input":
            return "input()"
        name = _dotted_name(node.func)
        if _suffix_match(name, _SOURCE_CALL_SUFFIXES):
            return f"{name}(...)"
        return None
    if isinstance(node, ast.Subscript):
        base = _dotted_name(node.value)
        if _suffix_match(base, _SOURCE_SUBSCRIPT_BASES):
            return f"{base}[...]"
        return None
    if isinstance(node, ast.Attribute):
        dotted = _dotted_name(node)
        if _suffix_match(dotted, _SOURCE_ATTR_NAMES):
            return dotted
    return None


def _is_tainted_expr(node, tainted):
    return any(isinstance(n, ast.Name) and n.id in tainted for n in ast.walk(node))


def _target_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []  # Attribute/Subscript targets aren't tracked


def _positional_params(fn):
    return list(fn.args.posonlyargs) + list(fn.args.args)


def _param_defaults(arguments):
    """dict[param_name -> default expr node], for whichever params have one."""
    defaults = {}
    positional = list(arguments.posonlyargs) + list(arguments.args)
    for arg, default in zip(reversed(positional), reversed(arguments.defaults)):
        defaults[arg.arg] = default
    for arg, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
        if default is not None:
            defaults[arg.arg] = default
    return defaults


def _is_depends_default(default):
    if not isinstance(default, ast.Call):
        return False
    name = _dotted_name(default.func)
    return name is not None and (name == "Depends" or name.endswith(".Depends"))


def _is_route_decorator(dec):
    return isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.func.attr in _ROUTE_DECORATOR_METHODS


def _route_handler_seed(fn):
    """Initial tainted dict for `fn` if it's a recognized route handler,
    else None (meaning "not a route handler -- start empty" to the caller).
    See the module docstring's "Route handler parameters" section.
    """
    if not any(_is_route_decorator(d) for d in fn.decorator_list):
        return None
    params = _positional_params(fn) + list(fn.args.kwonlyargs)
    defaults = _param_defaults(fn.args)
    seed = {}
    for i, param in enumerate(params):
        if i == 0 and param.arg in ("self", "cls"):
            continue
        if _is_depends_default(defaults.get(param.arg)):
            continue
        seed[param.arg] = f"route handler parameter '{param.arg}'"
    return seed


def _collect_named_functions(tree):
    """dict[name -> FunctionDef/AsyncFunctionDef], for every function/method
    defined anywhere in `tree`. A name defined more than once maps to None
    (ambiguous -- don't resolve calls to it) rather than guessing which
    definition a bare-name call site meant.
    """
    functions = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            functions[node.name] = None if node.name in functions else node
    return functions


def _merge_branches(tainted, *branch_states):
    tainted.clear()
    for state in branch_states:
        for name, prov in state.items():
            tainted.setdefault(name, prov)


class _Analyzer:
    """One instance per file. `functions` (built once) lets sink/return
    checks resolve a bare-name call like `helper(x)` to its definition
    elsewhere in the same module for the one-hop interprocedural check;
    `summaries` memoizes the per-parameter result of analyzing each such
    function, computed lazily and at most once per function per file.
    """

    def __init__(self, path, functions):
        self.path = path
        self.functions = functions
        self.summaries = {}

    def run(self, fn):
        """All `tainted-data-flow` issues in `fn`, with hop resolution on."""
        tainted = dict(_route_handler_seed(fn) or {})
        issues = []
        self._walk_stmts(fn.body, tainted, issues, None, resolve_helpers=True)
        return issues

    def _summary_for(self, name):
        """{"sink": bool, "returns_tainted": bool} per positional parameter
        of the function named `name`, or None if `name` doesn't resolve to
        exactly one function in this file. Computed with `resolve_helpers`
        off, so this never itself follows a further hop -- that's what
        bounds interprocedural tracking to exactly one level.
        """
        if name in self.summaries:
            return self.summaries[name]
        fn = self.functions.get(name)
        if fn is None:
            self.summaries[name] = None
            return None
        summary = {}
        for param in _positional_params(fn):
            issues, events = [], {"returns_tainted": False}
            seed = {param.arg: f"parameter '{param.arg}'"}
            self._walk_stmts(fn.body, seed, issues, events, resolve_helpers=False)
            summary[param.arg] = {"sink": bool(issues), "returns_tainted": events["returns_tainted"]}
        self.summaries[name] = summary
        return summary

    def _matching_tainted_args(self, node, fn, tainted):
        """(param_name, arg_name, provenance) for each of `node`'s positional
        args that's a currently-tainted bare Name, paired with the callee
        `fn`'s corresponding parameter. Keyword args make matching
        ambiguous enough to just skip (best-effort, same conservative
        stance as the SQL sink check below).
        """
        if node.keywords:
            return []
        return [
            (param.arg, arg.id, tainted[arg.id])
            for param, arg in zip(_positional_params(fn), node.args)
            if isinstance(arg, ast.Name) and arg.id in tainted
        ]

    def _helper_call_name(self, node):
        return node.func.id if isinstance(node.func, ast.Name) else None

    def _helper_sink_issue(self, node, tainted):
        name = self._helper_call_name(node)
        fn = self.functions.get(name) if name else None
        if fn is None:
            return None
        summary = self._summary_for(name)
        if summary is None:
            return None
        for pname, arg_name, prov in self._matching_tainted_args(node, fn, tainted):
            if summary.get(pname, {}).get("sink"):
                return Issue(
                    self.path, node.lineno, "security", "error", "tainted-data-flow",
                    f"'{arg_name}' (from {prov}) is passed to {name}(), whose '{pname}' parameter "
                    f"reaches a SQL execute call internally -- vulnerable to SQL injection"
                )
        return None

    def _helper_return_provenance(self, node, tainted):
        name = self._helper_call_name(node)
        fn = self.functions.get(name) if name else None
        if fn is None:
            return None
        summary = self._summary_for(name)
        if summary is None:
            return None
        for pname, _arg_name, prov in self._matching_tainted_args(node, fn, tainted):
            if summary.get(pname, {}).get("returns_tainted"):
                return prov
        return None

    def _sink_issue(self, node, tainted):
        """If Call `node` is a `cursor.execute(...)`-style call whose sole
        argument is a currently-tainted bare variable, return the Issue.
        Mirrors `python_security._sql_injection_issue`'s "exactly one arg,
        no separate params" shape -- the parameterized form isn't flagged.
        """
        func = node.func
        method = func.attr if isinstance(func, ast.Attribute) else (func.id if isinstance(func, ast.Name) else None)
        if method not in _SQL_EXEC_METHODS:
            return None
        if len(node.args) != 1 or node.keywords:
            return None
        arg = node.args[0]
        if not (isinstance(arg, ast.Name) and arg.id in tainted):
            return None
        return Issue(
            self.path, node.lineno, "security", "error", "tainted-data-flow",
            f"'{arg.id}' (from {tainted[arg.id]}, via one or more intermediate variables) is passed to "
            f".{method}() -- vulnerable to SQL injection"
        )

    def _scan_sinks(self, expr, tainted, issues, resolve_helpers):
        for node in ast.walk(expr):
            if not isinstance(node, ast.Call):
                continue
            issue = self._sink_issue(node, tainted)
            if issue is None and resolve_helpers:
                issue = self._helper_sink_issue(node, tainted)
            if issue is not None:
                issues.append(issue)

    def _rhs_provenance(self, rhs, tainted, resolve_helpers):
        prov = _source_provenance(rhs)
        if prov is not None:
            return prov
        if isinstance(rhs, ast.Name):
            return tainted.get(rhs.id)
        if resolve_helpers and isinstance(rhs, ast.Call):
            helper_prov = self._helper_return_provenance(rhs, tainted)
            if helper_prov is not None:
                return helper_prov
        first_tainted = next((n.id for n in ast.walk(rhs) if isinstance(n, ast.Name) and n.id in tainted), None)
        return tainted.get(first_tainted) if first_tainted else None

    def _apply_assign(self, targets, rhs, tainted, resolve_helpers):
        prov = self._rhs_provenance(rhs, tainted, resolve_helpers)
        names = [n for t in targets for n in _target_names(t)]
        for name in names:
            if prov is not None:
                tainted[name] = prov
            else:
                tainted.pop(name, None)

    def _apply_augassign(self, stmt, tainted, resolve_helpers):
        if not isinstance(stmt.target, ast.Name):
            return
        name = stmt.target.id
        prov = self._rhs_provenance(stmt.value, tainted, resolve_helpers) or tainted.get(name)
        if prov is not None:
            tainted[name] = prov

    def _walk_stmts(self, stmts, tainted, issues, events, resolve_helpers):
        for stmt in stmts:
            self._walk_stmt(stmt, tainted, issues, events, resolve_helpers)

    def _walk_stmt(self, stmt, tainted, issues, events, resolve_helpers):
        if isinstance(stmt, ast.If):
            self._scan_sinks(stmt.test, tainted, issues, resolve_helpers)
            then_state, else_state = dict(tainted), dict(tainted)
            self._walk_stmts(stmt.body, then_state, issues, events, resolve_helpers)
            self._walk_stmts(stmt.orelse, else_state, issues, events, resolve_helpers)
            _merge_branches(tainted, then_state, else_state)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            self._scan_sinks(stmt.iter, tainted, issues, resolve_helpers)
            pre_state, body_state = dict(tainted), dict(tainted)
            self._walk_stmts(stmt.body, body_state, issues, events, resolve_helpers)
            self._walk_stmts(stmt.orelse, body_state, issues, events, resolve_helpers)
            _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
        elif isinstance(stmt, ast.While):
            self._scan_sinks(stmt.test, tainted, issues, resolve_helpers)
            pre_state, body_state = dict(tainted), dict(tainted)
            self._walk_stmts(stmt.body, body_state, issues, events, resolve_helpers)
            self._walk_stmts(stmt.orelse, body_state, issues, events, resolve_helpers)
            _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                self._scan_sinks(item.context_expr, tainted, issues, resolve_helpers)
            self._walk_stmts(stmt.body, tainted, issues, events, resolve_helpers)
        elif isinstance(stmt, ast.Try):
            pre_state = dict(tainted)
            try_state = dict(pre_state)
            self._walk_stmts(stmt.body, try_state, issues, events, resolve_helpers)
            # Body may have failed partway through, or a handler may have
            # run instead of the body completing -- union try/pre/every
            # handler's outcome rather than picking one, same
            # over-approximation as If.
            branch_states = [try_state, pre_state]
            for handler in stmt.handlers:
                handler_state = dict(pre_state)
                self._walk_stmts(handler.body, handler_state, issues, events, resolve_helpers)
                branch_states.append(handler_state)
            _merge_branches(tainted, *branch_states)
            self._walk_stmts(stmt.orelse, tainted, issues, events, resolve_helpers)
            self._walk_stmts(stmt.finalbody, tainted, issues, events, resolve_helpers)
        elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            pass  # nested scope: analyzed independently by taint_issues' own ast.walk
        elif isinstance(stmt, ast.Assign):
            self._scan_sinks(stmt.value, tainted, issues, resolve_helpers)
            self._apply_assign(stmt.targets, stmt.value, tainted, resolve_helpers)
        elif isinstance(stmt, ast.AugAssign):
            self._scan_sinks(stmt.value, tainted, issues, resolve_helpers)
            self._apply_augassign(stmt, tainted, resolve_helpers)
        elif isinstance(stmt, ast.Return):
            if stmt.value is not None:
                self._scan_sinks(stmt.value, tainted, issues, resolve_helpers)
                if events is not None and _is_tainted_expr(stmt.value, tainted):
                    events["returns_tainted"] = True
        else:
            self._scan_sinks(stmt, tainted, issues, resolve_helpers)


def _in_scope(lineno, only_lines):
    return only_lines is None or lineno in only_lines


def taint_issues(tree, path, only_lines):
    """Every `tainted-data-flow` issue in `tree`: one independent taint
    analysis pass per function (including nested/methods), each seeded
    per `_route_handler_seed`, with one-hop interprocedural resolution
    against every other function defined in `tree` (see `_Analyzer`).
    """
    functions = _collect_named_functions(tree)
    analyzer = _Analyzer(path, functions)
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            issues.extend(i for i in analyzer.run(node) if _in_scope(i.line, only_lines))
    return issues
