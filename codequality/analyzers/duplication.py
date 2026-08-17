"""Cross-file duplicate-block detection.

Language agnostic by design: lines are normalized (whitespace collapsed)
and hashed in a sliding window. Any window that recurs anywhere else in
the analyzed file set is flagged as duplicated. This purposely does not
try to be smart about renamed variables etc. -- it catches copy/paste,
which is the common case worth flagging in v1.

`load_index`/`save_index`/`find_cross_project_duplicates` extend this with
an opt-in, on-disk index (see `--cross-project-dup`) so a block that
recurs in a *different* project scanned into the same index file is also
caught -- something a single scan can never see on its own. Kept as
separate functions from `find_duplicate_lines` (rather than folded in)
so the common single-scan path stays untouched and free of any disk I/O.
"""

import json
import os
import re
from collections import defaultdict

_WS_RE = re.compile(r"\s+")
_INDEX_KEY_SEP = "\x1f"


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


def default_index_path():
    """Default location for the cross-project duplication index: honors
    $XDG_CACHE_HOME like every other XDG-aware cache, otherwise
    ~/.cache/codequality/duplication_index.json.
    """
    cache_home = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(cache_home, "codequality", "duplication_index.json")


def load_index(path):
    """Load a persisted cross-project duplication index, or {} if it
    doesn't exist yet / is unreadable (e.g. corrupted by a crashed run --
    treated as empty rather than failing the scan over a cache file).
    """
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def save_index(path, index, cap=200_000):
    """Persist `index` (as built/returned by `find_cross_project_duplicates`)
    to `path`. Soft-capped at `cap` entries: once the index has grown past
    the cap, no further blocks are added on save (existing ones are kept)
    rather than implementing eviction -- a real limitation for very large
    or very long-lived shared indexes, documented in the README.
    """
    if len(index) > cap:
        index = dict(list(index.items())[:cap])
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(index, f)


def find_cross_project_duplicates(file_lines, persisted_index, project_id, window=6, min_line_len=4):
    """Like `find_duplicate_lines`, but checks each block against blocks
    recorded under a *different* `project_id` in `persisted_index` (as
    returned by `load_index`), not just against the rest of `file_lines`.

    Returns `(matches, updated_index)`:
    - `matches`: dict[path] -> list of `(line_start_0based, other_project_id,
      other_path)`, one entry per block whose first cross-project match is
      recorded (only the first match per block start is kept -- this is a
      "does this exist elsewhere," not an exhaustive-match report).
    - `updated_index`: `persisted_index` merged with this project's blocks,
      ready to pass to `save_index`.
    """
    matches = defaultdict(list)
    updated = {key: list(locations) for key, locations in persisted_index.items()}

    for path, lines in file_lines.items():
        if len(lines) < window:
            continue
        normalized = [_normalize(l) for l in lines]
        for i in range(len(normalized) - window + 1):
            block = normalized[i : i + window]
            if any(len(l) < min_line_len for l in block):
                continue
            key = _INDEX_KEY_SEP.join(block)
            locations = updated.get(key, [])
            other = next((loc for loc in locations if loc[0] != project_id), None)
            if other is not None:
                matches[path].append((i, other[0], other[1]))
            entry = [project_id, path, i]
            if entry not in locations:
                updated[key] = locations + [entry]

    return matches, updated
