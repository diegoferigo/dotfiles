# The persistent ledger (orchestrator memory)

## Contents

- Why it exists
- Where it lives
- Structure
- Status rendering
- Update cadence
- Compaction for multi-day runs
- Crash-safe state transitions
- Bootstrap-from-disk protocol
- Relationship with the `todos` table

## Why it exists

On long tasks the CLI may **compact conversation history and lose information**. The orchestrator's authoritative memory must therefore live **outside the target repository**, written so that the current session can recover after compaction. Claim cross-session recovery only when the chosen location persists across sessions and the ledger contains every resume-critical field below.

## Where it lives

Default to `<session-folder>/files/ORCHESTRATION.md` plus the session SQLite tables. This survives compaction without modifying or dirtying the target repository. For work that must survive a genuinely new session, use a stable external path such as `~/.copilot/orchestration/<repo-id>/<feature>/ORCHESTRATION.md`, record that path in the first status update, and store the complete resume state below. Never default to `ORCHESTRATION.md` or `.orchestration/` inside the target repository. A repo-local ledger is allowed only when the user or repository explicitly asks for it, and it must be gitignored unless it is an intentional deliverable.

For medium, single-session tasks, `todos`/`todo_deps` plus a dedicated session state table can serve as the structured ledger. `todos` is session-scoped and cannot support a cross-session claim by itself.

## Structure

```markdown
# Orchestration ledger: <feature>

## Goal
<one paragraph: what "done" means for the whole feature>

## Plan / decomposition
<numbered task list; each maps to a todos row id>

## Interface contracts
<shared types / APIs / signatures every subagent must honor>

## Repository baseline
<repo root, branch, base SHA, initial dirty paths, execution mode, worktree map,
scratch directory, required integration checks, and any server-side topology
snapshot the workflow can mutate>

## Task graph
| id | title | deps | status | agent | worktree | write set | tier/effort | reviewer | round | verdict | commit |
|----|-------|------|--------|-------|----------|-----------|-------------|----------|-------|---------|--------|
| t1 | ... | - | done | agent-id@time | /path | src/a.py | fast/default | fast | 1 | approve | <sha> |
| t2 | ... | t1 | in_rev | agent-id@time | /path | src/b/** | smart/high | smart (cap 5: base layer) | 2 | req-chg | - |
| t3 | ... | - | done | agent-id@time | /path | docs/** | fast/default | skipped: rename only, diff inspected | 1 | approve | <sha> |
| tS | whole-stack review | t1,t2,t3 | pending | - | - | - | smart/high | smart (fresh) | - | - | - |

## Task briefs
<the complete brief for every non-terminal task: objective, allowed files,
contracts, resource profile, exact checks, constraints, stop condition>

## Decisions & rationale
<dated bullets: choices made, alternatives rejected, why>

## Evidence log
<per task: commands run + results (build/test/lint), links to output>

## Escalation and agent state
<known agent ids and status, spawn time, iteration count, tier/effort changes,
failed attempts, and whether each agent can accept a follow-up turn>

## Active agent registry
| task | attempt | unique name | agent id | status | brief | worktree | branch/base | write set | tier/effort | complete by | last observed |
|------|---------|-------------|----------|--------|-------|----------|-------------|-----------|-------------|-------------|---------------|
<one row for every pending launch, running agent and returned agent>

## Operation journal
<operation id, task id, type, state, preconditions, expected result, recovery
action, observed result, and server topology snapshot for every non-idempotent
action>

## Open questions / risks
<blocking unknowns, escalations pending>

## Next actions
<the exact steps to take next, so a new session knows where to resume>
```

## Status rendering

The `status` shorthand keeps its shared-core meaning: report the current workflow state from the ledger without changing it. Include active agents versus the soft cap, a compact task table, blockers/open decisions and the next ready action. Add an ASCII DAG only when it reveals a non-obvious dependency.

Use this status mark legend consistently:

| mark   | state                                    |
| ------ | ---------------------------------------- |
| `[OK]` | done or approved                         |
| `[..]` | in progress, returned, or review pending |
| `[--]` | pending, queued, or ready                |
| `[XX]` | blocked                                  |

Base the suggested action on the ready set, agent registry, dependency graph, write/worktree ownership, blockers and budget. Reuse `deleg`, `deleg!` and `next`; route models and effort through [review-rubric.md](review-rubric.md#agent-model-and-reasoning-effort-routing).

## Update cadence

Write to the ledger **after every significant event, never batched**: plan created, subagent spawned (record its ID immediately), reviewer spawned, replaced or skipped (with the reason), review cap raised (with the reason), verdict emitted, nits applied, task committed, model/effort escalated, worktree changed, integration run. The "Next actions" section must always reflect the true current state.

## Compaction for multi-day runs

Frequent writes do not require one unbounded file. Keep the active ledger small enough for a fresh orchestrator to read in one pass:

1. At a daily checkpoint, before context pressure, or when terminal history dominates the active state, prepare an append-only archive segment under the same durable ledger directory.
2. Move only terminal task briefs, superseded evidence, verified operation details and closed decisions. Keep the goal, current profile and consent, non-terminal tasks, active agents, open operations, current topology, blockers and exact next actions in the active ledger.
3. Give each segment an ordered ID, covered event range and checksum. Add its path and one-line summary to an archive index in the active ledger.
4. Write and verify the archive first, then atomically replace the active ledger. Journal the compaction itself so recovery can distinguish old and new active snapshots.

Never compact a `prepared`, `running` or `blocked` operation, an active-agent row, the latest evidence for an unfinished task, or the state needed to reconcile an external mutation. Archives are history, not the source for the next scheduler decision.

## Crash-safe state transitions

No active task may exist without an active-agent-registry row and a complete persisted brief. The registry must be sufficient for a new session to reproduce the scheduler state without conversation history.

Treat every agent launch, worktree mutation, commit, cherry-pick, rebase, stack operation, push and external API write as a state transition:

1. **Prepare:** persist a unique operation ID, task/attempt ID, preconditions, expected result and recovery action before execution.
2. **Run:** mark it `running`, execute once, and capture the exact command or request.
3. **Reconcile:** inspect the real agent/git/remote state rather than assuming success from missing output.
4. **Complete:** record `applied` and then `verified`, or record `blocked` with the observed partial state.

Never repeat a `running` operation after a crash until reconciliation proves it did not apply. Use remote SHAs and explicit leases for pushes, and returned object IDs for API writes.

Write structured state in one SQLite transaction. Update the narrative ledger atomically by writing a sibling temporary file and renaming it over the previous version only after the write completes. Keep the previous valid snapshot until the replacement succeeds.

Before launching an agent, persist the full brief, unique task/attempt name, worktree, branch/base, write set, model/effort and a registry row with agent ID `pending`. Persist the returned ID immediately after launch.

After a session crash, a new session handles every non-terminal registry row:

1. Query the exact recorded agent ID. If the harness still exposes it, reattach and update `last seen`.
2. If the ID is unavailable, inspect the recorded worktree, branch, status, commits and expected output. Mark the attempt `orphaned`; do not assume its work was lost.
3. If valid work or a commit exists, review and continue from that state.
4. If continuation needs an agent, increment the attempt, spawn a replacement with the persisted brief plus recovered state, and assign the same worktree only after confirming no old agent can still write to it.

This makes the workflow reproducible even when an agent process cannot survive the session. Reattachment is preferred, but persisted briefs and isolated worktrees provide the fallback.

At every scheduling wave, scan `complete by` before filling new slots. An overdue timestamp is a prompt to inspect, not proof of failure:

1. Query the exact agent ID and update `last observed`.
2. If it returned, review it immediately. If it is still making progress, record a revised estimate and reason.
3. If it is responsive but stuck, send one bounded status/unblock request or mark the task blocked for respecification.
4. If it is unavailable, use the orphan reconciliation procedure above.

Never assign a replacement merely because a deadline elapsed. First prove the old attempt cannot still write, then reconcile its worktree and outputs.

## Bootstrap-from-disk protocol

On start, or whenever context feels degraded/post-compaction:

1. Read the ledger fully.
2. Find every operation left in `prepared` or `running` and reconcile it before scheduling new work.
3. Process every non-terminal active-agent-registry row using the recovery protocol above.
4. Verify the recorded repo root, branch/base SHA, dirty baseline and worktree map against git before touching anything.
5. Reconcile with `todos`, `git log`, remote refs and each working tree.
6. Recreate missing `todos`/`todo_deps` rows from the persisted task graph and full briefs when this is a new session.
7. Rebuild pending verdicts and resume from "Next actions". Do not trust in-context history over the ledger and git when they disagree.

## Relationship with the `todos` table

- `todos`/`todo_deps` = the machine-queryable task graph and status (use gerund titles, kebab-case ids, self-contained descriptions).
- Ledger file = durable narrative: decisions, contracts, evidence, verdicts.
- A companion state table may hold agent IDs, worktrees, write sets, tiers, reasoning effort and round counters without encoding them into todo prose.
- Keep the two consistent; the table drives "what's ready to delegate", the file explains "why and with what result".
