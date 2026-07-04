"""Inline suppression comments.

`codequality: ignore` or `codequality: ignore[symbol,symbol]` anywhere on a
line suppresses issues reported on that exact line. This is plain
substring matching, not tied to any one language's comment syntax --
`# codequality: ignore[...]` works in Python/Ruby and
`// codequality: ignore[...]` works in C-style languages identically,
since the marker text is what matters, not the prefix in front of it.

For per-function checks that are scored from a continuous metric rather
than from the issues list (high-complexity, long-function, deep-nesting,
missing-docstring), suppression also has to reach into the scorer --
otherwise the issue would disappear from the report while the score stayed
penalized, which would be confusing. `annotate_functions` attaches each
function's suppressed symbols (by its reported line) so `scorer.py` can
skip those specific penalties too.
"""

import re

ALL = "*"

_SUPPRESS_RE = re.compile(r"codequality:\s*ignore(?:\[([\w,\-\s]+)\])?", re.IGNORECASE)


def parse(source):
    """dict[1-based lineno] -> frozenset of suppressed symbols, where {ALL}
    means every symbol on that line is suppressed.
    """
    suppressions = {}
    for i, line in enumerate(source.splitlines(), start=1):
        m = _SUPPRESS_RE.search(line)
        if not m:
            continue
        symbols = m.group(1)
        if symbols is None:
            suppressions[i] = frozenset({ALL})
        else:
            suppressions[i] = frozenset(s.strip() for s in symbols.split(",") if s.strip())
    return suppressions


def is_suppressed(symbols, symbol):
    return bool(symbols) and (ALL in symbols or symbol in symbols)


def filter_issues(issues, suppressions):
    """Returns (kept_issues, suppressed_count)."""
    if not suppressions:
        return issues, 0
    kept = [i for i in issues if not is_suppressed(suppressions.get(i.line), i.symbol)]
    return kept, len(issues) - len(kept)


def annotate_functions(functions, suppressions):
    for fn in functions:
        fn.suppressed = suppressions.get(fn.lineno, frozenset())
