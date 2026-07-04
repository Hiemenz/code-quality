"""Deterministic proxy for "did this change stay in scope": tokenizes a
task description (a commit subject, by default) and each changed file's
path, then flags a changed file that shares no token with the description
while at least one *other* changed file in a different area does. That
combination is the signal -- it means the description is specific enough
to check against (something matched) and this file wasn't part of what
it described.

Silent by default whenever the description isn't specific enough to
check against: too few tokens, only one file changed (nothing to compare
against), or every file matches (or none do). A vague subject like "fix
bug" produces no usable tokens, so nothing is flagged rather than
everything.
"""

import os
import re

from codequality.analyzers.base import Issue

_STOPWORDS = {
    "the", "a", "an", "and", "or", "for", "to", "of", "in", "on", "with",
    "fix", "fixes", "fixed", "add", "adds", "added", "update", "updates",
    "updated", "remove", "removes", "removed", "refactor", "refactors",
    "implement", "implements", "support", "supports", "into", "from",
    "up", "out", "not", "use", "using", "uses", "used", "make", "makes",
    "change", "changes", "changed", "small", "minor", "misc", "various",
    "some", "this", "that", "also", "more", "new",
}

_WORD_RE = re.compile(r"[a-zA-Z]+")
_CAMEL_PART_RE = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])")
_MIN_TOKEN_LEN = 3


def _tokenize(text):
    """Lowercase word tokens, camelCase/snake_case/kebab-case-split,
    stopwords and short tokens dropped.
    """
    words = []
    for raw in _WORD_RE.findall(text or ""):
        parts = _CAMEL_PART_RE.findall(raw)
        words.extend(parts if parts else [raw])
    return {w.lower() for w in words if len(w.lower()) >= _MIN_TOKEN_LEN} - _STOPWORDS


def _path_tokens(rel_path):
    stem = os.path.splitext(rel_path)[0]
    return _tokenize(stem.replace("/", " ").replace("_", " ").replace("-", " "))


def _issue(path, task_description):
    return Issue(
        path, 1, "correctness", "info", "scope-mismatch",
        f"Changed but shares no keyword with the stated task ({task_description!r}) -- "
        f"check this file is actually in scope",
    )


def _is_checkable(subject_tokens, changed_rel_paths):
    return bool(subject_tokens) and len(changed_rel_paths) >= 2


def scope_mismatch_issues(task_description, changed_rel_paths):
    """dict[rel_path] -> [Issue] for files whose path shares no token with
    `task_description`, restricted to files in a directory none of the
    *matching* files are also in (so touching several files in the same
    area as the described change is never flagged, only files elsewhere).
    """
    changed_rel_paths = list(changed_rel_paths)
    subject_tokens = _tokenize(task_description)
    if not _is_checkable(subject_tokens, changed_rel_paths):
        return {}

    matched = {path: bool(_path_tokens(path) & subject_tokens) for path in changed_rel_paths}
    if not any(matched.values()) or all(matched.values()):
        return {}

    matched_dirs = {os.path.dirname(p) for p, is_match in matched.items() if is_match}
    return {
        path: [_issue(path, task_description)]
        for path, is_match in matched.items()
        if not is_match and os.path.dirname(path) not in matched_dirs
    }
