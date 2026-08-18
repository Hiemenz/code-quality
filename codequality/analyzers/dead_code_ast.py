"""AST-based cross-file dead-code detection.

Improves on the regex-based `dead_code.py` in two ways:

1. References are collected from actual AST nodes (Name loads and Attribute
   accesses) rather than whole-word text search.  String literals, comments,
   and docstrings that happen to contain a function's name are not counted as
   references.

2. Extends detection to public class methods, not just top-level
   functions/classes.  A method is flagged when its name never appears as an
   attribute access (`obj.method`, `Cls.method`) anywhere in the scanned
   Python files.

The same "heuristic signal, not a hard gate" philosophy applies: everything is
reported at `info` severity.  False negatives are possible for names reached
only through `getattr`/`__getattr__`, framework dispatch by convention, or
dynamic attribute construction -- the same caveats as the regex scanner.

Exemptions (identical to `dead_code.py` for top-level, extended for methods):
- private names (leading `_`) and dunders
- test-framework hooks (setUp/tearDown/…) and `test_`-prefixed names
- pytest/unittest class-discovery prefixes (`Test*`)
- `main` (conventional script entry point)
- names listed in a module's `__all__`
- decorated definitions (framework dispatch via decorator is invisible to
  static analysis)
- HTTP-verb method names (`get`, `post`, `put`, `patch`, `delete`, `head`,
  `options`) — these are dispatched by web frameworks, never by direct
  call in application code
"""

import ast

from codequality.analyzers.base import Issue

_TEST_HOOKS = frozenset({
    "setUp", "tearDown", "setUpClass", "tearDownClass",
    "setUpModule", "tearDownModule",
})

_HTTP_VERBS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})

_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _is_dunder(name):
    return name.startswith("__") and name.endswith("__")


def _is_exempt(name):
    if _is_dunder(name):
        return True
    if name in _TEST_HOOKS:
        return True
    if name.startswith("test_") or name.startswith("Test"):
        return True
    if name == "main":
        return True
    return False


def _dunder_all_names(tree):
    names = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            continue
        if isinstance(node.value, (ast.List, ast.Tuple, ast.Set)):
            names.update(
                elt.value for elt in node.value.elts
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
            )
    return names


def _top_level_candidates(tree):
    """Public, undecorated, non-exempt module-level function/class definitions."""
    exported = _dunder_all_names(tree)
    candidates = {}
    for node in tree.body:
        if not isinstance(node, _DEF_TYPES):
            continue
        name = node.name
        if name.startswith("_") or _is_exempt(name) or name in exported:
            continue
        if node.decorator_list:
            continue
        candidates[name] = node
    return candidates


def _method_candidates(tree):
    """Public, undecorated, non-exempt methods from all class bodies.

    Returns dict mapping (class_name, method_name) -> ast node.
    """
    candidates = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        for item in node.body:
            if not isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = item.name
            if _is_dunder(name) or name.startswith("_") or _is_exempt(name):
                continue
            if name in _HTTP_VERBS:
                continue
            if item.decorator_list:
                continue
            candidates[(node.name, name)] = item
    return candidates


def _collect_refs(trees):
    """Collect two reference sets across all parsed ASTs:

    name_refs      — every ``ast.Name`` in Load context (direct name uses)
    attribute_refs — every ``ast.Attribute`` attr in Load context (dotted
                     accesses, i.e. method / attribute calls)

    Both are pure name strings; no scope or type resolution is attempted.
    """
    name_refs = set()
    attribute_refs = set()
    for tree in trees.values():
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                name_refs.add(node.id)
            elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                attribute_refs.add(node.attr)
    return name_refs, attribute_refs


def find_dead_code_ast(file_sources):
    """AST-based dead-code detection across *file_sources*.

    ``file_sources`` is ``dict[path] -> source_text`` for every scanned
    Python file in the repo.

    Returns ``dict[path] -> list[Issue]`` with two rule symbols:

    * ``dead-code``    — top-level public function/class never referenced
      in code (same as the regex-based scanner, but references come from
      AST nodes rather than raw text).
    * ``unused-method`` — public class method whose name never appears as
      an attribute access anywhere in the scanned files.
    """
    trees = {}
    for path, source in file_sources.items():
        try:
            trees[path] = ast.parse(source, filename=path)
        except SyntaxError:
            continue

    name_refs, attribute_refs = _collect_refs(trees)

    issues_by_path = {}

    for path, tree in trees.items():
        top_dead = set()

        # --- top-level dead code ---
        for name, node in _top_level_candidates(tree).items():
            if name in name_refs or name in attribute_refs:
                continue
            top_dead.add(name)
            kind = "Class" if isinstance(node, ast.ClassDef) else "Function"
            issues_by_path.setdefault(path, []).append(
                Issue(path, node.lineno, "correctness", "info", "dead-code",
                      f"{kind} '{name}' is never referenced in code")
            )

        # --- unused methods ---
        for (class_name, method_name), node in _method_candidates(tree).items():
            # If the containing class is itself dead, skip to avoid cascading noise.
            if class_name in top_dead:
                continue
            if method_name in attribute_refs or method_name in name_refs:
                continue
            issues_by_path.setdefault(path, []).append(
                Issue(path, node.lineno, "correctness", "info", "unused-method",
                      f"Method '{class_name}.{method_name}' is never referenced anywhere in the repo")
            )

    return issues_by_path
