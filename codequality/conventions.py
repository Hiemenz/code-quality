"""Repo-convention conformance: learn the scanned repo's own dominant
conventions, then flag files that deviate from them.

Every other check in this tool scores code against *universal* rules
(complexity limits, security patterns, ...). This one answers a different
question, the one that matters most when judging LLM-written additions to
an existing codebase: does the new code look like it belongs to *this*
repo? The baseline isn't a style guide someone wrote -- it's computed,
deterministically, from the code being scanned, so the same repo always
teaches the same conventions. There is no configuration for what the
conventions "should" be, on purpose: the repo itself is the spec.

Four conventions, all Python-only in this first version (what counts as a
convention, and how to read it syntactically, is language-specific):

- **type hints** -- of all annotatable slots (parameters except
  `self`/`cls`, plus each function's return), what share is annotated?
  Only flagged one-way: a mostly-untyped file in a repo that has clearly
  committed to typing. The reverse (a typed file in an untyped repo) is
  an improvement, not a deviation.
- **quote style** -- single vs. double, from real tokenization (docstrings
  and triple-quoted strings excluded; a quote character inside a comment
  or string body can't miscount).
- **docstring style** -- Sphinx (`:param x:`) vs. Google (`Args:`) vs.
  NumPy (`Parameters` + underline), detected per docstring.
- **string formatting** -- f-strings vs. `.format(...)`. `%`-formatting is
  deliberately ignored: `logger.info("%s", x)` is the *correct* logging
  idiom, and telling deliberate lazy-formatting apart from legacy
  `%`-interpolation isn't worth the false positives.

A convention only becomes a baseline when the repo has actually committed
to it: at least `_MIN_REPO_SAMPLES` observations and at least
`_DOMINANCE` agreement. A file only deviates when it has enough samples
of its own (`_MIN_FILE_SAMPLES`) *and* its own majority points the other
way -- one odd string literal is noise, a whole file written against the
repo's grain is signal. Everything is report-only: there's no score, no
gate, and no exit-code failure. Deviations are for a reviewer's table,
not a build breaker.
"""

import ast
import io
import json
import os
import tokenize

_MIN_REPO_SAMPLES = 20
_MIN_FILE_SAMPLES = 5
_DOMINANCE = 0.75

# Type hints get their own thresholds: it's a continuous share, not a
# two-way vote. "The repo is typed" needs a high bar; "this file isn't"
# needs a low one, so the gray zone between them never flags.
_TYPED_REPO_SHARE = 0.7
_UNTYPED_FILE_SHARE = 0.3


class _FileStats:
    def __init__(self, rel_path):
        self.path = rel_path
        self.hint_slots = 0
        self.hint_annotated = 0
        self.quotes = {"'": 0, '"': 0}
        self.docstring_styles = {"sphinx": 0, "google": 0, "numpy": 0}
        self.formatting = {"f-string": 0, ".format()": 0}


def _annotation_stats(tree, stats):
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        args = node.args
        params = args.posonlyargs + args.args + args.kwonlyargs
        for i, arg in enumerate(params):
            if i == 0 and not args.posonlyargs and arg.arg in ("self", "cls"):
                continue
            stats.hint_slots += 1
            if arg.annotation is not None:
                stats.hint_annotated += 1
        stats.hint_slots += 1  # the return slot
        if node.returns is not None:
            stats.hint_annotated += 1


def _docstring_style(docstring):
    if ":param " in docstring or ":returns:" in docstring or ":rtype:" in docstring:
        return "sphinx"
    if "Args:" in docstring or "Returns:" in docstring or "Raises:" in docstring:
        return "google"
    if "Parameters\n" in docstring and "---" in docstring:
        return "numpy"
    return None


def _docstring_stats(tree, stats):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)):
            docstring = ast.get_docstring(node)
            if docstring:
                style = _docstring_style(docstring)
                if style:
                    stats.docstring_styles[style] += 1


def _formatting_stats(tree, stats):
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            stats.formatting["f-string"] += 1
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
            and isinstance(node.func.value, ast.Constant)
            and isinstance(node.func.value.value, str)
        ):
            stats.formatting[".format()"] += 1


def _quote_stats(source, stats):
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return
    for tok in tokens:
        if tok.type == tokenize.STRING:
            body = tok.string.lstrip("rRbBuUfF")
        elif getattr(tokenize, "FSTRING_START", None) == tok.type:
            body = tok.string.lstrip("rRbBuUfF")
        else:
            continue
        if body[:3] in ("'''", '"""'):
            continue  # triple-quoted/docstring: excluded, see module docstring
        if body[:1] in stats.quotes:
            stats.quotes[body[:1]] += 1


def _collect_file_stats(rel_path, source):
    stats = _FileStats(rel_path)
    try:
        tree = ast.parse(source, filename=rel_path)
    except SyntaxError:
        return None
    _annotation_stats(tree, stats)
    _docstring_stats(tree, stats)
    _formatting_stats(tree, stats)
    _quote_stats(source, stats)
    return stats


def _dominant(counts, min_samples=_MIN_REPO_SAMPLES):
    """(winner, share, total) if one key holds >= _DOMINANCE of at least
    `min_samples` observations, else (None, share_of_leader, total).
    """
    total = sum(counts.values())
    if total == 0:
        return None, 0.0, 0
    winner = max(counts, key=lambda k: counts[k])
    share = counts[winner] / total
    if total >= min_samples and share >= _DOMINANCE:
        return winner, share, total
    return None, share, total


def _file_majority(counts, min_samples=_MIN_FILE_SAMPLES):
    total = sum(counts.values())
    if total < min_samples:
        return None, 0.0, total
    winner = max(counts, key=lambda k: counts[k])
    return winner, counts[winner] / total, total


def _categorical_deviations(all_stats, attr, convention, dominant_key, min_file_samples):
    deviations = []
    for stats in all_stats:
        counts = getattr(stats, attr)
        majority, share, total = _file_majority(counts, min_file_samples)
        if majority is not None and majority != dominant_key and share > 0.5:
            deviations.append({
                "file": stats.path,
                "convention": convention,
                "detail": f"file is {share:.0%} {majority} ({total} samples) "
                          f"where the repo standard is {dominant_key}",
            })
    return deviations


def _collect_repo_stats(root, config):
    from codequality.scanner import discover_files

    all_stats = []
    for rel_path, lang in discover_files(root, config.exclude, include_generic=False):
        if lang != "python":
            continue
        try:
            with open(os.path.join(root, rel_path), "r", encoding="utf-8", errors="replace") as f:
                source = f.read()
        except OSError:
            continue
        stats = _collect_file_stats(rel_path, source)
        if stats is not None:
            all_stats.append(stats)
    return all_stats


def _aggregate(all_stats):
    totals = _FileStats("<repo>")
    for stats in all_stats:
        totals.hint_slots += stats.hint_slots
        totals.hint_annotated += stats.hint_annotated
        for d_name in ("quotes", "docstring_styles", "formatting"):
            agg, one = getattr(totals, d_name), getattr(stats, d_name)
            for key, n in one.items():
                agg[key] += n
    return totals


def _type_hint_convention(totals, all_stats, deviations):
    repo_share = totals.hint_annotated / totals.hint_slots if totals.hint_slots else 0.0
    typed_repo = totals.hint_slots >= _MIN_REPO_SAMPLES and repo_share >= _TYPED_REPO_SHARE
    if typed_repo:
        deviations.extend(_type_hint_deviations(all_stats, repo_share))
    return {
        "repo_share": round(repo_share, 3),
        "samples": totals.hint_slots,
        "established": typed_repo,
    }


def _type_hint_deviations(all_stats, repo_share):
    for stats in all_stats:
        if stats.hint_slots < _MIN_FILE_SAMPLES:
            continue
        file_share = stats.hint_annotated / stats.hint_slots
        if file_share <= _UNTYPED_FILE_SHARE:
            yield {
                "file": stats.path,
                "convention": "type_hints",
                "detail": f"only {file_share:.0%} of {stats.hint_slots} annotatable slots are typed "
                          f"in a repo that is {repo_share:.0%} typed",
            }


_CATEGORICAL_CONVENTIONS = (
    ("quotes", "quote_style", 10),
    ("docstring_styles", "docstring_style", 2),
    ("formatting", "string_formatting", 3),
)


def compute(root, config):
    """Learn the repo's conventions and list per-file deviations. Returns
    {"conventions": {...}, "deviations": [...]} -- see the module
    docstring for exactly when a convention counts as established and
    when a file counts as deviating.
    """
    all_stats = _collect_repo_stats(root, config)
    totals = _aggregate(all_stats)
    deviations = []
    conventions = {"type_hints": _type_hint_convention(totals, all_stats, deviations)}

    for attr, convention, min_file in _CATEGORICAL_CONVENTIONS:
        winner, share, total = _dominant(getattr(totals, attr))
        conventions[convention] = {
            "dominant": winner,
            "share": round(share, 3),
            "samples": total,
            "established": winner is not None,
        }
        if winner is not None:
            deviations.extend(_categorical_deviations(all_stats, attr, convention, winner, min_file))

    deviations.sort(key=lambda d: (d["convention"], d["file"]))
    return {
        "files_analyzed": len(all_stats),
        "conventions": conventions,
        "deviations": deviations,
    }


def render_text(result, max_rows=40):
    """Render `compute()`'s result as a terminal report: the learned
    conventions, then the deviating files, capped with an "...and N more"
    tail -- same convention as `todo-age`'s listing.
    """
    lines = ["Repo Convention Conformance (the repo itself is the baseline)", ""]
    lines.append(f"Files analyzed: {result['files_analyzed']}")
    lines.append("")
    lines.append("Learned conventions")
    for name, info in result["conventions"].items():
        if not info["established"]:
            status = f"not established ({info['samples']} samples)"
        elif name == "type_hints":
            status = f"typed ({info['repo_share']:.0%} of {info['samples']} slots annotated)"
        else:
            status = f"{info['dominant']} ({info['share']:.0%} of {info['samples']} samples)"
        lines.append(f"  {name:<18} {status}")

    deviations = result["deviations"]
    lines.append("")
    if not deviations:
        lines.append("No files deviate from the established conventions.")
        return "\n".join(lines)
    lines.append(f"Deviating files ({len(deviations)})")
    for dev in deviations[:max_rows]:
        lines.append(f"  {dev['file']}: [{dev['convention']}] {dev['detail']}")
    remaining = len(deviations) - max_rows
    if remaining > 0:
        lines.append(f"  ... and {remaining} more (see --format json)")
    return "\n".join(lines)


def render_json(result):
    return json.dumps(result, indent=2)
