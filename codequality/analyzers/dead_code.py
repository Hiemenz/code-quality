"""Cross-file dead-code detection: a top-level function or class defined
in one file but never referenced anywhere else in the repo.

The existing per-file unused-import/unused-variable checks (see
`python_analyzer.py`) can only ever look at one file at a time, so they
can't tell "nobody calls this" from "something in another module calls
this" -- they simply don't try. This is the whole-project version of the
same idea, following the same pattern as `duplication.py`: collect data
from every file first, then correlate across the whole set, rather than
judging each file in isolation.

Reference counting is deliberately dumb, the same "trade cleverness for
reproducibility" tradeoff every other analyzer in this tool makes: a
whole-word regex count of the candidate's identifier across every
scanned file's source, excluding the exact line it's defined on. No
attempt to resolve imports, aliases, or scopes -- just text, so the
result is 100% reproducible, at the cost of occasional false positives
(a name that happens to collide with an unrelated string/comment counts
as "used"; a name accessed only through `getattr`/reflection counts as
"unused"). That tradeoff is intentional: this is a heuristic signal,
reported at `info` severity, not a hard gate.

A name defined in file A and called elsewhere in that *same* file A
still counts as referenced -- only the definition line itself is
excluded from the count, not the rest of the file. Only a name with
*zero* occurrences anywhere else in the entire repo is flagged.

Exemptions, to keep the obvious false positives out:
- private names (leading `_`) -- not "public API" in the first place.
- dunder methods (`__init__`, `__all__`, ...) -- called by the
  interpreter, never by name.
- common test-framework hooks (`setUp`, `tearDown`, `test_*`, ...) --
  pytest/unittest call these by name/convention, never by direct
  reference.
- `main` -- the conventional `if __name__ == "__main__": main()` script
  entry point (usually is referenced anyway, but exempted explicitly in
  case the call site isn't in the scanned set).
- names listed in a module's `__all__` -- the module's own declaration
  that they're part of its public surface, same convention the
  unused-import check already follows.
- anything decorated. A decorator frequently means external dispatch
  (a Flask `@app.route`, a plugin registry, a CLI command via
  `@click.command`) that no amount of text search can see -- guessing
  here would just be noise, so decorated functions/classes are skipped
  entirely rather than risk a wrong flag.
"""

import ast
import re

from codequality.analyzers.base import Issue

_TEST_HOOKS = {"setUp", "tearDown", "setUpClass", "tearDownClass", "setUpModule", "tearDownModule"}


def _is_dunder(name):
    return name.startswith("__") and name.endswith("__")


def _is_exempt_name(name):
    if _is_dunder(name):
        return True
    if name in _TEST_HOOKS:
        return True
    if name.startswith("test_"):
        return True
    if name.startswith("Test"):
        # pytest/unittest also discover test *classes* by this naming
        # convention alone (a bare `unittest.TestCase` subclass, or any
        # pytest class prefixed `Test`) -- never by direct reference, same
        # reasoning as the `test_*` function exemption above.
        return True
    if name == "main":
        return True
    return False


def _dunder_all_names(tree):
    """String elements of a top-level `__all__ = [...]` assignment
    anywhere in `tree` -- those names are exempt, same convention as the
    unused-import check in python_analyzer.py.
    """
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


_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _candidates(tree):
    """dict[name] -> AST node, for top-level public, non-decorated,
    non-exempt function/class definitions in `tree`.
    """
    exported = _dunder_all_names(tree)
    candidates = {}
    for node in tree.body:
        if not isinstance(node, _DEF_TYPES):
            continue
        name = node.name
        if name.startswith("_") or _is_exempt_name(name) or name in exported:
            continue
        if node.decorator_list:
            continue
        candidates[name] = node
    return candidates


def _references_elsewhere(name, lines_by_path, own_path, own_lineno):
    """True if `name` occurs as a whole word anywhere in `lines_by_path`,
    excluding `own_path`'s `own_lineno` (the definition line itself).
    """
    pattern = re.compile(r"\b" + re.escape(name) + r"\b")
    for path, lines in lines_by_path.items():
        for idx, line in enumerate(lines, start=1):
            if path == own_path and idx == own_lineno:
                continue
            if pattern.search(line):
                return True
    return False


def find_dead_code(file_sources):
    """file_sources: dict[path] -> source text, for every scanned Python
    file in the repo (needs the whole set, like duplication.py, since a
    reference can live in any other file).

    Returns dict[path] -> list[Issue], one per top-level public
    function/class whose name never occurs anywhere else in the repo.
    """
    trees = {}
    for path, source in file_sources.items():
        try:
            trees[path] = ast.parse(source, filename=path)
        except SyntaxError:
            continue

    lines_by_path = {path: source.splitlines() for path, source in file_sources.items()}

    issues_by_path = {}
    for path, tree in trees.items():
        for name, node in _candidates(tree).items():
            if _references_elsewhere(name, lines_by_path, path, node.lineno):
                continue
            kind = "Class" if isinstance(node, ast.ClassDef) else "Function"
            issues_by_path.setdefault(path, []).append(
                Issue(path, node.lineno, "structure", "info", "dead-code",
                      f"{kind} '{name}' is defined but never referenced anywhere else in the repo")
            )
    return issues_by_path
