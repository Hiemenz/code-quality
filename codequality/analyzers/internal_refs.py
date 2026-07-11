"""Cross-file unresolved-internal-reference detection: `from utils import
frobnicate` (or `utils.frobnicate(...)`) where `utils` is a module *in this
repo* whose top level defines no `frobnicate`.

`--check-imports` catches a hallucinated *package* (a module that doesn't
resolve at all); this catches the repo-internal sibling of that failure
mode -- an LLM confidently importing or calling a helper that the named
module never defines. A human typo does this too, but an LLM does it far
more often: it "remembers" a utility that a similar codebase had. Unlike
`--check-imports` this needs no environment and never imports anything --
it's pure AST over the same `file_sources` map the other cross-file checks
(dead code, circular imports) already use, so it's always-on in full
`scan` mode and, like them, absent from `diff` mode (a diff has no view of
the rest of the repo to resolve against).

Two findings, split by how certain the failure is:

- **`from M import name`** where repo-module M has no top-level `name` and
  no submodule `M.name` -- `warn`: this is an ImportError the moment the
  file is imported.
- **`M.name` attribute access** (via `import M`/`import M as alias`) with
  the same non-existence -- `info`: modules are objects and attributes can
  be attached at runtime, so this is a strong hint rather than a proof.

What makes a target module *checkable* -- all skips are in the
fewer-false-positives direction:

- it parses (a syntax-error module verifies nothing);
- no `from ... import *` at its top level (its namespace can't be known
  statically);
- no module-level `__getattr__` (PEP 562 -- any attribute may resolve
  dynamically).

Top-level names are collected recursively through `if`/`try`/`for`/
`while`/`with` blocks at module scope (a name conditionally defined under
`try: import x / except ImportError:` still exists), but never inside
`def`/`class` bodies -- those aren't module attributes. Names the module
itself imports count as defined (re-export via `from .sub import name` is
normal `__init__.py` practice). An attribute the *referencing* file
assigns (`mod.cache = {}`) is treated as defined for that file.
"""

import ast

from codequality.analyzers.base import Issue
from codequality.analyzers.circular_imports import (
    _base_package,
    _module_name_for_path,
    _resolve_relative_base,
)

FROM_IMPORT_SYMBOL = "unresolved-internal-import"
ATTRIBUTE_SYMBOL = "unresolved-internal-attribute"

_SCOPE_STMTS = (ast.If, ast.Try, ast.For, ast.While, ast.With)
_BLOCK_FIELDS = ("body", "orelse", "finalbody", "handlers")


def _module_scope_statements(body):
    """Every statement reachable at module scope, descending into
    `if`/`try`/`for`/`while`/`with` blocks but never into `def`/`class`.
    """
    for stmt in body:
        yield stmt
        if isinstance(stmt, _SCOPE_STMTS) or isinstance(stmt, ast.ExceptHandler):
            for field in _BLOCK_FIELDS:
                yield from _module_scope_statements(getattr(stmt, field, []) or [])


class _ModuleInfo:
    def __init__(self):
        self.names = set()
        self.checkable = True


def _names_from_def(stmt, info):
    if stmt.name == "__getattr__" and isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
        info.checkable = False
    info.names.add(stmt.name)


def _names_from_assign(stmt, info):
    for target in stmt.targets:
        for node in ast.walk(target):
            if isinstance(node, ast.Name):
                info.names.add(node.id)


def _names_from_ann_or_aug_assign(stmt, info):
    if isinstance(stmt.target, ast.Name):
        info.names.add(stmt.target.id)


def _names_from_import(stmt, info):
    for alias in stmt.names:
        info.names.add(alias.asname or alias.name.split(".", 1)[0])


def _names_from_import_from(stmt, info):
    for alias in stmt.names:
        if alias.name == "*":
            info.checkable = False
        else:
            info.names.add(alias.asname or alias.name)


_NAME_COLLECTORS = (
    ((ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef), _names_from_def),
    ((ast.Assign,), _names_from_assign),
    ((ast.AnnAssign, ast.AugAssign), _names_from_ann_or_aug_assign),
    ((ast.Import,), _names_from_import),
    ((ast.ImportFrom,), _names_from_import_from),
)


def _collect_module_info(tree):
    """Top-level names a module defines (defs, classes, assignments,
    imports), plus whether its namespace is statically knowable at all.
    """
    info = _ModuleInfo()
    for stmt in _module_scope_statements(tree.body):
        for types, collector in _NAME_COLLECTORS:
            if isinstance(stmt, types):
                collector(stmt, info)
                break
    return info


def _build_module_map(file_sources):
    """(module_infos, submodules): dotted module name -> _ModuleInfo for
    every parseable file, and the set of every dotted name that is a repo
    module or package prefix (so `from pkg import sub` resolves).
    """
    module_infos = {}
    submodules = set()
    for rel_path, source in file_sources.items():
        module_name = _module_name_for_path(rel_path)
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            continue
        module_infos[module_name] = _collect_module_info(tree)
        parts = module_name.split(".")
        for i in range(1, len(parts) + 1):
            submodules.add(".".join(parts[:i]))
    return module_infos, submodules


def _defines(module_infos, submodules, module_name, attr):
    """True if repo-module `module_name` has top-level `attr` or a
    submodule by that name -- or if we can't say (module not checkable).
    """
    info = module_infos.get(module_name)
    if info is None or not info.checkable:
        return True
    return attr in info.names or f"{module_name}.{attr}" in submodules


def _missing_from_import_names(node, target, module_infos, submodules):
    for alias in node.names:
        if alias.name != "*" and not _defines(module_infos, submodules, target, alias.name):
            yield alias.name


def _from_import_issues(tree, rel_path, base_package, module_infos, submodules):
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        target = _resolve_relative_base(base_package, node.module, node.level) if node.level > 0 else node.module
        if target is None or target not in module_infos:
            continue
        for name in _missing_from_import_names(node, target, module_infos, submodules):
            issues.append(
                Issue(rel_path, node.lineno, "correctness", "warn", FROM_IMPORT_SYMBOL,
                      f"'from {target} import {name}': repo module '{target}' defines no "
                      f"top-level '{name}' -- this raises ImportError at import time")
            )
    return issues


def _bind_plain_import(node, module_infos, bindings):
    for alias in node.names:
        if alias.asname is not None:
            if alias.name in module_infos:
                bindings[alias.asname] = alias.name
            continue
        top = alias.name.split(".", 1)[0]
        if top in module_infos or any(m.startswith(top + ".") for m in module_infos):
            bindings[top] = top


def _bind_from_import(node, base_package, module_infos, bindings):
    base = _resolve_relative_base(base_package, node.module, node.level) if node.level > 0 else node.module
    if base is None:
        return
    for alias in node.names:
        if alias.name == "*":
            continue
        dotted = f"{base}.{alias.name}"
        if dotted in module_infos:
            bindings[alias.asname or alias.name] = dotted


def _import_bindings(tree, base_package, module_infos):
    """Local name -> repo dotted module it refers to, from `import M` /
    `import M as alias` / `from P import sub` where the target is a repo
    module. For a plain `import pkg.sub` the bound local name is `pkg`.
    """
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _bind_plain_import(node, module_infos, bindings)
        elif isinstance(node, ast.ImportFrom):
            _bind_from_import(node, base_package, module_infos, bindings)
    return bindings


def _attribute_chain(node):
    """['pkg', 'sub', 'attr'] for the Attribute node `pkg.sub.attr`, or
    None if the chain doesn't bottom out at a plain Name.
    """
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    return list(reversed(parts))


def _locally_assigned_attrs(tree):
    """{(base_name, attr)} for every `base.attr = ...` in this file -- an
    attribute this file itself attaches shouldn't be reported missing.
    """
    assigned = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.ctx, (ast.Store, ast.Del)):
            chain = _attribute_chain(node)
            if chain is not None and len(chain) >= 2:
                assigned.add((chain[0], chain[1]))
    return assigned


def _attribute_issues(tree, rel_path, bindings, module_infos, submodules):
    if not bindings:
        return []
    assigned = _locally_assigned_attrs(tree)
    shadowed = _shadowed_names(tree)
    issues = []
    for node, chain in _outermost_attribute_chains(tree):
        base = chain[0]
        if base not in bindings or base in shadowed or not isinstance(node.ctx, ast.Load):
            continue
        if (base, chain[1]) in assigned:
            continue
        module_name, attr = _split_module_prefix([bindings[base]] + chain[1:], module_infos)
        if attr is None:
            continue  # the whole chain is a module reference
        if not _defines(module_infos, submodules, module_name, attr):
            issues.append(
                Issue(rel_path, node.lineno, "correctness", "info", ATTRIBUTE_SYMBOL,
                      f"'{'.'.join(chain)}': repo module '{module_name}' defines no top-level "
                      f"'{attr}' -- possibly a hallucinated/renamed helper")
            )
    return issues


def _shadowed_names(tree):
    """Names re-bound anywhere in the file (assignment or function
    parameter) -- these may no longer refer to the module, so they're
    skipped entirely rather than scope-tracked.
    """
    shadowed = {
        n.id for n in ast.walk(tree)
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store)
    }
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            a = fn.args
            for arg in a.args + a.posonlyargs + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None:
                    shadowed.add(arg.arg)
    return shadowed


def _outermost_attribute_chains(tree):
    """(node, chain) for every outermost Attribute whose chain bottoms out
    at a plain Name -- inner Attribute nodes of an already-yielded chain
    are skipped so `pkg.sub.attr` is handled once, not three times.
    """
    seen_parts = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute) or id(node) in seen_parts:
            continue
        chain = _attribute_chain(node)
        if chain is None:
            continue
        inner = node.value
        while isinstance(inner, ast.Attribute):
            seen_parts.add(id(inner))
            inner = inner.value
        yield node, chain


def _split_module_prefix(dotted, module_infos):
    """Resolve the longest prefix of `dotted` that is itself a repo module;
    returns (module_name, first_segment_after_it), or (module_name, None)
    if the entire chain is a module reference.
    """
    module_name = dotted[0]
    idx = 1
    while idx < len(dotted) and f"{module_name}.{dotted[idx]}" in module_infos:
        module_name = f"{module_name}.{dotted[idx]}"
        idx += 1
    return module_name, (dotted[idx] if idx < len(dotted) else None)


def internal_reference_issues(file_sources):
    """dict[rel_path] -> list[Issue] over every scanned Python file -- see
    the module docstring for exactly what is and isn't flagged.
    """
    module_infos, submodules = _build_module_map(file_sources)
    results = {}
    for rel_path, source in file_sources.items():
        try:
            tree = ast.parse(source, filename=rel_path)
        except SyntaxError:
            continue
        base_package = _base_package(rel_path, _module_name_for_path(rel_path))
        issues = _from_import_issues(tree, rel_path, base_package, module_infos, submodules)
        bindings = _import_bindings(tree, base_package, module_infos)
        issues.extend(_attribute_issues(tree, rel_path, bindings, module_infos, submodules))
        if issues:
            results[rel_path] = issues
    return results
