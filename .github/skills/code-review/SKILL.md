---
name: code-review
description: >-
  Reviews pull requests, diffs, and branches in this repo, with attention on
  findings that are high-signal instead of already owned by CI. Use whenever the
  user asks for a review, a re-review, or a sanity check of code or docs changes.
---

# Reviewing changes in dotfiles

Review for real defects, stale docs, and behavior drift, not for nits. Read `AGENTS.md` first, especially the repo structure, Python style, architecture, agent-skills, and testing sections, then treat `pixi.toml` and `.pre-commit-config.yaml` as the other sources of truth for what the toolchain runs.

## Understand intent first

Before judging a line, establish what the change is trying to do and whether the repo already documents a decision about it. Read the issue, PR description, and relevant comments when they exist, then check whether `AGENTS.md` already fixes the expected behavior, invariants, or workflow.

Hold every finding to evidence from the code or docs. A diff can be internally clean and still be the wrong change if it breaks a documented invariant or leaves the docs behind.

## What CI already owns

CI and hooks own `ruff`, `pyright`, `shellcheck`, `tombi`, `mdformat`, and `pytest`. Do not spend review comments on issues those checks should catch. CI runs `pixi run hooks`, `pixi run test`, gitleaks for known secret patterns, and a Codespaces bootstrap job. Skip cosmetic nits and formatting.

The review should add value where automation is weak: semantics, drift from the repo's documented workflow, stale architecture docs, broken rollback behavior, ownership mistakes, unsafe secret handling, and misleading tests.

## Zoom out beyond the diff

Do not anchor on the edited lines. Check the surrounding workflow and the untouched consumers that still rely on the changed behavior.

In this repo, always check these surfaces when they are relevant:

- `AGENTS.md` stays in sync with the code, especially the repo tree, key components table, CLI behavior, and test list.
- Behavior-neutral refactors stay behavior-neutral. Flag any hidden behavior change, reordered side effect, or loss of guard/rollback semantics.
- Secrets are never printed, committed, or echoed in review evidence. Redact values and treat accidental disclosure risk as a top-severity finding.
- Rollback and ownership semantics stay intact. A change that skips a backup, loses manifest ownership, clobbers a `.local` backup, or weakens uninstall or update safety is a serious bug.
- Tests clone committed `HEAD`, not the working tree. If a test change assumes unstaged code, or a code change claims coverage without the needed commit, flag it.
- `.local/bin/dotfiles` is a single-file `pixi exec` shebang script. Check startup, dependency assumptions, and CLI entry-point behavior as one unit.
- Install and update flows stay idempotent. Re-running bootstrap, `--update`, secret apply, or uninstall should not duplicate work, drift state, or degrade backups.

## Where to apply judgment

Focus on defects a static check will miss:

- Contradictions with `AGENTS.md`, `pixi.toml`, or `.pre-commit-config.yaml`.
- Partial doc updates that leave the architecture or test strategy stale.
- Incorrect backup, restore, sparse-checkout, autostash, secret, or manifest logic.
- Refactors that claim no behavior change but alter prompts, rollback order, state ownership, exit status, or what gets written to `$HOME`.
- Tests that assert the wrong contract, miss the risky path, or stop proving the documented behavior.

When reviewing docs-only changes, still verify the described behavior against the code instead of assuming the prose is correct.

## Severity levels

- `blocker`: secret exposure risk, data loss, broken rollback or ownership semantics, false or dangerous docs, or a change that can leave a machine unrecoverable.
- `major`: wrong behavior, broken idempotence, stale AGENTS guidance, invalid test coverage claims, or a regression likely to affect normal bootstrap or update use.
- `minor`: real but limited correctness or maintainability issue that CI does not own.

## Output format

Report only findings worth action. Use this shape for each one:

`- [severity] path/to/file:line - finding` `  Evidence: short quote or concrete behavior, plus the source of truth it contradicts.`

Keep each finding self-contained. Include `file:line`, evidence, and severity. Skip padding, praise, and nits CI already owns.
