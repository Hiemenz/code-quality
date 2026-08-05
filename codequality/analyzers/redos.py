"""Catastrophic-backtracking (ReDoS) detection in regex literals.

A regex with *nested unbounded quantifiers* -- an unbounded repeat (`+`,
`*`, `{n,}`) whose body contains another unbounded repeat at the same
backtracking level -- can take exponential time on a non-matching input,
because the engine has exponentially many ways to divide the string
between the two quantifiers. `(a+)+$` against `"aaaa...aaab"` is the
textbook example: cheap to write, trivial to weaponize into a denial of
service.

This flags the unambiguous nested-quantifier shape -- `(a+)+`, `(a*)*`,
`(.*)+`, `(\\d+)+`, `(a+b+)+`, ... -- by parsing each regex *literal* with
the stdlib's own regex parser (never executing it) and looking for an
unbounded repeat whose body, unwrapped through group/alternation layers,
holds another unbounded repeat. Only string-literal patterns passed to an
`re.*` call are examined, the same no-dataflow approach the other
analyzers take. `warn`, security category.

Deliberately narrow: overlapping-alternation evil regexes like `(a|a)*`
are a real ReDoS class too but need overlap analysis that's far noisier,
so they're out of scope here in favour of near-zero false positives on the
nested-quantifier case, which covers the large majority of real reports.
"""

import ast
import warnings

from codequality.analyzers.base import Issue

SYMBOL = "catastrophic-regex"

# The stdlib regex parser moved from `sre_parse` to `re._parser` in 3.11;
# both still exist on 3.11-3.13. Import whichever resolves; if neither
# does (a future removal), the check degrades to a no-op rather than
# crashing -- same graceful-degradation stance as the other opt-in checks.
_parser = None
for _modname in ("re._parser", "sre_parse"):
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            _parser = __import__(_modname, fromlist=["parse", "MAXREPEAT"])
        break
    except Exception:  # pragma: no cover - only on a hypothetical future runtime
        continue

# Regex-emitting functions in the `re` module whose first positional
# argument is the pattern.
_RE_FUNCS = frozenset({
    "compile", "match", "search", "fullmatch", "findall", "finditer", "sub", "subn", "split",
})


def _in_scope(node, only_lines):
    if only_lines is None:
        return True
    lineno = getattr(node, "lineno", None)
    if lineno is None:
        return False
    end_lineno = getattr(node, "end_lineno", lineno)
    return any(lineno <= ln <= end_lineno for ln in only_lines)


def _is_unbounded_repeat(element):
    """True if `element` is a MAX_REPEAT/MIN_REPEAT with no finite upper bound."""
    if _parser is None:
        return False
    op, av = element
    if op not in (_parser.MAX_REPEAT, _parser.MIN_REPEAT):
        return False
    _lo, hi, _sub = av
    return hi == _parser.MAXREPEAT


def _immediate_repeats(seq):
    """Yield the repeat nodes reachable in `seq` at the current backtracking
    level -- descending through group (SUBPATTERN) and alternation (BRANCH)
    wrappers, but never into a repeat's own body (that's a deeper level).
    """
    for element in seq:
        op, av = element
        if op in (_parser.MAX_REPEAT, _parser.MIN_REPEAT):
            yield element
        elif op == _parser.SUBPATTERN:
            yield from _immediate_repeats(av[-1])
        elif op == getattr(_parser, "BRANCH", object()):
            for branch in av[1]:
                yield from _immediate_repeats(branch)


def _all_repeats(seq):
    """Yield every repeat node anywhere in the parsed pattern `seq`."""
    for element in seq:
        op, av = element
        if op in (_parser.MAX_REPEAT, _parser.MIN_REPEAT):
            yield element
            yield from _all_repeats(av[-1])
        elif op == _parser.SUBPATTERN:
            yield from _all_repeats(av[-1])
        elif op == getattr(_parser, "BRANCH", object()):
            for branch in av[1]:
                yield from _all_repeats(branch)


def pattern_is_catastrophic(pattern):
    """True if `pattern` (a regex string) has nested unbounded quantifiers."""
    if _parser is None:
        return False
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            parsed = list(_parser.parse(pattern))
    except Exception:
        return False  # not a valid regex -- not our job to report that here
    for repeat in _all_repeats(parsed):
        if not _is_unbounded_repeat(repeat):
            continue
        body = repeat[1][-1]
        if any(_is_unbounded_repeat(inner) for inner in _immediate_repeats(body)):
            return True
    return False


def _pattern_arg(call):
    """The first positional arg of an `re.<func>(...)` call, if it's a string literal."""
    func = call.func
    if not isinstance(func, ast.Attribute) or func.attr not in _RE_FUNCS:
        return None
    receiver = func.value
    if not (isinstance(receiver, ast.Name) and receiver.id == "re"):
        return None
    if not call.args:
        return None
    first = call.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first
    return None


def redos_issues(tree, path, only_lines=None):
    """Every catastrophic-regex finding in `tree`."""
    if _parser is None:
        return []
    issues = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not _in_scope(node, only_lines):
            continue
        arg = _pattern_arg(node)
        if arg is None or not pattern_is_catastrophic(arg.value):
            continue
        issues.append(Issue(
            path, node.lineno, "security", "warn", SYMBOL,
            "Regex has nested unbounded quantifiers (e.g. (a+)+) -- a "
            "non-matching input can trigger exponential backtracking (ReDoS); "
            "rewrite to avoid the nesting or bound the repetition"
        ))
    return issues
