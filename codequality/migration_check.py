"""Migration reversibility check: flags schema/data migrations that can't
be rolled back.

Three independent, unambiguous sources -- a repo missing all three simply
yields no issues, not a crash:

- **Django migrations** -- any `*.py` file (other than `__init__.py`)
  inside a directory literally named `migrations`, containing a
  `migrations.RunPython(...)` (or bare `RunPython(...)`) call with no
  second positional argument and no `reverse_code` keyword. Django raises
  `IrreversibleError` at rollback time in exactly this situation; passing
  `RunPython.noop` as the reverse counts as reversible here too -- it is
  Django's own documented way to declare "intentionally does nothing on
  the way back", which is a deliberate choice this tool doesn't second-
  guess, not an omission.
- **Alembic migrations** -- any `*.py` file with the alembic fingerprint
  (top-level `revision = ...` and `down_revision = ...` assignments,
  which essentially never appears outside alembic-generated files) that
  either has no `downgrade()` function at all, or has one whose body is
  effectively empty (`pass`/`...`/nothing but its own docstring).
- **Raw up/down SQL pairs** (the golang-migrate/similar convention) -- any
  `*.up.sql` file with no sibling `*.down.sql` file in the same directory.

None of this executes a migration or connects to a database -- every
finding comes from parsing the migration file's own source (AST for the
Python cases, filename pairing for the SQL case). A migration that's
irreversible *on purpose* (there is no meaningful way back for some data
transformations) will still be flagged -- this tool has no way to know
intent, only structure -- so a flagged file may be a deliberate, accepted
tradeoff rather than a mistake.
"""

import ast
import os

from codequality.analyzers.base import Issue
from codequality.config import DEFAULT_IGNORE_DIRS

SYMBOL_IRREVERSIBLE_RUNPYTHON = "irreversible-django-migration"
SYMBOL_ALEMBIC_NOOP_DOWNGRADE = "alembic-downgrade-noop"
SYMBOL_ALEMBIC_MISSING_DOWNGRADE = "alembic-downgrade-missing"
SYMBOL_SQL_MISSING_DOWN = "sql-migration-missing-down"


def _read_source(full_path):
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _walk_dirs(root):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_IGNORE_DIRS and not d.endswith(".egg-info")]
        yield dirpath, filenames


def _call_full_name(node):
    """Best-effort dotted name for a Call's callee, e.g. 'migrations.RunPython'."""
    func = node.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        parts = []
        cur = func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            return ".".join(reversed(parts))
    return None


def _is_runpython_call(node):
    return _call_full_name(node) in ("RunPython", "migrations.RunPython")


def _runpython_has_reverse(node):
    if len(node.args) >= 2:
        return True
    return any(kw.arg == "reverse_code" for kw in node.keywords)


def _django_migration_issues(rel_path, tree):
    issues = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _is_runpython_call(node) and not _runpython_has_reverse(node):
            issues.append(
                Issue(
                    rel_path, node.lineno, "correctness", "warn", SYMBOL_IRREVERSIBLE_RUNPYTHON,
                    "RunPython(...) has no reverse_code/second argument -- this migration can't be "
                    "rolled back (Django raises IrreversibleError)",
                )
            )
    return issues


def _check_django_migrations(root):
    issues = []
    for dirpath, filenames in _walk_dirs(root):
        if os.path.basename(dirpath) != "migrations":
            continue
        for fn in sorted(filenames):
            if not fn.endswith(".py") or fn == "__init__.py":
                continue
            full = os.path.join(dirpath, fn)
            source = _read_source(full)
            if source is None:
                continue
            try:
                tree = ast.parse(source, filename=full)
            except SyntaxError:
                continue
            issues.extend(_django_migration_issues(os.path.relpath(full, root), tree))
    return issues


def _is_alembic_shaped(tree):
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
    return "revision" in names and "down_revision" in names


def _find_function(tree, name):
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _is_effectively_empty_body(func_node):
    body = [s for s in func_node.body if not isinstance(s, ast.Pass)]
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]  # drop a leading docstring
    if not body:
        return True
    return (
        len(body) == 1 and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis
    )


def _alembic_migration_issues(rel_path, tree):
    if not _is_alembic_shaped(tree):
        return []
    downgrade = _find_function(tree, "downgrade")
    if downgrade is None:
        return [
            Issue(
                rel_path, 1, "correctness", "warn", SYMBOL_ALEMBIC_MISSING_DOWNGRADE,
                f"{rel_path} defines no downgrade() function -- this revision can't be rolled back",
            )
        ]
    if _is_effectively_empty_body(downgrade):
        return [
            Issue(
                rel_path, downgrade.lineno, "correctness", "warn", SYMBOL_ALEMBIC_NOOP_DOWNGRADE,
                "downgrade() has no real body (just pass/... or a docstring) -- this revision can't "
                "actually be rolled back",
            )
        ]
    return []


def _check_alembic_migrations(root):
    issues = []
    for dirpath, filenames in _walk_dirs(root):
        for fn in sorted(filenames):
            if not fn.endswith(".py"):
                continue
            full = os.path.join(dirpath, fn)
            source = _read_source(full)
            if source is None:
                continue
            try:
                tree = ast.parse(source, filename=full)
            except SyntaxError:
                continue
            issues.extend(_alembic_migration_issues(os.path.relpath(full, root), tree))
    return issues


def _check_sql_migrations(root):
    issues = []
    for dirpath, filenames in _walk_dirs(root):
        names = set(filenames)
        for fn in sorted(filenames):
            if not fn.endswith(".up.sql"):
                continue
            down_name = fn[: -len(".up.sql")] + ".down.sql"
            if down_name in names:
                continue
            rel_path = os.path.relpath(os.path.join(dirpath, fn), root)
            issues.append(
                Issue(
                    rel_path, 1, "correctness", "warn", SYMBOL_SQL_MISSING_DOWN,
                    f"{rel_path} has no matching '{down_name}' in the same directory -- this migration "
                    f"can't be rolled back",
                )
            )
    return issues


def check(root):
    """Runs every migration-reversibility check against `root` and returns
    a flat list[Issue]. Never raises; [] if none of the migration shapes
    this tool recognizes are present.
    """
    issues = []
    issues.extend(_check_django_migrations(root))
    issues.extend(_check_alembic_migrations(root))
    issues.extend(_check_sql_migrations(root))
    return issues


def render_text(issues):
    if not issues:
        return "Migration Reversibility Check\n\nNo issues found."
    lines = [f"Migration Reversibility Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.symbol}] {issue.message}")
    return "\n".join(lines)
