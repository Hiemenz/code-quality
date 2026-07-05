"""Shared hardcoded-secret-detection patterns.

Two callers need "does this look like a hardcoded secret" and must agree on
what that means:

- `python_security.py` -- AST-based: matches on an assignment target's
  *name* (`_SECRET_NAME_RE`), then checks whether the assigned string
  constant is an obvious placeholder.
- `generic_analyzer.py` -- line-level regex for every non-Python language:
  matches `name <assign-sigil> "value"` on raw text in one shot
  (`SECRET_ASSIGN_RE`), then applies the same placeholder check.

`codequality history-secrets` (see `codequality/history_secrets.py`) also
needs the line-level regex -- it scans lines *added* by past commits (plain
text from `git diff`/`git show`, no AST available), so it reuses
`SECRET_ASSIGN_RE`/`is_placeholder` exactly as `generic_analyzer.py` does,
rather than inventing a second pattern list that could drift out of sync.

Keeping every pattern in one module means "what looks like a secret" only
has to be updated in one place.
"""

import re

# Bare identifier fragment that looks secret-flavored -- used to gate an AST
# assignment target's *name* in python_security.py.
SECRET_NAME_RE = re.compile(r"(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key)", re.IGNORECASE)

# `name <assign-sigil> "value"` on one line of text -- used for the
# line-level regex path (every non-Python language, plus history-secrets).
# Group 1 is the matched name fragment, group 3 is the quoted value.
SECRET_ASSIGN_RE = re.compile(
    r"(?i)\b(pass(word|wd)?|secret|token|api[_-]?key|access[_-]?key)\b\s*[:=]\s*[\"']([^\"'\s]+)[\"']"
)

# Obvious non-secret placeholder values that would otherwise trip the checks
# above (the empty-string alternative covers the AST path, where the name
# already matched but the constant is "").
SECRET_PLACEHOLDER_RE = re.compile(
    r"^(|changeme|xxx+|todo|<.*>|\.\.\.|example|test|dummy|fake|placeholder)$", re.IGNORECASE
)


def is_placeholder(value):
    """True if `value` is an obvious non-secret placeholder rather than a
    real-looking secret -- the shared gate applied after a name/assignment
    pattern above already matched.
    """
    return bool(SECRET_PLACEHOLDER_RE.match(value.strip()))
