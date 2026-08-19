"""Best-effort intraprocedural taint tracking for JS/TS SQL sinks, via
tree-sitter. Ported from `taint_flow.py`'s design (same statement-walk
structure, same branch-union approach) onto tree-sitter nodes instead of
Python's `ast`. See that module's docstring for the underlying rationale
(why SQL only, why over-approximate on branches).

**Intraprocedural only for this v1** -- no interprocedural hop like
`taint_flow.py` grew in its second revision. That's a deliberate, cheap
follow-up once this proves out, not a design limitation baked in.

**No route-handler-parameter seeding**, unlike the Python version: Express/
Koa path/query/body data always arrives via `req.query`/`req.params`/
`req.body` member access, never as bare handler function parameters the
way FastAPI/Flask do, so source detection via member-expression matching
already covers it -- there's no JS equivalent of Python's
`_route_handler_seed` to build.
"""

from codequality.analyzers._ts_helpers import iter_kind as _iter_kind
from codequality.analyzers._ts_helpers import node_line as _line
from codequality.analyzers._ts_helpers import node_text as _text
from codequality.analyzers.base import Issue

_SQL_EXEC_METHODS = {"execute", "executemany", "raw", "query"}

# Member-expression prefixes treated as taint sources -- `req` is a
# heuristic common name for the Express/Koa request object (best-effort,
# same posture as the Python tracker's `request.*` matching).
_SOURCE_MEMBER_PREFIXES = ("req.query", "req.params", "req.body", "req.headers", "req.cookies", "process.env")
_SOURCE_SUBSCRIPT_BASES = ("process.argv",)


def _in_scope(lineno, only_lines):
    return only_lines is None or lineno in only_lines


def _dotted_name(node, source_bytes):
    kind = node.type
    if kind == "identifier":
        return _text(node, source_bytes)
    if kind == "member_expression":
        obj = node.child_by_field_name("object")
        prop = node.child_by_field_name("property")
        if obj is None or prop is None:
            return None
        base = _dotted_name(obj, source_bytes)
        return f"{base}.{_text(prop, source_bytes)}" if base is not None else None
    return None


def _source_provenance(node, source_bytes):
    """Short description of `node` if it's a known untrusted-input
    expression, else None. JS sources are (almost) all plain member
    access, not calls -- `req.query.id`, not `req.query.get('id')`.
    """
    kind = node.type
    if kind == "member_expression":
        dotted = _dotted_name(node, source_bytes)
        if dotted is not None and any(dotted == p or dotted.startswith(p + ".") for p in _SOURCE_MEMBER_PREFIXES):
            return dotted
        return None
    if kind == "subscript_expression":
        obj = node.child_by_field_name("object")
        base = _dotted_name(obj, source_bytes) if obj is not None else None
        if base is not None and base in _SOURCE_SUBSCRIPT_BASES:
            return f"{base}[...]"
    return None


def _is_tainted_expr(node, tainted, source_bytes):
    if node.type == "identifier" and _text(node, source_bytes) in tainted:
        return True
    for i in range(node.named_child_count):
        if _is_tainted_expr(node.named_child(i), tainted, source_bytes):
            return True
    return False


def _first_tainted_name(node, tainted, source_bytes):
    if node.type == "identifier":
        name = _text(node, source_bytes)
        if name in tainted:
            return name
        return None
    for i in range(node.named_child_count):
        found = _first_tainted_name(node.named_child(i), tainted, source_bytes)
        if found is not None:
            return found
    return None


def _rhs_provenance(rhs, tainted, source_bytes):
    prov = _source_provenance(rhs, source_bytes)
    if prov is not None:
        return prov
    if rhs.type == "identifier":
        return tainted.get(_text(rhs, source_bytes))
    name = _first_tainted_name(rhs, tainted, source_bytes)
    return tainted.get(name) if name is not None else None


def _declarator_target_names(declarator, source_bytes):
    """Name(s) bound by one `variable_declarator` -- just the plain-
    identifier case; destructuring patterns aren't tracked (best-effort,
    same posture as `taint_flow.py`'s `_target_names`).
    """
    name_node = declarator.child_by_field_name("name")
    if name_node is not None and name_node.type == "identifier":
        return [_text(name_node, source_bytes)]
    return []


def _apply_binding(names, prov, tainted):
    for name in names:
        if prov is not None:
            tainted[name] = prov
        else:
            tainted.pop(name, None)


def _sink_issue(node, path, tainted, source_bytes):
    """`<obj>.execute(...)`/`.query(...)`/`.raw(...)` called with a single,
    currently-tainted bare identifier argument.
    """
    func = node.child_by_field_name("function")
    if func is None or func.type != "member_expression":
        return None
    method = func.child_by_field_name("property")
    if method is None or _text(method, source_bytes) not in _SQL_EXEC_METHODS:
        return None
    args_node = node.child_by_field_name("arguments")
    if args_node is None or args_node.named_child_count != 1:
        return None
    arg = args_node.named_child(0)
    if arg.type != "identifier":
        return None
    name = _text(arg, source_bytes)
    if name not in tainted:
        return None
    return Issue(
        path, _line(node), "security", "error", "tainted-data-flow",
        f"'{name}' (from {tainted[name]}, via one or more intermediate variables) is passed to "
        f".{_text(method, source_bytes)}() -- vulnerable to SQL injection"
    )


def _scan_sinks(expr, tainted, issues, path, source_bytes):
    for node in _iter_kind(expr, "call_expression"):
        issue = _sink_issue(node, path, tainted, source_bytes)
        if issue is not None:
            issues.append(issue)


def _merge_branches(tainted, *branch_states):
    tainted.clear()
    for state in branch_states:
        for name, prov in state.items():
            tainted.setdefault(name, prov)


_DECLARATION_KINDS = {"lexical_declaration", "variable_declaration"}
_LOOP_KINDS = {"for_statement", "for_in_statement", "while_statement", "do_statement"}
_FUNCTION_KINDS = {"function_declaration", "function_expression", "arrow_function", "method_definition",
                   "class_declaration", "class"}


def _walk_stmts(stmts, tainted, issues, path, source_bytes):
    for stmt in stmts:
        _walk_stmt(stmt, tainted, issues, path, source_bytes)


def _statement_block_children(node):
    """Named children of a `statement_block`/`program` -- the top-level
    body a function or an if/for/while/try's braces wrap.
    """
    return [node.named_child(i) for i in range(node.named_child_count)]


def _walk_declaration(stmt, tainted, issues, path, source_bytes):
    for i in range(stmt.named_child_count):
        declarator = stmt.named_child(i)
        if declarator.type != "variable_declarator":
            continue
        value = declarator.child_by_field_name("value")
        names = _declarator_target_names(declarator, source_bytes)
        if value is None:
            _apply_binding(names, None, tainted)
            continue
        _scan_sinks(value, tainted, issues, path, source_bytes)
        prov = _rhs_provenance(value, tainted, source_bytes)
        _apply_binding(names, prov, tainted)


def _walk_assignment(expr, tainted, issues, path, source_bytes):
    left = expr.child_by_field_name("left")
    right = expr.child_by_field_name("right")
    if right is not None:
        _scan_sinks(right, tainted, issues, path, source_bytes)
    if left is None or right is None or left.type != "identifier":
        return
    prov = _rhs_provenance(right, tainted, source_bytes)
    _apply_binding([_text(left, source_bytes)], prov, tainted)


def _walk_stmt(stmt, tainted, issues, path, source_bytes):
    kind = stmt.type

    if kind in _DECLARATION_KINDS:
        _walk_declaration(stmt, tainted, issues, path, source_bytes)
        return

    if kind == "expression_statement":
        expr = stmt.named_child(0) if stmt.named_child_count else None
        if expr is not None and expr.type == "assignment_expression":
            _walk_assignment(expr, tainted, issues, path, source_bytes)
        elif expr is not None:
            _scan_sinks(expr, tainted, issues, path, source_bytes)
        return

    if kind == "if_statement":
        cond = stmt.child_by_field_name("condition")
        if cond is not None:
            _scan_sinks(cond, tainted, issues, path, source_bytes)
        then_state, else_state = dict(tainted), dict(tainted)
        consequence = stmt.child_by_field_name("consequence")
        if consequence is not None:
            _walk_stmts(_statement_block_children(consequence), then_state, issues, path, source_bytes)
        alternative = stmt.child_by_field_name("alternative")
        if alternative is not None:
            # `else_clause` wraps either a statement_block or another if_statement
            body = alternative.named_child(0) if alternative.named_child_count else None
            if body is not None:
                if body.type == "statement_block":
                    _walk_stmts(_statement_block_children(body), else_state, issues, path, source_bytes)
                else:
                    _walk_stmt(body, else_state, issues, path, source_bytes)
        _merge_branches(tainted, then_state, else_state)
        return

    if kind in _LOOP_KINDS:
        initializer = stmt.child_by_field_name("initializer")  # classic for(;;) only
        if initializer is not None:
            _walk_stmt(initializer, tainted, issues, path, source_bytes)
        for field in ("condition", "update", "right"):  # "right" is for-in/for-of's iterable
            part = stmt.child_by_field_name(field)
            if part is not None:
                _scan_sinks(part, tainted, issues, path, source_bytes)
        pre_state, body_state = dict(tainted), dict(tainted)
        body = stmt.child_by_field_name("body")
        if body is not None:
            children = _statement_block_children(body) if body.type == "statement_block" else [body]
            _walk_stmts(children, body_state, issues, path, source_bytes)
        _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
        return

    if kind == "try_statement":
        pre_state = dict(tainted)
        try_state = dict(pre_state)
        body = stmt.child_by_field_name("body")
        if body is not None:
            _walk_stmts(_statement_block_children(body), try_state, issues, path, source_bytes)
        branch_states = [try_state, pre_state]
        handler = stmt.child_by_field_name("handler")
        if handler is not None:
            handler_state = dict(pre_state)
            handler_body = handler.child_by_field_name("body")
            if handler_body is not None:
                _walk_stmts(_statement_block_children(handler_body), handler_state, issues, path, source_bytes)
            branch_states.append(handler_state)
        _merge_branches(tainted, *branch_states)
        finalizer = stmt.child_by_field_name("finalizer")  # finally_clause wrapping its own "body" field
        finalizer_body = finalizer.child_by_field_name("body") if finalizer is not None else None
        if finalizer_body is not None:
            _walk_stmts(_statement_block_children(finalizer_body), tainted, issues, path, source_bytes)
        return

    if kind in _FUNCTION_KINDS:
        return  # nested scope: analyzed independently by taint_issues' own sweep

    if kind == "return_statement":
        value = stmt.named_child(0) if stmt.named_child_count else None
        if value is not None:
            _scan_sinks(value, tainted, issues, path, source_bytes)
        return

    _scan_sinks(stmt, tainted, issues, path, source_bytes)


def _function_body_stmts(fn_node):
    body = fn_node.child_by_field_name("body")
    if body is None:
        return []
    if body.type == "statement_block":
        return _statement_block_children(body)
    return [body]  # arrow function with an expression body, e.g. `x => x.query(y)`


def taint_issues(root, path, source, only_lines):
    """Every `tainted-data-flow` issue in `root`: one independent taint
    analysis pass per function/method/arrow-function, each starting with
    no tainted names.
    """
    source_bytes = source.encode("utf-8", errors="replace")
    issues = []
    for kind in ("function_declaration", "function_expression", "arrow_function", "method_definition"):
        for fn in _iter_kind(root, kind):
            _analyze_one(fn, path, source_bytes, issues)
    return [i for i in issues if _in_scope(i.line, only_lines)]


def _analyze_one(fn, path, source_bytes, issues):
    local_issues = []
    _walk_stmts(_function_body_stmts(fn), {}, local_issues, path, source_bytes)
    issues.extend(local_issues)
