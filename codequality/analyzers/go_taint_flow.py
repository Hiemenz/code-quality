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


def _text(node, source):
    """`node`'s source text, going through the UTF-8-encoded bytes since
    tree-sitter node offsets are *byte* offsets, not str indices (see
    treesitter_analyzer._node_text for the same fix and why it matters).
    """
    encoded = source.encode("utf-8", errors="replace")
    return encoded[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _line(node):
    return node.start_point.row + 1


def _in_scope(lineno, only_lines):
    return only_lines is None or lineno in only_lines


def _selector_text(node, source):
    return _text(node, source) if node is not None and node.type == "selector_expression" else None


def _source_provenance(node, source):
    """Short description of `node` if it's a known untrusted-input
    expression, else None.
    """
    if node.type == "call_expression":
        func = node.child_by_field_name("function")
        text = _selector_text(func, source)
        if text is None:
            return None
        if text in _SOURCE_CALL_EXACT or any(text.endswith(suf) for suf in _SOURCE_CALL_SUFFIXES):
            return text
        return None
    if node.type == "index_expression":
        operand = node.child_by_field_name("operand")
        text = _selector_text(operand, source)
        if text is not None and text in _SOURCE_SUBSCRIPT_BASES:
            return f"{text}[...]"
        return None
    return None


def _is_tainted_expr(node, tainted, source):
    if node.type == "identifier" and _text(node, source) in tainted:
        return True
    for i in range(node.named_child_count):
        if _is_tainted_expr(node.named_child(i), tainted, source):
            return True
    return False


def _first_tainted_name(node, tainted, source):
    if node.type == "identifier":
        name = _text(node, source)
        return name if name in tainted else None
    for i in range(node.named_child_count):
        found = _first_tainted_name(node.named_child(i), tainted, source)
        if found is not None:
            return found
    return None


def _rhs_provenance(rhs, tainted, source):
    prov = _source_provenance(rhs, source)
    if prov is not None:
        return prov
    if rhs.type == "identifier":
        return tainted.get(_text(rhs, source))
    name = _first_tainted_name(rhs, tainted, source)
    return tainted.get(name) if name is not None else None


def _apply_binding(names, prov, tainted):
    for name in names:
        if name == "_":
            continue
        if prov is not None:
            tainted[name] = prov
        else:
            tainted.pop(name, None)


def _sink_issue(node, path, tainted, source):
    """`<db/tx>.Query/Exec/...(...)` called with a currently-tainted bare
    identifier as its query-text argument (index 0, or 1 for the
    `...Context` variants -- see go_security._query_arg_index).
    """
    func = node.child_by_field_name("function")
    text = _selector_text(func, source)
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
    name = _text(arg, source)
    if name not in tainted:
        return None
    return Issue(
        path, _line(node), "security", "error", "tainted-data-flow",
        f"'{name}' (from {tainted[name]}, via one or more intermediate variables) is passed to "
        f".{method}() -- vulnerable to SQL injection"
    )


def _scan_sinks(expr, tainted, issues, path, source):
    for node in _iter_kind(expr, "call_expression"):
        issue = _sink_issue(node, path, tainted, source)
        if issue is not None:
            issues.append(issue)


def _iter_kind(node, kind):
    if node.type == kind:
        yield node
    for i in range(node.named_child_count):
        yield from _iter_kind(node.named_child(i), kind)


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


def _walk_binding(stmt, tainted, issues, path, source):
    """`:=`/`=` -- both have `left`/`right` `expression_list`s. Go allows
    multi-target assignment (`a, b := f(), g()` or `a, b := f()` for a
    2-return call); when the counts line up 1:1 each target gets its own
    right-hand provenance, and when there's exactly one right-hand
    expression for multiple targets (a multi-return call) that one
    provenance is conservatively applied to every target rather than
    guessing which return value it corresponds to.
    """
    left = stmt.child_by_field_name("left")
    right = stmt.child_by_field_name("right")
    rights = _expr_list_items(right)
    for value in rights:
        _scan_sinks(value, tainted, issues, path, source)
    names = [_text(n, source) for n in _expr_list_items(left) if n.type == "identifier"]
    if not names:
        return
    if len(rights) == len(names):
        for name, value in zip(names, rights):
            _apply_binding([name], _rhs_provenance(value, tainted, source), tainted)
    elif len(rights) == 1:
        _apply_binding(names, _rhs_provenance(rights[0], tainted, source), tainted)


def _walk_stmt(stmt, tainted, issues, path, source):
    kind = stmt.type

    if kind in _BINDING_KINDS:
        _walk_binding(stmt, tainted, issues, path, source)
        return

    if kind == "if_statement":
        cond = stmt.child_by_field_name("condition")
        if cond is not None:
            _scan_sinks(cond, tainted, issues, path, source)
        then_state, else_state = dict(tainted), dict(tainted)
        consequence = stmt.child_by_field_name("consequence")
        for s in _block_stmts(consequence):
            _walk_stmt(s, then_state, issues, path, source)
        alternative = stmt.child_by_field_name("alternative")
        if alternative is not None:
            if alternative.type == "if_statement":
                _walk_stmt(alternative, else_state, issues, path, source)
            else:
                for s in _block_stmts(alternative):
                    _walk_stmt(s, else_state, issues, path, source)
        _merge_branches(tainted, then_state, else_state)
        return

    if kind in _LOOP_KINDS:
        clause = next((stmt.child(i) for i in range(stmt.child_count)
                        if stmt.child(i).type in ("for_clause", "range_clause")), None)
        if clause is not None and clause.type == "for_clause":
            initializer = clause.child_by_field_name("initializer")
            if initializer is not None:
                _walk_stmt(initializer, tainted, issues, path, source)
            for field in ("condition", "update"):
                part = clause.child_by_field_name(field)
                if part is not None:
                    _scan_sinks(part, tainted, issues, path, source)
        elif clause is not None and clause.type == "range_clause":
            iterable = clause.child_by_field_name("right")
            if iterable is not None:
                _scan_sinks(iterable, tainted, issues, path, source)
        pre_state, body_state = dict(tainted), dict(tainted)
        for s in _block_stmts(stmt.child_by_field_name("body")):
            _walk_stmt(s, body_state, issues, path, source)
        _merge_branches(tainted, body_state, pre_state)  # loop may run 0 or more times
        return

    if kind in _FUNCTION_KINDS:
        return  # nested scope: analyzed independently by taint_issues' own sweep

    if kind == "return_statement":
        for value in _expr_list_items(stmt.named_child(0) if stmt.named_child_count else None):
            _scan_sinks(value, tainted, issues, path, source)
        return

    _scan_sinks(stmt, tainted, issues, path, source)


def taint_issues(root, path, source, only_lines):
    """Every `tainted-data-flow` issue in `root`: one independent taint
    analysis pass per function/method/func-literal, each starting with no
    tainted names.
    """
    issues = []
    for kind in _FUNCTION_KINDS:
        for fn in _iter_kind(root, kind):
            local_issues = []
            tainted = {}
            for s in _block_stmts(fn.child_by_field_name("body")):
                _walk_stmt(s, tainted, local_issues, path, source)
            issues.extend(local_issues)
    return [i for i in issues if _in_scope(i.line, only_lines)]
