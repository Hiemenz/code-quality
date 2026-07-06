"""Flags config files (CI workflows, docker-compose, Makefiles) that
reference a local file/path that no longer exists on disk.

This is deliberately narrow, mirroring `codequality/dependency_check.py`'s
"structural consistency, not deep understanding" philosophy: judging
whether a config file's *feature* is still wanted is fuzzy and would need
real understanding of intent, which this tool never attempts. Whether a
path it *references* still exists is not fuzzy at all -- it's a plain
`os.path.exists()` check. A CI step that shells out to a deleted script, a
docker-compose service pointing at a missing Dockerfile, or a Makefile
target invoking a missing script are all unambiguous rot, independent of
whether anyone still wants the job/service/target itself.

Three sources, each read independently -- a repo missing all three simply
yields no issues, not a crash:

- **GitHub Actions workflows** (`.github/workflows/*.yml`/`*.yaml`) --
  `run:` steps that invoke a clear local script path (`./scripts/x.sh`,
  `bash scripts/x.sh`).
- **docker-compose files** (`docker-compose*.yml`/`compose.yml` at the
  repo root) -- a service's `build: context`/`build.dockerfile`, a bind
  `volumes:` entry, or an `env_file:` entry pointing at a local path.
- **Makefiles** (`Makefile`/`makefile` at the repo root) -- a recipe line
  invoking a script via a clear relative path.

Parsing tradeoff: no real YAML (or Makefile) parser is used, even though
PyYAML would make the GitHub Actions case more robust against unusual
formatting. Two reasons this stays regex/indentation-based instead of
gated behind a `yaml`-optional-extra like `mypy`/`coverage` do for their
checks: (1) the only structure this check actually needs out of a
workflow file is "what shell commands appear under `run:` keys," which a
few lines of indentation tracking answers directly, without needing a
document tree; and (2) requiring PyYAML would either become a hard new
dependency for a plain `pip install .` or force every caller through an
`AVAILABLE` gate for what is, in the end, still just string extraction.
The regex approach is intentionally conservative to compensate: only
clear, unambiguous relative paths are ever flagged (a leading `./`/`../`,
or an interpreter keyword like `bash`/`python` followed by a path ending
in a recognized script extension). A bare command (`pytest`), an absolute
path, a URL, or anything containing a shell/CI variable expansion
(`$FOO`, `${{ ... }}`) is silently skipped rather than guessed at -- see
`_is_resolvable`.

Categorized `documentation` (not `correctness` or `structure`): like
`env-check`'s drift checks, this is about a config file's *description*
of the world (a path it expects to exist) no longer matching reality, not
about code behaving incorrectly. `orphaned-config` is also a standalone
subcommand (like `dependency-check`/`env-check`), not folded into `scan`'s
score.
"""

import glob
import os
import re

from codequality.analyzers.base import Issue

SYMBOL = "orphaned-config-reference"

_INTERPRETERS = {"bash", "sh", "zsh", "python", "python3", "node", "ruby", "perl", "source"}
_SCRIPT_EXTENSIONS = (".sh", ".py", ".rb", ".pl", ".js", ".mjs", ".ts")

_RUN_KEY_RE = re.compile(r"^(?P<indent>[ \t]*)(?:-\s*)?run:[ \t]*(?P<inline>.*)$")
_BLOCK_SCALAR_RE = re.compile(r"^[|>][+-]?\s*(#.*)?$")

_COMPOSE_CONTEXT_RE = re.compile(r"^context:\s*[\"']?([^\"'\s#]+)")
_COMPOSE_DOCKERFILE_RE = re.compile(r"^dockerfile:\s*[\"']?([^\"'\s#]+)")
_COMPOSE_BUILD_SHORTHAND_RE = re.compile(r"^build:\s*[\"']?(\.{1,2}/[^\"'\s#]+)\s*$")
_COMPOSE_ENV_FILE_RE = re.compile(r"^-?\s*env_file:\s*[\"']?(\.{1,2}/[^\"'\s#]+|[\w.-]+\.env[\w.-]*)[\"']?\s*$")
_COMPOSE_VOLUME_RE = re.compile(r"^-\s*[\"']?(\.{1,2}/[^:\"'\s]+):")


def _is_resolvable(path):
    """A path is only ever checked if it's unambiguous: no shell/CI
    variable expansion, no glob, no home-dir shorthand. Anything else is
    skipped rather than guessed at.
    """
    if not path or path.startswith("~"):
        return False
    return not any(ch in path for ch in ("$", "*", "?"))


def _read_lines(full_path):
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return f.readlines()
    except OSError:
        return None


def _looks_like_relative_path(token):
    if not token:
        return False
    if token.startswith(("http://", "https://", "git://", "git@")):
        return False
    if token.startswith("/") or token.startswith("-"):
        return False
    if "://" in token:
        return False
    return _is_resolvable(token)


def extract_script_references(command_line):
    """Extracts unambiguous local script paths referenced in one shell
    command line (used for both a GitHub Actions `run:` line and a
    Makefile recipe line). Conservative by design -- see module
    docstring: only a leading `./`/`../`, or an interpreter keyword
    immediately followed by a path ending in a recognized script
    extension, counts. A bare command (`pytest`), a flag (`-c`), an
    absolute path, or a URL is skipped.
    """
    refs = []
    tokens = command_line.split()
    for i, raw_tok in enumerate(tokens):
        tok = raw_tok.strip("'\"")
        if tok.startswith("./") or tok.startswith("../"):
            if _looks_like_relative_path(tok):
                refs.append(tok)
            continue
        if tok in _INTERPRETERS and i + 1 < len(tokens):
            nxt = tokens[i + 1].strip("'\"")
            if (nxt.endswith(_SCRIPT_EXTENSIONS) and _looks_like_relative_path(nxt)
                    and not nxt.startswith(("./", "../"))):
                refs.append(nxt)
    return refs


def _iter_run_command_lines(lines):
    """Yields (1-based line_number, command_text) for every shell line
    inside a GitHub Actions `run:` step -- both the single-line
    `run: cmd` form and the `run: |`/`run: >` block-scalar form. Regex +
    indentation based, not a real YAML parse (see module docstring).
    """
    i = 0
    n = len(lines)
    while i < n:
        m = _RUN_KEY_RE.match(lines[i].rstrip("\n"))
        if not m:
            i += 1
            continue
        indent = len(m.group("indent"))
        inline = m.group("inline").strip()
        if inline and not _BLOCK_SCALAR_RE.match(inline):
            yield i + 1, inline
            i += 1
            continue
        i += 1
        while i < n:
            nxt = lines[i].rstrip("\n")
            if not nxt.strip():
                i += 1
                continue
            nxt_indent = len(nxt) - len(nxt.lstrip(" \t"))
            if nxt_indent <= indent:
                break
            yield i + 1, nxt.strip()
            i += 1


def _find_workflow_files(root):
    workflows_dir = os.path.join(root, ".github", "workflows")
    found = []
    for pattern in ("*.yml", "*.yaml"):
        found.extend(sorted(glob.glob(os.path.join(workflows_dir, pattern))))
    return found


def _orphaned_issue(rel_path, line_no, missing_path):
    return Issue(
        file=rel_path, line=line_no, category="documentation", severity="warn",
        symbol=SYMBOL,
        message=f"{rel_path} references '{missing_path}', which doesn't exist in this repo",
    )


def _check_workflow_file(root, full_path):
    rel_path = os.path.relpath(full_path, root)
    lines = _read_lines(full_path)
    if lines is None:
        return []
    issues = []
    for line_no, command in _iter_run_command_lines(lines):
        for ref in extract_script_references(command):
            if not os.path.exists(os.path.join(root, ref)):
                issues.append(_orphaned_issue(rel_path, line_no, ref))
    return issues


def _check_github_actions(root):
    issues = []
    for full_path in _find_workflow_files(root):
        issues.extend(_check_workflow_file(root, full_path))
    return issues


def _find_compose_files(root):
    found = []
    for pattern in ("docker-compose*.yml", "docker-compose*.yaml", "compose.yml", "compose.yaml"):
        found.extend(sorted(glob.glob(os.path.join(root, pattern))))
    return found


def _prune_stale_contexts(context_by_indent, indent):
    """Drops any tracked `context:` value whose indentation is deeper than
    the line currently being looked at -- it belongs to a build mapping
    we've already left.
    """
    for k in list(context_by_indent):
        if k > indent:
            del context_by_indent[k]


def _match_compose_line(stripped, indent, context_by_indent):
    """Returns the local path referenced by this one already-stripped
    line, if any, else None. Updates `context_by_indent` as a side effect
    when the line is a `context:` key, so a sibling `dockerfile:` line at
    the same indentation can combine the two.
    """
    m = _COMPOSE_CONTEXT_RE.match(stripped)
    if m:
        context_by_indent[indent] = m.group(1)
        return None

    m = _COMPOSE_DOCKERFILE_RE.match(stripped)
    if m:
        context = context_by_indent.get(indent)
        return os.path.join(context, m.group(1)) if context else m.group(1)

    for pattern in (_COMPOSE_BUILD_SHORTHAND_RE, _COMPOSE_ENV_FILE_RE, _COMPOSE_VOLUME_RE):
        m = pattern.match(stripped)
        if m:
            return m.group(1)

    return None


def _compose_refs(lines):
    """Yields (1-based line_number, path) for every build/volume/env_file
    reference found in a docker-compose file, resolved against the
    repo root (compose files in scope here are only ones at the repo
    root -- see module docstring).
    """
    context_by_indent = {}
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        _prune_stale_contexts(context_by_indent, indent)
        path = _match_compose_line(stripped, indent, context_by_indent)
        if path is not None:
            yield i + 1, path


def _check_compose_file(root, full_path):
    rel_path = os.path.relpath(full_path, root)
    lines = _read_lines(full_path)
    if lines is None:
        return []
    issues = []
    for line_no, ref in _compose_refs(lines):
        if not _is_resolvable(ref):
            continue
        if not os.path.exists(os.path.join(root, ref)):
            issues.append(_orphaned_issue(rel_path, line_no, ref))
    return issues


def _check_docker_compose(root):
    issues = []
    for full_path in _find_compose_files(root):
        issues.extend(_check_compose_file(root, full_path))
    return issues


def _find_makefile(root):
    for name in ("Makefile", "makefile"):
        full = os.path.join(root, name)
        if os.path.isfile(full):
            return full
    return None


def _makefile_recipe_lines(lines):
    """Yields (1-based line_number, command_text) for every Makefile
    recipe line (tab-indented, per make convention). Leading `@`/`-`
    recipe-line prefixes (silent/ignore-error) are stripped first so they
    don't get mistaken for part of the command.
    """
    for i, raw in enumerate(lines):
        line = raw.rstrip("\n")
        if not line.startswith("\t"):
            continue
        command = line[1:].lstrip()
        command = command.lstrip("@-").strip()
        if command:
            yield i + 1, command


def _check_makefile(root):
    full_path = _find_makefile(root)
    if full_path is None:
        return []
    rel_path = os.path.relpath(full_path, root)
    lines = _read_lines(full_path)
    if lines is None:
        return []
    issues = []
    for line_no, command in _makefile_recipe_lines(lines):
        for ref in extract_script_references(command):
            if not os.path.exists(os.path.join(root, ref)):
                issues.append(_orphaned_issue(rel_path, line_no, ref))
    return issues


def check(root):
    """Runs every orphaned-config-reference check against `root` and
    returns a flat list[Issue]. Returns [] (never raises) when none of
    the config file kinds it knows about are present.
    """
    issues = []
    issues.extend(_check_github_actions(root))
    issues.extend(_check_docker_compose(root))
    issues.extend(_check_makefile(root))
    return issues


def render_text(issues):
    if not issues:
        return "Orphaned Config Check\n\nNo issues found."
    lines = [f"Orphaned Config Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.symbol}] {issue.message}")
    return "\n".join(lines)
