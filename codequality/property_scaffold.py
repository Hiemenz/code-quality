"""Property-based testing: a detection signal plus a scaffold generator.

LLMs are known to write narrow, example-based tests that only exercise the
happy path they were already thinking about. Property-based testing
(Hypothesis, for Python) generates randomized/edge-case inputs against an
invariant you define, which catches exactly the class of bug an LLM's own
test suite is least likely to have thought to check.

Actually *writing* a meaningful property (the invariant/assertion) needs
semantic understanding of what the function is supposed to guarantee --
that's not something this deterministic tool can do without either
guessing or delegating to an LLM, which would defeat the point. So this
stays honest about scope: it (a) detects whether property-based testing is
used at all, as a report signal, and (b) generates *stub* tests -- input
generation wired up from type hints, with the actual assertion left as a
TODO for a human (or a supervised LLM) to fill in.
"""

import ast
import os

_STRATEGY_BY_ANNOTATION = {
    "int": "st.integers()",
    "float": "st.floats(allow_nan=False, allow_infinity=False)",
    "str": "st.text()",
    "bool": "st.booleans()",
    "bytes": "st.binary()",
    "list": "st.lists(st.integers())",
    "dict": "st.dictionaries(st.text(), st.integers())",
}

def _annotation_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _annotation_name(node.value)
    return None


def _strategy_for(annotation):
    """Returns (strategy_source, supported). `supported` is False when we
    fell back to a placeholder -- callers must put the "fill this in"
    comment on its own line, not inline in a multi-arg call, or a `#`
    would silently comment out the rest of the call.
    """
    name = _annotation_name(annotation) if annotation is not None else None
    strategy = _STRATEGY_BY_ANNOTATION.get(name)
    if strategy is None:
        return "st.nothing()", False
    return strategy, True


def _is_test_file(rel_path):
    base = os.path.basename(rel_path)
    return base.startswith("test_") or base.endswith("_test.py")


def _uses_hypothesis_given(node):
    for dec in node.decorator_list:
        target = dec.func if isinstance(dec, ast.Call) else dec
        if _annotation_name(target) == "given":
            return True
    return False


def scan_existing_property_tests(root, python_files):
    """dict[rel_path] -> count of @given-decorated test functions, for
    files that look like test files.
    """
    found = {}
    for rel_path in python_files:
        if not _is_test_file(rel_path):
            continue
        try:
            with open(os.path.join(root, rel_path), "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=rel_path)
        except (OSError, SyntaxError):
            continue
        count = sum(
            1 for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _uses_hypothesis_given(node)
        )
        if count:
            found[rel_path] = count
    return found


def _module_path_guess(rel_path):
    without_ext = rel_path[:-3] if rel_path.endswith(".py") else rel_path
    return without_ext.replace(os.sep, ".").replace("/", ".")


def _is_candidate(node):
    if node.name.startswith("_"):
        return False
    args = node.args
    if args.vararg or args.kwarg or not args.args:
        return False
    return not any(a.arg in ("self", "cls") for a in args.args)


def _stub_for(rel_path, node):
    module = _module_path_guess(rel_path)
    params = [(a.arg,) + _strategy_for(a.annotation) for a in node.args.args]
    # The "TODO"s below are generated-file content for the human editing the
    # stub, not real TODOs in codequality's own source.
    unsupported_note = "  # TODO: unsupported type -- write a real strategy"  # codequality: ignore[todo-marker]
    given_lines = "\n".join(
        f"    {name}={strategy}," + ("" if supported else unsupported_note)
        for name, strategy, supported in params
    )
    param_names = ", ".join(name for name, _, _ in params)
    # Two functions with the same name in different modules are common
    # (e.g. every analyzer has an `analyze`) -- qualify both the test name
    # and the import alias with the module, or (a) later stubs would
    # silently overwrite earlier ones with the same def name, and (b)
    # worse, every `from X import analyze` would bind the same bare name
    # in this one flat file, so all but the last-imported version would
    # silently call the wrong function.
    qualifier = module.replace(".", "_")
    test_name = f"test_{qualifier}_{node.name}_property"
    call_name = f"{qualifier}_{node.name}"
    import_line = f"from {module} import {node.name} as {call_name}"
    import_note = "  # TODO: verify this import"  # codequality: ignore[todo-marker]
    # Again, the embedded "TODO"s are for the generated file's reader, not us.
    return (
        f"{import_line}{import_note}\n\n\n"
        f"@given(\n{given_lines}\n)\n"
        f"def {test_name}({param_names}):\n"
        f"    # TODO: write the invariant that should hold for *all* inputs\n"  # codequality: ignore[todo-marker]
        f"    # e.g. idempotence, a round-trip, or a relationship to a simpler reference.\n"
        f"    {call_name}({param_names})\n"
    )


def find_candidates(root, python_files, limit=None):
    """List of (rel_path, node) for public top-level functions in
    non-test files that look like plausible property-test candidates:
    simple positional parameters, no *args/**kwargs, not a method.
    """
    candidates = []
    for rel_path in python_files:
        if _is_test_file(rel_path):
            continue
        try:
            with open(os.path.join(root, rel_path), "r", encoding="utf-8") as f:
                tree = ast.parse(f.read(), filename=rel_path)
        except (OSError, SyntaxError):
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and _is_candidate(node):
                candidates.append((rel_path, node))
    if limit is not None:
        candidates = candidates[:limit]
    return candidates


def render_stub_file(candidates):
    """Render `candidates` (as returned by `find_candidates`) as a complete,
    syntactically valid Python module of Hypothesis test stubs.
    """
    header = (
        '"""Property-test stubs generated by `codequality scaffold-properties`.\n\n'
        "These only wire up input generation from type hints -- fill in the\n"
        "actual invariant/assertion for each one, or delete stubs that don't\n"
        "apply. Imports are a best-effort guess and may need fixing.\n"
        '"""\n\n'
        "from hypothesis import given\n"
        "import hypothesis.strategies as st\n\n"
    )
    body = "\n\n".join(_stub_for(rel_path, node) for rel_path, node in candidates)
    return header + "\n" + body + "\n" if candidates else header
