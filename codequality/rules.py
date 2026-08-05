"""Registry of every rule symbol codequality can emit, for `codequality explain`.

Each entry maps the symbol (the `rule`/`symbol` field on issue objects) to
the scoring category it counts against, which command emits it, and a
plain-language description. `tests/test_rules_registry.py` cross-checks
this registry against the symbols the analyzers actually emit, so a new
check that forgets to register here fails the suite.
"""

# scope values: "scan" means scan/diff mode; anything else is the
# standalone subcommand that emits the rule.
RULES = {
    # -- complexity ------------------------------------------------------
    "high-complexity": {
        "category": "complexity", "scope": "scan",
        "description": "Function's cyclomatic (McCabe) complexity exceeds the limit (default 10). Split it into smaller functions.",
    },
    "high-cognitive-complexity": {
        "category": "complexity", "scope": "scan",
        "description": "Function's cognitive complexity (Sonar-style, nesting-weighted) exceeds the limit (default 15). Flattening the deepest branches helps most.",
    },
    "high-complexity-density": {
        "category": "complexity", "scope": "scan",
        "description": "File packs a high total decision count into few lines (non-Python heuristic path). Consider splitting the file.",
    },
    "deep-nesting": {
        "category": "complexity", "scope": "scan",
        "description": "Function nests blocks deeper than the limit (default 4). Use guard clauses or extract the inner levels.",
    },
    "long-function": {
        "category": "complexity", "scope": "scan",
        "description": "Function is longer than the line limit. Long functions hide bugs; extract cohesive chunks.",
    },
    "long-lambda": {
        "category": "complexity", "scope": "scan",
        "description": "Lambda is too long to stay readable inline. Promote it to a named function.",
    },
    "nested-comprehension": {
        "category": "complexity", "scope": "scan",
        "description": "Comprehension nests another comprehension. Unroll into loops or intermediate variables.",
    },
    "too-many-params": {
        "category": "complexity", "scope": "scan",
        "description": "Function takes more parameters than the limit. Group related ones into an object/dataclass.",
    },
    "too-many-return-statements": {
        "category": "complexity", "scope": "scan",
        "description": "Function has many return statements, which makes control flow hard to trace.",
    },
    "god-class": {
        "category": "complexity", "scope": "scan",
        "description": "Class has too many methods/responsibilities. Split it along its natural seams.",
    },
    "complexity-regression": {
        "category": "complexity", "scope": "scan",
        "description": "Diff mode: a function you touched got significantly more complex than it was at the base ref.",
    },
    # -- structure -------------------------------------------------------
    "long-file": {
        "category": "structure", "scope": "scan",
        "description": "File exceeds the line limit. Big files accrete unrelated code; split by responsibility.",
    },
    "circular-import": {
        "category": "structure", "scope": "scan",
        "description": "Module participates in an import cycle. Break the cycle with an interface module or late import.",
    },
    "missing-test-file": {
        "category": "structure", "scope": "scan",
        "description": "Source module has no matching test file under the repo's test naming convention.",
    },
    "scope-mismatch": {
        "category": "structure", "scope": "scan",
        "description": "Name is used at a wider scope than where it's defined (e.g. loop variable used after the loop).",
    },
    "star-import": {
        "category": "structure", "scope": "scan",
        "description": "`from x import *` hides what's actually used and pollutes the namespace. Import names explicitly.",
    },
    "future-import-order": {
        "category": "structure", "scope": "scan",
        "description": "`from __future__ import ...` must be the first statement after the module docstring.",
    },
    "relative-before-absolute": {
        "category": "structure", "scope": "scan",
        "description": "Relative imports appear before absolute ones, against the conventional grouping order.",
    },
    "breaking-signature-change": {
        "category": "structure", "scope": "scan",
        "description": "Diff mode: a public function's signature changed incompatibly (removed/reordered params).",
    },
    # -- documentation ---------------------------------------------------
    "missing-docstring": {
        "category": "documentation", "scope": "scan",
        "description": "Public module/class/function has no docstring.",
    },
    "missing-type-annotations": {
        "category": "documentation", "scope": "scan",
        "description": "Public function has no parameter/return type annotations.",
    },
    "stale-docstring-param": {
        "category": "documentation", "scope": "scan",
        "description": "Docstring documents a parameter the function no longer has (or misnames one).",
    },
    "broken-doc-example": {
        "category": "documentation", "scope": "scan",
        "description": "A code example in docs/README doesn't parse or references symbols that don't exist.",
    },
    # -- style -----------------------------------------------------------
    "long-line": {
        "category": "style", "scope": "scan",
        "description": "Line exceeds the length limit.",
    },
    "trailing-whitespace": {
        "category": "style", "scope": "scan",
        "description": "Line ends with whitespace.",
    },
    "tab-indent": {
        "category": "style", "scope": "scan",
        "description": "Line is indented with tabs in a space-indented file.",
    },
    "bad-function-name": {
        "category": "style", "scope": "scan",
        "description": "Function name doesn't match the repo's dominant naming convention (e.g. snake_case).",
    },
    "bad-class-name": {
        "category": "style", "scope": "scan",
        "description": "Class name doesn't match the repo's dominant naming convention (e.g. PascalCase).",
    },
    "comparison-to-none": {
        "category": "style", "scope": "scan",
        "description": "Uses `== None` / `!= None`; use `is None` / `is not None`.",
    },
    "comparison-to-true": {
        "category": "style", "scope": "scan",
        "description": "Compares explicitly to True/False; use the value's truthiness directly.",
    },
    "magic-number": {
        "category": "style", "scope": "scan",
        "description": "Unexplained numeric literal in logic. Name it as a constant.",
    },
    "redundant-else": {
        "category": "style", "scope": "scan",
        "description": "`else` after a branch that always returns/raises/continues. Dedent the else body.",
    },
    "shadowed-builtin": {
        "category": "style", "scope": "scan",
        "description": "Variable/parameter shadows a Python builtin (list, dict, id, ...). Rename it.",
    },
    "boolean-trap": {
        "category": "style", "scope": "scan",
        "description": "Function takes a bare positional boolean, unreadable at call sites. Make it keyword-only.",
    },
    "f-string-no-placeholder": {
        "category": "style", "scope": "scan",
        "description": "f-string contains no placeholder; drop the f prefix.",
    },
    "implicit-string-concat": {
        "category": "style", "scope": "scan",
        "description": "Adjacent string literals concatenate implicitly — usually a missing comma in a list/tuple.",
    },
    "todo-marker": {
        "category": "style", "scope": "scan",
        "description": "TODO/FIXME/XXX comment. Track it in the issue tracker or resolve it (see also `codequality todo-age`).",
    },
    "placeholder-comment": {
        "category": "style", "scope": "scan",
        "description": "Comment that marks unwritten code (\"implement later\", \"rest goes here\", ...).",
    },
    # -- security --------------------------------------------------------
    "hardcoded-secret": {
        "category": "security", "scope": "scan",
        "description": "String that looks like a credential/API key/token committed in source. Move it to env/secret storage (see also `codequality history-secrets`).",
    },
    "dangerous-eval": {
        "category": "security", "scope": "scan",
        "description": "eval/exec on data that may be attacker-influenced.",
    },
    "shell-true": {
        "category": "security", "scope": "scan",
        "description": "subprocess with shell=True; prefer an argument list to avoid shell injection.",
    },
    "sql-injection-risk": {
        "category": "security", "scope": "scan",
        "description": "SQL built by string formatting/concatenation with runtime values. Use parameterized queries.",
    },
    "unsafe-yaml-load": {
        "category": "security", "scope": "scan",
        "description": "yaml.load without a safe Loader can execute arbitrary Python. Use yaml.safe_load.",
    },
    "unsafe-deserialization": {
        "category": "security", "scope": "scan",
        "description": "pickle/marshal/shelve loading of untrusted data can execute arbitrary code.",
    },
    "weak-hash": {
        "category": "security", "scope": "scan",
        "description": "MD5/SHA-1 used; for anything security-relevant use SHA-256+ (or pass usedforsecurity=False).",
    },
    "insecure-tempfile": {
        "category": "security", "scope": "scan",
        "description": "tempfile.mktemp is race-prone; use NamedTemporaryFile/mkstemp.",
    },
    "assert-as-validation": {
        "category": "security", "scope": "scan",
        "description": "assert used to validate input; asserts vanish under `python -O`. Raise a real exception.",
    },
    "sensitive-data-logging": {
        "category": "security", "scope": "scan",
        "description": "Logging call includes what looks like a password/token/secret value.",
    },
    "fstring-log-arg": {
        "category": "security", "scope": "scan",
        "description": "Logging with pre-formatted f-string; pass lazy %-style args so formatting only happens when the level is enabled.",
    },
    "catastrophic-regex": {
        "category": "security", "scope": "scan",
        "description": "Regex has nested unbounded quantifiers (e.g. (a+)+); a non-matching input triggers exponential ReDoS backtracking.",
    },
    # -- correctness -----------------------------------------------------
    "syntax-error": {
        "category": "correctness", "scope": "scan",
        "description": "File doesn't parse. Everything else about it is unknowable until this is fixed.",
    },
    "bare-except": {
        "category": "correctness", "scope": "scan",
        "description": "`except:` catches SystemExit/KeyboardInterrupt too. Catch specific exceptions.",
    },
    "broad-except-swallow": {
        "category": "correctness", "scope": "scan",
        "description": "`except Exception` whose body discards the error (pass / bare return). At minimum log it.",
    },
    "lost-exception-context": {
        "category": "correctness", "scope": "scan",
        "description": "Raising a new exception inside except without `from err` loses the original traceback.",
    },
    "mutable-default-arg": {
        "category": "correctness", "scope": "scan",
        "description": "Mutable default argument ([], {}, set()) is shared across calls. Default to None and create inside.",
    },
    "mutable-class-attribute": {
        "category": "correctness", "scope": "scan",
        "description": "Mutable class-level attribute is shared by all instances. Initialize it in __init__.",
    },
    "unused-import": {
        "category": "correctness", "scope": "scan",
        "description": "Imported name is never used in the module.",
    },
    "unused-variable": {
        "category": "correctness", "scope": "scan",
        "description": "Local variable is assigned but never read.",
    },
    "unused-dependency": {
        "category": "correctness", "scope": "scan",
        "description": "Declared dependency is never imported anywhere in the repo.",
    },
    "unreachable-code": {
        "category": "correctness", "scope": "scan",
        "description": "Statements after an unconditional return/raise/break/continue can never run.",
    },
    "dead-code": {
        "category": "correctness", "scope": "scan",
        "description": "Module-private function/class is never referenced (see also `codequality dead-code-confidence`).",
    },
    "unclosed-resource": {
        "category": "correctness", "scope": "scan",
        "description": "open()/connect() result is never closed on all paths. Use a with-statement.",
    },
    "unawaited-coroutine": {
        "category": "correctness", "scope": "scan",
        "description": "Coroutine call whose result is discarded without await — the coroutine never runs.",
    },
    "string-concat-in-loop": {
        "category": "correctness", "scope": "scan",
        "description": "`s += ...` in a loop is O(n²); collect parts in a list and ''.join at the end.",
    },
    "query-in-loop": {
        "category": "correctness", "scope": "scan",
        "description": "Database/HTTP call inside a loop (N+1 pattern). Batch the query outside the loop.",
    },
    "deprecated-api": {
        "category": "correctness", "scope": "scan",
        "description": "Call to a stdlib API that is deprecated or removed in current Python versions.",
    },
    "unresolved-import": {
        "category": "correctness", "scope": "scan",
        "description": "Opt-in (--check-imports): import doesn't resolve in this environment — possibly hallucinated or missing from deps.",
    },
    "unresolved-attribute": {
        "category": "correctness", "scope": "scan",
        "description": "Opt-in (--check-imports): attribute doesn't exist on the imported stdlib module.",
    },
    "unresolved-internal-import": {
        "category": "correctness", "scope": "scan",
        "description": "`from <repo-module> import name` where the repo module doesn't define that name.",
    },
    "unresolved-internal-attribute": {
        "category": "correctness", "scope": "scan",
        "description": "`repo_module.attr` where the repo module doesn't define that attribute.",
    },
    "type-error": {
        "category": "correctness", "scope": "scan",
        "description": "Opt-in (--check-types): mypy-reported error folded into the correctness category.",
    },
    "assertion-free-test": {
        "category": "correctness", "scope": "scan",
        "description": "Test function contains no assertion — it can only fail by crashing.",
    },
    "tautological-test": {
        "category": "correctness", "scope": "scan",
        "description": "Assertion is always true (e.g. assert True, assert x == x).",
    },
    "mock-only-test": {
        "category": "correctness", "scope": "scan",
        "description": "Test only asserts against its own mocks, so it verifies the mocks, not the code.",
    },
    "print-in-library-code": {
        "category": "correctness", "scope": "scan",
        "description": "print() left in non-CLI library code; use logging.",
    },
    "stub-implementation": {
        "category": "correctness", "scope": "scan",
        "description": "Function body is only pass/.../NotImplementedError — looks shipped but does nothing.",
    },
    "naive-datetime": {
        "category": "correctness", "scope": "scan",
        "description": "datetime.now()/.today()/.utcnow() called without tz= returns a naive datetime; use timezone-aware calls.",
    },
    "float-equality": {
        "category": "correctness", "scope": "scan",
        "description": "Exact == or != comparison against a float literal; floats rarely compare bit-exact. Use math.isclose().",
    },
    "mutable-global": {
        "category": "correctness", "scope": "scan",
        "description": "Function declares a name `global` and assigns to it; module-level mutable state is order-dependent and hard to test.",
    },
    # -- standalone subcommands ------------------------------------------
    "removed-public-file": {
        "category": "structure", "scope": "api-diff",
        "description": "A file that exported public API at the old ref no longer exists at the new ref.",
    },
    "duplicate-dependency": {
        "category": "correctness", "scope": "dependency-check",
        "description": "Same package declared in more than one requirements source.",
    },
    "inconsistent-pinning": {
        "category": "correctness", "scope": "dependency-check",
        "description": "Some deps pinned exactly, others unpinned, in the same file — pick one strategy.",
    },
    "unpinned-in-lockfile-repo": {
        "category": "correctness", "scope": "dependency-check",
        "description": "Repo has a lockfile but this dependency is declared unpinned.",
    },
    "undocumented-env-var": {
        "category": "documentation", "scope": "env-check",
        "description": "Code reads an environment variable that no .env.example/README documents.",
    },
    "unused-documented-env-var": {
        "category": "documentation", "scope": "env-check",
        "description": "Documented environment variable is never read by the code.",
    },
    "large-file": {
        "category": "structure", "scope": "large-files",
        "description": "Tracked file exceeds the size threshold.",
    },
    "large-binary-file": {
        "category": "structure", "scope": "large-files",
        "description": "Large binary tracked in git; consider Git LFS or excluding it.",
    },
    "orphaned-config-reference": {
        "category": "correctness", "scope": "orphaned-config",
        "description": "Config file references a path/module/script that doesn't exist in the repo.",
    },
    "layering-violation": {
        "category": "structure", "scope": "arch-conformance",
        "description": "Import crosses layers in the wrong direction per the declared architecture rules.",
    },
    "low-bus-factor": {
        "category": "structure", "scope": "ownership",
        "description": "File's git history is dominated by a single author beyond the threshold.",
    },
    "alembic-downgrade-missing": {
        "category": "correctness", "scope": "migration-check",
        "description": "Alembic migration has no downgrade() — it can't be rolled back.",
    },
    "alembic-downgrade-noop": {
        "category": "correctness", "scope": "migration-check",
        "description": "Alembic downgrade() exists but is only `pass` — rollback silently does nothing.",
    },
    "irreversible-django-migration": {
        "category": "correctness", "scope": "migration-check",
        "description": "Django migration uses RunPython/RunSQL without a reverse operation.",
    },
    "sql-migration-missing-down": {
        "category": "correctness", "scope": "migration-check",
        "description": "SQL migration has an up script but no matching down/rollback script.",
    },
    "config-drift": {
        "category": "correctness", "scope": "config-drift",
        "description": "Key present in one sibling environment config file but missing from another — likely a forgotten addition.",
    },
}


def get_rule(symbol):
    """Return the registry entry for `symbol`, or None if unknown."""
    return RULES.get(symbol)


def all_rules():
    """Return (symbol, entry) pairs sorted by category then symbol."""
    return sorted(RULES.items(), key=lambda kv: (kv[1]["category"], kv[0]))
