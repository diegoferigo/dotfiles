# Subagent task brief & return contract

## Contents

- Principle
- Task brief template
- Structured return contract
- Spawning & tiering notes

## Principle

Subagents **do not share your context**. Every brief must be **self-contained**: a fresh, smaller model with no history should be able to complete the task from the brief alone. Give a bounded objective and an explicit stop condition; ask for execution, not advice.

Subagents also **do not inherit the skills you have loaded**. Resolve instruction precedence, read each applicable persona/style/workflow skill, and copy its current task-relevant constraints into the brief. Include concrete commit identity, message and trailer rules when the worker may commit; do not rely on a pointer to a skill the worker cannot read.

## Task brief template

Give each spawned subagent a prompt containing:

```markdown
## Objective
<single, bounded, verifiable outcome>

## Identity
- Task / attempt: <stable-task-id>/<attempt-number>
- Unique agent name: <name persisted in the active agent registry>
- Complete by: <soft deadline derived from expected runtime; overdue triggers inspection, not automatic reassignment>

## Context
<the minimum needed: feature summary, where this task fits>

## Interface contracts (must honor)
<shared types / APIs / signatures - copy from the ledger>

## Files
- May edit: <explicit list/globs>
- Must not touch: <explicit list/globs to avoid conflicts>
- Repository root: <absolute path>
- Execution mode: <shared main tree | isolated worktree>
- Worktree / working directory: <absolute path>
- Branch and base SHA: <branch> at <sha>
- Baseline: <initial git status/diff reference>
- Scratch directory: <absolute, already-created session path>
- Stack context: <none | stack id, layer, parent, target commit and whether amend is allowed>

## Resource profile (so the orchestrator can schedule parallelism safely)
- Compute: <estimated CPU cores/load; GPU device and VRAM if used>.
- Memory and disk: <estimated peak RAM; expected temporary/build disk>.
- Runtime and services: <expected duration; ports, databases, caches or devices>.
- Filesystem writes: <the exact repo paths/globs this task will create or modify, so two
  parallel tasks are only co-scheduled when their write sets are disjoint (see the
  concurrent-tree hazard in git-modes.md)>
- Repo access: <read-only research | writes to THIS repo working tree | writes only to a
  scratch/session dir | read-only on an EXTERNAL repo (e.g. a legacy clone), which is
  always parallel-safe>
- Git mutation: <none | may commit in isolated worktree>; under this
  orchestration runtime, workers never rebase, change stack topology or push
- Network / external services: <none | web/GitHub research | package install>
- Compatible parallel work: <tasks/resources that may safely run at the same time>


## Acceptance criteria (definition of done)
<binary, checkable statements>

## Tests / verification to run
<exact commands to build/test/lint; add tests first where possible>

## Constraints
<resolved style/workflow constraints, no new deps unless X,
performance/security notes, commit identity/message/trailers when applicable>

## Delegation
- Nested delegation: <forbidden | allowed>
- If allowed: <sub-cap, permitted agent types, independent write sets/worktrees>

## Stop condition
Finish when acceptance criteria are met and all checks are green. If blocked by a
missing decision, stop and report the open question. Do not guess on interfaces.

## Return format
<paste the return contract below>
```

## Structured return contract

Require every subagent to end with exactly this, so review is by evidence:

```markdown
### Summary
<what was done, in a few lines>

### Files changed
<path - one-line reason each>

### Commands run + results
<command -> pass/fail + key output (build, test, lint, typecheck; structural checks for non-code artifacts)>

### Assumptions
<anything inferred that the reviewer should confirm>

### Open questions
<blocking or follow-up items; empty if none>

### Self-assessment vs. acceptance criteria
<criterion -> met/not-met + evidence>
```

## Spawning & tiering notes

- Choose the agent type first: `explore` for substantial read-only code exploration, `task` for verbose builds/tests/installations, `general-purpose` for implementation, `code-review` for substantial diff review, `rubber-duck` for plan/design challenge, `research` for sourced web or GitHub research, and `security-review` first for an explicit vulnerability or security-review request.
- When available, `rubber-duck` automatically uses a contrasting model from the driving Claude/GPT session. Use that model diversity for plan and design challenge; do not treat it as an evidence-based diff reviewer.
- Choose model tier and reasoning effort separately using [review-rubric.md](review-rubric.md). Prefer the harness-configured defaults. Specify an exact model or effort override only when the user or active instructions require it and the chosen agent type supports it.
- Launch **independent** tasks in parallel up to the concurrency cap; serialize writers in shared-tree mode. Worktree writers still serialize overlapping files/interfaces and shared resources (see [git-modes.md](git-modes.md)).
- Nested delegation is forbidden unless the root orchestrator explicitly enables it in the brief. When enabled, give the worker a sub-cap, permitted agent types, and independent worktrees/write sets. Keep the tree shallow enough to repay coordination overhead and obey the harness depth limit.
- The concurrency budget is shared across the whole tree. A parent must give each child a sub-cap, and each level still serializes tasks that share files plus its own verify and commit critical section.
- Use a `rubber-duck` agent when a worker needs to sanity-check a decomposition, plan, or risky design before or alongside implementation. Use a separate reviewer agent when returned output is uncertain and needs evidence-based artifact review.
- **Spawn resumable subagents** if you want the same-agent revision loop and subagent-side commits: they must accept follow-up turns while idle. One-shot background spawns can't be messaged again. With those, `request-changes` means respawn-with-full-context (cold) and the orchestrator does the commits.
- For `request-changes`, send the fix list to the **same** live subagent as a follow-up turn (it retains its context and diff). Respawn only if its context is polluted or you're switching tier.
- If a subagent returns no useful output, **do not relaunch the same objective** - narrow or re-specify the task.
- Record every returned agent ID immediately. Reuse that ID for status, follow-up or cancellation through the harness's agent tools; `stop_bash` does not control agents.
- Do **not** brief a subagent to edit `site-packages` or any installed dependency in place; carry the fix at the source (a vendored recipe/patch) instead.
- Do not send a worker to `/tmp`. Put the absolute, already-created session scratch path in its brief; a fresh worker cannot infer "the session scratch directory".
