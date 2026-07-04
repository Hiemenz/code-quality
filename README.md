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
# "Correctness checks", "Test coverage", "Mutation testing", and
# "Optional: LLM-based review" below:
pip install "git+https://github.com/Hiemenz/code-quality.git#egg=codequality[types,coverage,mutation,llm]"
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

# Of the lines a commit added, how many are unchanged at HEAD vs. rewritten?
codequality edit-distance .

# Of the hallucination-style findings above, how many trace back (via git
# blame) to AI-assisted vs. human commits, per 1,000 lines?
codequality hallucination-rate . --check-imports --check-types

# Property-based test usage + generated Hypothesis stubs to fill in
codequality scaffold-properties .

# Full pipeline: your own format/lint/test commands, then codequality's
# own scan, as one combined report + exit code (see "Pipeline" below)
codequality pipeline .
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
| `--llm-review` | Ask an LLM to judge architecture/readability/instruction-adherence (opt-in; network call, costs money, not reproducible -- see [Optional: LLM-based review](#optional-llm-based-review-not-part-of-the-deterministic-score)) |
| `--llm-model MODEL` | Override the model used by `--llm-review` (default: `claude-haiku-4-5-20251001`, or `$CODEQUALITY_LLM_MODEL`) |
| `--llm-task "..."` | The task/prompt text the reviewed code was supposed to implement, so `--llm-review` can score instruction adherence (omit to leave that score unset) |

`diff` additionally takes `--base REF` and `--head REF` (default: auto-detect,
see above).

`scan` additionally takes `--record-history FILE`, which appends this run's
overall/category scores as one JSON line to `FILE` — see
[Tracking score history](#tracking-score-history).

Seven more subcommands, each documented in its own section below:
`codequality baseline`, `codequality trend FILE`, `codequality churn`,
`codequality edit-distance`, `codequality hallucination-rate`,
`codequality scaffold-properties`, and `codequality pipeline`. Plus
`codequality mutation`, which is deliberately separate from everything
else — see [Mutation testing](#mutation-testing).

Exit codes: `0` = passed threshold, `1` = below threshold, `2` = usage/git error.

## How the score is built

Eight categories, each 0-100, combined by weight into the overall score:

| Category | Default weight | What it measures |
|---|---|---|
| Complexity | 15 | Cyclomatic complexity per function (McCabe-style: branches, loops, boolean operators, comprehensions, `except` clauses) |
| Structure | 10 | Function length, nesting depth, file length |
| Duplication | 10 | Copy-pasted blocks (6+ line sliding-window hash, cross-file) |
| Documentation | 8 | Docstring coverage on public functions and modules, plus stale docstrings that document a removed parameter |
| Style | 12 | Long lines, trailing whitespace, TODO markers, bare `except:`, `except Exception: pass`-style silent swallowing, wildcard imports, mutable default arguments, unused imports/variables, non-conventional naming |
| Security | 15 | `eval`/`exec`, `shell=True`, unsafe deserialization (`pickle`, `yaml.load`), hardcoded-looking secrets |
| Correctness | 15 | Always-on: assertion-free tests, unreachable code. Opt-in: unresolved imports (`--check-imports`), real type errors (`--check-types`) — see [Correctness checks](#correctness-checks-opt-in) |
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
imports/variables, `pickle`/`yaml.load` deserialization checks,
assertion-free tests, broad exception-swallowing, stale-docstring
parameters, and unreachable code. Hardcoded-secret and `eval`/`exec`
detection run for every language via a line-level regex. Function-naming
convention checks run for Python and, when the `tree-sitter` extra is
installed, for JS/TS/Java/C#/Go/Ruby/Rust/Kotlin/Swift/Scala too — each
checked against that language's own convention (e.g. `camelCase` for
JS/Java, `snake_case` for Ruby/Rust, `PascalCase` for C#), with
constructors exempted. C, C++, and PHP are deliberately skipped here since
real-world naming style in those languages is too mixed to check without
a lot of noise.

Four of the checks above are specifically aimed at judging whether code —
LLM-written or not — actually does what it claims, rather than just
looking tidy:

- **`assertion-free-test`** — a `test_*` function with no `assert`,
  `self.assertX`, or `pytest.raises`/`warns` anywhere in its body passes
  regardless of what the code under test does. This is the purest form of
  "test theater" and is distinct from coverage (which only asks "did
  anything call this") — it's a static, instant check for tests that
  can't fail even in principle.
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

Two checks only make sense with an "old" and a "new" version to compare,
so they only run in `diff` mode, and both are always-on (no opt-in, no
extra dependency):

- **`breaking-signature-change`** — compares each changed Python
  function/method's old and new parameter list and flags removed
  parameters, newly-required parameters, and reordered positional
  parameters: the three ways a signature change silently breaks existing
  callers. Limited to top-level functions and methods of top-level
  classes — that's what "public API" means for most codebases.
- **`scope-mismatch`** — tokenizes a task description (`--task-description`,
  defaulting to the last commit's subject line) and each changed file's
  path, then flags a changed file that shares no keyword with the
  description while another changed file elsewhere does. Only fires when
  the description is specific enough to produce a match at all, and never
  flags a file that shares a directory with one that did match — a vague
  subject like `"fix bug"` or a commit that only touches one area never
  triggers it.

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

Everything above this section other than `assertion-free-test` and
`unreachable-code` is static analysis: it can tell you code is tidy,
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

## Edit distance: how much of a commit survives to HEAD

```bash
codequality edit-distance .
codequality edit-distance . --marker "Generated-By: MyBot" --since "3 months ago"
```

`churn` answers "did this commit's *files* get touched again soon after" —
file-level. `edit-distance` answers the sharper, line-level version: of
the lines a commit *added*, how many are still exactly as that commit left
them? For each classified commit, it diffs against the first parent to
find the lines it added, then blames `HEAD` for every file it touched to
see how many of those exact lines are still attributed to that commit.
`edit_distance` is `1 - lines_survived / lines_added` — 0.0 means nothing
has changed since, 1.0 means every added line has since been rewritten or
removed. This is a proxy for "how many lines does a developer end up
changing before/after a change lands," adapted to a single git history
instead of needing a PR review API. Same marker/`--since` conventions as
`churn`; commits that only delete lines are skipped since the ratio is
undefined for them.

## Hallucination rate

```bash
codequality hallucination-rate . --check-imports --check-types
codequality hallucination-rate . --check-imports --marker "Generated-By: MyBot"
```

`churn` looks at commit history alone; this looks at the actual findings
from `--check-imports`/`--check-types` (see
[Correctness checks](#correctness-checks-opt-in) above) and attributes
each flagged line to whoever last touched it, via `git blame -w HEAD`.
Every scanned file's commit shas are classified AI-assisted vs. human by
the same marker-substring rule as `churn` (default
`"Co-Authored-By: Claude"`, case-insensitive), then rolled up per group
into total lines of code, flagged lines, and a rate per 1,000 lines
(`flagged / loc * 1000`). Unlike `churn`, which is file-level and answers
"did this file need a second look," this is line-level and answers a
narrower question: of the code each source is responsible for, how much
of it is an unresolved import or a real type error — the two checks this
tool has that most directly catch LLM hallucination. Requires at least
one of `--check-imports`/`--check-types` (that's what produces the
findings being rolled up); `--check-types` additionally needs
`pip install codequality[types]`.

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

### Optional: LLM-based review (not part of the deterministic score)

```bash
export ANTHROPIC_API_KEY=sk-...
codequality scan . --llm-review
codequality diff . --llm-review --llm-task "Add retry logic to the upload path"
codequality scan . --llm-review --llm-model claude-opus-4-8
```

Be clear-eyed about what this is: **this is the one feature in `codequality`
that breaks the "no LLM calls, no network access" promise from the top of
this README.** It makes a real API call to Anthropic, costs real money per
run, and — unlike every deterministic check above — the same input is not
guaranteed to produce the same output twice. It exists because two
questions genuinely can't be answered by parsing: is this a *good design*
for the problem, and does this diff do *what the task actually asked* (no
more, no less)? Those require judgment, not a formula, so this is the one
place in the tool that reaches for an LLM instead of `ast`.

Because it contradicts the tool's core promise, it's designed to be
impossible to trigger by accident:

- **Opt-in flag, always.** `--llm-review` is never implied by anything else,
  and `scan`/`diff` behave identically whether or not this feature exists
  if you don't pass it.
- **Opt-in extra, always.** The `anthropic` package is not a base dependency
  -- `pip install codequality[llm]` is required, following the exact
  `AVAILABLE`-flag pattern used by `--check-types` (mypy) and
  `--check-coverage` (coverage.py). Passing `--llm-review` without the extra
  installed, or without `ANTHROPIC_API_KEY` set, prints a clear error to
  stderr and exits non-zero -- it never silently skips the check you asked
  for, and it never fabricates a score in its place.
- **Never part of the 0-100 score.** The result is reported under its own
  top-level `"llm_review"` key (JSON), or a clearly labeled "LLM Review
  (opt-in, subjective -- not part of the score)" section (text/markdown) --
  never folded into `categories`, and never affects `--fail-under` gating
  or `--record-history`.
- **API key from the environment only.** `ANTHROPIC_API_KEY` is read from
  the environment, never from `.codequality.toml` or any other config file,
  so a key can't accidentally end up committed to the target repo.

What it reports, per run: a 0-10 **architecture** score, a 0-10
**readability** score, a 0-10 **instruction adherence** score (only when
`--llm-task "..."` is given a description of what the code was supposed to
do -- otherwise this is left `null`/unset rather than the model inventing a
task to grade against), and a short rationale.

Scope of what gets sent to the model: `codequality diff --llm-review` sends
the full unified diff (with context, unlike the `-U0` diff used for
scoring). `codequality scan --llm-review` sends the contents of the
lowest-scoring files (the same list as the "Lowest-scoring files" table),
not the whole repository -- this bounds the size and cost of the request
regardless of repo size, at the cost of not reviewing files the
deterministic score already considers healthy.

Model choice: the default is `claude-haiku-4-5-20251001`, Anthropic's fastest and
cheapest current model -- appropriate for a linting-adjacent check that
might run on every PR. Override it with `--llm-model` or
`$CODEQUALITY_LLM_MODEL` if you want a more capable (and more expensive)
model's judgment instead.

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

# External steps for `codequality pipeline` -- see "Pipeline" above.
# Empty by default: this tool never assumes which formatter/linter/
# benchmark a repo uses.
[[pipeline.steps]]
name = "format-check"
command = "black --check ."

[[pipeline.steps]]
name = "lint"
command = "ruff check ."
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
