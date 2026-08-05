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

_MESSAGE = (
    "Implicit string concatenation -- "
    "add '+' if intentional, or a comma if a separator is missing"
)

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

_OPENING = frozenset({"(", "[", "{"})
_CLOSING = frozenset({")", "]", "}"})


class _ConcatScanner:
    """Streams tokens, tracking the previous adjacent string token, the
    bracket context, and 3.12+ f-string nesting; records an issue whenever
    two string tokens are adjacent in a flaggable context.
    """

    def __init__(self, path, only_lines):
        self.path = path
        self.only_lines = only_lines
        self.issues = []
        self.prev_str = None       # last STRING/FSTRING_END token, or None
        self.fstring_depth = 0     # >0 while inside a Python 3.12+ f-string
        self.bracket_stack = []    # stack of opening bracket chars: '(', '[', '{'

    def _should_flag(self, second_tok):
        """True when an implicit concat between prev_str and second_tok is reportable."""
        # Same line: always a typo, regardless of bracket context.
        if self.prev_str.start[0] == second_tok.start[0]:
            return True
        # Cross-line: only flag inside [...] or {...} where a comma is likely missing.
        innermost = self.bracket_stack[-1] if self.bracket_stack else None
        return innermost in _FLAGGED_BRACKETS

    def _record_if_concat(self, tok):
        """Record an issue if `tok` starts a string adjacent to a previous one."""
        if self.prev_str is None or not self._should_flag(tok):
            return
        lineno = tok.start[0]
        if self.only_lines is None or lineno in self.only_lines:
            self.issues.append(Issue(self.path, lineno, "style", "warn", SYMBOL, _MESSAGE))

    def _consume_fstring_token(self, tok):
        """Handle 3.12+ f-string boundary/interior tokens; True if consumed."""
        if _FSTRING_START is not None and tok.type == _FSTRING_START:
            if self.fstring_depth == 0:
                self._record_if_concat(tok)
            self.fstring_depth += 1
            return True
        if _FSTRING_END is not None and tok.type == _FSTRING_END:
            self.fstring_depth -= 1
            if self.fstring_depth == 0:
                self.prev_str = tok
            return True
        return self.fstring_depth > 0

    def _consume_op(self, tok):
        ch = tok.string
        if ch in _OPENING:
            self.bracket_stack.append(ch)
        elif ch in _CLOSING and self.bracket_stack:
            self.bracket_stack.pop()
        self.prev_str = None  # any operator/punctuation breaks adjacency

    def consume(self, tok):
        """Feed one token through the scanner."""
        if self._consume_fstring_token(tok):
            return
        if tok.type in _IGNORED:
            return
        if tok.type == _tokenize.OP:
            self._consume_op(tok)
        elif tok.type == _tokenize.STRING:
            self._record_if_concat(tok)
            self.prev_str = tok
        else:
            # NEWLINE (logical end-of-statement) and every other token
            # type breaks string adjacency.
            self.prev_str = None


def implicit_string_concat_issues(source, path, only_lines=None):
    """Every implicit-string-concat finding in `source`."""
    try:
        tokens = list(_tokenize.generate_tokens(io.StringIO(source).readline))
    except _tokenize.TokenError:
        return []
    scanner = _ConcatScanner(path, only_lines)
    for tok in tokens:
        scanner.consume(tok)
    return scanner.issues
