"""Tests for codequality.config_validate."""

import json
import os
import tempfile

import pytest

from codequality.config_validate import validate, render_text


def _write_json(data):
    f = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    json.dump(data, f)
    f.close()
    return f.name


def _severities(issues):
    return [i.severity for i in issues]


def _messages(issues):
    return [i.message for i in issues]


def test_no_config_file_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        path, issues = validate(root=d)
    assert path is None
    assert issues == []


def test_valid_empty_config():
    p = _write_json({})
    try:
        path, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert errors == []
    finally:
        os.unlink(p)


def test_unknown_key_warns():
    p = _write_json({"unknown_key": True})
    try:
        _, issues = validate(explicit_path=p)
        assert any("unknown_key" in i.message for i in issues)
        assert any(i.severity == "warn" for i in issues)
    finally:
        os.unlink(p)


def test_wrong_type_errors():
    p = _write_json({"check_imports": "yes"})
    try:
        _, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert any("check_imports" in i.message for i in errors)
    finally:
        os.unlink(p)


def test_invalid_weight_category_warns():
    p = _write_json({"weights": {"nonexistent_category": 5}})
    try:
        _, issues = validate(explicit_path=p)
        assert any("nonexistent_category" in i.message for i in issues)
    finally:
        os.unlink(p)


def test_negative_weight_errors():
    p = _write_json({"weights": {"style": -1}})
    try:
        _, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert any("style" in i.message for i in errors)
    finally:
        os.unlink(p)


def test_invalid_limit_key_warns():
    p = _write_json({"limits": {"made_up_limit": 10}})
    try:
        _, issues = validate(explicit_path=p)
        assert any("made_up_limit" in i.message for i in issues)
    finally:
        os.unlink(p)


def test_fail_under_out_of_range_errors():
    p = _write_json({"thresholds": {"fail_under": 150}})
    try:
        _, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert any("fail_under" in i.message for i in errors)
    finally:
        os.unlink(p)


def test_pipeline_step_missing_name_errors():
    p = _write_json({"pipeline": {"steps": [{"command": "pytest"}]}})
    try:
        _, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert any("name" in i.message for i in errors)
    finally:
        os.unlink(p)


def test_pipeline_step_missing_command_errors():
    p = _write_json({"pipeline": {"steps": [{"name": "test"}]}})
    try:
        _, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert any("command" in i.message for i in errors)
    finally:
        os.unlink(p)


def test_architecture_layer_missing_name_errors():
    p = _write_json({"architecture": {"layers": [{"modules": ["myapp.api"]}]}})
    try:
        _, issues = validate(explicit_path=p)
        errors = [i for i in issues if i.severity == "error"]
        assert any("name" in i.message for i in errors)
    finally:
        os.unlink(p)


def test_coverage_contradiction_info():
    p = _write_json({"weights": {"coverage": 15}})
    try:
        _, issues = validate(explicit_path=p)
        assert any(i.severity == "info" and "coverage" in i.message for i in issues)
    finally:
        os.unlink(p)


def test_render_text_no_issues():
    text = render_text("/path/to/config.toml", [])
    assert "Config OK" in text


def test_render_text_with_issues():
    from codequality.config_validate import ConfigIssue
    issues = [ConfigIssue("error", "something bad"), ConfigIssue("warn", "something iffy")]
    text = render_text("/path/to/config.toml", issues)
    assert "ERROR" in text
    assert "WARN" in text
    assert "something bad" in text
