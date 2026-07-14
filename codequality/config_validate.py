"""Structural validation of a codequality config file.

Checks that every key is recognised, every value is the right type, and no
obviously contradictory settings are present.  Purely static: no scanning,
no network access.

The canonical set of valid keys and types is derived directly from
`config.DEFAULT_CONFIG` so this module stays in sync without a separate
allowlist to maintain.
"""

from dataclasses import dataclass

from codequality.config import DEFAULT_CONFIG, _find_config_file, _read_config_file


@dataclass
class ConfigIssue:
    severity: str   # "error" | "warn" | "info"
    message: str

    def to_dict(self):
        return {"severity": self.severity, "message": self.message}


_KNOWN_WEIGHT_CATEGORIES = frozenset(DEFAULT_CONFIG["weights"])
_KNOWN_LIMIT_KEYS = frozenset(DEFAULT_CONFIG["limits"])
_KNOWN_TOP_KEYS = frozenset(DEFAULT_CONFIG)


def _check_types(issues, data):
    """Flag values whose types don't match the defaults."""
    type_map = {
        "include_generic_languages": bool,
        "check_imports": bool,
        "check_types": bool,
        "check_coverage": bool,
        "include_generated": bool,
        "test_command": str,
        "exclude": list,
        "weights": dict,
        "limits": dict,
        "thresholds": dict,
        "pipeline": dict,
        "architecture": dict,
    }
    for key, expected in type_map.items():
        if key not in data:
            continue
        if not isinstance(data[key], expected):
            actual = type(data[key]).__name__
            issues.append(ConfigIssue(
                "error",
                f"'{key}' should be {expected.__name__}, got {actual}"
            ))


def _check_unknown_keys(issues, data):
    for key in data:
        if key not in _KNOWN_TOP_KEYS:
            issues.append(ConfigIssue("warn", f"Unknown config key '{key}' -- will be ignored"))


def _check_weights(issues, data):
    weights = data.get("weights")
    if not isinstance(weights, dict):
        return
    for cat in weights:
        if cat not in _KNOWN_WEIGHT_CATEGORIES:
            issues.append(ConfigIssue("warn", f"weights.{cat} is not a recognised category"))
        elif not isinstance(weights[cat], (int, float)):
            issues.append(ConfigIssue("error", f"weights.{cat} must be a number"))
        elif weights[cat] < 0:
            issues.append(ConfigIssue("error", f"weights.{cat} must be >= 0"))


def _check_limits(issues, data):
    limits = data.get("limits")
    if not isinstance(limits, dict):
        return
    for key in limits:
        if key not in _KNOWN_LIMIT_KEYS:
            issues.append(ConfigIssue("warn", f"limits.{key} is not a recognised limit key"))
        elif not isinstance(limits[key], (int, float)):
            issues.append(ConfigIssue("error", f"limits.{key} must be a number"))
        elif limits[key] <= 0:
            issues.append(ConfigIssue("error", f"limits.{key} must be > 0"))


def _check_thresholds(issues, data):
    thresholds = data.get("thresholds")
    if not isinstance(thresholds, dict):
        return
    fail_under = thresholds.get("fail_under")
    if fail_under is not None:
        if not isinstance(fail_under, (int, float)):
            issues.append(ConfigIssue("error", "thresholds.fail_under must be a number"))
        elif not (0 <= fail_under <= 100):
            issues.append(ConfigIssue("error", "thresholds.fail_under must be between 0 and 100"))


def _check_pipeline(issues, data):
    pipeline = data.get("pipeline")
    if not isinstance(pipeline, dict):
        return
    steps = pipeline.get("steps", [])
    if not isinstance(steps, list):
        issues.append(ConfigIssue("error", "pipeline.steps must be a list"))
        return
    for i, step in enumerate(steps):
        if not isinstance(step, dict):
            issues.append(ConfigIssue("error", f"pipeline.steps[{i}] must be a table/dict"))
            continue
        if "name" not in step:
            issues.append(ConfigIssue("error", f"pipeline.steps[{i}] is missing required key 'name'"))
        if "command" not in step:
            issues.append(ConfigIssue("error", f"pipeline.steps[{i}] is missing required key 'command'"))


def _check_architecture(issues, data):
    arch = data.get("architecture")
    if not isinstance(arch, dict):
        return
    layers = arch.get("layers", [])
    if not isinstance(layers, list):
        issues.append(ConfigIssue("error", "architecture.layers must be a list"))
        return
    for i, layer in enumerate(layers):
        if not isinstance(layer, dict):
            issues.append(ConfigIssue("error", f"architecture.layers[{i}] must be a table/dict"))
            continue
        if "name" not in layer:
            issues.append(ConfigIssue("error", f"architecture.layers[{i}] is missing required key 'name'"))
        if "modules" not in layer:
            issues.append(ConfigIssue("error", f"architecture.layers[{i}] is missing required key 'modules'"))
        elif not isinstance(layer["modules"], list):
            name = layer.get("name", i)
            issues.append(ConfigIssue("error", f"architecture.layers '{name}'.modules must be a list of strings"))


def _check_contradictions(issues, data):
    """Flag combinations that are always no-ops or contradictory."""
    weights = data.get("weights", {})
    if isinstance(weights, dict):
        coverage_w = weights.get("coverage", DEFAULT_CONFIG["weights"]["coverage"])
        check_coverage = data.get("check_coverage", False)
        if coverage_w and coverage_w > 0 and not check_coverage:
            issues.append(ConfigIssue(
                "info",
                "weights.coverage > 0 but check_coverage is false -- "
                "coverage score will always be 100 unless --check-coverage is passed"
            ))


def validate(root=None, explicit_path=None):
    """Validate the config file found in `root` (or `explicit_path`).

    Returns ``(path, issues)`` where `path` is the config file used (or None
    if no config was found) and `issues` is a (possibly empty) list of
    ConfigIssue.
    """
    path = explicit_path or (root and _find_config_file(root))
    if not path:
        return None, []

    try:
        data = _read_config_file(path)
    except Exception as exc:
        return path, [ConfigIssue("error", f"Could not parse config file: {exc}")]

    issues = []
    _check_unknown_keys(issues, data)
    _check_types(issues, data)
    _check_weights(issues, data)
    _check_limits(issues, data)
    _check_thresholds(issues, data)
    _check_pipeline(issues, data)
    _check_architecture(issues, data)
    _check_contradictions(issues, data)
    return path, issues


def render_text(path, issues):
    if not issues:
        return f"Config OK: {path}" if path else "No config file found -- using defaults."
    lines = [f"Config: {path}", ""]
    for issue in issues:
        lines.append(f"  [{issue.severity.upper():<5}] {issue.message}")
    return "\n".join(lines)
