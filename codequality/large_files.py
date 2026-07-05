"""Flag tracked files that bloat the git repo: oversized blobs and binary
content committed directly into history.

A classic real-world accident: someone commits a `node_modules/`
directory, a build artifact, a multi-hundred-MB dataset, or a binary blob
(image, zip, `.pyc`, compiled library) into git. Unlike most quality
problems, git history never forgets a committed blob -- even deleting the
file in a later commit leaves its bytes in every clone forever (short of a
history rewrite) -- so this is worth catching structurally, and it needs
no code understanding at all: just file size and a content sniff.

Lives as a top-level module next to `churn.py`/`dependency_check.py`
rather than under `codequality/analyzers/`: every analyzer under
`analyzers/` operates on one already-discovered *source* file's parsed/
tokenized content (an `ast` tree, a token stream, a line list). This
check instead starts from the raw `git ls-tree -r -l HEAD` listing --
sizes and paths exactly as git already knows them, including files that
`scanner.discover_files` would never even see (a `.zip`, a `.png`, a
`node_modules/` tree that isn't in `DEFAULT_IGNORE_DIRS`, a huge binary
with no recognized source extension). There is no "parsed" representation
to dispatch into an analyzer for those files, so this doesn't fit the
analyzer contract at all.

Two independent checks per tracked file:

- **`large-file`** -- the blob is larger than `max_size_mb` (default 5).
  Severity `warn`. This is the direct "repo bloat" signal.
- **`large-binary-file`** -- the file is very likely binary: a NUL byte
  in its first few KB (the same heuristic git/most diff tools use to
  decide "don't try to diff this"), or an extension from a fixed list of
  known binary types (images, archives, compiled artifacts, fonts, ...).
  Always reported when detected -- even a small binary file is worth a
  second look ("should this really be committed, or does it belong in an
  assets dir / Git LFS / an external store") -- but severity is `warn`
  only once it also clears a much smaller size floor
  (`binary_threshold_bytes`, default 100KB); a tiny binary file is just
  `info`, since a handful of small icons are common and not worth failing
  a build over.

Deliberately no attempt to distinguish "an intentional assets directory"
from "binary content that snuck into source" -- same "no cleverness, just
reproducibility" tradeoff as every other heuristic check in this tool.
False positives (a legitimately-committed image, a fixture `.pdf` for a
test) are expected and fine; this is a signal to look at, not a build
killer by itself.

Categorized `structure`, not `style` or `correctness`: this isn't about
line-level code tidiness (style) or code behaving correctly
(correctness) -- it's about the physical size/shape of what's checked
into the repo, the same bucket the existing file-length check already
lives in under Structure (see README's Structure category and
`dependency_check.py`'s module docstring for the parallel reasoning on
why *that* check picked `style` over `correctness`).

Standalone subcommand (`codequality large-files`), not folded into
`scan`: `scan`'s per-category scoring only ever attaches issues to a
`FileMetrics` produced by `scanner.discover_files` + `analyze_file`, and
most files this check cares about (binaries, oversized blobs) were never
discovered in the first place -- they don't have a source extension
`discover_files` recognizes, or they live in a directory
(`node_modules/`, `dist/`, ...) that's excluded from that walk entirely.
There is no `FileMetrics` to attach a `large-file` issue to for those, so
this can't be wired in as a `_apply_large_files(root, metrics_by_path)`
step the way `circular_imports`/`dead_code` were -- it needs its own pass
over the raw git listing instead, same shape as `dependency_check`,
`churn`, and friends, none of which fold into `scan`'s score either.
"""

import fnmatch
import os

from codequality.analyzers.base import Issue
from codequality.git_utils import GitError, _run

__all__ = [
    "check", "render_text", "DEFAULT_MAX_SIZE_MB", "DEFAULT_BINARY_THRESHOLD_BYTES",
]

DEFAULT_MAX_SIZE_MB = 5
DEFAULT_BINARY_THRESHOLD_BYTES = 100 * 1024  # 100 KB

# Known binary file types -- images, archives, compiled artifacts, fonts,
# documents. Not exhaustive by design (NUL-byte sniffing below catches
# most of what this misses); this list mainly speeds up/covers binary
# formats that don't happen to contain a NUL in their first few KB.
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp", ".tiff",
    ".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".jar", ".war",
    ".pyc", ".pyo", ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".class",
    ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".eot",
    ".db", ".sqlite", ".sqlite3", ".mp3", ".mp4", ".mov", ".avi", ".wav",
}

_SNIFF_BYTES = 8192


def _is_excluded(rel_path, patterns):
    return any(
        fnmatch.fnmatch(rel_path, pat) or fnmatch.fnmatch(os.path.basename(rel_path), pat) for pat in patterns
    )


def _list_tracked_blobs(root):
    """Every tracked file at HEAD as (path, size_bytes) tuples, via
    `git ls-tree -r -l HEAD` -- the `-l` flag includes each blob's size
    directly, so this needs no `os.stat` walk of the working tree (which
    would also miss the point: an untracked multi-GB file sitting in the
    working tree isn't what bloats the *repo*, only what's actually
    committed does). Returns [] for a repo with no commits yet, or any
    other git failure, rather than raising -- this check should never be
    the thing that crashes a scan.
    """
    try:
        out = _run(["ls-tree", "-r", "-l", "HEAD"], root)
    except GitError:
        return []
    blobs = []
    for line in out.splitlines():
        if "\t" not in line:
            continue
        info, path = line.split("\t", 1)
        parts = info.split()
        if len(parts) < 4 or parts[1] != "blob":
            continue  # skip commit entries (submodules); trees don't appear under -r
        try:
            size = int(parts[3])
        except ValueError:
            continue
        blobs.append((path, size))
    return blobs


def _looks_binary(root, rel_path):
    """Extension check first (cheap, no disk read needed), then a NUL-byte
    sniff of the first few KB -- the same "is this text" heuristic git
    itself and most diff tools use. A file that no longer exists in the
    working tree (e.g. deleted since HEAD, or checked out elsewhere) is
    treated as not-binary rather than raising.
    """
    ext = os.path.splitext(rel_path)[1].lower()
    if ext in BINARY_EXTENSIONS:
        return True
    full = os.path.join(root, rel_path)
    try:
        with open(full, "rb") as f:
            chunk = f.read(_SNIFF_BYTES)
    except OSError:
        return False
    return b"\x00" in chunk


def _human_size(num_bytes):
    if num_bytes >= 1024 * 1024:
        return f"{num_bytes / (1024 * 1024):.1f}MB"
    if num_bytes >= 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes}B"


def check(root, config=None, max_size_mb=DEFAULT_MAX_SIZE_MB, binary_threshold_bytes=DEFAULT_BINARY_THRESHOLD_BYTES):
    """Runs both checks against every file tracked at HEAD and returns a
    flat list[Issue]. `config`, if given, only supplies `.exclude` glob
    patterns (the same convention `scan`/`diff` use) so vendored/generated
    trees a repo has already opted out of elsewhere stay opted out here
    too. Returns [] (never raises) for a repo with no commits yet.
    """
    exclude = list(getattr(config, "exclude", None) or [])
    max_size_bytes = max_size_mb * 1024 * 1024
    issues = []
    for rel_path, size in _list_tracked_blobs(root):
        if _is_excluded(rel_path, exclude):
            continue
        over_max = size > max_size_bytes
        if over_max:
            issues.append(Issue(
                file=rel_path, line=1, category="structure", severity="warn",
                symbol="large-file",
                message=f"{_human_size(size)} tracked in git (limit {max_size_mb}MB) -- large blobs bloat "
                        f"repo history permanently; consider Git LFS or removing it from version control",
            ))
        if _looks_binary(root, rel_path):
            issues.append(Issue(
                file=rel_path, line=1, category="structure",
                severity="warn" if size > binary_threshold_bytes else "info",
                symbol="large-binary-file",
                message=f"binary file ({_human_size(size)}) committed to git -- binary blobs can't be "
                        f"diffed or merged; consider Git LFS or an external artifact store",
            ))
    return sorted(issues, key=lambda i: (i.file, i.symbol))


def render_text(issues):
    if not issues:
        return "Large/Binary File Check\n\nNo issues found."
    lines = [f"Large/Binary File Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.severity}] [{issue.symbol}] {issue.message}")
    return "\n".join(lines)
