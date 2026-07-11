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
split across source lines) produces the same token pattern, so all findings
are `warn` severity with a message that acknowledges both interpretations --
add `+` to make it explicit, or a comma if the intent was a separator.
"""

import io
import tokenize as _tokenize

from codequality.analyzers.base import Issue

SYMBOL = "implicit-string-concat"

# NL (non-logical newline inside brackets) is ignored so that multi-line
# implicit concatenations inside parens/brackets are still detected.
# NEWLINE (logical end-of-statement) is NOT ignored -- it resets adjacency
# tracking so that "a" # comment\n"b" (two separate statements) is not flagged.
_IGNORED = frozenset({
    _tokenize.NL, _tokenize.COMMENT,
    _tokenize.INDENT, _tokenize.DEDENT, _tokenize.ENCODING,
})

# Python 3.12+ tokenises f-strings as FSTRING_START / FSTRING_MIDDLE / FSTRING_END
# rather than a single STRING token.  Detect them when available.
_FSTRING_START = getattr(_tokenize, "FSTRING_START", None)
_FSTRING_MIDDLE = getattr(_tokenize, "FSTRING_MIDDLE", None)
_FSTRING_END = getattr(_tokenize, "FSTRING_END", None)

# Extra tokens that appear inside an f-string expression that should not
# reset adjacency tracking while we're inside the f-string.
_FSTRING_INNER = frozenset(t for t in (_FSTRING_MIDDLE,) if t is not None)


def implicit_string_concat_issues(source, path, only_lines=None):
    """Every implicit-string-concat finding in `source`."""
    try:
        tokens = list(_tokenize.generate_tokens(io.StringIO(source).readline))
    except _tokenize.TokenError:
        return []

    issues = []
    prev_str = None
    fstring_depth = 0  # >0 while inside f-string braces (Python 3.12+)

    for tok in tokens:
        # --- Handle Python 3.12+ f-string tokens ---
        if _FSTRING_START is not None and tok.type == _FSTRING_START:
            if fstring_depth == 0:
                # Outermost f-string start: check adjacency, then begin tracking
                if prev_str is not None:
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
                prev_str = tok  # f-string ended; track position for next token
            continue
        if fstring_depth > 0:
            # Inside an f-string body: skip all tokens (don't reset prev_str)
            continue

        # --- Regular token handling ---
        if tok.type in _IGNORED:
            continue
        if tok.type == _tokenize.NEWLINE:
            # Logical end-of-statement: break adjacency so that
            # '"a"  # comment\n"b"' (two separate statements) is not flagged.
            prev_str = None
            continue
        if tok.type == _tokenize.STRING:
            if prev_str is not None:
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
