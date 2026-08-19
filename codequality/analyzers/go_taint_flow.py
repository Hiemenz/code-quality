"""Best-effort intraprocedural taint tracking for Go SQL sinks, via
tree-sitter. Ported from `js_taint_flow.py`'s design (same statement-walk
structure, same branch-union approach), adapted to Go's grammar. See
`taint_flow.py`'s docstring for the underlying rationale (why SQL only,
why over-approximate on branches).

**Intraprocedural only for this v1**, same scoping as `js_taint_flow.py`.

**Source detection differs from JS/Python in shape, not just names.**
JS/Python untrusted input arrives as *member access* (`req.query`,
`request.args`) or a bare parameter (FastAPI/Flask route params). Go's
`net/http`/`os` expose the equivalent data through *method/function
calls* instead: `r.FormValue("x")`, `r.Header.Get("x")`,
`r.URL.Query().Get("x")`, `os.Getenv("x")`. So where the JS tracker
pattern-matches a `member_expression`'s dotted name, this one
pattern-matches a `call_expression`'s function text -- see
`_source_provenance` below. `os.Args` remains a plain (subscripted)
selector, same shape as JS's `process.argv`.

**No branch-local state tracking through `switch`/`select`.** Unlike
`if`/`for`, Go's `switch`/`select` case bodies aren't walked for new
bindings (see `_walk_stmt`'s fallback branch) -- only scanned for sink
calls against whatever was already tainted going in. A source read and a
sink call in the *same* switch case would still need the source to have
happened before the switch to be caught. This is a narrower gap than it
sounds: the fallback (`_scan_sinks`) still finds a sink call anywhere
inside an unhandled statement's subtree, so a tainted variable already
bound before the switch is still tracked into it -- what's not tracked is
a *new* taint binding created inside a case, for use after the switch
ends.
"""

from codequality.analyzers._ts_helpers import iter_kind as _iter_kind
from codequality.analyzers._ts_helpers import node_line as _line
from codequality.analyzers._ts_helpers import node_text as _text
from codequality.analyzers.base import Issue

_SQL_EXEC_METHODS = {"Query", "QueryContext", "QueryRow", "QueryRowContext", "Exec", "ExecContext"}

# Suffixes of a call's flattened function text that mark its return value
# as untrusted input. Checked with a suffix match against the full
# selector-expression text (e.g. "r.URL.Query().Get"), not a parsed dotted
# chain -- Go source chains often thread through intermediate calls
# (`r.URL.Query()`), so raw text matching is simpler and just as
# reproducible as JS's member-expression walk.
_SOURCE_CALL_SUFFIXES = (".FormValue", ".PostFormValue", ".Header.Get", ".URL.Query().Get")
_SOURCE_CALL_EXACT = ("os.Getenv",)
_SOURCE_SUBSCRIPT_BASES = ("os.Args",)


def _in_scope(lineno, only_lines):
    return only_lines is None or lineno in only_lines


def _selector_text(node, source_bytes):
    return _text(node, source_bytes) if node is not None and node.type == "selector_expression" else None


def _source_provenance(node, source_bytes):
    """Short description of `node` if it's a known untrusted-input
    expression, else None.
    """
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        text = _selector_text(func, source_bytes)
        if text is None:
            return None
        if text in _SOURCE_CALL_EXACT or any(text.endswith(suf) for suf in _SOURCE_CALL_SUFFIXES):
            return text
        return None
    if node.type == "index_expression":
        operand = node.child_by_field_name("operand")
        text = _selector_text(operand, source_bytes)
        if text is not None and text in _SOURCE_SUBSCRIPT_BASES:
            return f"{text}[...]"
        return None
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
        return name if name in tainted else None
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


def _apply_binding(names, prov, tainted):
    for name in names:
        if name == "_":
            continue
        if prov is not None:
            tainted[name] = prov
        else:
            tainted.pop(name, None)


def _sink_issue(node, path, tainted, source_bytes):
    """`<db/tx>.Query/Exec/...(...)` called with a currently-tainted bare
    identifier as its query-text argument (index 0, or 1 for the
    `...Context` variants -- see go_security._query_arg_index).
    """
    func = node.child_by_field_name("function")
    text = _selector_text(func, source_bytes)
    if text is None:
        return None
    method = text.rsplit(".", 1)[-1]
    if method not in _SQL_EXEC_METHODS:
        return None
    args_node = node.child_by_field_name("arguments")
    idx = 1 if method.endswith("Context") else 0
    if args_node is None or args_node.named_child_count <= idx:
        return None
    arg = args_node.named_child(idx)
    if arg.type != "identifier":
        return None
    name = _text(arg, source_bytes)
    if name not in tainted:
        return None
    return Issue(
        path, _line(node), "security", "error", "tainted-data-flow",
        f"'{name}' (from {tainted[name]}, via one or more intermediate variables) is passed to "
        f".{method}() -- vulnerable to SQL injection"
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


def _block_stmts(node):
    """Named statements inside a `block` -- Go wraps them in an
    intermediate `statement_list` node (absent entirely for an empty
    block), unlike JS's `statement_block`, whose named children are the
    statements directly.
    """
    if node is None or node.type != "block" or node.named_child_count == 0:
        return []
    stmt_list = node.named_child(0)
    if stmt_list.type != "statement_list":
        return [stmt_list]
    return [stmt_list.named_child(i) for i in range(stmt_list.named_child_count)]


def _expr_list_items(node):
    if node is None:
        return []
    if node.type != "expression_list":
        return [node]
    return [node.named_child(i) for i in range(node.named_child_count)]


_BINDING_KINDS = {"short_var_declaration", "assignment_statement"}
_LOOP_KINDS = {"for_statement"}
_FUNCTION_KINDS = {"function_declaration", "method_declaration", "func_literal"}


def _walk_binding(stmt, tainted, issues, path, source_bytes):
    """`:=`/`=` -- both have `left`/`right` `expression_list`s. Go allows
    multi-target assignment (`a, b := f(), g()` or `a, b := f()` for a
    2-return call); when the counts line up 1:1 each target gets its own
    right-hand provenance positionally, and when there's exactly one right-
    hand expression for multiple targets (a multi-return call) that one
    provenance is conservatively applied to every identifier target rather
    than guessing which return value it corresponds to.

    Non-identifier LHS targets (index expressions, selector expressions like
    `x[0]` or `s.Field`) are skipped for binding purposes but do NOT shift
    the positional pairing with the RHS list -- the comparison is against the
    full LHS item count, not just the identifier subset.
    """
    left = stmt.child_by_field_name("left")
    right = stmt.child_by_field_name("right")
    lefts = _expr_list_items(left)
    rights = _expr_list_items(right)
    for value in rights:
        _scan_sinks(value, tainted, issues, path, source_bytes)
    if not lefts:
        return
    if len(rights) == len(lefts):
        # 1:1 positional assignment -- pair each LHS node with its RHS value.
        for left_node, value in zip(lefts, rights):
            if left_node.type == "identifier":
                name = _text(left_node, source_bytes)
                _apply_binding([name], _rhs_provenance(value, tainted, source_bytes), tainted)
    elif len(rights) == 1:
        # Multi-return call: one RHS maps to all LHS targets.
        prov = _rhs_provenance(rights[0], tainted, source_bytes)
        for left_node in lefts:
            if left_node.type == "identifier":
                name = _text(left_node, source_bytes)
                _apply_binding([name], prov, tainted)


def _walk_stmt(stmt, tainted, issues, path, source_bytes):
    kind = stmt.type

    if kind in _BINDING_KINDS:
        _walk_binding(stmt, tainted, issues, path, source_bytes)
        return

    if kind == "if_statement":
        cond = stmt.child_by_field_name("condition")
        if cond is not None:
            _scan_sinks(cond, tainted, issues, path, source_bytes)
        then_state, else_state = dict(tainted), dict(tainted)
        consequence = stmt.child_by_field_name("consequence")
        for s in _block_stmts(consequence):
            _walk_stmt(s, then_state, issues, path, source_bytes)
        alternative = stmt.child_by_field_name("alternative")
        if alternative is not None:
            if alternative.type == "if_statement":
                _walk_stmt(alternative, else_state, issues, path, source_bytes)
            else:
                for s in _block_stmts(alternative):
                    _walk_stmt(s, else_state, issues, path, source_bytes)
        _merge_branches(tainted, then_state, else_state)
        return

    if kind in _LOOP_KINDS:
        clause = next((stmt.child(i) for i in range(stmt.child_count)
                        if stmt.child(i).type in ("for_clause", "range_clause")), None)
        if clause is not None and clause.type == "for_clause":
            initializer = clause.child_by_field_name("initializer")
            if initializer is not None:
                _walk_stmt(initializer, tainted, issues, path, source_bytes)
            for field in ("condition", "update"):
                part = clause.child_by_field_name(field)
                if part is not None:
                    _scan_sinks(part, tainted, issues, path, source_bytes)
        elif clause is not None and clause.type == "range_clause":
            iterable = clause.child_by_field_name("right")
            if iterable is not None:
                _scan_sinks(iterable, tainted, issues, path, source_bytes)
        pre_state, body_state = dict(tainted), dict(tainted)
        for s in _block_stmts(stmt.child_by_field_name("body")):
            _walk_stmt(s, body_state, issues, path, source_bytes)
        _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
        return

    if kind in _FUNCTION_KINDS:
        return  # nested scope: analyzed independently by taint_issues' own sweep

    if kind == "return_statement":
        for value in _expr_list_items(stmt.named_child(0) if stmt.named_child_count else None):
            _scan_sinks(value, tainted, issues, path, source_bytes)
        return

    _scan_sinks(stmt, tainted, issues, path, source_bytes)


def taint_issues(root, path, source, only_lines):
    """Every `tainted-data-flow` issue in `root`: one independent taint
    analysis pass per function/method/func-literal, each starting with no
    tainted names.
    """
    source_bytes = source.encode("utf-8", errors="replace")
    issues = []
    for kind in _FUNCTION_KINDS:
        for fn in _iter_kind(root, kind):
            local_issues = []
            tainted = {}
            for s in _block_stmts(fn.child_by_field_name("body")):
                _walk_stmt(s, tainted, local_issues, path, source_bytes)
            issues.extend(local_issues)
    return [i for i in issues if _in_scope(i.line, only_lines)]
