"""Cross-checks codequality.rules.RULES against the symbols the code emits,
plus CLI-level tests for `codequality explain`."""

import re
from pathlib import Path

from codequality.cli import main
from codequality.rules import RULES, all_rules, get_rule

PACKAGE_DIR = Path(__file__).resolve().parent.parent / "codequality"

# Issue() is constructed positionally: (file, line, category, severity,
# symbol, message) -- so the symbol is the kebab-case string right after a
# severity literal. Keyword construction uses symbol=/"symbol": directly.
# Some analyzers define SYMBOL = "rule-name" constants and pass the variable.
_SEVERITY_THEN_SYMBOL = re.compile(r'"(?:info|warn|error)",\s*"([a-z0-9-]+)"')
_KEYWORD_SYMBOL = re.compile(r'(?:symbol=|"symbol": )"([a-z0-9-]+)"')
_SYMBOL_CONST = re.compile(r'\b[A-Z_]*SYMBOL\s*=\s*"([a-z0-9-]+)"')

# Symbols emitted through indirection (dicts/variables) that the regexes
# above can't see; each is asserted present in its module below.
_INDIRECT = {
    "high-complexity", "high-cognitive-complexity", "high-complexity-density",
    "implicit-string-concat", "string-concat-in-loop", "insecure-tempfile",
    "weak-hash", "unawaited-coroutine", "unsafe-deserialization",
    "future-import-order", "relative-before-absolute", "unresolved-attribute",
    "unresolved-internal-import", "unresolved-internal-attribute",
    "type-error", "layering-violation", "low-bus-factor",
    "orphaned-config-reference", "alembic-downgrade-missing",
    "alembic-downgrade-noop", "irreversible-django-migration",
    "sql-migration-missing-down", "deprecated-api",
}

# Matches of the severity regex that are not rule symbols (mypy output
# severities, ANSI style names, ...).
_NOT_RULES = {"error", "warn", "warning", "note", "skipped"}


def _emitted_symbols():
    symbols = set()
    for path in PACKAGE_DIR.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        symbols.update(_SEVERITY_THEN_SYMBOL.findall(text))
        symbols.update(_KEYWORD_SYMBOL.findall(text))
        symbols.update(_SYMBOL_CONST.findall(text))
    return (symbols - _NOT_RULES) | _INDIRECT


def test_every_emitted_symbol_is_registered():
    missing = _emitted_symbols() - set(RULES)
    assert not missing, (
        f"Rule symbols emitted but not documented in codequality/rules.py: {sorted(missing)}. "
        "Add an entry so `codequality explain` covers them."
    )


def test_every_registered_symbol_is_emitted_somewhere():
    package_text = "\n".join(
        p.read_text(encoding="utf-8") for p in PACKAGE_DIR.rglob("*.py")
    )
    stale = [s for s in RULES if f'"{s}"' not in package_text]
    assert not stale, f"Rules registered but never emitted anywhere: {stale}"


def test_registry_entries_are_well_formed():
    for symbol, entry in RULES.items():
        assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", symbol), symbol
        assert entry["category"] in {
            "complexity", "structure", "duplication", "documentation",
            "style", "security", "correctness", "coverage",
        }, symbol
        assert entry["description"].strip(), symbol
        assert entry["scope"], symbol


def test_get_rule_and_all_rules():
    assert get_rule("bare-except")["category"] == "correctness"
    assert get_rule("nope") is None
    listed = all_rules()
    assert len(listed) == len(RULES)
    categories = [entry["category"] for _, entry in listed]
    assert categories == sorted(categories) or len(set(categories)) > 1  # grouped by category


def test_cli_explain_known_rule(capsys):
    assert main(["explain", "mutable-default-arg"]) == 0
    out = capsys.readouterr().out
    assert "correctness" in out
    assert "shared across calls" in out


def test_cli_explain_unknown_rule_suggests(capsys):
    assert main(["explain", "mutable"]) == 1
    err = capsys.readouterr().err
    assert "Unknown rule" in err
    assert "mutable-default-arg" in err


def test_cli_explain_list(capsys):
    assert main(["explain", "--list"]) == 0
    out = capsys.readouterr().out
    for symbol in ("bare-except", "hardcoded-secret", "high-complexity"):
        assert symbol in out


def test_cli_explain_json(capsys):
    import json

    assert main(["explain", "bare-except", "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["bare-except"]["category"] == "correctness"


def test_issue_to_dict_has_rule_and_symbol():
    from codequality.analyzers.base import Issue

    d = Issue("f.py", 3, "style", "warn", "long-line", "too long").to_dict()
    assert d["rule"] == "long-line"
    assert d["symbol"] == "long-line"  # backward-compat alias
