"""Configuration drift detector: flags when sibling per-environment config
files don't declare the same set of keys.

Two independent, narrow sources -- a repo missing both simply yields no
issues, not a crash:

- **Root `.env` variants** (`.env`, `.env.example`, `.env.development`,
  `.env.production`, ...) -- every `.env*` file directly at the repo root,
  parsed with the same simple `KEY=value` line format `env_check.py` uses.
  `.envrc` (direnv's shell-script config, a different format entirely) is
  deliberately excluded even though it matches the `.env*` glob.
- **A `config`/`environments`/`envs` directory** at the repo root, grouped
  by file extension (`.env`/`.yaml`/`.yml`/`.json`) so e.g. `dev.yaml` is
  only ever compared against other `.yaml` siblings, never against a
  `.json` file with an unrelated shape.

For YAML, there is deliberately no real parser (same tradeoff
`orphaned_config.py` makes, for the same reason: avoiding a hard new
dependency for what only needs a shallow answer here) -- only top-level,
zero-indent `key:` lines are extracted; nested keys, lists, and anchors are
invisible to this check. For JSON, only a top-level object's keys count;
a file that isn't valid JSON or isn't a top-level object is silently
skipped rather than guessed at.

Only compares *key sets*, never values -- so a rendered message is safe to
show even when the underlying file holds secrets: a key name like
`AWS_SECRET_ACCESS_KEY` may appear, its value never does.

A group of fewer than two comparable files (e.g. only one `.env` file
exists, or a `config/` directory has just one `.yaml`) produces no issues
-- there is nothing to compare against. Standalone subcommand (like
`orphaned-config`/`env-check`), not folded into `scan`'s score: like those,
this is about a config file's declared shape drifting from its own
siblings, not about code correctness.
"""

import glob
import json
import os
import re

from codequality.analyzers.base import Issue

SYMBOL = "config-drift"

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_YAML_TOP_KEY_RE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s|$)")
_CONFIG_DIR_NAMES = ("config", "environments", "envs")
_MAX_KEYS_IN_MESSAGE = 8


def _read_text(full_path):
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


def _parse_env_keys(text):
    keys = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if _ENV_KEY_RE.match(key):
            keys.add(key)
    return keys


def _parse_yaml_top_keys(text):
    keys = set()
    for raw in text.splitlines():
        if raw[:1].isspace():
            continue  # indented -- not a top-level key, see module docstring
        m = _YAML_TOP_KEY_RE.match(raw.strip())
        if m:
            keys.add(m.group(1))
    return keys


def _parse_json_top_keys(text):
    try:
        data = json.loads(text)
    except ValueError:
        return None
    return set(data.keys()) if isinstance(data, dict) else None


def _keys_for_file(full_path):
    ext = os.path.splitext(full_path)[1].lower()
    text = _read_text(full_path)
    if text is None:
        return None
    if ext == ".json":
        return _parse_json_top_keys(text)
    if ext in (".yaml", ".yml"):
        return _parse_yaml_top_keys(text)
    return _parse_env_keys(text)


def _find_root_env_files(root):
    found = []
    for full in sorted(glob.glob(os.path.join(root, ".env*"))):
        name = os.path.basename(full)
        if not os.path.isfile(full):
            continue
        if name != ".env" and not name.startswith(".env."):
            continue  # excludes .envrc and similar unrelated dotfiles
        found.append(full)
    return found


def _find_config_dir_groups(root):
    """Groups of sibling config files inside a config/environments/envs
    directory at the repo root, grouped by extension.
    """
    groups = []
    for dirname in _CONFIG_DIR_NAMES:
        dirpath = os.path.join(root, dirname)
        if not os.path.isdir(dirpath):
            continue
        by_ext = {}
        for name in sorted(os.listdir(dirpath)):
            full = os.path.join(dirpath, name)
            if not os.path.isfile(full):
                continue
            ext = os.path.splitext(name)[1].lower()
            if ext in (".env", ".yaml", ".yml", ".json"):
                by_ext.setdefault(ext, []).append(full)
        groups.extend(files for files in by_ext.values() if len(files) > 1)
    return groups


def _drift_message(rel_path, missing, sources):
    preview = ", ".join(missing[:_MAX_KEYS_IN_MESSAGE])
    if len(missing) > _MAX_KEYS_IN_MESSAGE:
        preview += f", ... ({len(missing) - _MAX_KEYS_IN_MESSAGE} more)"
    return (
        f"{rel_path} is missing {len(missing)} key(s) present in sibling config file(s) "
        f"({', '.join(sources)}): {preview}"
    )


def _drift_issues_for_group(root, files):
    parsed = {full: keys for full, keys in ((f, _keys_for_file(f)) for f in files) if keys is not None}
    if len(parsed) < 2:
        return []

    all_keys = set()
    for keys in parsed.values():
        all_keys |= keys

    issues = []
    for full, keys in sorted(parsed.items()):
        missing = sorted(all_keys - keys)
        if not missing:
            continue
        rel_path = os.path.relpath(full, root)
        missing_set = set(missing)
        sources = sorted(os.path.relpath(f, root) for f, k in parsed.items() if f != full and k & missing_set)
        issues.append(
            Issue(
                file=rel_path, line=1, category="documentation", severity="warn", symbol=SYMBOL,
                message=_drift_message(rel_path, missing, sources),
            )
        )
    return issues


def check(root):
    """Runs the config-drift check against `root` and returns a flat
    list[Issue]. Never raises; [] if there are no comparable sibling
    config files.
    """
    issues = []
    env_files = _find_root_env_files(root)
    if len(env_files) > 1:
        issues.extend(_drift_issues_for_group(root, env_files))
    for files in _find_config_dir_groups(root):
        issues.extend(_drift_issues_for_group(root, files))
    return issues


def render_text(issues):
    if not issues:
        return "Configuration Drift Check\n\nNo issues found."
    lines = [f"Configuration Drift Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.symbol}] {issue.message}")
    return "\n".join(lines)
