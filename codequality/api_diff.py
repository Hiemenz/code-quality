"""`codequality api-diff`: public API comparison between two arbitrary git
refs (e.g. two tags -- "what broke between v1.2 and v1.3").

This is a generalization of the `breaking-signature-change` check that
already runs inside `codequality diff` (see `analyzers/signature_diff.py`
and `scanner._apply_signature_diff`): that check only ever compares the
working tree (or one `--base`) against the current `diff` invocation, and
only for files that happen to appear in that one diff. This module instead
walks *every* Python file that ever existed at either ref, fetches its
content at both ends via `git show`, and runs the exact same signature
comparison on each pair -- so it works for "what changed between two
releases" even when neither ref is the current HEAD or working tree.

All comparison logic is reused as-is from `analyzers/signature_diff.py`
(`qualified_functions`, `signature_diff_issues`) -- this module only adds
the git plumbing to enumerate files at two refs and the handling for files
that exist at just one of them:

- a file that only exists at `to_ref` (added since `from_ref`) has no old
  version to compare against -- `signature_diff_issues` already returns []
  for that case (`old_source is None`), so it's a silent skip, same as
  a brand-new file within a single `diff` run.
- a file that only exists at `from_ref` (deleted by `to_ref`) is the one
  case `signature_diff` never has to handle, since `diff` mode only looks
  at files that still exist to diff against. Deleting a file removes its
  entire public API in one shot, which is arguably the most "breaking"
  change possible -- so this is flagged explicitly, one `removed-public-file`
  issue per public top-level function/method/class that the vanished file
  used to export (rather than one vague "file removed" note), so the
  report stays consistent with every other check's "one issue per symbol"
  granularity.
"""

import ast
import json

from codequality.analyzers.base import Issue
from codequality.analyzers.signature_diff import qualified_functions, signature_diff_issues
from codequality.git_utils import _run, get_file_at_ref

__all__ = ["compare", "render_text", "render_json"]


def _list_python_files(ref, cwd):
    """Every `.py` path tracked at `ref` (via `git ls-tree`, not a working-tree
    walk -- refs other than HEAD have no working tree to walk).
    """
    out = _run(["ls-tree", "-r", "--name-only", ref], cwd)
    return [line for line in out.splitlines() if line.endswith(".py")]


def _removed_symbols(tree):
    """(qualified_name, node) for every public top-level function/method and
    every public top-level class -- classes with no public methods have no
    entry in `qualified_functions`, so they're added here directly, one
    issue per class rather than being silently dropped.
    """
    symbols = list(qualified_functions(tree).items())
    has_public_method = {name.split(".")[0] for name in qualified_functions(tree)}
    for node in tree.body:
        if isinstance(node, ast.ClassDef) and not node.name.startswith("_") and node.name not in has_public_method:
            symbols.append((node.name, node))
    return sorted(symbols, key=lambda pair: pair[1].lineno)


def _removed_file_issues(old_source, path, from_ref, to_ref):
    """The whole file (and its public API) was deleted between `from_ref`
    and `to_ref`. One issue per public top-level function/method/class it
    used to export; falls back to a single file-level note if the old
    source doesn't even parse.
    """
    try:
        tree = ast.parse(old_source, filename=path)
    except SyntaxError:
        return [Issue(path, 1, "correctness", "error", "removed-public-file",
                       f"File removed between {from_ref} and {to_ref} (its previous contents "
                       f"couldn't be parsed to enumerate what public API went with it)")]

    symbols = _removed_symbols(tree)
    if not symbols:
        return [Issue(path, 1, "correctness", "error", "removed-public-file",
                       f"File removed between {from_ref} and {to_ref} (no public top-level "
                       f"functions/classes found in its previous contents)")]

    return [
        Issue(path, node.lineno, "correctness", "error", "removed-public-file",
              f"Public '{name}' removed -- its file {path} was deleted between {from_ref} and {to_ref}")
        for name, node in symbols
    ]


def compare(root, from_ref, to_ref="HEAD"):
    """Compare every Python file's public API between `from_ref` and
    `to_ref`. Returns a JSON-serializable dict; raises `GitError` if either
    ref doesn't resolve.
    """
    old_files = set(_list_python_files(from_ref, root))
    new_files = set(_list_python_files(to_ref, root))
    all_files = sorted(old_files | new_files)

    issues = []
    for path in all_files:
        old_source = get_file_at_ref(from_ref, path, root) if path in old_files else None
        new_source = get_file_at_ref(to_ref, path, root) if path in new_files else None
        if new_source is not None:
            issues.extend(signature_diff_issues(old_source, new_source, path))
        elif old_source is not None:
            issues.extend(_removed_file_issues(old_source, path, from_ref, to_ref))
    issues.sort(key=lambda i: (i.file, i.line))

    return {
        "tool": "codequality",
        "mode": "api-diff",
        "from_ref": from_ref,
        "to_ref": to_ref,
        "files_compared": len(all_files),
        "issues": [i.to_dict() for i in issues],
    }


def render_text(result):
    """Render `compare()`'s result dict as a plain-text report."""
    lines = [
        f"API Diff: {result['from_ref']} -> {result['to_ref']}",
        f"Files compared: {result['files_compared']}",
        "",
    ]
    issues = result["issues"]
    if not issues:
        lines.append("No breaking public-API changes detected.")
        return "\n".join(lines)

    lines.append(f"{len(issues)} issue(s):")
    for issue in issues:
        sev = issue["severity"].upper()
        lines.append(f"  [{sev:<5}] {issue['file']}:{issue['line']}  {issue['symbol']} - {issue['message']}")
    return "\n".join(lines)


def render_json(result):
    return json.dumps(result, indent=2, sort_keys=False)
