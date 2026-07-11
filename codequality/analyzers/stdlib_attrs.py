"""Hallucinated stdlib attribute detection: `os.path.exists_dir(...)`,
`shutil.copytreee`, `from json import dumpss` -- an attribute or name that
the *real* stdlib module doesn't have. The stdlib-level sibling of
`--check-imports`' unresolved-import check (a module that exists but the
member doesn't), and, like it, opt-in under the same flag: results depend
on the interpreter `codequality` runs under, since stdlib surfaces change
between Python versions.

This imports stdlib modules to inspect them -- and *only* stdlib modules,
as listed by `sys.stdlib_module_names`, so the "never execute code from
the scanned repo" rule holds: nothing here can cause a scanned repo's (or
a third-party package's) import-time side effects to run. A tiny denylist
skips the stdlib's own joke/side-effect modules (`antigravity` opens a
browser, `this` prints), and any module that fails to import in this
environment (`tkinter` without a display, platform-specific modules) is
silently skipped rather than guessed at.

Attribute chains are verified left to right, descending only while the
object in hand is a module or a class -- `datetime.datetime.utcnow` walks
module -> class -> method and is fully verified, but `sys.stdout.write`
stops at `stdout` (an instance; what instances have at runtime isn't this
tool's business). Names re-bound anywhere in the file, and attributes the
file itself assigns onto a module (monkeypatching), are skipped -- same
conservative conventions as internal_refs.py.
"""

import ast
import importlib
import sys

from codequality.analyzers.base import Issue
from codequality.analyzers.internal_refs import (
    _locally_assigned_attrs,
    _outermost_attribute_chains,
    _shadowed_names,
)

SYMBOL = "unresolved-attribute"

_SIDE_EFFECT_MODULES = {"antigravity", "this", "idlelib", "turtledemo"}

_STDLIB_MODULES = frozenset(getattr(sys, "stdlib_module_names", ())) - _SIDE_EFFECT_MODULES

_module_cache = {}


def _import_stdlib(dotted):
    """The imported module, or None if it isn't importable stdlib here."""
    if dotted.split(".", 1)[0] not in _STDLIB_MODULES:
        return None
    if dotted not in _module_cache:
        try:
            _module_cache[dotted] = importlib.import_module(dotted)
        except Exception:
            _module_cache[dotted] = None
    return _module_cache[dotted]


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _bind_plain_import(node, bindings):
    for alias in node.names:
        top = alias.name.split(".", 1)[0]
        if top not in _STDLIB_MODULES:
            continue
        if alias.asname is not None:
            bindings[alias.asname] = alias.name
        else:
            bindings[top] = top


def _bind_from_import(node, bindings):
    if node.level > 0 or node.module is None:
        return
    for alias in node.names:
        if alias.name == "*":
            continue
        dotted = f"{node.module}.{alias.name}"
        if _import_stdlib(dotted) is not None:
            bindings[alias.asname or alias.name] = dotted


def _bindings(tree):
    """Local name -> stdlib dotted module, from this file's imports."""
    bindings = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            _bind_plain_import(node, bindings)
        elif isinstance(node, ast.ImportFrom):
            _bind_from_import(node, bindings)
    return bindings


def _first_missing_segment(module, segments):
    """Walk `segments` from `module`, descending only through modules and
    classes. Returns the first segment that doesn't exist, or None if the
    chain verifies (or verification had to stop at an instance).
    """
    current = module
    for seg in segments:
        if not (hasattr(current, seg)):
            # A submodule that isn't imported as an attribute yet (e.g.
            # `os.path` is, but some packages' submodules aren't) still
            # counts if it's importable.
            if isinstance(current, type(sys)) and _import_stdlib(f"{current.__name__}.{seg}") is not None:
                current = _import_stdlib(f"{current.__name__}.{seg}")
                continue
            return seg
        current = getattr(current, seg)
        if not isinstance(current, (type(sys), type)):
            return None  # instance -- stop verifying, assume fine
    return None


def _missing_from_import_names(node, module):
    for alias in node.names:
        if alias.name == "*":
            continue
        if hasattr(module, alias.name) or _import_stdlib(f"{node.module}.{alias.name}") is not None:
            continue
        yield alias.name


def _verify_from_imports(tree, path, only_lines):
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level > 0 or node.module is None:
            continue
        if not _in_scope(node, only_lines):
            continue
        module = _import_stdlib(node.module)
        if module is None:
            continue
        for name in _missing_from_import_names(node, module):
            issues.append(
                Issue(path, node.lineno, "correctness", "warn", SYMBOL,
                      f"'from {node.module} import {name}': the stdlib '{node.module}' module has no "
                      f"'{name}' in this environment (Python {sys.version_info.major}."
                      f"{sys.version_info.minor})")
            )
    return issues


def _verify_attribute_chains(tree, path, only_lines):
    bindings = _bindings(tree)
    if not bindings:
        return []
    shadowed = _shadowed_names(tree)
    assigned = _locally_assigned_attrs(tree)
    issues = []
    for node, chain in _outermost_attribute_chains(tree):
        base = chain[0]
        if not isinstance(node.ctx, ast.Load) or not _in_scope(node, only_lines):
            continue
        if base not in bindings or base in shadowed or (base, chain[1]) in assigned:
            continue
        module = _import_stdlib(bindings[base])
        if module is None:
            continue
        missing = _first_missing_segment(module, chain[1:])
        if missing is not None:
            issues.append(
                Issue(path, node.lineno, "correctness", "warn", SYMBOL,
                      f"'{'.'.join(chain)}': '{missing}' does not exist on stdlib '{bindings[base]}' in this "
                      f"environment (Python {sys.version_info.major}.{sys.version_info.minor})")
            )
    return issues


def stdlib_attribute_issues(tree, path, only_lines=None):
    """Every unresolved-attribute issue in `tree` -- opt-in via
    --check-imports, see the module docstring.
    """
    return _verify_from_imports(tree, path, only_lines) + _verify_attribute_chains(tree, path, only_lines)
