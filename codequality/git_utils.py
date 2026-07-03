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


def parse_added_lines(diff_text):
    """Returns dict[path] -> set of 1-based line numbers added/modified in
    the new version of the file (renames/deletes resolve to the new path;
    deleted files are omitted since there is no new-file line to grade).
    """
    files = {}
    current_file = None
    current_new_line = None

    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            raw_path = line[4:].strip()
            if raw_path == "/dev/null":
                current_file = None
            else:
                current_file = raw_path[2:] if raw_path.startswith(("a/", "b/")) else raw_path
                files.setdefault(current_file, set())
        elif line.startswith("@@"):
            m = _HUNK_RE.match(line)
            if m:
                current_new_line = int(m.group(1))
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            if current_file is not None and current_new_line is not None:
                files[current_file].add(current_new_line)
                current_new_line += 1
        elif line.startswith("-"):
            continue  # removed line: doesn't exist in the new file, doesn't advance new-line counter

    return files


def get_changed_files(base, head, cwd):
    diff_text = get_diff_text(base, head, cwd)
    return parse_added_lines(diff_text)
