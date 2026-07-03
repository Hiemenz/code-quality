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


def find_duplicate_lines(file_lines, window=6, min_line_len=4):
    """
    file_lines: dict[path] -> list[str] (raw lines, no trailing newline needed)
    Returns: dict[path] -> set of 0-based line indices that are part of a
             duplicated block.
    """
    seen = defaultdict(list)  # normalized-block -> [(path, start_idx), ...]
    normalized_cache = {}

    for path, lines in file_lines.items():
        normalized = [_normalize(l) for l in lines]
        normalized_cache[path] = normalized
        n = len(normalized)
        if n < window:
            continue
        for i in range(n - window + 1):
            block = normalized[i : i + window]
            if any(len(l) < min_line_len for l in block):
                continue
            seen[tuple(block)].append((path, i))

    duplicates = defaultdict(set)
    for block, locations in seen.items():
        if len(locations) > 1:
            for path, i in locations:
                for k in range(window):
                    duplicates[path].add(i + k)

    return duplicates
