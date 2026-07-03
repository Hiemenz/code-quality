"""Cross-file duplicate-block detection.

Language agnostic by design: lines are normalized (whitespace collapsed)
and hashed in a sliding window. Any window that recurs anywhere else in
the analyzed file set is flagged as duplicated. This purposely does not
try to be smart about renamed variables etc. -- it catches copy/paste,
which is the common case worth flagging in v1.
"""

import re
from collections import defaultdict

_WS_RE = re.compile(r"\s+")


def _normalize(line):
    return _WS_RE.sub(" ", line.strip())


def _index_blocks(path, lines, window, min_line_len, seen):
    """Hash every window-sized slice of `lines` into `seen[block] -> [(path, start_idx), ...]`."""
    normalized = [_normalize(l) for l in lines]
    for i in range(len(normalized) - window + 1):
        block = normalized[i : i + window]
        if any(len(l) < min_line_len for l in block):
            continue
        seen[tuple(block)].append((path, i))


def find_duplicate_lines(file_lines, window=6, min_line_len=4):
    """
    file_lines: dict[path] -> list[str] (raw lines, no trailing newline needed)
    Returns: dict[path] -> set of 0-based line indices that are part of a
             duplicated block.
    """
    seen = defaultdict(list)  # normalized-block -> [(path, start_idx), ...]
    for path, lines in file_lines.items():
        if len(lines) >= window:
            _index_blocks(path, lines, window, min_line_len, seen)

    duplicates = defaultdict(set)
    for locations in seen.values():
        if len(locations) <= 1:
            continue
        for path, i in locations:
            duplicates[path].update(range(i, i + window))

    return duplicates
