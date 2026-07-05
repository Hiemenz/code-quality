"""Structural consistency checks on dependency manifests.

This is deliberately *not* a vulnerability or staleness scanner: it never
makes a network call, never asks a registry "what's the latest version,"
and never has an opinion on whether a pinned version is old or has a CVE.
Doing any of that would break this tool's core "no network access, ever"
promise (see README). Everything here is a purely offline, structural read
of the dependency declarations as they already exist in the repo:

- **`inconsistent-pinning`** -- most of a manifest's dependencies are
  pinned to an exact version but a few aren't (or vice versa). The
  inconsistency itself is the signal, not "unpinned is bad."
- **`duplicate-dependency`** -- the same package declared in more than one
  manifest (e.g. `requirements.txt` and `requirements-dev.txt`, or
  `dependencies` and `devDependencies`) with *different* version specs --
  a real source of "works on my machine" bugs.
- **`unpinned-in-lockfile-repo`** -- a repo that already has a lockfile
  (implying it cares about reproducible installs) has a manifest entry
  with no version constraint at all. Best-effort and lower-confidence by
  design: lockfile contents are never parsed, only their presence checked.

Categorized as `style` (not `correctness`): every other `correctness`
check in this tool (unresolved imports, mypy errors, assertion-free tests,
unreachable code) is about whether code *behaves* correctly. These checks
are about whether dependency *declarations* are internally consistent --
closer in spirit to the naming/consistency conventions already scored
under `style` than to a behavioral-correctness question. `dependency-check`
is also a standalone subcommand (like `churn`/`mutation`), not folded into
`scan`'s score, so the category mainly keeps the `Issue` shape consistent
with the rest of the tool.

TOML parsing uses the stdlib `tomllib` (Python >= 3.11), following the
same graceful-skip convention as `codequality/config.py`: on older
interpreters, `pyproject.toml` is simply skipped rather than the tool
hard-failing.
"""

import glob
import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None

from codequality.analyzers.base import Issue

PINNING_THRESHOLD = 0.7  # >= this fraction pinned (or <= 1 - this fraction) triggers inconsistent-pinning
MIN_DEPS_FOR_PINNING_CHECK = 5  # don't flag tiny manifests where one dep swings the ratio

LOCKFILE_NAMES = ("package-lock.json", "poetry.lock", "Pipfile.lock", "uv.lock")

_REQ_ENTRY_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*(.*)$")
_NPM_RANGE_PREFIXES = ("^", "~", ">", "<", "=", "*", "x", "X")


@dataclass
class Dependency:
    name: str  # normalized (lowercased, -/_/. collapsed to '-') for grouping/matching
    raw_name: str  # as declared in the manifest
    spec: str  # raw version spec/range string, "" if no constraint at all
    pinned: bool  # exact version ("==1.2.3", bare "1.2.3") vs. a range/no constraint
    manifest: str  # relative path to the file this came from
    section: str  # human-readable source label, e.g. "package.json:devDependencies"
    line: int  # 1-based line number, or 1 if unknown (TOML/JSON don't expose one easily)


def find_manifests(root):
    """Every dependency manifest this check knows how to read, relative to `root`."""
    found = []
    for pattern in ("requirements*.txt", os.path.join("requirements", "*.txt")):
        for path in sorted(glob.glob(os.path.join(root, pattern))):
            if os.path.isfile(path):
                found.append(os.path.relpath(path, root))
    if os.path.isfile(os.path.join(root, "pyproject.toml")):
        found.append("pyproject.toml")
    if os.path.isfile(os.path.join(root, "package.json")):
        found.append("package.json")
    return found


def _normalize_name(name):
    return re.sub(r"[-_.]+", "-", name.strip()).lower()


def _classify_pip_spec(spec):
    """Returns (pinned, spec). Pinned: '==1.2.3'/'===1.2.3', or a bare
    version with no operator at all. Anything else (>=, <=, ~=, !=, a
    comma-separated range, or no spec at all) is unpinned.
    """
    spec = spec.strip()
    if not spec:
        return False, spec
    if re.fullmatch(r"===?[A-Za-z0-9.+!_-]+", spec):
        return True, spec
    if re.fullmatch(r"[A-Za-z0-9.+!_-]+", spec):
        return True, spec
    return False, spec


def _classify_npm_spec(spec):
    """Returns (pinned, spec). Pinned: a bare semver-ish string with no
    range operator prefix. Anything with ^/~/>/</*, a non-registry source
    (file:/git/http/workspace:/link:), or a complex range is unpinned.
    """
    spec = spec.strip()
    if not spec or spec in ("*", "latest", "next"):
        return False, spec
    if spec.startswith(("file:", "git", "http", "workspace:", "link:")):
        return False, spec
    if spec[0] in _NPM_RANGE_PREFIXES:
        return False, spec
    if " " in spec or "||" in spec:
        return False, spec
    return True, spec


def _parse_requirement_line(line):
    """Parses one PEP 508-ish requirement string (a requirements.txt line,
    minus comments, or one pyproject.toml dependency string) into
    (raw_name, spec) or None if it doesn't look like a plain name/spec pair.
    """
    body = line.split(";", 1)[0].strip()  # drop environment markers
    if not body:
        return None
    m = _REQ_ENTRY_RE.match(body)
    if not m:
        return None
    return m.group(1), m.group(3).strip()


def _parse_requirements_file(root, rel_path):
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    deps = []
    for i, raw_line in enumerate(lines, start=1):
        line = raw_line.split("#", 1)[0].strip()
        if not line or line.startswith("-") or "://" in line:
            continue  # blank, -r/-e/--hash/etc., or a VCS/URL requirement
        parsed = _parse_requirement_line(line)
        if parsed is None:
            continue
        raw_name, spec = parsed
        pinned, spec = _classify_pip_spec(spec)
        deps.append(Dependency(
            name=_normalize_name(raw_name), raw_name=raw_name, spec=spec, pinned=pinned,
            manifest=rel_path, section=rel_path, line=i,
        ))
    return deps


def _pep508_to_dep(spec_str, rel_path, section_id):
    parsed = _parse_requirement_line(spec_str)
    if parsed is None:
        return None
    raw_name, spec = parsed
    pinned, spec = _classify_pip_spec(spec)
    return Dependency(
        name=_normalize_name(raw_name), raw_name=raw_name, spec=spec, pinned=pinned,
        manifest=rel_path, section=section_id, line=1,
    )


def _load_toml(full_path):
    try:
        with open(full_path, "rb") as f:
            return tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError):
        return None


def _parse_dep_list(specs, rel_path, section_id):
    parsed = (_pep508_to_dep(s, rel_path, section_id) for s in specs if isinstance(s, str))
    return [d for d in parsed if d is not None]


def _pyproject_main_section(project, rel_path):
    main_deps = project.get("dependencies")
    if not isinstance(main_deps, list) or not main_deps:
        return {}
    section_id = f"{rel_path}[project.dependencies]"
    return {section_id: _parse_dep_list(main_deps, rel_path, section_id)}


def _pyproject_optional_sections(project, rel_path):
    optional = project.get("optional-dependencies")
    if not isinstance(optional, dict):
        return {}
    sections = {}
    for extra, specs in optional.items():
        if not isinstance(specs, list) or not specs:
            continue
        section_id = f"{rel_path}[project.optional-dependencies.{extra}]"
        sections[section_id] = _parse_dep_list(specs, rel_path, section_id)
    return sections


def _parse_pyproject(root, rel_path):
    """Returns {section_id: [Dependency, ...]} for [project.dependencies]
    and each [project.optional-dependencies.<extra>] list. Empty (not a
    crash) if tomllib isn't available on this interpreter, or the file
    doesn't parse.
    """
    if tomllib is None:
        return {}
    data = _load_toml(os.path.join(root, rel_path))
    if data is None:
        return {}
    project = data.get("project", {})
    sections = {}
    sections.update(_pyproject_main_section(project, rel_path))
    sections.update(_pyproject_optional_sections(project, rel_path))
    return sections


def _parse_package_json(root, rel_path):
    """Returns {section_id: [Dependency, ...]} for `dependencies` and
    `devDependencies`. Empty (not a crash) if the file isn't valid JSON.
    """
    full = os.path.join(root, rel_path)
    try:
        with open(full, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}

    sections = {}
    for key in ("dependencies", "devDependencies"):
        block = data.get(key)
        if not isinstance(block, dict) or not block:
            continue
        section_id = f"{rel_path}:{key}"
        deps = []
        for raw_name, spec in block.items():
            if not isinstance(spec, str):
                continue
            pinned, cleaned_spec = _classify_npm_spec(spec)
            deps.append(Dependency(
                name=_normalize_name(raw_name), raw_name=raw_name, spec=cleaned_spec, pinned=pinned,
                manifest=rel_path, section=section_id, line=1,
            ))
        sections[section_id] = deps
    return sections


def _collect_deps_by_section(root):
    """{section_id: [Dependency, ...]} across every manifest found in `root`."""
    deps_by_section = {}
    for rel_path in find_manifests(root):
        if rel_path.endswith(".json"):
            deps_by_section.update(_parse_package_json(root, rel_path))
        elif rel_path.endswith(".toml"):
            deps_by_section.update(_parse_pyproject(root, rel_path))
        else:
            deps = _parse_requirements_file(root, rel_path)
            if deps:
                deps_by_section[rel_path] = deps
    return deps_by_section


def _pinning_outliers(deps):
    """For one section's dependencies, returns (outliers, ratio, label) if
    pinning is skewed >= PINNING_THRESHOLD one way or the other, else None.
    """
    if len(deps) < MIN_DEPS_FOR_PINNING_CHECK:
        return None
    ratio = sum(1 for d in deps if d.pinned) / len(deps)
    if ratio >= PINNING_THRESHOLD:
        return [d for d in deps if not d.pinned], ratio, "exactly pinned"
    if ratio <= 1 - PINNING_THRESHOLD:
        return [d for d in deps if d.pinned], ratio, "a range or unpinned"
    return None


def _pinning_issue(d, section_id, ratio, norm):
    return Issue(
        file=d.manifest, line=d.line, category="style", severity="info",
        symbol="inconsistent-pinning",
        message=(
            f"{d.raw_name} ({d.spec or 'no version constraint'}) breaks from the pattern in "
            f"{section_id}, where {ratio * 100:.0f}% of dependencies are {norm}"
        ),
    )


def _check_inconsistent_pinning(deps_by_section):
    issues = []
    for section_id, deps in deps_by_section.items():
        result = _pinning_outliers(deps)
        if result is None:
            continue
        outliers, ratio, norm = result
        issues.extend(_pinning_issue(d, section_id, ratio, norm) for d in outliers)
    return issues


def _group_by_ecosystem_and_name(deps_by_section):
    by_key = defaultdict(list)
    for deps in deps_by_section.values():
        for d in deps:
            ecosystem = "npm" if d.manifest.endswith(".json") else "pip"
            by_key[(ecosystem, d.name)].append(d)
    return by_key


def _duplicate_issue(d, deps):
    others = sorted(f"{o.section} ({o.spec or 'unpinned'})" for o in deps if o.section != d.section)
    return Issue(
        file=d.manifest, line=d.line, category="style", severity="warn",
        symbol="duplicate-dependency",
        message=(
            f"{d.raw_name} is '{d.spec or 'unpinned'}' here but declared differently in "
            f"{', '.join(others)} -- a source of install-order-dependent bugs"
        ),
    )


def _check_duplicate_dependency(deps_by_section):
    issues = []
    for deps in _group_by_ecosystem_and_name(deps_by_section).values():
        sources = {d.section for d in deps}
        specs = {d.spec for d in deps}
        if len(sources) <= 1 or len(specs) <= 1:
            continue  # only declared in one place, or declared identically everywhere
        issues.extend(_duplicate_issue(d, deps) for d in deps)
    return issues


def _has_lockfile(root):
    if any(os.path.isfile(os.path.join(root, name)) for name in LOCKFILE_NAMES):
        return True
    return bool(glob.glob(os.path.join(root, "*.lock")))


def _check_unpinned_in_lockfile_repo(root, deps_by_section):
    if not _has_lockfile(root):
        return []
    issues = []
    for section_id, deps in deps_by_section.items():
        for d in deps:
            if d.spec:
                continue  # has *some* constraint; this rule is only about totally bare deps
            issues.append(Issue(
                file=d.manifest, line=d.line, category="style", severity="info",
                symbol="unpinned-in-lockfile-repo",
                message=(
                    f"{d.raw_name} has no version constraint in {section_id}, but this repo has a lockfile "
                    f"present -- lower-confidence, best-effort signal (lockfile contents aren't parsed)"
                ),
            ))
    return issues


def check(root):
    """Runs every dependency-manifest check against `root` and returns a
    flat list[Issue]. Returns [] (never raises) when no manifest is found.
    """
    deps_by_section = _collect_deps_by_section(root)
    issues = []
    issues.extend(_check_inconsistent_pinning(deps_by_section))
    issues.extend(_check_duplicate_dependency(deps_by_section))
    issues.extend(_check_unpinned_in_lockfile_repo(root, deps_by_section))
    return issues


def render_text(issues):
    if not issues:
        return "Dependency Check\n\nNo issues found."
    lines = [f"Dependency Check ({len(issues)} issue(s))", ""]
    for issue in sorted(issues, key=lambda i: (i.file, i.line, i.symbol)):
        lines.append(f"  {issue.file}:{issue.line} [{issue.symbol}] {issue.message}")
    return "\n".join(lines)
