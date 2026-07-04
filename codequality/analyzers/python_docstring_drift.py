"""Docstring/signature drift: flags a docstring that documents a parameter
the function no longer actually has -- a common staleness bug after a
signature is edited without updating its own docstring (an LLM editing a
function is just as likely to forget this as a human is).

Deliberately asymmetric: this only flags *documented-but-removed*
parameters, never *undocumented* ones. Checking for missing documentation
would be much noisier (plenty of legitimate docstrings don't document
every trivial parameter) and the existing `missing-docstring` check
already covers "no docstring at all". This one is narrowly about a
docstring making a claim that's now false.

Supports the three common conventions: Sphinx (`:param name:`), Google
(an "Args:"/"Parameters:" section with indented `name: ...` lines), and
NumPy (a "Parameters" section with `name : type` lines under a `----`
underline). Best-effort regex parsing, not a full docstring parser.
"""

import ast
import re

from codequality.analyzers.base import Issue

_SPHINX_PARAM_RE = re.compile(r":param\s+(\**\w+):")
_SECTION_HEADER_RE = re.compile(r"^[ \t]*(Args|Arguments|Parameters)[ \t]*:?[ \t]*$", re.MULTILINE | re.IGNORECASE)
_GOOGLE_PARAM_RE = re.compile(r"^[ \t]{2,}(\*{0,2}\w+)\s*(?:\([^)]*\))?\s*:")
_NUMPY_PARAM_RE = re.compile(r"^[ \t]*(\*{0,2}\w+)[ \t]*:[ \t]*\S")
_NUMPY_UNDERLINE_RE = re.compile(r"^[ \t]*-{3,}[ \t]*$")


def _section_param_names(docstring):
    header = _SECTION_HEADER_RE.search(docstring)
    if header is None:
        return set()
    # header.end() lands right before the newline that ends the header
    # line itself (the header regex is anchored with $ in MULTILINE mode),
    # so the slice starts with that newline -- strip it first, or
    # splitlines() produces a spurious leading '' that looks like an
    # immediate blank-line end-of-section.
    lines = docstring[header.end():].lstrip("\n").splitlines()
    is_numpy = bool(lines) and _NUMPY_UNDERLINE_RE.match(lines[0] or "")
    body = lines[1:] if is_numpy else lines
    pattern = _NUMPY_PARAM_RE if is_numpy else _GOOGLE_PARAM_RE

    names = set()
    for line in body:
        if not line.strip():
            break  # blank line ends the section
        m = pattern.match(line)
        if m:
            names.add(m.group(1).lstrip("*"))
        elif not line.startswith((" ", "\t")):
            break  # a dedented, non-blank line means a new section started
    return names


def _documented_param_names(docstring):
    names = {m.group(1).lstrip("*") for m in _SPHINX_PARAM_RE.finditer(docstring)}
    names |= _section_param_names(docstring)
    return names


def _actual_param_names(node):
    args = node.args
    names = {a.arg for a in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    if args.vararg:
        names.add(args.vararg.arg)
    if args.kwarg:
        names.add(args.kwarg.arg)
    return names


def docstring_drift_issues(fn_node, path):
    """[]/[Issue]s for parameters `fn_node`'s docstring documents that no
    longer exist in its actual signature.
    """
    docstring = ast.get_docstring(fn_node)
    if not docstring:
        return []
    documented = _documented_param_names(docstring)
    if not documented:
        return []
    stale = sorted(documented - _actual_param_names(fn_node) - {"self", "cls"})
    return [
        Issue(path, fn_node.lineno, "documentation", "info", "stale-docstring-param",
              f"Docstring for '{fn_node.name}' documents parameter '{name}', "
              f"which is not in the actual signature")
        for name in stale
    ]
