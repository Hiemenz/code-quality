# codequality

A deterministic, programmatic code quality scanner. Think "Grammarly for
code": it reads your repo and gives you a 0-100 score with a letter grade,
broken down by category, plus a list of specific issues with file:line
locations.

No LLM calls, no network access, no required external dependencies. Every
number comes from parsing the code (Python via `ast`, other languages via
`tree-sitter` if installed, otherwise heuristics) and running it through
fixed, documented formulas. The same input always produces the same
output — safe to gate a CI pipeline on.

Two modes:

- **`scan`** — score the whole repository. Use this to track overall
  codebase health over time.
- **`diff`** — score only the code that changed (via `git diff`). Use this
  to gate pull requests without requiring the entire repo to be clean
  first.

Both work in a pipeline (JSON/markdown output, exit codes for gating) and
standalone on your machine (colored terminal output).

## Intent

Linters catch style violations; type checkers catch type errors. Neither
answers the question a reviewer actually asks: is this change, or this
codebase, in good shape? `codequality` exists to turn that judgment call
into a number you can compute the same way every time, so it can gate a
merge instead of just informing one person's opinion.

That leads to a few deliberate choices:

- **Deterministic over clever.** Every check is a fixed formula over
  parsed code or git history — no LLM calls, no network access, no
  run-to-run variance. A score you can't reproduce isn't a score you can
  gate CI on.
- **Diff-scoped by default.** Most repos have pre-existing debt that no
  one wants to clean up before merging an unrelated PR. `diff` scores
  only what changed, so new code is held to a bar without requiring the
  whole repo to be clean first; `scan` is there separately for tracking
  overall health over time.
- **Breadth over a single metric.** A "quality score" that only measures
  complexity misses secrets committed to git history, dependencies that
  are declared but unused, tests that run code without asserting
  anything, config drift between environments, and dozens of other
  failure modes that are individually easy to check for and easy to miss
  by hand. The checks accumulate rather than replace each other.
- **AI-authorship-aware, not AI-powered.** As more commits are AI-assisted,
  several checks (hallucinated imports, edit-distance survival, rework
  rate) specifically compare AI-assisted vs. human commits using git
  blame — without ever calling an LLM to do it.

## Install

```bash
pip install .
```

Or run it with zero install, straight from a checkout:

```bash
python3 -m codequality scan .
```

### Installing it into a different repo

`codequality` isn't published to PyPI; install it straight from git into
whatever repo you want scored:

```bash
pip install "git+https://github.com/Hiemenz/code-quality.git"

# for real per-function parsing of JS/TS/Go/Java/C/C++/C#/Ruby/PHP/Rust/
# Kotlin/Swift/Scala instead of the heuristic fallback (see "Language
# support" below):
pip install "git+https://github.com/Hiemenz/code-quality.git#egg=codequality[treesitter]"

# other optional extras, each backing one opt-in check -- see
# "Correctness checks", "Test coverage", and "Mutation testing" below:
pip install "git+https://github.com/Hiemenz/code-quality.git#egg=codequality[types,coverage,mutation]"
```

Then, in the target repo:

1. **Add a config file** at the repo root — `.codequality.toml`:
   ```toml
   [thresholds]
   fail_under = 60   # start low for an existing codebase; raise it over time

   [limits]
   exclude = ["vendor/*", "migrations/*", "node_modules/*"]
   ```
   (See [Configuration](#configuration) below for every field.)

2. **Copy the CI workflow** — `.github/workflows/code-quality.yml` in this
   repo is a ready-to-use template: full `scan` on every push to main
   (tracked as an artifact, not gated), plus a `diff` scan gated at
   `--fail-under` on every PR with the result posted as a PR comment. Copy
   it into the target repo and adjust the branch name/threshold. Not on
   GitHub Actions? The same two `codequality scan`/`codequality diff`
   commands work in any CI system — see [CI pipeline example](#ci-pipeline-example).

3. **Snapshot a baseline** so gating starts today instead of after a
   cleanup sprint:
   ```bash
   codequality baseline .
   codequality scan . --baseline .codequality-baseline.json --fail-under 70
   ```
   The `worst_files` list in `--format json` (or the "Lowest-scoring files"
   table in the terminal report) is your prioritized to-do list for paying
   that debt down over time — see
   [Baseline mode](#baseline-mode-gating-a-messy-repo-without-a-cleanup-sprint).

4. **Track improvement over time** — see [Tracking score history](#tracking-score-history).

## Usage

```bash
# Full repo, human-readable report in the terminal
codequality scan .

# Only what changed vs. main, as markdown (e.g. for a PR comment)
codequality diff . --base origin/main --format markdown

# Only what changed vs. the last commit (default when the working tree is
# clean), or vs. HEAD when there are uncommitted changes -- handy for
# "did I just make this worse?" while you're still editing
codequality diff .

# CI gate: non-zero exit if the score is below the threshold
codequality scan . --fail-under 70
echo $?

# GitHub code-scanning annotations
codequality scan . --format sarif --output results.sarif

# Track the score over time, then look at the trend
codequality scan . --record-history history.jsonl
codequality trend history.jsonl

# Track per-function cyclomatic complexity over time, then see which
# functions have been quietly getting more complex
codequality complexity-trend snapshot . -o complexity-history.jsonl
codequality complexity-trend show complexity-history.jsonl

# Snapshot current issues, then gate only on issues beyond that snapshot
codequality baseline .
codequality scan . --baseline .codequality-baseline.json --fail-under 70

# Correctness checks: hallucinated imports + real type errors (opt-in)
codequality scan . --check-imports --check-types

# Did the tests actually exercise this code? (opt-in, runs your test suite)
codequality scan . --check-coverage

# Does the test suite actually assert behavior, or just run the code?
codequality mutation .

# AI-assisted vs. human commits: which needs rework sooner after landing?
codequality churn .

# Of the lines a commit added, how many are unchanged at HEAD vs. rewritten?
codequality edit-distance .

# Do commit subject lines meet basic quality bars (length, not a generic
# placeholder like "fix" / "wip"), broken down AI-assisted vs. human?
codequality commit-lint .

# Of the hallucination-style findings above, how many trace back (via git
# blame) to AI-assisted vs. human commits, per 1,000 lines?
codequality hallucination-rate . --check-imports --check-types

# Per-file bus factor: who (or what share of one git identity) owns each
# file's current lines, plus what fraction trace to an AI-assisted commit
codequality ownership .

# How old is each TODO/FIXME/XXX/HACK marker, and did an AI-assisted or
# human commit introduce it? Anything past --stale-days is flagged.
codequality todo-age . --stale-days 90

# Cross-file dead-code findings, aged via git blame: the longer a
# "never referenced" function/class has sat untouched, the safer it is
# to actually remove
codequality dead-code-confidence . --stale-days 180

# Does the test suite pass reliably, or does a test's result depend on
# luck? (opt-in, runs your test suite N times -- can be slow)
codequality flakiness .

# Property-based test usage + generated Hypothesis stubs to fill in
codequality scaffold-properties .

# What broke in the public API between two tags/branches/commits?
codequality api-diff . --from v1.2 --to v1.3

# Which functions got significantly more complex between two tags/branches/commits?
codequality complexity-regression . --from v1.2 --to v1.3

# Structural consistency checks on requirements.txt/pyproject.toml/package.json
# -- no network access, ever (see "Dependency consistency check" below)
codequality dependency-check .

# Which declared dependencies are both heavily imported AND structurally
# risky (per dependency-check above) -- offline impact-weighted priority
# list, NOT a staleness/CVE check (see "Dependency risk" below)
codequality dependency-risk .

# CI workflows/docker-compose files/Makefiles that reference a local script
# or path that no longer exists (see "Orphaned config references" below)
codequality orphaned-config .

# Which files are both complex AND changed constantly -- the highest-risk
# refactoring targets (see "Hotspots" below)
codequality hotspots .

# Which files are both complex AND have no matching test file at all --
# what to write a test for first (see "Complexity x test presence risk" below)
codequality complexity-coverage-risk .

# Env vars read in code vs. env vars documented in .env.example/README.md,
# flagged in either direction (see "Environment variable drift" below)
codequality env-check .

# Secrets that were ever committed, even if a later commit deleted the
# line -- deleting a line doesn't remove it from git's history
codequality history-secrets .

# Did someone accidentally commit a node_modules/, a build artifact, or a
# multi-MB binary blob into git? (see "Large/binary file check" below)
codequality large-files . --max-size-mb 5

# Sibling per-environment config files (.env variants, or a config/
# directory) whose key sets don't match (see "Configuration drift" below)
codequality config-drift .

# Django/Alembic/raw-SQL migrations that can't be rolled back (see
# "Migration reversibility check" below)
codequality migration-check .

# Age every feature-flag-looking reference via git blame -- flags whose
# oldest reference is past --stale-days are cleanup candidates (see
# "Feature flag aging" below)
codequality feature-flags . --stale-days 180

# Config-driven import-direction check across named layers -- a no-op
# until [architecture].layers is configured (see "Architecture
# conformance" below)
codequality arch-conformance .

# Full pipeline: your own format/lint/test commands, then codequality's
# own scan, as one combined report + exit code (see "Pipeline" below)
codequality pipeline .

# One dashboard: churn + edit-distance + commit-lint + hallucination-rate,
# AI-assisted vs. human, side by side (see "AI code quality report" below)
codequality ai-report . --check-imports --check-types

# Learn the repo's own dominant conventions (type hints, quotes, docstring
# style, string formatting) and list files that deviate -- the repo itself
# is the baseline, report-only (see "Repo conventions" below)
codequality conventions .
```

`scan` and `diff` share the same flags:

| Flag | Meaning |
|---|---|
| `path` | Root directory to analyze (default `.`) |
| `--format` | `text` (default, colored), `json`, `markdown`, or `sarif` |
| `--output FILE` | Write the report to a file instead of stdout |
| `--fail-under N` | Exit 1 if the overall score is below N |
| `--config PATH` | Explicit config file (see below) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--no-generic` | Only analyze Python; skip the analyzer for other languages |
| `--include-generated` | Score auto-detected generated files too (excluded by default — see [Generated files are excluded from scoring](#generated-files-are-excluded-from-scoring)) |
| `--baseline FILE` | Forgive issues already recorded in this baseline file — see below |
| `--check-imports` | Flag Python imports that don't resolve in this environment, and hallucinated stdlib attributes like `os.path.exists_dir()` (opt-in) |
| `--check-types` | Run mypy and fold its findings into the correctness category (opt-in) |
| `--check-coverage` | Run the repo's own test suite under coverage.py (opt-in; executes your code) |
| `--test-command "..."` | Command to run under `--check-coverage`, as args after `python -m` |

`diff` additionally takes `--base REF` and `--head REF` (default: auto-detect,
see above).

`scan` additionally takes `--record-history FILE`, which appends this run's
overall/category scores as one JSON line to `FILE` — see
[Tracking score history](#tracking-score-history).

Twenty-four more subcommands, each documented in its own section below:
`codequality conventions` (the repo's own conventions as the baseline —
see [Repo conventions](#repo-conventions-the-scanned-repo-is-the-baseline)),
`codequality baseline`, `codequality trend FILE`, `codequality churn`,
`codequality edit-distance`, `codequality commit-lint`,
`codequality hallucination-rate`, `codequality ai-report`,
`codequality ownership`, `codequality todo-age`,
`codequality dead-code-confidence`,
`codequality scaffold-properties`,
`codequality pipeline`, `codequality complexity-trend`,
`codequality dependency-check`, `codequality dependency-risk` (usage count
x `dependency-check`'s own structural flags — see [Dependency
risk](#dependency-risk-usage-count-x-structural-risk-flags)),
`codequality orphaned-config` (config files -- CI workflows/docker-
compose/Makefiles -- that reference a local path that no longer exists,
see [Orphaned config references](#orphaned-config-references)),
`codequality env-check`,
`codequality history-secrets` (secrets that were ever committed, even if
since removed — see [Secrets in git history](#secrets-in-git-history)),
`codequality large-files`, `codequality hotspots` (complexity crossed with
change frequency — see
[Hotspots](#hotspots-complexity-x-change-frequency)),
`codequality complexity-coverage-risk` (complexity crossed with structural
test presence — see
[Complexity x test presence risk](#complexity-x-test-presence-risk)), and
`codequality api-diff` (public API comparison between any two git refs —
see
[`codequality api-diff`](#codequality-api-diff-public-api-comparison-across-any-two-refs)),
and `codequality complexity-regression` (per-function complexity
comparison between any two git refs — see
[`codequality complexity-regression`](#codequality-complexity-regression-per-function-complexity-comparison-across-any-two-refs)).
Plus `codequality mutation` and `codequality flakiness`, which are
deliberately separate from everything else — see
[Mutation testing](#mutation-testing) and
[Flaky test detection](#flaky-test-detection-executes-your-tests-n-times).

Exit codes: `0` = passed threshold, `1` = below threshold, `2` = usage/git error.

## How the score is built

Eight categories, each 0-100, combined by weight into the overall score:

| Category | Default weight | What it measures |
|---|---|---|
| Complexity | 15 | Cyclomatic complexity per function (McCabe-style: branches, loops, boolean operators, comprehensions, `except` clauses), plus *cognitive* complexity (Sonar-style, nesting-weighted: a branch buried four levels deep costs more than the same branch laid flat — the better proxy for "how hard is this to read"). Every scan also prints a repo-wide "Most complex functions" table (see below) so the hardest functions stay visible even when nothing crosses a limit |
| Structure | 10 | Function length, nesting depth, file length, circular imports (cross-file). Also reports (but doesn't score, see below) cross-file dead code: public top-level functions/classes never referenced anywhere else in the repo |
| Duplication | 10 | Copy-pasted blocks (6+ line sliding-window hash, cross-file) |
| Documentation | 8 | Docstring coverage on public functions and modules, stale docstrings that document a removed parameter, and Markdown code examples that no longer parse (reported, doesn't affect this category's score -- see below) |
| Style | 12 | Long lines, trailing whitespace, TODO markers, bare `except:`, `except Exception: pass`-style silent swallowing, raising a new exception without chaining from the original, wildcard imports, mutable default arguments, unused imports/variables, non-conventional naming, `print()` calls left in library code |
| Security | 15 | `eval`/`exec`, `shell=True`, unsafe deserialization (`pickle`, `yaml.load`), hardcoded-looking secrets, SQL built via string interpolation instead of parameters, logging a secret-looking variable |
| Correctness | 15 | Always-on: assertion-free tests, tautological tests (every assertion trivially true), mock-only tests, unreachable code, unclosed resources (`open()`/`socket.socket()`/`urlopen()` never used as a context manager or explicitly closed), a query-shaped call inside a loop (N+1 pattern), a local async function called without awaiting/scheduling it, stub function bodies (`pass`/`...`/`raise NotImplementedError` only), placeholder comments (`# ... rest of the code ...`), deprecated/removed stdlib APIs (`imp`, `distutils`, `datetime.utcnow()`, ...), and — full scan only, cross-file — references to repo-internal names that don't exist (`from utils import frobnicate` where `utils.py` has no `frobnicate`). Opt-in: unresolved imports *and* hallucinated stdlib attributes like `os.path.exists_dir()` (`--check-imports`), real type errors (`--check-types`) — see [Correctness checks](#correctness-checks-opt-in) |
| Coverage | 15 | Opt-in: line coverage from your own test suite (`--check-coverage`). 100 until you opt in — see [Test coverage](#test-coverage-opt-in-executes-your-code) |

The first six categories are pure static analysis — the same "is this code
tidy and safe" question every linter asks. Correctness and Coverage are a
different kind of question: "does this code actually work," which nothing
above can answer without either running it or checking it against a type
system. See [Correctness checks](#correctness-checks-opt-in) below for why
that split exists and what it's specifically useful for with LLM-generated
code.

Each category is scored from *defect density*, not raw counts — a 3,000-line
file isn't unfairly punished the same as a 30-line file for having one long
function. See `codequality/scorer.py` for the exact formulas; they're
simple arithmetic on purpose, not a black box.

Python-only checks (no equivalent yet for other languages): unused
imports/variables, cross-file dead-code detection, `pickle`/`yaml.load`
deserialization checks, assertion-free tests, tautological tests,
mock-only tests, broad exception-swallowing,
lost exception chaining, stale-docstring parameters, unreachable code,
unclosed resources, query-in-loop, unawaited coroutines, SQL-injection-
shaped queries, sensitive-data logging, circular imports,
unresolved internal references, deprecated stdlib APIs, cognitive
complexity, and
**`print-in-library-code`** — a `print(...)` call found anywhere in a
Python file, `info` severity, since it's a heuristic. A common smell,
especially in LLM-generated code that defaults to `print()` for
debugging/status output instead of proper logging. Deliberately scoped
to "no equivalent for other languages": what counts as a legitimate
top-level print idiom vs. a debug leftover varies too much by language
and logging convention to check generically. Exempted, since these are
legitimate producers of terminal output rather than an accidental debug
leftover: test files (same `tests/`/`test_*.py` convention as
elsewhere), files under an `examples/`/`scripts/` directory, and —the
main exemption in practice — any file containing a module-level `if
__name__ == "__main__":` guard anywhere, a strong, simple signal that
the file is a script/CLI entry point meant to be run directly rather
than a library module imported by other code (this is why this tool's
own `cli.py`, which legitimately prints its reports to the terminal,
doesn't flood the self-scan with false positives).

Hardcoded-secret and `eval`/`exec` detection run for every language via a
line-level regex. Function-naming convention checks run for Python and,
when the `tree-sitter` extra is
installed, for JS/TS/Java/C#/Go/Ruby/Rust/Kotlin/Swift/Scala too — each
checked against that language's own convention (e.g. `camelCase` for
JS/Java, `snake_case` for Ruby/Rust, `PascalCase` for C#), with
constructors exempted. C, C++, and PHP are deliberately skipped here since
real-world naming style in those languages is too mixed to check without
a lot of noise.

Several of the checks above are specifically aimed at judging whether code —
LLM-written or not — actually does what it claims, rather than just
looking tidy:

- **`assertion-free-test`** — a `test_*` function with no `assert`,
  `self.assertX`, or `pytest.raises`/`warns` anywhere in its body passes
  regardless of what the code under test does. This is the purest form of
  "test theater" and is distinct from coverage (which only asks "did
  anything call this") — it's a static, instant check for tests that
  can't fail even in principle.
- **`tautological-test`** — the sneakier sibling of the above: the test
  *has* assertions, but every single one is trivially true (`assert True`,
  `assertEqual(x, x)`, `assert f(1) == f(1)` comparing an expression to
  itself), so it still can't fail no matter what the code under test does.
  Common in LLM-written tests generated to satisfy a "write tests"
  instruction rather than to verify behavior. Only fires when *all* of a
  test's assertions are tautological — one `assert True` next to a real
  assertion is odd but harmless, and flagging it would be noise.
- **`mock-only-test`** — the test's every assertion is a mock-interaction
  assertion (`assert_called_once_with`, `assert_awaited`,
  `assert_has_calls`, ...): it verifies mocks were poked, never a real
  output or state change. Interaction-testing is a legitimate style when
  the interaction *is* the contract, so this is `info` — a
  distribution-level signal (a suite where most tests assert only on
  mocks is classic "mock everything, then assert the mocks are mocks"
  LLM output), not a per-test verdict. A single real assertion clears
  the test, and a test with no assertions at all is
  `assertion-free-test`'s finding instead.
- **`broad-except-swallow`** — `except Exception:` (or `BaseException`)
  whose body is just `pass` (optionally with a leading string "comment")
  and nothing else: no re-raise, no logging, no returned error signal.
  Distinct from the existing bare-`except:` check, and a much more common
  pattern in practice — code that looks defensive but actually hides every
  failure with no trace.
- **`stale-docstring-param`** — a docstring (Sphinx `:param:`, Google
  `Args:`, or NumPy `Parameters` style) documents a parameter that no
  longer exists in the actual signature. Deliberately asymmetric: this
  only flags *removed* parameters, never *undocumented* ones, since the
  latter would be far noisier for little benefit.
- **`unreachable-code`** — a statement following an unconditional
  `return`/`raise`/`continue`/`break` in the same block. Occasionally
  shows up in LLM output as a leftover branch after code that already
  unconditionally exits it.
- **`unclosed-resource`** — `open()`/`socket.socket()`/`urlopen()` that is
  neither used as a `with`/`async with` context manager nor explicitly
  `.close()`d, returned, or passed to another call anywhere in the same
  function. Conservative by design (see
  `codequality/analyzers/resource_lifecycle.py`'s module docstring for
  exactly which cases are excluded to keep false positives low).
- **`query-in-loop`** — a database-looking call (Django `.objects.get`/
  `.filter`/..., a SQLAlchemy `.session.query`/`.execute`, a DB-API
  `.cursor.execute`, or a raw `conn`/`connection`/`db.execute`/`.query`)
  sitting inside a `for`/`while` loop body — the N+1 query shape. Only
  receiver patterns that are unambiguously DB-flavored are matched, so a
  plain `dict.get()`/`list` call in a loop is not flagged.
- **`lost-exception-context`** — an `except ... as e:` handler that raises
  a brand-new exception without either explicit chaining
  (`raise ... from e`) or referencing `e` anywhere in the new exception's
  construction. A bare re-raise (`raise`) or re-raising the same bound
  name (`raise e`) is not this pattern.
- **`unawaited-coroutine`** — a call to a locally-defined `async def`
  function/method whose result is never `await`ed, scheduled
  (`asyncio.create_task`/`gather`/...), returned, or assigned to a name
  that's later awaited/scheduled. A purely syntactic, very common
  real-world bug — the coroutine object is created but its body never
  actually runs. Matches by the call's last dotted segment only (`foo` for
  both `foo()` and `self.foo()`), since this tool has no type information
  to know which class `self` actually is.
- **`sql-injection-risk`** — `cursor.execute(...)`/`.executemany(...)`/
  Django's `QuerySet.raw(...)` called with exactly one argument that's a
  Python-formatted string (f-string interpolation, `%`/`+`, or
  `.format()`) instead of a query string plus parameters passed
  separately. The safe, parameterized form
  (`execute("... WHERE x=%s", (value,))`) is not flagged — it's the
  second, separate argument that matters, not whether the query text
  itself contains a placeholder.
- **`sensitive-data-logging`** — a `logger.<level>(...)`/`print(...)` call
  that references a variable whose *name* looks like a secret (same
  `SECRET_NAME_RE` used for `hardcoded-secret`). Logging a credential is a
  common way secrets leak into log aggregators/terminals even when the
  value itself is never hardcoded anywhere.
- **`stub-implementation`** — a function whose body (after its docstring)
  is nothing but `pass`, `...`, or a single `raise NotImplementedError`:
  code that *looks* finished but was never written, an LLM failure mode
  far more common than in hand-written code. The `raise
  NotImplementedError` form is `warn` (calling it crashes; outside an
  abstract base that's almost never intentional); the `pass`/`...`/
  docstring-only forms are `info`, since an intentional no-op hook is a
  legitimate pattern this check can't distinguish from an unfinished one.
  Exempt, all in the fewer-false-positives direction (see
  `codequality/analyzers/placeholder_code.py`'s module docstring): any
  decorated function (`@abstractmethod`/`@overload`/framework hooks),
  every method of an abstract-looking class (a `Protocol`/`ABC` base,
  `metaclass=ABCMeta`, or any sibling `@abstractmethod`), and `test_*`
  functions (`assertion-free-test` already covers those). Other
  languages get a line-level version via the generic analyzer: `throw
  new NotImplementedException`, `throw new Error('not implemented')`,
  Go's `panic("not implemented")`, Rust's `todo!()`/`unimplemented!()`,
  Kotlin's `TODO()`, always `info` there since without a parser an
  intentionally-abstract member can't be told apart.
- **`placeholder-comment`** — a standalone comment line matching a fixed
  set of "the model elided the code here" phrases: `# ... rest of the
  code ...`, `# your logic here`, `# implementation omitted`, `# ...
  existing code ...`, and so on. Near-unambiguous: a human rarely writes
  these, an LLM asked to edit a file writes them constantly — and the
  code that was supposed to be there is simply missing. Only lines that
  *start* with a comment marker are scanned, so a `#` inside a string
  (a URL fragment, say) can't false-positive. Runs for every supported
  language (`//`, `#`, `/*`, `--` comments alike), same shared phrase
  list.
- **`unresolved-internal-import`** / **`unresolved-internal-attribute`** —
  the repo-internal sibling of `--check-imports`' package hallucination:
  `from utils import frobnicate` (`warn` — a guaranteed ImportError), or
  `utils.frobnicate(...)` (`info` — module attributes can be attached at
  runtime), where repo-local `utils` defines no top-level `frobnicate`.
  An LLM does this far more often than a human typo: it "remembers" a
  helper that a similar codebase had. Pure AST over the same cross-file
  source map dead-code uses — no imports executed, always-on in full
  `scan`, absent from `diff` (no view of the rest of the repo there).
  Conservative skips (star-importing modules, PEP 562 module
  `__getattr__`, shadowed/reassigned names, locally-attached attributes)
  are documented in `codequality/analyzers/internal_refs.py`.
- **`deprecated-api`** — imports and calls matched against a fixed table
  of stdlib APIs that are deprecated or physically removed in current
  CPython: the PEP 594 "dead batteries" (`cgi`, `telnetlib`, `pipes`,
  ...), `imp`/`distutils` (removed 3.12), `ssl.wrap_socket` (removed
  3.12), unittest's removed `assertEquals`-style aliases,
  `datetime.utcnow()`/`utcfromtimestamp()` (deprecated 3.12, and a naive-
  datetime bug factory), `asyncio.get_event_loop()` outside a running
  loop, `pkg_resources`, `locale.getdefaultlocale()`. Removed APIs are
  `warn` (the code crashes on a modern interpreter); deprecated-but-
  working ones are `info`. This one is aimed squarely at the "model
  trained on an old corpus" signature: a *fresh* file using `imp` or
  `utcnow()` is strong evidence the code was generated from stale
  training data and never run against a current runtime. No version
  sniffing, no network — just a name table
  (`codequality/analyzers/deprecated_api.py`).

### Generated files are excluded from scoring

Nobody hand-edits a protobuf `_pb2.py` file, an OpenAPI/Swagger client stub,
or a migration script an ORM wrote for you -- so flagging one for missing
docstrings or high complexity is noise, and a single large generated client
can meaningfully skew a repo's score if it's counted like hand-written code.
`scan`/`diff` auto-detect these and exclude them the same way a
`config.exclude` glob does -- they don't appear in the metrics, the issue
list, or `files_analyzed` at all.

Detection (`codequality/generated_code.py`) is content-first: a case-
insensitive marker on its own comment line (`#`, `//`, `/* */`, `<!--`, or
`--`) within roughly the first 20 lines of the file --

- `do not edit`
- `@generated`
- `code generated by`
- `this file was automatically generated`
- `autogenerated` / `auto-generated`

-- since that's how most generation tools (protoc, openapi-generator,
swagger-codegen, GraphQL codegen, ...) mark their own output, regardless of
filename convention. Restricting the check to comment lines (rather than a
full-line substring search) matters: a docstring or README paragraph that
merely *discusses* code generation shouldn't itself get excluded from
scoring just for using the same words in a sentence.

Only when no content marker is found, a small, deliberately conservative
set of filename/path conventions is checked as a fallback: protobuf's
`*_pb2.py`/`*_pb2_grpc.py` suffix, an Alembic- or Django-style migration
filename (a hex revision id or zero-padded number prefix) under a
`migrations/` directory, `*.generated.*`, and anything under a directory
literally named `generated` or `__generated__`. Nothing broader than that
is guessed at: a missed generated file just gets scored like normal code
(mildly annoying), while a hand-written file wrongly excluded would hide
real issues from the report -- so the fallback stays narrow on purpose.

Pass `--include-generated` to `scan`/`diff` to score these files anyway, for
anyone who disagrees with the auto-detection on their repo (or the same
field, `include_generated = true`, in `.codequality.toml` -- see
[Configuration](#configuration)).

### Cross-file dead code

The `unused-import`/`unused-variable` checks above only ever look at one
file at a time. `scan` (full repo only — there's no "rest of the repo" to
check against in `diff` mode, same reasoning as duplication below) also
runs a whole-project version of that idea: a public top-level function or
class, defined in one file, whose name never occurs — as a whole word,
anywhere, excluding its own definition line — in any other scanned file's
source, is flagged as **`dead-code`**.

This is intentionally a blunt, text-level check (no import/scope
resolution, same "no cleverness, just reproducibility" tradeoff every
other analyzer here makes), so it's reported at `info` severity under the
Structure category without affecting that category's score — a heuristic
signal to look at, not something that should fail a build on its own.
False positives are expected (e.g. a name only ever reached via
`getattr`/reflection); the checks below rule out the common, obvious
ones:

- names in a module's `__all__`;
- dunder methods, and the conventional `main()` script entry point;
- pytest/unittest hooks discovered by name/convention rather than direct
  reference: `test_*` functions, `setUp`/`tearDown` (and the `Class`/
  `Module` variants), and `Test*`-prefixed classes;
- anything decorated — a decorator often means external dispatch (a
  Flask route, a plugin registry, a CLI command) that a text search can't
  see, so decorated functions/classes are skipped entirely rather than
  guessed at.

This check alone can't tell a `dead-code` finding that's brand new (maybe
just not wired up yet) from one that's sat unused for years -- see
[Dead-code confidence](#dead-code-confidence) below for the git-blame-based
age dimension `codequality dead-code-confidence` adds on top of it.

### Doc examples that no longer parse

`stale-docstring-param` (above) catches doc rot in a parameter list;
`scan` (full repo only, same "no rest of the repo to compare in `diff`
mode" reasoning as dead code above -- a README example can rot from a
change to a file that isn't even part of the diff) also checks the
cheapest form of rot in a *code example*: does it still parse as valid
Python at all? Every fenced ` ```python `/` ```py ` block in every
Markdown file in the repo (README included) is run through `ast.parse()`
-- never executed, just parsed, the same "never run code from the
scanned repo" rule every other check here follows -- and a block that
raises `SyntaxError` is flagged as **`broken-doc-example`** at the file
and line where the block starts, with the parser's own error message
included. This is deliberately a much narrower claim than "the example
still matches current behavior" (which would require actually running
it); it only catches the case where nobody has so much as glanced at the
example since an edit made its syntax invalid (a leftover Python 2-ism, a
copy-paste that dropped a closing paren, ...). Reported at `warn`
severity under the Documentation category without affecting that
category's score, the same "signal, not a build-breaker" treatment
`dead-code` gets above. Non-Python fences (` ```bash `, ` ```json `, ...)
are never touched. This first version only looks at Markdown fences --
see `codequality/analyzers/doc_examples.py`'s module docstring for why
docstring-embedded (`>>>`-style) examples are scoped out for now rather
than half-built.

### Language support

**Python gets full analysis**: real functions, real complexity, real
nesting, via `ast` — no extra install required.

**Other languages** get real per-function analysis too, *if* the optional
`tree-sitter` extra is installed:

```bash
pip install "codequality[treesitter]"
```

This adds real function/method boundaries, real cyclomatic complexity, and
real nesting depth for `.js .jsx .ts .tsx .java .go .c .cpp .cs .rb .php
.rs .kt .swift .scala` via [`tree-sitter-language-pack`](https://github.com/xberg-io/tree-sitter-language-pack)
grammars — the same idea as the Python path, just driven by a table of
node-kind names per language instead of Python's `ast` module.

**Without the extra installed**, those languages fall back to a lighter
heuristic pass: no parser, so complexity is approximated from
branching-keyword density and nesting from indentation, at file
granularity rather than per-function. Treat those scores as directional.
The extra is optional (not a base dependency) specifically so the default
install stays dependency-free and installable anywhere `pip` runs.

### Diff mode is scoped to the actual change, not just the changed files

`codequality diff` doesn't just re-run the full-file analysis on whatever
files changed. It parses the unified diff to find exactly which lines were
added, then:

- only counts a function toward complexity/structure/nesting scoring if
  the change actually touches that function;
- only runs line-level checks (long lines, TODOs, trailing whitespace, bare
  except, ...) on the added lines themselves.

So editing one line in a 40-function file scores that one line's context,
not the other 39 functions you didn't touch.

### Diff-only correctness checks

Three checks only make sense with an "old" and a "new" version to compare,
so they only run in `diff` mode, and all three are always-on (no opt-in,
no extra dependency):

- **`breaking-signature-change`** — compares each changed Python
  function/method's old and new parameter list and flags removed
  parameters, newly-required parameters, and reordered positional
  parameters: the three ways a signature change silently breaks existing
  callers. Limited to top-level functions and methods of top-level
  classes — that's what "public API" means for most codebases.
- **`complexity-regression`** — the complexity-focused sibling of
  `breaking-signature-change`: same old-vs-new comparison, same
  top-level-functions-and-methods scope (`analyzers/signature_diff.py`'s
  `qualified_functions`, reused as-is rather than re-derived), but compares
  each matched function's cyclomatic complexity instead of its parameter
  list (the same per-function number the Complexity category is already
  scored from). A function whose complexity increased by more than 5
  (an absolute delta, not a percentage — see
  `codequality/analyzers/complexity_regression.py`'s module docstring for
  why one fixed threshold was chosen over a relative "doubled" rule) is
  flagged with its old and new numbers, e.g. `cyclomatic complexity
  increased from 4 to 12 (+8)`. Reported under the Complexity category
  (not Correctness, unlike the other two checks in this section) at `warn`
  severity. A brand-new function has no old counterpart to compare against
  and is silently skipped, same as `breaking-signature-change`.
- **`scope-mismatch`** — tokenizes a task description (`--task-description`,
  defaulting to the last commit's subject line) and each changed file's
  path, then flags a changed file that shares no keyword with the
  description while another changed file elsewhere does. Only fires when
  the description is specific enough to produce a match at all, and never
  flags a file that shares a directory with one that did match — a vague
  subject like `"fix bug"` or a commit that only touches one area never
  triggers it.

`complexity-regression` also has a standalone, `api-diff`-style subcommand
for comparing any two points in history on demand (not just the current
`diff` invocation) — see
[`codequality complexity-regression`](#codequality-complexity-regression-per-function-complexity-comparison-across-any-two-refs)
below.

## Suppressing false positives

Put `codequality: ignore` (optionally scoped to specific rules) anywhere on
a line to suppress issues reported there:

```python
eval(trusted_input)  # codequality: ignore[dangerous-eval]

PASSWORD = "not-a-real-secret-this-is-a-fixture"  # codequality: ignore
```

This isn't tied to any one language's comment syntax — the marker text is
what matters, not the `#`/`//` in front of it, so it works the same in
every supported language. Suppression affects the score too, not just the
printed report: a suppressed `high-complexity`/`long-function`/
`deep-nesting`/`missing-docstring` stops contributing its penalty, and a
suppressed style/security issue is simply excluded from those categories'
issue-density calculation. Suppressed counts show up in the report summary
(`"suppressed": N` in JSON, a `Suppressed: N` note in text/markdown) so
they stay visible rather than silently disappearing.

## Baseline mode: gating a messy repo without a cleanup sprint

`codequality baseline` snapshots how many issues of each (file, rule) pair
currently exist. `scan --baseline FILE` then forgives up to that many —
only issues *beyond* what was already there count as new, and those are
what fail the build:

```bash
codequality baseline .                      # writes .codequality-baseline.json
codequality scan . --baseline .codequality-baseline.json --fail-under 70
```

This is the mechanism to reach for when adopting `codequality` on an
existing codebase: turn on gating immediately without fixing everything
first, then either delete the baseline once the backlog is paid down, or
periodically re-run `codequality baseline` to ratchet it as the codebase
improves. Mechanically it's just a bulk, auto-generated version of the
inline suppression comments above — same underlying mechanism, generated
from a snapshot instead of written by hand.

## Correctness checks (opt-in)

Everything above this section other than the always-on correctness checks
(assertion-free/tautological/mock-only tests, unreachable code, stubs,
unresolved internal references, ...) is static analysis: it can tell you code is tidy,
documented, and free of obvious security footguns, but none of it can
tell you the code actually *does what it's supposed to do*. An LLM can
write low-complexity, well-documented, secure-looking code that's still
wrong, and most checks above would score it well anyway. Two more checks
close part of that gap — both opt-in (unlike the two above), because both
depend on the environment `codequality` runs in, not on the source alone:

```bash
codequality scan . --check-imports   # flag imports that don't resolve here
codequality scan . --check-types     # run mypy, fold results into "correctness"
```

`--check-imports` catches a well-documented LLM failure mode: inventing a
plausible-sounding package or module that doesn't actually exist ("package
hallucination"). It resolves each top-level import against what's actually
installed in the environment you run `codequality` in — which means it's
only meaningful if that environment has the target repo's real
dependencies installed (run it in CI after your normal install step, not
in a bare virtualenv).

The same flag also verifies *stdlib attribute* access
(**`unresolved-attribute`**): `os.path.exists_dir(...)`, `from json
import dumpss` — a module that exists whose member doesn't. Only modules
in `sys.stdlib_module_names` are ever imported for inspection (so the
"never execute code from the scanned repo" rule holds; third-party
packages are never touched), a tiny denylist skips the stdlib's
side-effect modules (`antigravity`, `this`), and attribute chains are
verified only while the object in hand is a module or a class —
`datetime.datetime.utcnowww` is caught, `sys.stdout.write` stops at the
instance. Names the file re-binds or monkeypatches are skipped — see
`codequality/analyzers/stdlib_attrs.py` for every conservatism. Like the
import check, results depend on the running interpreter's version — the
message says which Python it checked against.

`--check-types` runs `mypy` (`pip install codequality[types]`) over the
whole repo and folds its errors into the correctness category. Like
`--check-imports`, results depend on the type hints actually being present
and on dependencies being resolvable — same environment caveat applies.

## Test coverage (opt-in, executes your code)

```bash
codequality scan . --check-coverage
codequality scan . --check-coverage --test-command "pytest -q"
codequality diff . --check-coverage   # "patch coverage": just the changed lines
```

Requires `pip install codequality[coverage]`. Untested code is the
single strongest signal that a change — LLM-written or not — hasn't been
verified to do anything in particular; did the LLM write tests, and do
they cover the lines it just changed? This is the one check in
`codequality` that actually **runs the target repo's own test suite**
(via `coverage.py`) rather than only parsing source — every other check
in this tool never executes the code it's scoring. `--test-command` takes
the args you'd normally pass after `python -m` (default:
`"unittest discover -s tests"`); override it for `pytest`, `nose2`,
whatever the repo actually uses. In `diff` mode, the ratio measures just
the lines that changed ("patch coverage"), not the whole file.

## Further reading

Per-subcommand documentation lives in [docs/subcommands.md](docs/subcommands.md):
`api-diff`, `complexity-regression`, `history-secrets`, `conventions`,
`mutation`, `flakiness`, `churn`, `edit-distance`, `commit-lint`,
`hallucination-rate`, `ownership`, `todo-age`, `dead-code-confidence`,
`ai-report`, `scaffold-properties`, `dependency-check`, `dependency-risk`,
`orphaned-config`, `hotspots`, `complexity-coverage-risk`, `env-check`,
`large-files`, `config-drift`, `migration-check`, `feature-flags`,
`arch-conformance`, `complexity-trend`.

## Pipeline: one gate for format, lint, test, and codequality's own scan

A full code-quality pipeline usually looks like: format check -> lint ->
static analysis -> security scan -> unit tests -> coverage -> complexity ->
benchmark. `codequality scan`/`diff` already cover static analysis,
security, complexity, and (opt-in) coverage in one deterministic pass.
Formatting, linting, and benchmarking are deliberately **not**
reimplemented here — every repo already has its own preferred tool for
those (`black`/`prettier`/`ruff`/`eslint`/`pytest-benchmark`/...).
`codequality pipeline` instead orchestrates whatever external commands the
target repo already uses, runs `codequality`'s own scan alongside them,
and produces one combined report + exit code for CI to gate on:

```bash
codequality pipeline .
codequality pipeline . --fail-under 70 --format json --output pipeline.json
codequality pipeline . --continue-on-failure   # run every step even after one fails
```

Configure steps in `.codequality.toml` (or the JSON/pyproject equivalent):

```toml
[[pipeline.steps]]
name = "format-check"
command = "black --check ."

[[pipeline.steps]]
name = "lint"
command = "ruff check ."

[[pipeline.steps]]
name = "test"
command = "pytest -q"
allow_failure = false   # default; set true to record a step without gating on it
```

Each step's `command` is split with `shlex.split()` and run directly
(never through a shell), so config-file content can't inject shell syntax
— the same reasoning behind this tool's own `dangerous-shell-true`
security check. Steps run in order and the pipeline stops at the first
failing step (skipping the rest) unless that step has `allow_failure =
true`, or `--continue-on-failure` is passed. Either way, `codequality`'s
own scan runs as the final step and folds its pass/fail
(`score >= fail_under`) into the same report — the whole pipeline only
exits `0` if every step passed (or was `allow_failure`) and the
codequality score cleared the threshold.

| Flag | Meaning |
|---|---|
| `path` | Repo root to run the pipeline in (default `.`) |
| `--config PATH` | Explicit config file |
| `--fail-under N` | Threshold for codequality's own scan step |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |
| `--continue-on-failure` | Run every step even after one fails |

## Configuration

Optional `.codequality.toml` (or `.codequality.json`, or a
`[tool.codequality]` table in `pyproject.toml`) at the repo root:

```toml
[thresholds]
fail_under = 70

[weights]
complexity = 15
structure = 10
duplication = 10
documentation = 8
style = 12
security = 15
correctness = 15
coverage = 15

[limits]
max_line_length = 120
max_function_lines = 60
max_file_lines = 600
max_complexity = 10
max_nesting = 4
docstring_min_lines = 8   # don't demand docstrings on tiny helpers

exclude = ["migrations/*", "vendor/*"]
include_generic_languages = true

# Auto-detected generated files (protobuf, migrations, OpenAPI stubs, ...)
# are excluded from scoring by default -- see "Generated files are excluded
# from scoring" above. Set true (or pass --include-generated) to score them.
include_generated = false

# Opt-in correctness/coverage checks (all false by default -- see
# "Correctness checks" and "Test coverage" above)
check_imports = false
check_types = false
check_coverage = false
test_command = "unittest discover -s tests"

# External steps for `codequality pipeline` -- see "Pipeline" above.
# Empty by default: this tool never assumes which formatter/linter/
# benchmark a repo uses.
[[pipeline.steps]]
name = "format-check"
command = "black --check ."

[[pipeline.steps]]
name = "lint"
command = "ruff check ."

# Named layers for `codequality arch-conformance` -- see "Architecture
# conformance" above. Empty by default: entirely opt-in, this tool never
# assumes a repo's layering.
[[architecture.layers]]
name = "api"
modules = ["myapp.api"]

[[architecture.layers]]
name = "service"
modules = ["myapp.service"]
```

All fields are optional and merge over the built-in defaults.

## CI pipeline example

See `.github/workflows/code-quality.yml`: it runs a full `scan` on every
push (tracked as an artifact), and a `diff` scan gated at `--fail-under 70`
on every PR, posting the markdown report as a PR comment. Adapt the same
two commands to any other CI system — it's a plain CLI with JSON/markdown
output and a real exit code.

For GitHub's native code-scanning UI (inline annotations on the Security
tab instead of a PR comment), add a SARIF step:

```yaml
- run: codequality scan . --format sarif --output results.sarif
- uses: github/codeql-action/upload-sarif@v3
  with:
    sarif_file: results.sarif
```

## Tracking score history

`scan --record-history FILE` appends one JSON line per run (timestamp,
overall score, per-category scores, plus `test_loc`/`source_loc`/
`test_ratio` -- see below) to `FILE`. `codequality trend FILE` renders a
sparkline and a score/grade/delta table across every recorded run,
followed by the same for the test-to-source LOC ratio:

```bash
codequality scan . --record-history codequality-history.jsonl
codequality trend codequality-history.jsonl
```

Every recorded run also splits lines of code into `test_loc` and
`source_loc`, plus a `test_ratio = test_loc / source_loc` (each scanned
file is classified as a test file if its name matches `test_*.py`/
`*_test.py`, or it lives in a `tests/`/`test/` directory -- the same
convention `codequality scaffold-properties` already uses to skip test
files when looking for functions that need one). `test_ratio` is `null`
when `source_loc` is 0 (e.g. a test-only checkout) rather than dividing by
zero. `codequality trend FILE --format json` includes these fields on
every entry alongside `overall`/`grade`/`categories`; the text report adds
a second "Test Ratio History" section below the score table, in the same
sparkline + table style.

The cheapest way to persist `FILE` across CI runs is to just commit it to
the repo (add a step to your main-branch workflow that commits the updated
`codequality-history.jsonl`) — it's a small, append-only file, so `git log`
on it doubles as an audit trail, and `trend` works the same whether you run
it in CI or locally.

## Development

```bash
python3 -m unittest discover -s tests
```

The tool dogfoods itself — `codequality scan .` on this repo is part of
sanity-checking any change to the analyzers or scorer.
