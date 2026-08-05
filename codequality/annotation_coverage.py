"""Annotation-coverage report: what fraction of public function signatures
have type annotations?

For each Python file, ``codequality annotation-coverage`` walks every public
function and method (names not starting with ``_``) and counts:

  * Annotatable positions -- every parameter except ``self``/``cls``, plus
    the return type.
  * Annotated positions -- subset that have an explicit type annotation.

A coverage percentage is computed per-file and summarised for the whole repo.
Files below ``--min-coverage`` (default 0, i.e. informational only) are
highlighted. Dunder methods (``__init__``, ``__str__``, ...) are public by
Python convention but are excluded unless ``--include-dunders`` is passed.

This is purely structural AST analysis -- no type-checker is invoked, no
imports resolved, and no network access occurs.
"""

import ast
import os

from codequality.scanner import discover_files


def _is_public(name, include_dunders=False):
    if name.startswith("__") and name.endswith("__"):
        return include_dunders
    return not name.startswith("_")


def _param_count_annotated(func_node, include_dunders=False):
    """Return (total_annotatable, annotated) for the function's signature.

    ``self`` and ``cls`` are excluded from the count.
    Return type annotation counts as one additional annotatable position.
    """
    args = func_node.args
    all_args = (
        list(args.posonlyargs) + list(args.args)
        + ([args.vararg] if args.vararg else [])
        + list(args.kwonlyargs)
        + ([args.kwarg] if args.kwarg else [])
    )
    skip = {"self", "cls"}
    total = 0
    annotated = 0
    for arg in all_args:
        if arg.arg in skip:
            continue
        total += 1
        if arg.annotation is not None:
            annotated += 1
    # Return annotation
    total += 1
    if func_node.returns is not None:
        annotated += 1
    return total, annotated


def _file_results(source, rel_path, include_dunders=False):
    """Return a list of per-function dicts for a single file."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    results = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not _is_public(node.name, include_dunders):
            continue
        total, annotated = _param_count_annotated(node, include_dunders)
        results.append({
            "file": rel_path,
            "line": node.lineno,
            "function": node.name,
            "total": total,
            "annotated": annotated,
            "coverage": (annotated / total * 100.0) if total > 0 else 100.0,
        })
    return results


def _read_source(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _file_summary(rel_path, funcs):
    ft = sum(f["total"] for f in funcs)
    fa = sum(f["annotated"] for f in funcs)
    return {
        "file": rel_path,
        "function_count": len(funcs),
        "total": ft,
        "annotated": fa,
        "coverage": (fa / ft * 100.0) if ft > 0 else 100.0,
    }


def compute(root, exclude=None, min_coverage=0.0, include_dunders=False):
    """Walk Python files in `root` and return annotation-coverage stats.

    Returns a dict with:
      ``functions``  -- list of per-function dicts
      ``files``      -- list of per-file summary dicts
      ``overall``    -- {"total", "annotated", "coverage"}
      ``below_threshold``  -- functions below min_coverage
    """
    files = discover_files(root, exclude or [], include_generic=False)
    all_funcs = []
    by_file = {}

    for rel_path, lang in files:
        if lang != "python":
            continue
        source = _read_source(root, rel_path)
        if source is None:
            continue
        funcs = _file_results(source, rel_path, include_dunders=include_dunders)
        all_funcs.extend(funcs)
        if funcs:
            by_file[rel_path] = _file_summary(rel_path, funcs)

    total = sum(f["total"] for f in all_funcs)
    annotated = sum(f["annotated"] for f in all_funcs)
    overall_coverage = (annotated / total * 100.0) if total > 0 else 100.0

    below = [f for f in all_funcs if f["coverage"] < min_coverage]

    return {
        "functions": all_funcs,
        "files": sorted(by_file.values(), key=lambda r: r["coverage"]),
        "overall": {"total": total, "annotated": annotated, "coverage": overall_coverage},
        "below_threshold": sorted(below, key=lambda f: f["coverage"]),
        "min_coverage": min_coverage,
    }


def render_text(result, max_listing=25):
    ov = result["overall"]
    overall_line = (
        f"  Overall:  {ov['annotated']}/{ov['total']} positions annotated"
        f"  ({ov['coverage']:.1f}%)"
    )
    lines = ["Annotation Coverage", "", overall_line, ""]

    # Files table (worst first)
    file_rows = sorted(result["files"], key=lambda r: r["coverage"])[:max_listing]
    if file_rows:
        lines.append(f"  {'File':<50}{'Funcs':>7}{'Cover':>8}")
        lines.append(f"  {'-'*50}{'-'*7}{'-'*8}")
        for row in file_rows:
            cov = f"{row['coverage']:.1f}%"
            lines.append(f"  {row['file']:<50}{row['function_count']:>7}{cov:>8}")
        if len(result["files"]) > max_listing:
            lines.append(f"  ... and {len(result['files']) - max_listing} more files")

    below = result["below_threshold"]
    if below and result["min_coverage"] > 0:
        lines.append("")
        lines.append(
            f"  Functions below {result['min_coverage']:.0f}% "
            f"({len(below)} total, showing up to {max_listing}):"
        )
        for fn in below[:max_listing]:
            lines.append(
                f"    {fn['file']}:{fn['line']}  {fn['function']}  "
                f"{fn['annotated']}/{fn['total']} ({fn['coverage']:.1f}%)"
            )
        if len(below) > max_listing:
            lines.append(f"    ... and {len(below) - max_listing} more")

    return "\n".join(lines)
