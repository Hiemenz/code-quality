# Subcommand reference

Detailed docs for every standalone `codequality` subcommand.

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

## `codequality complexity-regression`: per-function complexity comparison across any two refs

```bash
codequality complexity-regression . --from v1.2 --to v1.3
codequality complexity-regression . --from HEAD~20   # --to defaults to HEAD
codequality complexity-regression . --from origin/main --threshold 3 --format json
```

The same generalization `api-diff` is to `breaking-signature-change`,
applied to `complexity-regression` (above): `diff`'s check only ever
compares the working tree (or one `--base`) against the current `diff`
invocation, and only for files that happen to appear in that one diff.
`codequality complexity-regression` answers a different question instead:
"which functions have gotten meaningfully more complex between any two
points in history" — two tags, two branches, or two commit shas — walking
every Python file that exists at `--to` (via `git ls-tree`), fetching each
file's content at both ends, and running the exact same comparison
(`analyzers/complexity_regression.py`'s `compare_functions`, the same
function `diff` mode's wiring calls) on each pair. Still pure `ast`
comparison plus the same McCabe counting `python_analyzer.py`'s score
already uses — no LLM, no network call.

Unlike `api-diff`, there is no `removed-public-file`-style third outcome
here: a function (or a whole file) deleted between the two refs has no
*new* complexity number to report a regression on, so it's silently
skipped, the same "nothing to compare" treatment a brand-new function gets
in the other direction.

| Flag | Meaning |
|---|---|
| `path` | Git repo root to compare (default `.`) |
| `--from REF` | Git ref for the "before" state (required) |
| `--to REF` | Git ref for the "after" state (default `HEAD`) |
| `--threshold N` | Absolute complexity increase above which a function is flagged (default `5`) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

Exit code: `1` if any function's complexity regressed past the threshold,
`0` if none did, `2` on a git/usage error (e.g. a ref that doesn't
resolve) — so, like `api-diff`, it doubles as a CI gate, this time for
"did this release make anything meaningfully harder to reason about"
between two tags.

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

## Repo conventions: the scanned repo is the baseline

```bash
codequality conventions .
codequality conventions . --format json
```

Every other check scores code against *universal* rules. This subcommand
answers the question that matters most when judging LLM-written additions
to an existing codebase: **does the new code look like it belongs to this
repo?** It learns the repo's own dominant conventions — deterministically,
from the code being scanned, with no configuration of what the conventions
"should" be — then lists the files written against that grain:

- **type hints** — share of annotatable slots (params minus `self`/`cls`,
  plus returns) annotated. Flagged one-way only: a mostly-untyped file in
  a repo that has clearly committed to typing (≥70% typed overall). A
  typed file in an untyped repo is an improvement, not a deviation.
- **quote style** — single vs. double, from real tokenization; docstrings
  and triple-quoted strings excluded.
- **docstring style** — Sphinx (`:param x:`) vs. Google (`Args:`) vs.
  NumPy (`Parameters` + underline).
- **string formatting** — f-strings vs. `.format(...)`. `%`-formatting is
  deliberately ignored: `logger.info("%s", x)` is the *correct* logging
  idiom, not legacy interpolation.

A convention only becomes a baseline once the repo has committed to it
(enough samples, ≥75% agreement), and a file only deviates when it has
enough samples of its own *and* its majority points the other way — one
odd string literal is noise; a whole file against the repo's grain is
signal. Python-only in this first version (what counts as a convention is
language-specific). **Report-only**: no score, no gate, exit 0 — these go
on the review table, they don't break builds.

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to analyze (default `.`) |
| `--config PATH` / `--exclude` | Same conventions as `scan` |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

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

## Dead-code confidence

```bash
codequality dead-code-confidence .
codequality dead-code-confidence . --stale-days 30 --format json
```

The Structure category's cross-file `dead-code` check (see
[Cross-file dead code](#cross-file-dead-code) above) is a pure snapshot: a
public top-level function/class either has zero references anywhere else
in the repo right now, or it doesn't -- it has no notion of *when* that
code was last touched. A function that looks unused but was written
yesterday might just be new/in-progress work nobody has wired up yet; one
that's looked unused for two years is a much safer removal candidate.
`dead-code-confidence` adds that missing time dimension, the same way
`todo-age` (above) added one to the `todo-marker` style check: it calls
straight into `analyzers/dead_code.py`'s `find_dead_code` (no
re-detection, no re-litigating its exemptions -- `__all__`-exported
names, dunders, `test_*`/`Test*` hooks, and anything decorated are still
skipped exactly as they are in a normal `scan`) and, for every finding,
`git blame -w --line-porcelain HEAD` (same technique as `todo-age`/
`edit-distance`) finds the commit that introduced the exact `def`/`class`
line, and that commit's author date gives the finding an age.

`confidence` is a plain 3-tier label off that single number -- one signal
can't honestly support more precision than a coarse label, so this
doesn't invent a numeric score the way a genuinely multi-signal check
might (same "auditable, not a black box" convention as `scorer.py`'s
formulas). Given `age_days` and `--stale-days` (default 180):

- **`high`** -- `age_days >= stale_days` (default: at least 180 days old).
- **`medium`** -- `age_days >= stale_days / 2` (default: at least 90 days old).
- **`low`** -- everything else.

Results are sorted by `age_days` descending -- oldest, and so
highest-confidence, first. That ordering doubles as a prioritized removal
to-do list: the dead code that's been sitting untouched the longest is the
safest to look at first. The text report is a `file:line` / name / age /
confidence table, capped with an "...and N more" tail -- same convention as
`todo-age`'s stale-marker listing. A finding whose introducing line can't
be blamed (e.g. an untracked file with no git history yet) contributes
nothing rather than reporting a fake age, the same "no age to report"
rule `todo-age` follows.

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to analyze (default `.`) |
| `--config PATH` | Explicit config file (for `exclude`/`include_generic_languages`) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--stale-days N` | Age in days at/above which a finding is `high` confidence, half of which is `medium` (default 180) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

## AI code quality report

```bash
codequality ai-report .
codequality ai-report . --check-imports --check-types
codequality ai-report . --marker "Generated-By: MyBot" --since "3 months ago"
```

The four features above --
[`churn`](#tracking-ai-vs-human-rework),
[`edit-distance`](#edit-distance-how-much-of-a-commit-survives-to-head),
[`commit-lint`](#commit-message-quality), and
[`hallucination-rate`](#hallucination-rate) -- each already split the same
git history into AI-assisted vs. human by the same marker-substring
convention and answer one question apiece. Getting the full picture meant
running all four and combining the numbers by hand. `codequality ai-report`
is that combination, done once: a single dashboard with those four metrics
side by side, AI-assisted vs. human. **It is not a new detection
technique** -- it calls straight into each module's own `compute()` and
reshapes the results; every number it prints is traceable to one of the
four sections linked above.

`hallucination-rate`'s row only appears when at least one of
`--check-imports`/`--check-types` is passed (the same requirement that
subcommand has on its own); without either flag, `ai-report` still prints
the other three rows and notes that the hallucination-rate row was
skipped and why, rather than erroring out or showing a misleading zero.
`hallucination-rate` also has no notion of a time window, so combining it
with `--since` prints a caveat that that one row still covers full
history while the other three are scoped to `--since`.

Deliberately **no single fabricated "AI code quality score"**: rework rate
(a fraction of commits), edit distance (a fraction of lines), commit-lint
pass rate (a fraction of commits by an unrelated rule), and hallucination
rate (findings per 1,000 lines) are four different units measuring four
different things. Averaging them into one number would invent a weighting
that doesn't exist and imply a precision this data doesn't have, so the
report always shows the four numbers next to each other, clearly labeled,
and leaves interpretation to the reader.

The report header states how many commits were classified into each
group. A group with zero AI-marked (or zero human) commits shows `n/a` on
every metric for that group, not a fake `0%` -- same "an empty group is a
missing value, not a real zero" rule `churn.py` already follows -- so it's
easy to tell whether a repo actually has enough AI-marked history yet for
these numbers to mean anything.

| Flag | Meaning |
|---|---|
| `path` | Git repo root (default `.`) |
| `--marker` | Substring marking a commit AI-assisted (default `"Co-Authored-By: Claude"`) |
| `--since` | Only consider commits since this date/ref (git `--since` syntax; doesn't affect the hallucination-rate row -- see above) |
| `--check-imports` / `--check-types` | Same opt-in flags as `hallucination-rate`; gate whether that row runs |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

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

## Dependency risk: usage count x structural risk flags

```bash
codequality dependency-risk .
codequality dependency-risk . --top 10 --format json
```

**This is not a staleness or CVE check, and it can't become one.** The
natural next question after `dependency-check` above is "which of these
dependencies is most worth fixing first" -- which usually means "which
ones are outdated and heavily used." Answering "is this the latest
version" requires asking a package registry (PyPI/npm), and this tool's
core promise, stated at the top of this README, is no network access,
ever. `dependency-risk` answers a narrower, honest version of that
question instead, built entirely from two signals this tool already
computes offline:

- **usage count** -- how many times each declared dependency is actually
  imported across the codebase's Python files, via a real `ast` walk
  (`Import`/`ImportFrom` nodes, matched on the top-level module name --
  e.g. `import requests.sessions` and `from requests.auth import
  HTTPBasicAuth` both count as one use of `requests`). This is a proxy for
  "how much of the codebase would be affected if this package broke or
  had to be migrated off of." **Python-only in this first version** --
  there's no per-ecosystem regex fallback for JS `require`/`import`, Go,
  Ruby, etc., the same "document the limitation plainly instead of
  half-implementing it" choice this tool makes elsewhere (see `doc
  examples` above). Declared npm packages are still listed, always with
  `usage_count = 0`. Matching a declared PyPI name to the module Python
  code actually imports is a simple, deterministic normalization
  (lowercase, `-`/`.` collapsed to `_`) -- it does **not** resolve the
  well-known cases where a package's PyPI name and import name differ
  (`PyYAML` -> `yaml`, `beautifulsoup4` -> `bs4`, `python-dateutil` ->
  `dateutil`, ...); see `codequality/dependency_risk.py`'s module
  docstring for why that's a deliberate limitation rather than a
  hardcoded alias table.
- **structural risk flags** -- reuses `dependency-check`'s own findings
  directly (`inconsistent-pinning`, `duplicate-dependency`,
  `unpinned-in-lockfile-repo`, all described above): no new detection
  logic, this feature is purely a recombination.

`risk_score` is the package's usage count if it has at least one
structural issue from `dependency-check`, otherwise `0` -- heavy use of a
correctly-declared dependency isn't the problem this feature targets,
only heavy use *combined with* an existing structural inconsistency is.
Results are sorted by `risk_score` descending, so the top of the list is
the highest-priority dependency to look at first; a heavily-imported but
cleanly-declared package still appears in the report (for visibility) but
always at `risk_score = 0`, and a barely-used package with a real
structural issue ranks above it only if its own score is higher.

| Flag | Meaning |
|---|---|
| `path` | Repo root to analyze (default `.`) |
| `--config PATH` | Explicit config file (for `exclude`) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--top N` | Max number of packages to report (default 25) |

## Orphaned config references

```bash
codequality orphaned-config .
codequality orphaned-config . --format json
```

Same "structural consistency, not deep understanding" spirit as
`dependency-check`, applied to config files instead of dependency
manifests: rather than judging whether a CI job, a docker-compose
service, or a Makefile target is still *wanted* -- too fuzzy, would need
real understanding of intent -- this only checks the one thing that's
unambiguous either way: does the config file reference a local file/path
that no longer exists on disk. Three sources, each read independently:

- **GitHub Actions workflows** (`.github/workflows/*.yml`/`*.yaml`) --
  a `run:` step invoking a clear local script path, e.g.
  `run: ./scripts/deploy.sh` or `run: bash scripts/deploy.sh`, where that
  path doesn't exist relative to the repo root.
- **docker-compose files** (`docker-compose*.yml`/`compose.yml` at the
  repo root) -- a service's `build: context`/`build.dockerfile`
  (combined, when both are present), a bind-mount `volumes:` entry
  (`./local/path:/container/path`), or an `env_file:` entry, pointing at
  a path that doesn't exist.
- **Makefiles** (`Makefile`/`makefile` at the repo root) -- a target's
  recipe line invoking a script via a clear relative path.

Only clear, unambiguous relative paths are ever flagged: a leading
`./`/`../`, or an interpreter keyword (`bash`/`python`/`node`/...)
immediately followed by a path ending in a recognized script extension.
A bare command (`pytest`), an absolute path, a URL, or anything
containing a shell/CI variable expansion (`$FOO`, `${{ ... }}`) is
silently skipped rather than guessed at -- a false positive here is worse
than a missed one, since the whole point is a signal you can trust
without re-deriving it yourself.

No YAML (or Makefile) parser is used, even for the GitHub Actions case --
see `codequality/orphaned_config.py`'s module docstring for the full
reasoning. In short: the only structure this check needs out of a
workflow file is "what shell commands appear under `run:` keys," which a
few lines of indentation tracking answers directly without a hard new
PyYAML dependency for a plain `pip install .` (contrast this with
`--check-types`/`--check-coverage`, which *do* gate a real optional
dependency behind an `AVAILABLE` flag, because those checks need the real
thing -- mypy's type inference, coverage.py's instrumentation -- not just
string extraction).

Issues are reported `category="documentation"`, `severity="warn"`,
`symbol="orphaned-config-reference"` -- like `dependency-check`, this is
a standalone subcommand, not folded into `scan`'s score.

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

## Complexity x test presence risk

```bash
codequality complexity-coverage-risk .
codequality complexity-coverage-risk . --top 10
codequality complexity-coverage-risk . --format json --output risk.json
```

`hotspots` (above) crosses complexity with *change frequency*.
`complexity-coverage-risk` crosses the same complexity number with a
different, simpler second axis: does this file have a corresponding test
file at all? A highly complex file with zero tests anywhere is a much
stronger risk signal than complexity alone -- nobody has so much as
written a test for it -- and a much stronger signal than "untested" alone,
since an untested-but-trivial file isn't much of a risk either. Crossing
the two picks out the files most worth writing a test for first.

This is deliberately **structural test presence**, not actual line
coverage -- it never executes anything, so it can't tell you an existing
test actually exercises the risky branches. That's a different, stronger
claim, and it's what `--check-coverage` is for (opt-in, since it runs the
target repo's own test suite -- see
[Test coverage](#test-coverage-opt-in-executes-your-code)).
`complexity-coverage-risk` stays in the same no-execution trust-boundary
category as `hotspots`/`dependency-check`/`ownership`: it only reads file
paths and parses source.

For each non-test source file (test files themselves, per the same
`test_*.py`/`*_test.py`/`tests/`-directory convention
`codequality scaffold-properties` and the test-ratio-trend feature already
use, are skipped -- they don't need a test of their own):

- **`complexity`** -- the same "max cyclomatic complexity of any function
  in the file" convention `hotspots` uses (see
  [Hotspots](#hotspots-complexity-x-change-frequency) above for the full
  reasoning). A file with no functions at all scores 0 and is never a risk.
- **`has_test`** -- whether some file anywhere in the scanned repo looks
  like a test for this one, by filename stem alone: `foo.py` counts as
  tested if `test_foo.py` or `foo_test.py` exists anywhere in the scan, no
  matter what directory either one lives in. Purely a filename
  correspondence check -- no import-graph tracing, same "simple,
  heuristic, expect some noise" tradeoff as every other check in this
  tool.
- **`risk_score`** -- `complexity` for a file with no matching test, `0`
  for a file that has one (chosen over `None` so it sorts and formats like
  every other numeric column here without special-casing; `has_test` stays
  its own column precisely so a `0` risk score is never ambiguous with
  "measured and found safe").

Results are sorted by `risk_score` descending -- the prioritized "write a
test for this first" list. Both raw signals (`complexity`, `has_test`)
stay visible next to the composite score, same "auditable, not a black
box" convention as `hotspots`/`scorer.py`. The text report only lists
files with `complexity > 0` (a file with no functions can't be a risk, so
it would only crowd out the files actually worth showing).

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to analyze (default `.`) |
| `--config PATH` | Explicit config file (see [Configuration](#configuration)) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--no-generic` | Only analyze Python; skip the analyzer for other languages |
| `--top N` | Max number of files to report (default 25) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

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

## Configuration drift

```bash
codequality config-drift .
codequality config-drift . --format json
```

Flags sibling per-environment config files whose declared key sets don't
match -- e.g. `.env.production` missing a key that `.env.example` has, or
`config/production.yaml` missing a key that `config/dev.yaml` has. Two
independent sources, each read independently:

- **Root `.env` variants** (`.env`, `.env.example`, `.env.development`,
  `.env.production`, ...) -- every `.env*` file directly at the repo
  root, parsed as simple `KEY=value` lines (same parser `env-check`
  uses). `.envrc` (direnv's shell-script config, a different format
  entirely) is deliberately excluded even though it matches the `.env*`
  glob.
- **A `config`/`environments`/`envs` directory** at the repo root,
  grouped by file extension (`.env`/`.yaml`/`.yml`/`.json`) so a
  `dev.yaml` is only ever compared against other `.yaml` siblings, never
  against an unrelated `.json` shape.

Only key sets are ever compared, never values -- a rendered message may
show a key name (`AWS_SECRET_ACCESS_KEY`), never the value behind it.
There's deliberately no real YAML parser (same tradeoff
`orphaned-config` makes): only top-level, zero-indent `key:` lines are
extracted, so nested keys are invisible to this check. A group of fewer
than two comparable files produces no issues.

Issues are reported `category="documentation"`, `severity="warn"`,
`symbol="config-drift"` -- standalone subcommand, not folded into
`scan`'s score.

| Flag | Meaning |
|---|---|
| `path` | Repo root to check (default `.`) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

## Migration reversibility check

```bash
codequality migration-check .
codequality migration-check . --format json
```

Flags schema/data migrations that can't be rolled back, from three
independent, unambiguous sources -- never executes a migration or
connects to a database, everything comes from parsing the migration
file's own source:

- **Django migrations** -- a `migrations.RunPython(...)` call (inside a
  `migrations/` directory) with no second positional argument and no
  `reverse_code` keyword. Django raises `IrreversibleError` at rollback
  time in exactly this situation. Passing `RunPython.noop` as the
  reverse counts as reversible -- that's Django's own documented way to
  declare "intentionally does nothing on the way back," a deliberate
  choice this tool doesn't second-guess.
- **Alembic migrations** -- any file with the alembic fingerprint
  (top-level `revision =`/`down_revision =` assignments) that has no
  `downgrade()` function at all, or one whose body is effectively empty
  (`pass`/`...`/just its own docstring).
- **Raw up/down SQL pairs** (the golang-migrate/similar convention) --
  any `*.up.sql` file with no sibling `*.down.sql` in the same directory.

A migration that's irreversible *on purpose* (some data transformations
genuinely have no meaningful way back) will still be flagged -- this tool
has no way to know intent, only structure -- so a flagged file may be a
deliberate, accepted tradeoff rather than a mistake.

Issues are reported `category="correctness"`, `severity="warn"`,
symbols `irreversible-django-migration`/`alembic-downgrade-missing`/
`alembic-downgrade-noop`/`sql-migration-missing-down` -- standalone
subcommand, not folded into `scan`'s score.

| Flag | Meaning |
|---|---|
| `path` | Repo root to check (default `.`) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

## Feature flag aging

```bash
codequality feature-flags .
codequality feature-flags . --stale-days 180 --format json
```

Same "age it via git blame" idea as `todo-age`/`dead-code-confidence`,
applied to feature flags: finds flag-looking references/definitions
across the codebase and reports how long each has been sitting there. A
flag whose oldest reference predates `--stale-days` (default 180 -- a
longer runway than `todo-age`'s 90-day default, reflecting that a flag
typically needs a full release/rollout cycle before it's safe to remove)
is a cleanup candidate: either it should have been fully rolled out and
deleted by now, or it's effectively permanent configuration masquerading
as a flag.

Detection is a family of narrow, best-effort regexes -- there is no single
standard shape for "check a feature flag" across LaunchDarkly/Split/
Unleash/Django-waffle/home-grown dict lookups:

- **A flag-check call** -- `is_enabled("x")`, `flag_enabled("x")`,
  `feature_enabled("x")`, `flag_is_active(request, "x")`,
  `switch_is_active("x")`, `is_active("x")` -- the first quoted string
  literal anywhere in the parens is taken as the flag name, regardless of
  which argument position a given SDK puts it in.
- **A dict-like flag lookup** -- `FEATURE_FLAGS["x"]`, `flags.get("x")`.
- **A boolean flag constant** -- `ENABLE_NEW_CHECKOUT = True` or
  `NEW_CHECKOUT_ENABLED = False`.

Expect noise, especially from the dict-lookup shape (a variable that
merely contains "flag" in its name). This is an opt-in, best-effort
signal, not a hard rule -- same posture as `env-check`. Requires a git
repository (uses `git blame`); like `todo-age`, results are grouped by
flag name with the oldest reference's age deciding staleness.

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to scan (default `.`) |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--stale-days N` | Age in days after which a flag's oldest reference is flagged stale (default 180) |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

## Architecture conformance

```bash
codequality arch-conformance .
codequality arch-conformance . --format json
```

Config-driven import-direction check across named layers -- entirely
opt-in, a no-op unless `[architecture].layers` is declared (see
"Configuration" below). A layer is a name plus a list of dotted Python
module prefixes it owns; the declared *order* is the rule -- a layer may
import from itself or any layer declared *after* it, never one declared
before it:

```toml
[[architecture.layers]]
name = "api"
modules = ["myapp.api"]

[[architecture.layers]]
name = "service"
modules = ["myapp.service"]

[[architecture.layers]]
name = "data"
modules = ["myapp.data", "myapp.models"]
```

With the layers above, `api` may import `service`/`data`, `service` may
import `data`, but `data` importing anything from `service`/`api` is a
violation, and so is `service` importing `api`.

Deliberately does not resolve imports to actual files on disk -- that
would need real package-resolution logic (`sys.path`, namespace packages,
editable installs, ...). Instead, both a file's own layer and each
import's layer are decided purely by dotted-name prefix matching against
the file's own relative path (`myapp/service/orders.py` is module
`myapp.service.orders`). Only absolute imports are resolvable this way; a
relative import (`from . import x`) is silently skipped. A file whose own
module name doesn't match any configured layer is skipped entirely --
this check only ever judges files that were explicitly placed into a
layer.

Issues are reported `category="structure"`, `severity="warn"`,
`symbol="layering-violation"` -- standalone subcommand, not folded into
`scan`'s score.

| Flag | Meaning |
|---|---|
| `path` | Repo/directory root to check (default `.`) |
| `--config PATH` | Path to a `.codequality.toml`/`.json` config file |
| `--exclude PATTERN` | Glob to exclude, repeatable |
| `--format` | `text` (default) or `json` |
| `--output FILE` | Write the report to a file instead of stdout |

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

