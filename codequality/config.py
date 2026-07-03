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
        "complexity": 30,
        "structure": 20,
        "duplication": 15,
        "documentation": 15,
        "style": 20,
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
}


class Limits:
    """Attribute-style view over config["limits"] for readable call sites."""

    def __init__(self, d):
        self.__dict__.update(d)


class Config:
    def __init__(self, data):
        merged = _deep_merge(DEFAULT_CONFIG, data or {})
        self.weights = merged["weights"]
        self.limits = Limits(merged["limits"])
        self.fail_under = merged["thresholds"]["fail_under"]
        self.exclude = list(merged["exclude"])
        self.include_generic_languages = merged["include_generic_languages"]

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
