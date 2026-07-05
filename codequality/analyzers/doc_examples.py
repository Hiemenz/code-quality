"""Doc-example validation: catches the cheapest form of documentation rot --
a fenced Python code example in a Markdown file that no longer even parses
as valid Python.

An API can change out from under a README/docstring example and nobody
notices, because nobody re-runs a README code block. This deliberately
doesn't try to answer "does the example still reflect current behavior"
(that would require actually *running* the example against the current
code -- this tool never executes anything from the repo it's scanning,
see the top-level README). It answers a narrower, much cheaper question:
does the example still parse as valid Python at all? A `SyntaxError` here
is an unambiguous, zero-judgment signal that nobody has looked at this
example since some earlier edit made its syntax invalid (a Python-2-only
`print` statement, a copy-paste that dropped a closing paren, ...) -- the
same "a documented claim is now false" idea as `stale-docstring-param`
(analyzers/python_docstring_drift.py), just aimed at code examples instead
of parameter lists.

Scope, deliberately narrow for this first version: Markdown fenced code
blocks (```python / ```py) only. Docstring-embedded examples (a `>>>`
doctest block, or an "Example:"/"Usage:" section) are a plausible future
extension -- extracting *doctest* blocks would just mean stripping the
`>>>`/`...` prompts before parsing, but reliably deciding which
non-doctest indented text even counts as "a code example" (as opposed to
prose, a shell transcript, a directory listing, ...) isn't a small
addition, so it's left out rather than half-built. Every extracted block
is only ever passed to `ast.parse()`, never executed -- Markdown examples
are exactly the kind of untrusted, arbitrary-author content the
"parse-only, never exec" rule exists for.
"""

import ast
import re

from codequality.analyzers.base import Issue

_FENCE_RE = re.compile(r"^```+[ \t]*(\S+)?[ \t]*$")
_PYTHON_FENCE_LANGS = {"python", "py"}


def extract_python_blocks(source):
    """[(start_line, code)] for every ```python / ```py fenced block in
    `source`. `start_line` is the 1-based line number of the first line of
    *code* inside the block (the line right after the opening fence),
    which is what gets reported as the issue location.

    Fences are matched on the stripped line so an indented fence (e.g.
    inside a Markdown list item) is still recognized. An unclosed fence at
    end-of-file is treated as ending there rather than looping forever or
    being silently dropped.
    """
    blocks = []
    lines = source.splitlines()
    i = 0
    n = len(lines)
    while i < n:
        m = _FENCE_RE.match(lines[i].strip())
        if m and (m.group(1) or "").lower() in _PYTHON_FENCE_LANGS:
            start_line = i + 2
            body = []
            i += 1
            while i < n and not lines[i].strip().startswith("```"):
                body.append(lines[i])
                i += 1
            blocks.append((start_line, "\n".join(body)))
        i += 1
    return blocks


def check_markdown_source(rel_path, source):
    """[Issue] for every fenced Python block in `source` (the content of
    Markdown file `rel_path`) that fails to `ast.parse()`. Never executes
    anything -- parse-only, by design (see module docstring).
    """
    issues = []
    for start_line, code in extract_python_blocks(source):
        if not code.strip():
            continue
        try:
            ast.parse(code)
        except SyntaxError as e:
            issues.append(
                Issue(
                    rel_path, start_line, "documentation", "warn", "broken-doc-example",
                    f"Python code example no longer parses as valid Python: {e.msg} "
                    f"(near line {(e.lineno or 1) + start_line - 1})",
                )
            )
    return issues
