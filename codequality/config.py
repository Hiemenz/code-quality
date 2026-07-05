"""Configuration: built-in defaults, overridable via .codequality.toml,
.codequality.json, or a [tool.codequality] table in pyproject.toml.

Kept dependency-free: uses the stdlib `tomllib` (Python >= 3.11). On older
interpreters, TOML config is skipped with a warning and JSON/defaults still
work -- the tool never hard-fails just because TOML parsing isn't available.
"""

import json
import os

try:
    import tomllib
except ImportError:  # pragma: no cover - Python < 3.11
    tomllib = None

DEFAULT_IGNORE_DIRS = {
    ".git",
    "node_modules",
    "venv",
    ".venv",
    "env",
    "__pycache__",
    "dist",
    "build",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "vendor",
    "target",
    ".idea",
    ".vscode",
    "coverage",
    ".next",
    "egg-info",
}

PYTHON_EXTENSIONS = {".py"}

GENERIC_EXTENSIONS = {
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".java": "java",
    ".go": "go",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".hpp": "cpp",
    ".cs": "csharp",
    ".rb": "ruby",
    ".php": "php",
    ".rs": "rust",
    ".kt": "kotlin",
    ".swift": "swift",
    ".scala": "scala",
}

DEFAULT_CONFIG = {
    "weights": {
        "complexity": 15,
        "structure": 10,
        "duplication": 10,
        "documentation": 8,
        "style": 12,
        "security": 15,
        "correctness": 15,
        "coverage": 15,
    },
    "limits": {
        "max_line_length": 120,
        "max_function_lines": 60,
        "max_file_lines": 600,
        "max_complexity": 10,
        "max_nesting": 4,
        "docstring_min_lines": 8,  # don't demand docstrings on tiny helpers
    },
    "thresholds": {
        "fail_under": 60,
    },
    "exclude": [],
    "include_generic_languages": True,
    # All three are opt-in (not part of a plain `scan`) because they either
    # depend on the environment codequality itself runs in, or (coverage)
    # actually execute the target repo's own test suite -- a different
    # trust boundary than every other check in this tool. See README.
    "check_imports": False,
    "check_types": False,
    "check_coverage": False,
    "test_command": "unittest discover -s tests",
    # Generated files (protobuf `_pb2.py`, migration scripts, OpenAPI client
    # stubs, ...) are auto-detected (see codequality/generated_code.py) and
    # excluded from scoring by default, the same way config.exclude globs
    # are. Set True (or pass --include-generated) to score them anyway.
    "include_generated": False,
    # External steps (format/lint/test/benchmark/...) that `codequality
    # pipeline` runs before folding its own scan in as one more step --
    # see PipelineStep and codequality/pipeline.py. Empty by default: this
    # tool never assumes which formatter/linter/benchmark a repo uses.
    "pipeline": {
        "steps": [],
    },
}


class Limits:
    """Attribute-style view over config["limits"] for readable call sites."""

    def __init__(self, d):
        self.__dict__.update(d)


class PipelineStep:
    """Attribute-style view over one [[pipeline.steps]] config entry.

    `command` is run via `subprocess.run(shlex.split(command), ...)` --
    never shell=True, so config-file content can't inject shell syntax.
    """

    def __init__(self, d):
        self.name = d["name"]
        self.command = d["command"]
        self.allow_failure = d.get("allow_failure", False)


class Config:
    def __init__(self, data):
        merged = _deep_merge(DEFAULT_CONFIG, data or {})
        self.weights = merged["weights"]
        self.limits = Limits(merged["limits"])
        self.fail_under = merged["thresholds"]["fail_under"]
        self.exclude = list(merged["exclude"])
        self.include_generic_languages = merged["include_generic_languages"]
        self.check_imports = merged["check_imports"]
        self.check_types = merged["check_types"]
        self.check_coverage = merged["check_coverage"]
        self.test_command = merged["test_command"]
        self.include_generated = merged["include_generated"]
        self.pipeline_steps = [PipelineStep(s) for s in merged["pipeline"]["steps"]]

    @classmethod
    def load(cls, root, explicit_path=None, overrides=None):
        data = {}
        path = explicit_path or _find_config_file(root)
        if path:
            data = _read_config_file(path)
        if overrides:
            data = _deep_merge(data, overrides)
        return cls(data)


def _deep_merge(base, override):
    result = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _find_config_file(root):
    for name in (".codequality.toml", ".codequality.json"):
        candidate = os.path.join(root, name)
        if os.path.isfile(candidate):
            return candidate
    pyproject = os.path.join(root, "pyproject.toml")
    if os.path.isfile(pyproject) and tomllib is not None:
        with open(pyproject, "rb") as f:
            data = tomllib.load(f)
        if "tool" in data and "codequality" in data["tool"]:
            return pyproject
    return None


def _read_config_file(path):
    if path.endswith(".json"):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    if tomllib is None:
        print(f"warning: {path} found but TOML support requires Python 3.11+; ignoring")
        return {}
    with open(path, "rb") as f:
        data = tomllib.load(f)
    if os.path.basename(path) == "pyproject.toml":
        return data.get("tool", {}).get("codequality", {})
    return data
