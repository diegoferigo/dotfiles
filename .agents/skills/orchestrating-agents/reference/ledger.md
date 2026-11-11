# The persistent ledger (orchestrator memory)

## Contents

- Why it exists
- Where it lives
- Structure
- Status rendering
- Update cadence
- What the ledger persists for re-entry
- Compaction for multi-day runs
- Crash-safe state transitions
- Bootstrap-from-disk protocol
- The native `todos`

## Why it exists

On long tasks the CLI may **compact conversation history and lose information**. The orchestrator's authoritative memory must therefore live **outside the target repository**, written so that the current session can recover after compaction. Claim cross-session recovery only when the ledger contains every resume-critical field below.

## Where it lives

The canonical ledger is `${XDG_STATE_HOME:-~/.local/state}/orchestration/<repo-id>/<slug>/ORCHESTRATION.md`. It mirrors the layout of worktrunk, so one `find` or `rg` over the state directory and over `.worktrees/` uses the same names: `<repo-id>` is the worktrunk project identifier (`github.com/<owner>/<repo>`, as shown by `wt config show`) and `<slug>` is the sanitized branch name, or a short feature name when there is no branch. Work that spans several repositories is recorded under the repository that owns the integration branch. The ledger lives outside the repository, so the working tree stays clean, and it is plain Markdown that any harness can read. It is runtime state, not configuration: never put it under `.agents/` or inside the repository. Record the path in the first status update. When the work is done, set `Status: done` in the header so that old ledgers can be listed and pruned.

In a native session, keep `<session-folder>/files/ORCHESTRATION.md` as a symlink to the canonical ledger and list the session in the `Sessions:` line of the header (harness, session id, date), so that either side leads to the other. Link `tasks.toml` the same way. Write only to the canonical paths: the session entry is a read-only mirror, because an atomic rename over it would replace the symlink with a regular file.

The native `todos` of the harness are the agent's own notes, not part of the ledger. A short single-session task still keeps the ledger file, which can stay brief.

## Structure

```markdown
# Orchestration ledger: <feature>

Status: <active | done>. Task graph: tasks.toml, format 1. Constraints: constraints.md. Sessions: <harness, session id, date; one per line>.

## Goal
<one paragraph: what "done" means for the whole feature>

## Plan / decomposition
<the plan in prose: the epics and the order of the work. The tasks themselves are in `tasks.toml`>

## Interface contracts
<shared types / APIs / signatures every subagent must honor>

## Repository baseline
<repo root, branch, base SHA, initial dirty paths, execution mode, worktree map,
scratch directory, required integration checks, and any server-side topology
snapshot the workflow can mutate>

## Task graph
The task graph is not in this file. It is `tasks.toml` next to this ledger, and it holds the epics, the tasks, `deps`, `parent`, `status`, `priority`, `milestone`, `write_set`, the `blocker` of a task that waits on a human, and the `worktree` and `branch` where a task runs. Do not add a task table here, and never paste the output of a command under this heading. The format, the rules and the commands are in [task-graph.md](task-graph.md), and the format versions are in [formats.md](formats.md). The runtime state of an attempt (agent, tier and effort, reviewer, round, verdict, commit) lives in the Active agent registry below, which may also list the worktree of that attempt.

## Epics
<per epic: id, goal, details, a checkable definition of done, the acceptance check to run once all leaves are done, and its milestone. An epic is done only after that check ran and its result is in the evidence log>

## Task briefs
<the complete brief for every non-terminal task: objective, allowed files,
contracts, resource profile, exact checks, constraints, stop condition.
Each brief is briefs/<id>.md next to tasks.toml with the sections Change,
Acceptance, Constraints and Return, and the shared rules live once in
briefs/_common.md; see task-brief.md>

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

## Attempt events

Every attempt writes `launch` and `return` events to `events/<id>.jsonl` (fields in [task-runtime.md](task-runtime.md#attempt-events)), and may write `heartbeat`. `resume` derives alive, finished or orphan from them and prints the host and pane to reattach. An orphan is never resumed automatically: use the orphan reconciliation procedure above.

## What the ledger persists for re-entry

After a compaction of the conversation context, the orchestrator rebuilds its state from files alone. The design, the digest that `resume` is meant to print and the flow are in [compaction.md](compaction.md). What each file must hold for that to work:

- **`tasks.toml`:** every task with its `status`, `deps`, `blocker`, `worktree` and `branch`. It is the only place of the schedule.
- **The briefs:** the complete brief of every non-terminal task, written before its launch.
- **The agent registry:** one row per launch with the agent id, written as `pending` before the launch and replaced by the real id right after it, the worktree, the branch, the tier and effort, and the status `idle` for a returned writer that may get a follow-up, so that it can be steered by id.
- **Decisions & rationale:** every arbitration verdict and every decision, with its reason, dated, in the turn it is taken.
- **The inbox:** the context files `inbox/<task-id>.md` of the questions that wait on a human ([task-graph.md](task-graph.md#the-inbox-view)).
- **The constraints file:** `constraints.md` next to the ledger, named in the `Constraints:` header. It holds the standing rules of the run (user rules, tier and cap policy, no-push and consent rules), which a summary of the chat would drop.
- **Next actions:** the exact next step, always current.

The chat is not on this list. Anything that exists only in the chat is lost at the next compaction.

## Compaction for multi-day runs

This is the archiving of a large ledger file, not the compaction of the conversation context ([compaction.md](compaction.md)). Frequent writes do not require one unbounded file. Keep the active ledger small enough for a fresh orchestrator to read in one pass:

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

Update the narrative ledger atomically by writing a sibling temporary file and renaming it over the previous version only after the write completes. Keep the previous valid snapshot until the replacement succeeds.

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

1. Run `scripts/ledger_graph.py tasks.toml resume`, read the constraints file named in the header, then read the ledger fully. The flow and the digest are in [compaction.md](compaction.md#re-entry-flow).
2. Find every operation left in `prepared` or `running` and reconcile it before scheduling new work.
3. Process every non-terminal active-agent-registry row using the recovery protocol above.
4. Verify the recorded repo root, branch/base SHA and dirty baseline against git before touching anything, then reconcile each worktree with "Recover active work" in [../SKILL.md](../SKILL.md#recover-active-work).
5. Reconcile with `git log` and the remote refs.
6. Relaunch the unfinished tasks from the persisted task graph and full briefs when this is a new session. When the session runs on another harness than the one in `Sessions:`, mark every recorded agent ID as `stale`, relaunch the unfinished tasks from their briefs and add the new session to `Sessions:`.
7. Rebuild pending verdicts and resume from "Next actions". Do not trust in-context history over the ledger and git when they disagree.

## Register before launch

Every `task` call carries `[ledger:<id>]` in its prompt, where `<id>` is an `in_progress` task of `tasks.toml` with a `briefs/<id>.md`. Run `scripts/ledger_graph.py <tasks.toml> register ...` (or `set <id> status in_progress` for a planned task) first and paste the printed gate into the prompt. The agent checks it as its own first action ([task-brief.md](task-brief.md#ledger-gate)). Record every decision in `decisions.md` at the moment it is made, in every mode.

## The native `todos`

- `tasks.toml` is the only plan and the source of truth. A new session starts with `scripts/ledger_graph.py tasks.toml resume`.
- The `todos` of the harness are native and stay the agent's own: the ledger does not mirror them, and nothing syncs the ledger with them.
- Ledger file = durable narrative: decisions, contracts, evidence, verdicts.
- A companion state table may hold agent IDs, worktrees, write sets, tiers, reasoning effort and round counters.
