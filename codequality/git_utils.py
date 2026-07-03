"""Git plumbing for diff-scoped scans: no GitPython dependency, just
`git` subprocess calls plus a small unified-diff parser.
"""

import re
import subprocess


class GitError(RuntimeError):
    pass


def _run(args, cwd):
    result = subprocess.run(
        ["git"] + args, cwd=cwd, capture_output=True, text=True
    )
    if result.returncode != 0:
        raise GitError(result.stderr.strip() or f"git {' '.join(args)} failed")
    return result.stdout


def is_git_repo(cwd):
    try:
        _run(["rev-parse", "--is-inside-work-tree"], cwd)
        return True
    except GitError:
        return False


def resolve_default_base(cwd):
    """Pick a sensible base when the user didn't specify one:
    uncommitted changes present -> diff against HEAD (working tree);
    otherwise -> diff the most recent commit against its parent.
    """
    status = _run(["status", "--porcelain"], cwd)
    if status.strip():
        return "HEAD", None
    try:
        _run(["rev-parse", "HEAD~1"], cwd)
        return "HEAD~1", "HEAD"
    except GitError:
        return "HEAD", None  # first commit in the repo, nothing to diff against


def get_diff_text(base, head, cwd):
    if head:
        rev_range = f"{base}...{head}"
        args = ["diff", rev_range, "-U0", "--no-color"]
    else:
        args = ["diff", base, "-U0", "--no-color"]
    return _run(args, cwd)


_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


class _DiffState:
    """Mutable cursor threaded through the unified-diff line scan."""

    def __init__(self):
        self.files = {}
        self.current_file = None
        self.current_new_line = None


def _handle_file_header(line, state):
    raw_path = line[4:].strip()
    if raw_path == "/dev/null":
        state.current_file = None
        return
    state.current_file = raw_path[2:] if raw_path.startswith(("a/", "b/")) else raw_path
    state.files.setdefault(state.current_file, set())


def _handle_hunk_header(line, state):
    m = _HUNK_RE.match(line)
    if m:
        state.current_new_line = int(m.group(1))


def _handle_added_line(state):
    if state.current_file is not None and state.current_new_line is not None:
        state.files[state.current_file].add(state.current_new_line)
        state.current_new_line += 1


def parse_added_lines(diff_text):
    """Returns dict[path] -> set of 1-based line numbers added/modified in
    the new version of the file (renames/deletes resolve to the new path;
    deleted files are omitted since there is no new-file line to grade).
    """
    state = _DiffState()
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            _handle_file_header(line, state)
        elif line.startswith("@@"):
            _handle_hunk_header(line, state)
        elif line.startswith("+"):
            _handle_added_line(state)
        # unrecognized/removed ('-') lines don't affect state
    return state.files


def get_changed_files(base, head, cwd):
    diff_text = get_diff_text(base, head, cwd)
    return parse_added_lines(diff_text)
