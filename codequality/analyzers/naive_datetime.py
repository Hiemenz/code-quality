"""Naive-datetime detection: constructing a timezone-unaware `datetime`
where an aware one is almost always meant.

`datetime.now()`, `datetime.today()`, `datetime.fromtimestamp(x)`, and
`datetime.utcfromtimestamp(x)` all return a naive datetime (no `tzinfo`)
unless a timezone is passed. Naive datetimes silently assume local time,
compare/​subtract incorrectly against aware ones (`TypeError` at best, a
wrong answer at worst), and serialize without an offset -- the exact class
of bug that produces "works on my machine, wrong in CI" timestamp errors.

Flagged (all `warn`, correctness category):

- `datetime.now()` with no argument, or `datetime.now(None)` -- pass
  `datetime.now(timezone.utc)` (or a real zone) instead.
- `datetime.today()` -- always naive, no tz parameter at all; use
  `datetime.now(timezone.utc)`.
- `datetime.fromtimestamp(x)` with no `tz` -- add `tz=timezone.utc`.
- `datetime.utcnow()` / `datetime.utcfromtimestamp()` -- these return a
  naive datetime *claiming* to be UTC, the sharpest version of the trap
  (also deprecated in 3.12; `deprecated_api` flags the deprecation, this
  flags the naivety).

Matched syntactically by the trailing attribute name, the same
no-type-information approach the rest of the analyzers take: `datetime.now()`,
`dt.datetime.now()`, and `datetime.datetime.now()` all match on `now`. A
bare `now()` with no receiver is not matched (too likely to be an unrelated
local function).
"""

import ast

from codequality.analyzers.base import Issue

SYMBOL = "naive-datetime"

# attribute name -> (has a tz/None-able keyword we should check, guidance)
_NO_TZ_PARAM = frozenset({"today", "utcnow"})           # never take a tz
_TZ_FIRST_POSITIONAL = {"now": 0}                       # now(tz) -- tz is arg 0
_TZ_KEYWORD = frozenset({"fromtimestamp", "utcfromtimestamp"})  # accept tz=...

_ALL = _NO_TZ_PARAM | set(_TZ_FIRST_POSITIONAL) | _TZ_KEYWORD


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _attr_name(func):
    """The trailing attribute of a call target: `a.b.now` -> 'now'; else None."""
    return func.attr if isinstance(func, ast.Attribute) else None


def _has_timezone_arg(call, attr):
    """True if this call already passes a non-None timezone."""
    if attr in _NO_TZ_PARAM:
        return False  # these constructors have no tz parameter at all
    if attr in _TZ_FIRST_POSITIONAL:
        idx = _TZ_FIRST_POSITIONAL[attr]
        if len(call.args) > idx and not _is_none(call.args[idx]):
            return True
    if attr in _TZ_KEYWORD:
        for kw in call.keywords:
            if kw.arg == "tz" and not _is_none(kw.value):
                return True
    # a tz passed by keyword to now() (now(tz=...)) counts too
    for kw in call.keywords:
        if kw.arg == "tz" and not _is_none(kw.value):
            return True
    return False


def _is_none(node):
    return isinstance(node, ast.Constant) and node.value is None


def naive_datetime_issues(tree, path, only_lines=None):
    """Every naive-datetime finding in `tree`."""
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _in_scope(node, only_lines):
            continue
        attr = _attr_name(node.func)
        if attr not in _ALL:
            continue
        # The receiver's trailing name must plausibly be `datetime` to avoid
        # matching unrelated `.now()`/`.today()` on other objects.
        receiver = node.func.value
        recv_name = receiver.attr if isinstance(receiver, ast.Attribute) else (
            receiver.id if isinstance(receiver, ast.Name) else None
        )
        if recv_name != "datetime":
            continue
        if _has_timezone_arg(node, attr):
            continue
        issues.append(Issue(
            path, node.lineno, "correctness", "warn", SYMBOL,
            f"datetime.{attr}(...) returns a timezone-naive datetime -- "
            f"pass tz=timezone.utc (or a real zone) so it isn't silently local time"
        ))
    return issues
