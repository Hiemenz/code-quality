"""Best-effort intraprocedural taint tracking for SQL execute calls.

Deliberately narrow in two ways:

- **Intraprocedural only** -- tracking is per function body; a tainted
  value returned from a helper function, or passed into one, is not
  followed. No interprocedural call graph is built.
- **One sink family** -- SQL execute methods (`_SQL_EXEC_METHODS`) are the
  only sink checked here, on purpose. `python_security.py`'s existing
  checks for eval/exec/os.system/subprocess shell=True/yaml.load already
  fire *unconditionally* on any call to those (regardless of where the
  argument came from), so tracking taint into them would only ever
  produce a second, redundant issue on a line `python_security.py` has
  already flagged. `python_security._sql_injection_issue` is the one
  check that only looks at the argument *expression* (f-string/%/+/
  .format() built inline at the call site) -- a query string assembled a
  few lines earlier and passed in as a bare variable
  (`query = f"...{x}"`; ...; `cursor.execute(query)`) slips past it
  entirely. That gap is what this module fills.

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
)
_SOURCE_ATTR_NAMES = (
    "request.json", "request.args", "request.form", "request.values", "request.GET", "request.POST",
)
_SOURCE_SUBSCRIPT_BASES = ("sys.argv", "os.environ")


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


def _target_names(target):
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        names = []
        for elt in target.elts:
            names.extend(_target_names(elt))
        return names
    return []  # Attribute/Subscript targets aren't tracked


def _rhs_provenance(rhs, tainted):
    prov = _source_provenance(rhs)
    if prov is not None:
        return prov
    if isinstance(rhs, ast.Name):
        return tainted.get(rhs.id)
    first_tainted = next((n.id for n in ast.walk(rhs) if isinstance(n, ast.Name) and n.id in tainted), None)
    return tainted.get(first_tainted) if first_tainted else None


def _apply_assign(targets, rhs, tainted):
    prov = _rhs_provenance(rhs, tainted)
    names = [n for t in targets for n in _target_names(t)]
    for name in names:
        if prov is not None:
            tainted[name] = prov
        else:
            tainted.pop(name, None)


def _apply_augassign(stmt, tainted):
    if not isinstance(stmt.target, ast.Name):
        return
    name = stmt.target.id
    prov = _rhs_provenance(stmt.value, tainted) or tainted.get(name)
    if prov is not None:
        tainted[name] = prov


def _sink_issue(node, path, tainted):
    """If Call `node` is a `cursor.execute(...)`-style call whose sole
    argument is a currently-tainted bare variable, return the Issue.
    Mirrors `python_security._sql_injection_issue`'s "exactly one arg, no
    separate params" shape -- the parameterized form is still not flagged.
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
        path, node.lineno, "security", "error", "tainted-data-flow",
        f"'{arg.id}' (from {tainted[arg.id]}, via one or more intermediate variables) is passed to "
        f".{method}() -- vulnerable to SQL injection"
    )


def _scan_sinks(expr, tainted, issues, path):
    for node in ast.walk(expr):
        if isinstance(node, ast.Call):
            issue = _sink_issue(node, path, tainted)
            if issue is not None:
                issues.append(issue)


def _merge_branches(tainted, *branch_states):
    tainted.clear()
    for state in branch_states:
        for name, prov in state.items():
            tainted.setdefault(name, prov)


def _walk_stmts(stmts, tainted, issues, path):
    for stmt in stmts:
        _walk_stmt(stmt, tainted, issues, path)


def _walk_stmt(stmt, tainted, issues, path):
    if isinstance(stmt, ast.If):
        _scan_sinks(stmt.test, tainted, issues, path)
        then_state, else_state = dict(tainted), dict(tainted)
        _walk_stmts(stmt.body, then_state, issues, path)
        _walk_stmts(stmt.orelse, else_state, issues, path)
        _merge_branches(tainted, then_state, else_state)
    elif isinstance(stmt, (ast.For, ast.AsyncFor)):
        _scan_sinks(stmt.iter, tainted, issues, path)
        pre_state, body_state = dict(tainted), dict(tainted)
        _walk_stmts(stmt.body, body_state, issues, path)
        _walk_stmts(stmt.orelse, body_state, issues, path)
        _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
    elif isinstance(stmt, ast.While):
        _scan_sinks(stmt.test, tainted, issues, path)
        pre_state, body_state = dict(tainted), dict(tainted)
        _walk_stmts(stmt.body, body_state, issues, path)
        _walk_stmts(stmt.orelse, body_state, issues, path)
        _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
    elif isinstance(stmt, (ast.With, ast.AsyncWith)):
        for item in stmt.items:
            _scan_sinks(item.context_expr, tainted, issues, path)
        _walk_stmts(stmt.body, tainted, issues, path)
    elif isinstance(stmt, ast.Try):
        pre_state = dict(tainted)
        try_state = dict(pre_state)
        _walk_stmts(stmt.body, try_state, issues, path)
        # Body may have failed partway through, or a handler may have run
        # instead of the body completing -- union try/pre/every handler's
        # outcome rather than picking one, same over-approximation as If.
        branch_states = [try_state, pre_state]
        for handler in stmt.handlers:
            handler_state = dict(pre_state)
            _walk_stmts(handler.body, handler_state, issues, path)
            branch_states.append(handler_state)
        _merge_branches(tainted, *branch_states)
        _walk_stmts(stmt.orelse, tainted, issues, path)
        _walk_stmts(stmt.finalbody, tainted, issues, path)
    elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        pass  # nested scope: analyzed independently by taint_issues' own ast.walk
    elif isinstance(stmt, ast.Assign):
        _scan_sinks(stmt.value, tainted, issues, path)
        _apply_assign(stmt.targets, stmt.value, tainted)
    elif isinstance(stmt, ast.AugAssign):
        _scan_sinks(stmt.value, tainted, issues, path)
        _apply_augassign(stmt, tainted)
    else:
        _scan_sinks(stmt, tainted, issues, path)


def _in_scope(lineno, only_lines):
    return only_lines is None or lineno in only_lines


def taint_issues(tree, path, only_lines):
    """Every `tainted-data-flow` issue in `tree`: one independent taint
    analysis pass per function (including nested/methods), each starting
    with no tainted names.
    """
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            local_issues = []
            _walk_stmts(node.body, {}, local_issues, path)
            issues.extend(i for i in local_issues if _in_scope(i.line, only_lines))
    return issues
