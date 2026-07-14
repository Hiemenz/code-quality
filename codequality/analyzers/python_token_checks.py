"""Token-level checks for Python source: patterns that are lost after AST
parsing because the parser resolves them at compile time.

Currently: implicit string concatenation -- two adjacent string literals with
no explicit operator or separator between them. The Python parser merges them
into a single string at compile time, so they're invisible in the AST. The
common mistake is a missing comma in a list/tuple:

    items = [
        "hello"
        "world"   # ← this is NOT a two-element list; it's ["helloworld"]
    ]

Intentional multi-line string splitting (a long SQL query or error message
split across source lines inside parentheses) is common and deliberate, so
the check uses bracket context to reduce noise:

  - Always flagged: two strings on the SAME line (`x = "a" "b"`) -- almost
    always a typo; no legitimate style splits strings on a single line.
  - Flagged inside [ / { (list, set, dict contexts): missing-comma bugs happen
    here, intentional splits don't.
  - NOT flagged inside ( ... ) across lines: splitting a long string across
    parenthesised lines is a well-understood idiom for readability
    (`p.add_argument("--flag", "long help text " "that continues here")`).
"""

import io
import tokenize as _tokenize

from codequality.analyzers.base import Issue

SYMBOL = "implicit-string-concat"

# NL (non-logical newline inside brackets) is ignored so that multi-line
# implicit concatenations inside [...] / {...} are still detected.
# NEWLINE (logical end-of-statement) resets adjacency: two strings on
# separate top-level lines are two separate expression statements, not a concat.
_IGNORED = frozenset({
    _tokenize.NL, _tokenize.COMMENT,
    _tokenize.INDENT, _tokenize.DEDENT, _tokenize.ENCODING,
})

# Python 3.12+ tokenises f-strings as FSTRING_START / FSTRING_MIDDLE / FSTRING_END
# rather than a single STRING token.
_FSTRING_START = getattr(_tokenize, "FSTRING_START", None)
_FSTRING_END = getattr(_tokenize, "FSTRING_END", None)

# Bracket types where a missing comma is the likely bug.
_FLAGGED_BRACKETS = frozenset({"[", "{"})


def implicit_string_concat_issues(source, path, only_lines=None):
    """Every implicit-string-concat finding in `source`."""
    try:
        tokens = list(_tokenize.generate_tokens(io.StringIO(source).readline))
    except _tokenize.TokenError:
        return []

    issues = []
    prev_str = None          # last STRING/FSTRING_END token, or None
    fstring_depth = 0        # >0 while inside a Python 3.12+ f-string
    bracket_stack = []       # stack of opening bracket chars: '(', '[', '{'

    def _innermost_bracket():
        return bracket_stack[-1] if bracket_stack else None

    def _should_flag(second_tok):
        """True when an implicit concat between prev_str and second_tok is reportable."""
        # Same line: always a typo, regardless of bracket context.
        if prev_str is not None and prev_str.start[0] == second_tok.start[0]:
            return True
        # Cross-line: only flag inside [...] or {...} where a comma is likely missing.
        return _innermost_bracket() in _FLAGGED_BRACKETS

    for tok in tokens:
        # --- Handle Python 3.12+ f-string tokens ---
        if _FSTRING_START is not None and tok.type == _FSTRING_START:
            if fstring_depth == 0:
                if prev_str is not None and _should_flag(tok):
                    lineno = tok.start[0]
                    if only_lines is None or lineno in only_lines:
                        issues.append(Issue(
                            path, lineno, "style", "warn", SYMBOL,
                            "Implicit string concatenation -- "
                            "add '+' if intentional, or a comma if a separator is missing"
                        ))
            fstring_depth += 1
            continue
        if _FSTRING_END is not None and tok.type == _FSTRING_END:
            fstring_depth -= 1
            if fstring_depth == 0:
                prev_str = tok
            continue
        if fstring_depth > 0:
            continue

        # --- Regular token handling ---
        if tok.type in _IGNORED:
            continue
        if tok.type == _tokenize.NEWLINE:
            prev_str = None
            continue
        if tok.type == _tokenize.OP:
            ch = tok.string
            if ch in ("(", "[", "{"):
                bracket_stack.append(ch)
            elif ch in (")", "]", "}") and bracket_stack:
                bracket_stack.pop()
            prev_str = None  # any operator/punctuation breaks adjacency
            continue
        if tok.type == _tokenize.STRING:
            if prev_str is not None and _should_flag(tok):
                lineno = tok.start[0]
                if only_lines is None or lineno in only_lines:
                    issues.append(Issue(
                        path, lineno, "style", "warn", SYMBOL,
                        "Implicit string concatenation -- "
                        "add '+' if intentional, or a comma if a separator is missing"
                    ))
            prev_str = tok
        else:
            prev_str = None
    return issues
