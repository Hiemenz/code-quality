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

# Property-based test usage + generated Hypothesis stubs to fill in
codequality scaffold-properties .
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
| `--baseline FILE` | Forgive issues already recorded in this baseline file — see below |
| `--check-imports` | Flag Python imports that don't resolve in this environment (opt-in) |
| `--check-types` | Run mypy and fold its findings into the correctness category (opt-in) |
| `--check-coverage` | Run the repo's own test suite under coverage.py (opt-in; executes your code) |
| `--test-command "..."` | Command to run under `--check-coverage`, as args after `python -m` |

`diff` additionally takes `--base REF` and `--head REF` (default: auto-detect,
see above).

`scan` additionally takes `--record-history FILE`, which appends this run's
overall/category scores as one JSON line to `FILE` — see
[Tracking score history](#tracking-score-history).

Four more subcommands, each documented in its own section below:
`codequality baseline`, `codequality trend FILE`, `codequality churn`, and
`codequality scaffold-properties`. Plus `codequality mutation`, which is
deliberately separate from everything else — see
[Mutation testing](#mutation-testing).

Exit codes: `0` = passed threshold, `1` = below threshold, `2` = usage/git error.

## How the score is built

Eight categories, each 0-100, combined by weight into the overall score:

| Category | Default weight | What it measures |
|---|---|---|
| Complexity | 15 | Cyclomatic complexity per function (McCabe-style: branches, loops, boolean operators, comprehensions, `except` clauses) |
| Structure | 10 | Function length, nesting depth, file length |
| Duplication | 10 | Copy-pasted blocks (6+ line sliding-window hash, cross-file) |
| Documentation | 8 | Docstring coverage on public functions and modules |
| Style | 12 | Long lines, trailing whitespace, TODO markers, bare `except:`, wildcard imports, mutable default arguments, unused imports/variables, non-conventional naming |
| Security | 15 | `eval`/`exec`, `shell=True`, unsafe deserialization (`pickle`, `yaml.load`), hardcoded-looking secrets |
| Correctness | 15 | Opt-in: unresolved imports (`--check-imports`), real type errors (`--check-types`). 100 until you opt in — see [Correctness checks](#correctness-checks-opt-in) |
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
imports/variables, `pickle`/`yaml.load` deserialization checks.
Hardcoded-secret and `eval`/`exec` detection run for every language via a
line-level regex. Function-naming convention checks run for Python and,
when the `tree-sitter` extra is installed, for JS/TS/Java/C#/Go/Ruby/Rust/
Kotlin/Swift/Scala too — each checked against that language's own
convention (e.g. `camelCase` for JS/Java, `snake_case` for Ruby/Rust,
`PascalCase` for C#), with constructors exempted. C, C++, and PHP are
deliberately skipped here since real-world naming style in those languages
is too mixed to check without a lot of noise.

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

Everything above this section is static analysis: it can tell you code is
tidy, documented, and free of obvious security footguns, but none of it
can tell you the code actually *does what it's supposed to do*. An LLM can
write low-complexity, well-documented, secure-looking code that's still
wrong, and every check above would score it well anyway. Two checks close
part of that gap — both opt-in, because both depend on the environment
`codequality` runs in, not on the source alone:

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

## Mutation testing

```bash
codequality mutation .
```

Coverage answers "did anything run this code"; mutation testing answers
the sharper question: "does the test suite actually notice when the code's
behavior changes." It's the real antidote to LLM-written tests that pass
trivially without asserting anything meaningful — `mutmut` mutates the
code (flips a `<` to `<=`, deletes a line, ...) and reruns your tests
against each mutant; a mutant that still passes is one your tests didn't
actually check for. A low kill rate means the tests are theater.

Requires `pip install codequality[mutation]` and a `[tool.mutmut]` section
in the target repo's `pyproject.toml` (`codequality` never writes to that
file on your behalf — run `codequality mutation` once and it'll print the
minimal config to add if it's missing). This is always its own explicit
command, never part of `scan`: mutmut reruns your test suite once per
mutant, so even a modest codebase can take minutes.

## Tracking AI vs. human rework

```bash
codequality churn .
codequality churn . --marker "Generated-By: MyBot" --window-days 30
```

An empirical trust signal computed from what actually happened after code
landed, instead of from re-reading the code: `codequality churn` walks the
git log, classifies each commit as AI-assisted by a marker string in the
commit message (default `"Co-Authored-By: Claude"`, matching the trailer
this tool's own commits use — case-insensitive, since GitHub's squash-merge
normalizes the casing), and reports what fraction of each group's commits
had a touched file modified again within a window (default 14 days) —
a proxy for "did this need a second look soon after landing." Compare the
AI-assisted rate to the human rate to see whether one source of changes
needs more follow-up than the other, in *your* repo's actual history.

## Property-based test scaffolding

```bash
codequality scaffold-properties .
```

LLMs tend to write narrow, example-based tests that only exercise the
happy path they were already thinking about. Property-based testing
(Hypothesis) generates randomized/edge-case inputs against an invariant
you define, which catches exactly the class of bug a hand-picked example
is least likely to hit. Writing the actual invariant needs semantic
understanding this deterministic tool doesn't have — so `scaffold-properties`
stays honest about scope: it reports how much `@given`-based testing
already exists, and writes `property_test_stubs.py` with input generation
wired up from type hints for public functions that don't have one yet.
The assertion itself is left as a `TODO` for a human (or a supervised LLM)
to fill in; treat the generated imports as a best-effort guess to be
checked, not a guarantee.

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

# Opt-in correctness/coverage checks (all false by default -- see
# "Correctness checks" and "Test coverage" above)
check_imports = false
check_types = false
check_coverage = false
test_command = "unittest discover -s tests"
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
overall score, per-category scores) to `FILE`. `codequality trend FILE`
renders a sparkline and a score/grade/delta table across every recorded
run:

```bash
codequality scan . --record-history codequality-history.jsonl
codequality trend codequality-history.jsonl
```

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
