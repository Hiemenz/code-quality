"""Public API signature-change detection: compares the old and new version
of each changed Python function/method to catch a specific "fix one thing,
silently break the API" failure mode. Every other check in this tool only
ever looks at one version of the code at a time; this is the one place
`codequality` compares two versions of the same function against each
other, which is only possible in `diff` mode (there's no "old version" in
a plain `scan`).

Deliberately limited to top-level functions and methods one level inside
a top-level class -- that's what "public API" means for most codebases,
and going deeper (nested functions, nested classes) would mostly just add
noise from implementation details nothing external calls anyway.
"""

import ast

from codequality.analyzers.base import Issue

_FUNC_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _public_methods(class_node):
    return {
        f"{class_node.name}.{child.name}": child
        for child in class_node.body
        if isinstance(child, _FUNC_TYPES) and not child.name.startswith("_")
    }


def qualified_functions(tree):
    """dict[qualified_name] -> FunctionDef/AsyncFunctionDef, for public
    top-level functions and public methods of public top-level classes.
    """
    functions = {}
    for node in tree.body:
        if isinstance(node, _FUNC_TYPES) and not node.name.startswith("_"):
            functions[node.name] = node
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            functions.update(_public_methods(node))
    return functions


def _names(args):
    return [a.arg for a in args if a.arg not in ("self", "cls")]


def signature(node):
    """A comparable summary of a function's parameter contract."""
    args = node.args
    positional = _names(list(args.posonlyargs) + list(args.args))
    n_defaults = len(args.defaults)
    required_positional = positional[: len(positional) - n_defaults] if n_defaults <= len(positional) else []
    keyword_only = _names(args.kwonlyargs)
    required_keyword_only = {
        a.arg for a, default in zip(args.kwonlyargs, args.kw_defaults)
        if default is None and a.arg not in ("self", "cls")
    }
    return {
        "positional": positional,
        "required_positional": set(required_positional),
        "keyword_only": set(keyword_only),
        "required_keyword_only": required_keyword_only,
    }


def _issue(path, node, name, detail):
    return Issue(path, node.lineno, "correctness", "error", "breaking-signature-change",
                 f"Public '{name}' {detail}")


def _removed_params(name, old_sig, new_sig, new_node, path):
    old_all = set(old_sig["positional"]) | old_sig["required_keyword_only"] | old_sig["keyword_only"]
    new_all = set(new_sig["positional"]) | new_sig["keyword_only"]
    removed = old_all - new_all
    if not removed:
        return None
    return _issue(path, new_node, name,
                  f"removed parameter(s) {sorted(removed)} -- existing callers passing them will break")


def _new_required_params(name, old_sig, new_sig, new_node, path):
    old_all = set(old_sig["positional"]) | old_sig["required_keyword_only"] | old_sig["keyword_only"]
    new_required = new_sig["required_positional"] | new_sig["required_keyword_only"]
    added = new_required - old_all
    if not added:
        return None
    return _issue(path, new_node, name,
                  f"added required parameter(s) {sorted(added)} -- existing callers omitting them will break")


def _reordered_params(name, old_sig, new_sig, new_node, path):
    old_common = [n for n in old_sig["positional"] if n in new_sig["positional"]]
    new_common = [n for n in new_sig["positional"] if n in old_sig["positional"]]
    if old_common == new_common:
        return None
    return _issue(path, new_node, name,
                  f"reordered positional parameters ({old_common} -> {new_common}) -- breaks positional callers")


_CHECKS = (_removed_params, _new_required_params, _reordered_params)


def _compare(name, old_node, new_node, path):
    old_sig, new_sig = signature(old_node), signature(new_node)
    issues = (check(name, old_sig, new_sig, new_node, path) for check in _CHECKS)
    return [issue for issue in issues if issue is not None]


def signature_diff_issues(old_source, new_source, path):
    """old_source may be None (a newly-added file has nothing to compare
    against). Returns [] on any parse failure -- this is a bonus signal,
    not core analysis, so it fails quiet rather than blocking the scan.
    """
    if old_source is None:
        return []
    try:
        old_tree = ast.parse(old_source, filename=path)
        new_tree = ast.parse(new_source, filename=path)
    except SyntaxError:
        return []

    old_functions = qualified_functions(old_tree)
    new_functions = qualified_functions(new_tree)
    issues = []
    for name, new_node in new_functions.items():
        old_node = old_functions.get(name)
        if old_node is not None:
            issues.extend(_compare(name, old_node, new_node, path))
    return issues
