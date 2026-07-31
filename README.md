# ci

Shared CI for `nickjoven/*`. One definition, referenced everywhere, audited into place.

## Why this exists

CI kept surfacing as a task. The reason is structural: a workflow copied into
each repository is a workflow that must be *maintained* in each repository, and
maintenance is precisely what you notice — a red X per repo, a stale action
version per repo, the same fix applied twenty times.

The account before this repo existed:

```
0    ket            ← the flagship
0    canon.d
13   harmonics
```

Coverage was uncorrelated with importance. It tracked which repo I happened to
be in the mood to wire. That is the signature of reproduction.

Archivist Law 4 already had the answer: **reference over reproduction. Never
store content that can be referenced.**

## Three layers

**1 · Reference.** The workflows here are `on: workflow_call`. Each repo carries
an eight-line stub that calls one. Bump an action version here and every caller
inherits it; no repository accumulates CI of its own.

```yaml
jobs:
  ci:
    uses: nickjoven/ci/.github/workflows/rust.yml@main
```

**2 · Convergence.** `scripts/audit.py` enumerates the account, detects each
repo's language, and opens a PR adding the stub wherever it is missing.
Scaffolding at creation time fails because you can forget to use the scaffold.
An audit that reconciles cannot be forgotten — a new repo appears, the next run
wires it. Coverage stops being something you do and becomes something that is
true.

**3 · Silence.** A `pre-push` hook (shipped via chezmoi's `init.templateDir`)
runs the same checks locally. CI only surfaces when it *discovers* something; if
local caught it thirty seconds earlier, CI is confirming, and confirmation is
quiet.

Net effect: you notice CI twice — when you set it up, and if it ever legitimately
catches a bug.

## Reusable workflows

| Workflow | Checks | Notable inputs |
|---|---|---|
| `rust.yml` | `cargo fmt --check`, `clippy -D warnings`, `cargo test` | `strict-fmt`, `run-tests`, `test-args`, `clippy-args`, `toolchain` |
| `python.yml` | `compileall`, `ruff`, `pytest` if a suite exists | `strict`, `run-tests`, `python-version` |

Defaults are deliberately tolerant. **A rollout that turns eight repos red at
once trains you to ignore the notification** — which is the failure mode this
whole system exists to prevent. Tighten per-repo once a repo is actually clean.

## The audit

```bash
python3 scripts/audit.py                    # dry run, report only
python3 scripts/audit.py --apply            # open PRs
python3 scripts/audit.py --only ket canon.d # restrict
python3 scripts/audit.py --max-age 90       # ignore repos dormant >90d
```

It classifies every repo as **adopted** (references this repo), **own** (has
workflows of its own — left alone), **actionable** (no workflows, has a Rust or
Python marker), **dormant**, or **not applicable**.

The `own` category is load-bearing. An earlier version checked for a file named
literally `ci.yml`, which flagged `harmonics` — thirteen workflows, gate named
`ci-gates.yml` — as uncovered. That would have opened a noise PR against the most
rigorously tested repo in the account.

Nothing merges automatically. Same discipline as
`harmonics/substrate-maintenance.yml`: the bot reconciles what is mechanical and
refuses anything requiring judgment.

## Setup

The scheduled audit needs a PAT with `repo` scope as secret **`CI_AUDIT_TOKEN`**.
The default `GITHUB_TOKEN` is scoped to this repository and cannot write to
others.

```bash
gh secret set CI_AUDIT_TOKEN --repo nickjoven/ci
```

Runs Mondays 09:00 UTC, or on demand via `workflow_dispatch` with a `dry-run`
toggle.
