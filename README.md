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

# Does the test suite pass reliably, or does a test's result depend on
# luck? (opt-in, runs your test suite N times -- can be slow)
codequality flakiness .

# Property-based test usage + generated Hypothesis stubs to fill in
codequality scaffold-properties .

# What broke in the public API between two tags/branches/commits?
codequality api-diff . --from v1.2 --to v1.3

# Structural consistency checks on requirements.txt/pyproject.toml/package.json
# -- no network access, ever (see "Dependency consistency check" below)
codequality dependency-check .

# Which files are both complex AND changed constantly -- the highest-risk
# refactoring targets (see "Hotspots" below)
codequality hotspots .

# Env vars read in code vs. env vars documented in .env.example/README.md,
# flagged in either direction (see "Environment variable drift" below)
codequality env-check .

# Secrets that were ever committed, even if a later commit deleted the
# line -- deleting a line doesn't remove it from git's history
codequality history-secrets .

# Did someone accidentally commit a node_modules/, a build artifact, or a
# multi-MB binary blob into git? (see "Large/binary file check" below)
codequality large-files . --max-size-mb 5

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

`diff` additionally takes `--base REF` and `--head REF` (default: auto-detect,
see above).

`scan` additionally takes `--record-history FILE`, which appends this run's
overall/category scores as one JSON line to `FILE` — see
[Tracking score history](#tracking-score-history).

Nineteen more subcommands, each documented in its own section below:
`codequality baseline`, `codequality trend FILE`, `codequality churn`,
`codequality edit-distance`, `codequality commit-lint`,
`codequality hallucination-rate`, `codequality ownership`,
`codequality todo-age`, `codequality scaffold-properties`,
`codequality pipeline`, `codequality complexity-trend`,
`codequality dependency-check`, `codequality env-check`,
`codequality history-secrets` (secrets that were ever committed, even if
since removed — see [Secrets in git history](#secrets-in-git-history)),
`codequality large-files`, `codequality hotspots` (complexity crossed with
change frequency — see
[Hotspots](#hotspots-complexity-x-change-frequency)), and
`codequality api-diff` (public API comparison between any two git refs —
see
[`codequality api-diff`](#codequality-api-diff-public-api-comparison-across-any-two-refs)).
Plus `codequality mutation` and `codequality flakiness`, which are
deliberately separate from everything else — see
[Mutation testing](#mutation-testing) and
[Flaky test detection](#flaky-test-detection-executes-your-tests-n-times).

Exit codes: `0` = passed threshold, `1` = below threshold, `2` = usage/git error.

## How the score is built

Eight categories, each 0-100, combined by weight into the overall score:

| Category | Default weight | What it measures |
|---|---|---|
| Complexity | 15 | Cyclomatic complexity per function (McCabe-style: branches, loops, boolean operators, comprehensions, `except` clauses) |
| Structure | 10 | Function length, nesting depth, file length, circular imports (cross-file). Also reports (but doesn't score, see below) cross-file dead code: public top-level functions/classes never referenced anywhere else in the repo |
| Duplication | 10 | Copy-pasted blocks (6+ line sliding-window hash, cross-file) |
| Documentation | 8 | Docstring coverage on public functions and modules, stale docstrings that document a removed parameter, and Markdown code examples that no longer parse (reported, doesn't affect this category's score -- see below) |
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
imports/variables, cross-file dead-code detection, `pickle`/`yaml.load`
deserialization checks, assertion-free tests, broad exception-swallowing,
stale-docstring parameters, unreachable code, and circular imports.
Hardcoded-secret and `eval`/`exec` detection run for every language via a
line-level regex. Function-naming convention checks run for Python and,
when the `tree-sitter` extra is
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

## `codequality api-diff`: public API comparison across any two refs

```bash
codequality api-diff . --from v1.2 --to v1.3
codequality api-diff . --from HEAD~20   # --to defaults to HEAD
codequality api-diff . --from origin/main --format json --output api-diff.json
```

`diff`'s `breaking-signature-change` check (above) only ever compares the
working tree (or one `--base`) against the current `diff` invocation, and
only for files that happen to appear in that one diff — it's folded into a
single scoring run. `api-diff` is a different tool for a different
question: "what broke in the public API between any two points in
history," independent of any single `scan`/`diff` run. Point it at two
tags, two branches, or two commit shas and it walks *every* Python file
that existed at either ref (via `git ls-tree`), fetches each file's content
at both ends, and runs the exact same signature comparison
(`analyzers/signature_diff.py`) on each pair — still pure `ast` comparison,
no LLM, no network call, same as everything else in this tool.

Three outcomes per file:

- exists at both refs — compared exactly like `breaking-signature-change`
  (removed parameters, newly-required parameters, reordered positional
  parameters).
- exists only at `--to` (added since `--from`) — nothing to compare
  against, silently skipped.
- exists only at `--from` (deleted by `--to`) — flagged as
  **`removed-public-file`**, one issue per public top-level
  function/method/class the vanished file used to export (deleting a file
  deletes its entire public API in one shot, arguably the most breaking
  change there is), so the report stays at the same "one issue per symbol"
  granularity as every other check instead of collapsing to one vague "file
  removed" line.

| Flag | Meaning |
|---|---|
| `path` | Git repo root to compare (default `.`) |
| `--from REF` | Git ref for the "before" state (required) |
| `--to REF` | Git ref for the "after" state (default `HEAD`) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

Exit code: `1` if any issue was found (breaking change or removed file),
`0` if the API is unchanged between the two refs, `2` on a git/usage error
(e.g. a ref that doesn't resolve) — so it doubles as a CI gate for
"did this release break the public API" between two tags.

## Secrets in git history

```bash
codequality history-secrets .
codequality history-secrets . --since v1.0 --format json
codequality history-secrets . --all-commits   # scan full history, not just the most recent 500 commits
```

The Security category's `hardcoded-secret` check (above) only ever looks at
the current working tree — `scan`/`diff` parse the files as they exist
*right now*. That misses the actually dangerous case: a secret that was
committed and later deleted from the file is still sitting in one of git's
historical blobs forever, reachable by anyone who has (or ever clones) the
repo, unless it was scrubbed with something like `git filter-repo`/BFG.
Deleting the line only removes it from `HEAD`, not from history — most
people assume it does.

`history-secrets` walks commits (newest-first), diffs each one against its
first parent, and runs the *exact same* secret-detection pattern the
generic per-language analyzer uses (`codequality.analyzers.secrets` — one
shared module, imported by both the AST-based Python check and the
line-level regex check for every other language, so the two never drift
out of sync) against only the lines that commit *added*. For every hit, it
also checks whether that same secret value still appears in the file at
`HEAD`:

- **still in `HEAD`** — also caught by a normal `scan`; reported here for
  completeness.
- **removed from `HEAD` but still in history** — the headline finding of
  this whole feature: gone from the working tree today, but still fetchable
  from an old commit by anyone with clone access. Reported distinctly, and
  listed first in the text report.

Matched secrets are redacted in the report (first/last few characters
only, e.g. `sk-e...-key`) — never the full value, even though it's already
sitting in history regardless of what this tool prints.

Walking every commit in a large repo's full history means one `git diff` /
`git show` subprocess call per commit touched, which can be slow on a repo
with tens of thousands of commits. `--max-commits` bounds the walk to the
N most recent commits (default 500); pass `--all-commits` to scan
everything instead. `--since REF` bounds it to commits reachable from
`HEAD` but not from `REF` (a `git log REF..HEAD` range) — note this is a
git ref/tag/sha, unlike the date-string `--since` accepted by
`churn`/`edit-distance`/`commit-lint`.

| Flag | Meaning |
|---|---|
| `path` | Git repo root to scan (default `.`) |
| `--since REF` | Only walk commits reachable from HEAD but not from this ref |
| `--max-commits N` | Cap on how many of the most recent commits to walk (default 500) |
| `--all-commits` | Scan the entire history instead of capping at `--max-commits` |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

Exit code: `1` if any hardcoded-secret-looking line was found (in history
or still at `HEAD`), `0` if none were, `2` on a git/usage error.

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

## Flaky test detection (executes your tests N times)

```bash
codequality flakiness .
codequality flakiness . --runs 10 --test-command "pytest -q"
```

**This runs the target repo's own test suite repeatedly** — like
`--check-coverage`, it's in the "executes your code" trust-boundary
category, which is why it's a separate, explicitly-invoked subcommand
rather than something folded into `scan`/`diff`, and why it can be slow
(`--runs` executions of the whole suite; the default is 5). No judgment
is involved — it's deterministic repeated execution and comparison, not
a heuristic: a test is reported flaky if its pass/fail/error result
differs across at least two of the `--runs` runs.

`--test-command` takes the same convention as `--check-coverage` — the
args you'd normally pass after `python -m` (default: `"unittest discover
-s tests"`). A verbose flag (`-v`) is added automatically for recognized
`unittest`/`pytest` invocations so `codequality` can parse per-test
results out of the output; anything else (a custom runner, `nose2`, a
Makefile wrapper, ...) still runs `--runs` times, but the report falls
back to overall per-run pass/fail counts only, since there's no per-test
result to extract from output in a format `codequality` doesn't
recognize.

Report shape: total runs, and for each flaky test its sequence of
per-run results plus a flip count (how many times the result changed
between consecutive runs); a clean run reports `"N tests, N runs, 0
flaky"`.

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

## Commit message quality

```bash
codequality commit-lint .
codequality commit-lint . --strict --since "3 months ago"
```

`churn` and `edit-distance` look at what happened to the *code* a commit
touched; `commit-lint` looks at the commit *message* itself, walking the
same non-merge commit log and classifying AI-assisted vs. human by the
same marker convention (default `"Co-Authored-By: Claude"`). Each subject
line is run through a fixed set of structural/lexical rules — never
anything requiring understanding what the commit actually means, since
that would need an LLM, which this tool never calls:

- **`too-short`** (on by default) — subject line under a configurable
  minimum length (default 10 characters).
- **`generic-subject`** (on by default) — subject, case-insensitively and
  stripped of trailing punctuation, is *exactly* one of a banned list of
  placeholders: `fix`, `wip`, `stuff`, `update`, `updates`, `misc`,
  `changes`, `fixes`, `asdf`, `test`, `tmp`, `more changes`, `fix bug`,
  `bug fix`. Deliberately an exact match only, so a real descriptive
  subject like "Fix the null pointer in auth" is never flagged just for
  starting with "fix".
- **`trailing-period`** (opt-in via `--strict`) — subject ends with a
  period.
- **`not-capitalized`** (opt-in via `--strict`) — subject doesn't start
  with an uppercase letter.

The first two are on unconditionally since they catch messages that are
almost never intentional; the `--strict` pair is more a house-style
opinion than a quality signal, so it's off unless asked for. The report
shows, per group, how many commits failed at least one rule, a breakdown
by rule name, and a capped list of the actual failing commits (short sha +
subject + which rules failed) — same "cap the list, keep the true total"
convention as the issue listing in `scan`/`diff`.

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

## Ownership / bus factor

```bash
codequality ownership .
codequality ownership . --marker "Generated-By: MyBot" --threshold 0.8
```

`hallucination-rate` attributes specific *findings* to AI vs. human via
`git blame`; `ownership` uses the same blame-driven attribution but asks a
plainer per-file question, with no findings required: for every file,
who currently owns its lines, and how much of it came from an AI-assisted
commit? For each scanned, git-tracked file it runs `git blame
--line-porcelain -w HEAD` (same technique as `edit-distance`'s blame pass)
and reports two deliberately separate signals:

- **`top_author_share`** — of the file's current lines, the share owned
  by its single largest-contributing git identity (author name + email).
  This is the bus-factor signal: a file where one identity owns 90%+ of
  the lines (the default `--threshold`) is flagged `low-bus-factor` — a
  risk if that person leaves, or, read the other way, "one clean file
  nobody has needed to touch since it was written."
- **`ai_line_fraction`** — of the file's current lines, the fraction last
  touched by a commit whose message matches the AI marker (same
  `--marker` convention as `churn`/`edit-distance`/`commit-lint`/
  `hallucination-rate`, default `"Co-Authored-By: Claude"`,
  case-insensitive).

These two are kept as separate columns rather than folded into one,
deliberately: authorship is tracked per-*commit* (an AI-assisted commit's
author is still a human's git identity, just one who had help), while
concentration is tracked per-*identity*. A file whose top contributing
identity happens to be AI-marked doesn't make "top author" itself an AI —
conflating the two would misreport both. `low_bus_factor` is
informational/reporting only, like `churn`/`edit-distance`/
`hallucination-rate`/`dependency-check` — there's no pass/fail gate here.

The text report is a table sorted by `top_author_share` descending, with
`ai_line_fraction` shown as its own column rather than a second sort key.

| Flag | Meaning |
|---|---|
| `path` | Repo root to analyze (default `.`) |
| `--marker` | Substring marking a commit AI-assisted (default `"Co-Authored-By: Claude"`) |
| `--threshold` | Single-identity line share at/above which a file is flagged `low-bus-factor` (default `0.9`) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

## TODO aging

```bash
codequality todo-age .
codequality todo-age . --stale-days 30 --marker "Generated-By: MyBot"
```

The Style category's `todo-marker` check (see the table above) flags every
`TODO`/`FIXME`/`XXX`/`HACK` comment as a snapshot — it can't tell a marker
added yesterday from one that's been sitting there for three years.
`todo-age` adds that missing time dimension: for every marker line found
by the same regex the style check uses, `git blame -w HEAD` finds the
commit that introduced that exact line, and that commit's author date and
AI-assisted/human classification (same marker-substring convention as
`churn`, default `"Co-Authored-By: Claude"`) give the marker an age and an
origin. Anything older than `--stale-days` (default 90) is flagged
`stale-todo`. Results are rolled up per group — count, average age,
oldest marker, and how many are stale — the same two-group shape as
`churn`/`edit-distance`/`commit-lint`, so you can see whether AI-introduced
TODOs tend to linger longer than human ones, or get cleaned up sooner, in
*your* repo's actual history. The text report also lists the stale
markers themselves (file:line, age, snippet, group), capped and with an
"...and N more" tail — same convention as `commit-lint`'s failure listing.

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

## Dependency consistency check

```bash
codequality dependency-check .
codequality dependency-check . --format json
```

Parses whichever dependency manifests exist at the repo root --
`requirements*.txt` (including `requirements/*.txt`), `pyproject.toml`'s
`[project.dependencies]`/`[project.optional-dependencies]` (via `tomllib`,
same graceful-skip-on-old-Python convention as `.codequality.toml`
support -- see [Configuration](#configuration)), and `package.json`'s
`dependencies`/`devDependencies` -- and runs three purely structural
checks, no network access, ever:

- **`inconsistent-pinning`** -- a manifest where 70%+ of dependencies are
  pinned to an exact version flags the ones that aren't (and vice versa).
  The inconsistency itself is the signal, not "unpinned is bad" -- a
  manifest that's consistently one style or the other never triggers this.
- **`duplicate-dependency`** -- the same package declared in more than one
  manifest (e.g. both `requirements.txt` and `requirements-dev.txt`, or
  `dependencies` and `devDependencies`) with *different* version specs --
  a real source of "works on my machine" bugs. Declaring it identically in
  both places, or in only one place, never triggers this.
- **`unpinned-in-lockfile-repo`** -- if the repo has a lockfile present
  (`package-lock.json`, `poetry.lock`, `Pipfile.lock`, `uv.lock`, or any
  `*.lock`), a dependency declared with no version constraint at all is
  flagged as a lower-confidence, best-effort signal. Lockfile *contents*
  are never parsed -- only their presence/absence -- so this stays cheap
  and purely structural.

This is explicitly **not** a vulnerability or staleness scanner: it never
asks a registry what the latest version is, so it can't tell you a pin is
old or has a CVE -- doing that would require a network call, which
contradicts this tool's core "no network access, ever" promise (see the
top of this README). It only checks whether the manifests as they already
exist in the repo are internally consistent. Issues are reported with the
same `Issue` shape as every other check (`file`, `line`, `category`,
`severity`, `symbol`, `message`), categorized `style` -- see
`codequality/dependency_check.py`'s module docstring for why `style` over
`correctness`. Like `churn`/`edit-distance`/`mutation`, this is a
standalone subcommand, not folded into `scan`'s score.

| Flag | Meaning |
|---|---|
| `path` | Repo root to check (default `.`) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

## Hotspots: complexity x change frequency

```bash
codequality hotspots .
codequality hotspots . --since "6 months ago" --top 10
codequality hotspots . --format json --output hotspots.json
```

Every check above answers "is this file complex" or "is this file changed
a lot," but never both at once. A complex file that's been stable for a
year is low-risk -- nobody's touching it, so its complexity isn't costing
anyone anything right now. A complex file that a different person edits
every week is the opposite: every one of those edits has to first
untangle the same complexity, and each one is a chance to get it wrong.
`codequality hotspots` is Michael Feathers' classic "hotspot" technique --
cross per-file complexity with git change frequency to find the second
kind of file, which is a far better prioritized refactoring to-do list
than complexity alone.

It's not a new analysis; it recombines two numbers this tool already
computes elsewhere: a normal full scan (same per-function cyclomatic
complexity `scorer.py` already scores) plus one `git log --name-only` pass
over the whole repo's history (same single-pass-over-the-whole-log
technique `codequality churn`'s internals use, so this stays one git
invocation regardless of file count instead of one `git log --follow` per
file). For each file:

- **`complexity`** -- the *maximum* cyclomatic complexity of any function
  in the file, not the average, so one deeply-tangled function isn't
  diluted into blandness by a pile of trivial one-line helpers sharing its
  file. A file with no functions at all scores 0 and can never be a
  hotspot, no matter how often it's touched.
- **`commit_count`** -- how many non-merge commits touched that file
  (optionally scoped with `--since`, same `git --since` syntax as
  `churn`/`edit-distance`/`commit-lint`).
- **`hotspot_score`** -- `complexity * log(commit_count + 1)`. The log
  dampens churn so a file touched 500 times isn't literally 500x riskier
  than one touched 50 times -- a real difference, but not a
  two-orders-of-magnitude one -- while still letting a heavily-churned
  file clearly outrank an equally complex one that's barely been touched.

See `codequality/hotspots.py`'s module docstring for the full reasoning
behind the formula; both raw numbers stay visible in the output alongside
the composite score, same "auditable, not a black box" convention as every
other score in this tool.

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to analyze (default `.`) |
| `--since REF` | Only count commits since this date/ref (`git --since` syntax) |
| `--top N` | Max number of files to report (default 25) |

## Environment variable drift

```bash
codequality env-check .
codequality env-check . --format json
```

Compares environment variables actually *referenced* in the codebase
against the ones actually *documented*, and flags a mismatch in either
direction:

- **`undocumented-env-var`** -- read somewhere in the code (`os.environ["X"]`,
  `os.environ.get("X")`, `os.getenv("X")` for Python -- a real `ast` walk,
  not regex) but not found in any documented source.
- **`unused-documented-env-var`** -- documented, but never referenced
  anywhere in the scanned code.

"Documented" comes from whichever of these actually exist in the repo --
`.env.example`/`.env.sample`/`.env.template` (plain `KEY=value` lines,
comments/blanks ignored), and/or a section of `README.md` that looks like
an env var reference: a fenced code block shaped like a `.env` file, or a
markdown table with a header cell like "Environment Variable"/"Env Var".
If neither is present, the documented side is simply empty rather than
guessed at -- every usage found in the code is then reported as
`undocumented-env-var` (nothing to compare it against), and no
`unused-documented-env-var` can fire.

Python detection is exact (`ast`, same reliability as every other
Python-specific check in this tool); every other language (JS
`process.env.X`, Go's `os.Getenv("X")`, Ruby's `ENV["X"]`, PHP/C's
`getenv("X")`, Java's `System.getenv("X")`, ...) falls back to a
line-level regex, the same heuristic tradeoff
`analyzers/generic_analyzer.py` already makes for languages without a
real parser here. Both issue types are reported `category="documentation"`,
`severity="info"` -- a heuristic signal to look at, expected to be noisy
especially from the non-Python fallback, not something that fails a build
on its own. Like `dependency-check`, this is a standalone subcommand, not
folded into `scan`'s score.

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to check (default `.`) |
| `--config PATH` | Explicit config file (for `exclude`/`include_generic_languages`) |
| `--exclude PATTERN` | Glob to exclude, repeatable |

## Large/binary file check

```bash
codequality large-files .
codequality large-files . --max-size-mb 10 --format json
```

A common real-world accident: someone commits a `node_modules/`
directory, a build artifact, a multi-hundred-MB dataset, or a binary blob
(image, zip, `.pyc`, compiled library) into git. Git history doesn't
forget a committed blob -- even deleting the file in a later commit
leaves its bytes in every clone forever short of a history rewrite -- so
this is worth catching early, and it needs no code understanding: just
file size and a content sniff, read straight from `git ls-tree -r -l
HEAD` (which reports each tracked blob's size directly, and reflects what
is actually committed rather than the working tree, so an untracked huge
file sitting around locally is correctly ignored). Two checks, both
per-file:

- **`large-file`** -- the blob is bigger than `--max-size-mb` (default 5).
  Severity `warn`.
- **`large-binary-file`** -- the file is very likely binary: a NUL byte in
  its first few KB (the same heuristic git/most diff tools use), or a
  known binary extension (images, archives, compiled artifacts, fonts,
  ...). Always reported when detected, but severity is `warn` only once
  the file also clears a much smaller size floor (100KB) -- a tiny binary
  file (a small icon, a tiny fixture) is just `info`, a "worth a look, not
  a build-breaker" signal.

No attempt is made to guess "is this an intentional assets directory" --
same "no cleverness, just reproducibility" tradeoff as every other
heuristic check in this tool, so false positives (a legitimately-committed
image or fixture) are expected and fine. Issues use the same `Issue`
shape as every other check, categorized `structure` (this is about the
physical size/shape of what's checked into the repo, not line-level
tidiness or code behaving correctly -- see
`codequality/large_files.py`'s module docstring). Like
`dependency-check`/`churn`/`mutation`, this is a standalone subcommand,
not folded into `scan`'s score: most of what it flags (binaries,
oversized blobs) was never discovered by `scan`'s file walk in the first
place, so there's no per-file score to attach it to.

| Flag | Meaning |
|---|---|
| `path` | Git repo root to check (default `.`) |
| `--config PATH` | Explicit config file (only `exclude` patterns are used) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--max-size-mb N` | Flag tracked files bigger than this as `large-file` (default 5) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

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

## Per-function complexity trend

```bash
codequality complexity-trend snapshot . -o complexity-history.jsonl
codequality complexity-trend show complexity-history.jsonl
codequality complexity-trend show complexity-history.jsonl --top 10 --format json
```

`scan --record-history`/`trend` (above) track one number over time: the
overall repo score. `complexity-trend` tracks a finer-grained one: the
cyclomatic complexity of every individual function (the same per-function
number the Complexity category is already scored from -- see
`codequality/analyzers/base.py`'s `FunctionMetrics.complexity`), so you can
see *which* functions have been quietly getting more complex, not just
that the overall score moved.

It's two subcommands, meant to be run repeatedly over time the same way
`--record-history` is -- typically once per CI run on your main branch:

- **`complexity-trend snapshot [path] -o FILE`** runs a normal scan and
  appends one JSON line to `FILE`, recording every function's current
  complexity (keyed `path::function_name`) plus a timestamp and the
  current git commit sha. `FILE` is created if it doesn't exist yet, and
  each run appends -- never overwrites -- so `FILE` accumulates one line
  per run, the same append-only shape as `--record-history`'s file (but
  its own separate file; the two are not interchangeable).
- **`complexity-trend show FILE [--top N] [--format text|json]`** reads
  every snapshot back, compares the *earliest* and *most recent* one, and
  reports every function present in both -- sorted by biggest complexity
  increase first, which is the actionable part: the top of that list is
  "these functions have been getting more complex over time and are
  worth a look." A function that only shows up in one of the two
  snapshots (new or deleted since) is left out, since there's no
  before/after to compare. `--top` caps how many rows are shown (default
  25). Fewer than two snapshots in `FILE` just produces an empty report,
  not an error.

Like `codequality-history.jsonl`, the cheapest way to persist the snapshot
file across CI runs is to commit it to the repo from a main-branch
workflow step.

This is a fully self-contained feature -- its own module
(`codequality/complexity_trend.py`), its own snapshot file format, and its
own subcommand. It shares no code or storage with `scan
--record-history`/`codequality trend` (overall score history) or with any
test-to-code-ratio tracking; each of those is a separate timeline over a
separate JSONL file.

## Development

```bash
python3 -m unittest discover -s tests
```

The tool dogfoods itself — `codequality scan .` on this repo is part of
sanity-checking any change to the analyzers or scorer.
