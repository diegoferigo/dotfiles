---
name: orchestrating-agents
description: >-
  Orchestrates agentic coding by decomposing complex features into independent
  tasks, delegating to cheaper/faster subagents, reviewing each result by
  evidence, maintaining a ledger, and integrating the final change. Also runs
  crash-safe stateful and event-driven workflows such as review/CI loops through
  gather-classify-act reconciliation, including single-agent execution. Use
  whenever the user wants to orchestrate agents, run a multi-agent pipeline,
  coordinate subagents, split a large feature, delegate to cheaper/faster
  subagents, run parallel worktree-based implementation, or keep useful work
  moving while an external workflow waits, even if they do not name this skill.
---

# Orchestrating agents for agentic coding

## Contents

- [The core loop](#the-core-loop)
- [Stateful workflow runtime](#stateful-workflow-runtime)
- [Quick start](#quick-start)
- [Command shorthands](#command-shorthands)
- [Guardrails](#guardrails)
- [Improving this skill](#improving-this-skill)
- [Reference files](#reference-files)
- [Examples](#examples)

Act as the **orchestrator**: plan, delegate, review, and integrate. In a decomposed implementation pipeline, keep direct implementation to tiny nits. A stateful workflow may instead assign its single writer to the main agent when the execution-mode rules select single-agent operation.

If the repo or project provides its own orchestration workflow, follow that first; this personal skill fills gaps.

When skills compose, the domain skill owns consent and completion semantics, this skill owns scheduling, execution ownership and journaling, and a tool skill such as `gh-stack` owns exact commands and recovery. The runtime may narrow who executes a mutation, but it never broadens domain consent.

Two disciplines make this work:

1. **Delegate implementation and review by evidence.** Read diffs, summaries, and test output instead of whole files; direct coding consumes the context budget this pattern protects.
2. **Keep durable memory outside the target repository.** The ledger supports compaction recovery by default; claim cross-session recovery only when it contains the complete persisted state defined in `ledger.md`.

## The core loop

```
1. PLAN     Decompose the feature into independent, verifiable tasks.
            Define interface contracts, acceptance criteria, and the task graph.
            Write everything to the ledger. ([reference/ledger.md](reference/ledger.md))
2. DELEGATE Spawn a subagent per ready task with a self-contained brief.
            Run independent tasks in parallel at each useful tree level
            (respecting the shared concurrency cap).
            ([reference/task-brief.md](reference/task-brief.md))
3. REVIEW   When a subagent returns, judge by evidence against acceptance
            criteria and emit one verdict; for non-code tasks, require
            deterministic structural-check output. Optionally use a separate
            read-only reviewer subagent for uncertain, larger, critical, or
            ambiguous artifacts;
            you still arbitrate and issue the verdict. ([reference/review-rubric.md](reference/review-rubric.md))
              approve            -> commit the task (commits are serialized;
                                   see [reference/git-modes.md](reference/git-modes.md)), release the subagent
                                   (let it go idle), mark done
              accept-with-nits   -> YOU apply the tiny nits, re-run the fast
                                   check, commit; release the subagent
              needs-evidence     -> run/request the missing check, no code change
              request-changes    -> send fixes to the SAME live subagent, repeat
              reject-and-respec  -> re-scope the task, respawn
4. INTEGRATE Merge dependent tasks, run the full build/test/lint suite (or a
            deterministic structural check for non-code artifacts) with no
            active writers, then do a final review. This serialized
            run, not the subagents' self-reported checks, is the authoritative
            green gate.
5. LEDGER   Update the ledger after EVERY significant event (never batch it).
```

## Stateful workflow runtime

For review/CI loops and other event-driven workflows, use the same orchestration engine through [reference/workflow-runtime.md](reference/workflow-runtime.md). The domain skill supplies a workflow profile with units, gather sources, classification, dependencies, completion, reopen rules and consent. This skill owns scheduling, active waiting, agents, resources, git isolation, durable state and crash recovery. Single-unit and multi-unit runs share one primitive.

**Start parallel-safe work after planning.** Launch independent read-only, external-repo and isolated-worktree tasks promptly. In shared main-tree mode, only one writer runs at a time; other slots may run read-only work or commands that do not touch that tree. Keep one slot free for review or a revision unless the user explicitly prioritizes using all capacity. See [reference/git-modes.md](reference/git-modes.md).

**Tooling map (Copilot CLI):** interactive slash commands such as `/plan`, `/fleet`, `/worktree`, `/review` and `/rubber-duck` are user-facing controls, not assumed callable tools. The orchestrator spawns through the available agent tool and selects an agent type using [reference/task-brief.md](reference/task-brief.md). Prefer configured model defaults; override model or reasoning effort only when the user or active instructions require it. Persist the full brief and a pending agent-registry row before launch, then record every returned agent ID immediately.

## Quick start

1. **Scope & plan.** Restate the goal, then decompose into tasks that are each a coherent unit with its own checks. Record the goal, dependencies, contracts and acceptance criteria in the ledger and `todos`. Size tasks as **S** (focused), **M** (multi-file or judgment-heavy) or **L** (must be decomposed). **XL/XXL** are planning signals, not tasks: split them before delegation. Start S tasks on the fast tier, M tasks on the tier their risk requires, and never delegate L or larger as one unit. Consider a stacked-PR output for a large sequential deliverable; see [reference/git-modes.md](reference/git-modes.md).
2. **Define done first.** For each task write explicit acceptance criteria and, where possible, the tests, *before* delegating. Review then becomes binary.
3. **Delegate ready tasks.** For each task with all deps `done`, spawn a subagent using the brief template. Choose the starting tier from the task's real need: use the **fast/cheap tier** for ordinary or mechanical tasks, and start judgment-heavy, high-complexity, high-ambiguity, or critical/hard-to-reverse tasks directly on the **smart tier**. Escalate per the escalation policy when a round proves under-powered. Launch independent tasks in parallel up to the cap. Nested delegation is off unless the root brief explicitly authorizes it with a sub-cap, permitted agent types and isolated write sets. Stagger reviews as workers return and start dependent/fix rounds as results arrive.
4. **Review on return.** Require the subagent's structured return (files, commands run + results, assumptions, open questions, self-assessment). Verify the reported checks are relevant and green; if confidence is low, route the artifact to a separate reviewer instead of accepting it on trust. Emit exactly one verdict.
5. **Iterate.** For `request-changes`, reuse the same subagent when possible so it keeps task context. Enforce the iteration cap; then take over or redefine the task.
6. **Integrate & finalize.** After merging, run the full build/test/lint suite or the defined structural check for non-code deliverables, then do a final integration review. Mark a task done only when green evidence is in the ledger, not on the subagent's word alone.
7. **Keep the ledger current.** Record every new task or follow-up in `todos` immediately, with dependencies. Do not leave required work only in prose.

## Command shorthands

Invoked as `/orchestrating-agents <verb>[!] [text] [cap=N]`: the text after the verb is this turn's input and the verb is its first token; with no verb, it runs `?`. `!` on a verb escalates the subagent tier (fast to smart). These wrap the loop above, they are not a new mechanism, and each expansion defers to the host harness's native features where they exist (see the notes below).

| shorthand          | expands to                                                                                                                                                                                                                                                                          |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `decompose <goal>` | Run the decomposition and persist it: interface contracts, acceptance criteria, the ledger, and the `todos`/`todo_deps` graph. These are writes, so `decompose` runs in normal mode; the shorthand cannot switch the harness into native plan mode.                                 |
| `deleg <task>`     | Spawn one subagent (fast tier) with the brief template, acceptance criteria, and the structured-return contract.                                                                                                                                                                    |
| `deleg! <task>`    | Same, smart tier, for a judgment-heavy, ambiguous, or hard-to-reverse task.                                                                                                                                                                                                         |
| `next [cap=N]`     | Delegate ready tasks within the soft cap while reserving one slot for review/revision unless all capacity was requested.                                                                                                                                                            |
| `judge <task-id>`  | Judge the returned artifact by evidence and emit one verdict; delegate review only when it needs substantial separate context, then arbitrate.                                                                                                                                      |
| `integrate`        | Merge the `done` tasks, run the full suite with no active writers, do the final review, update the ledger.                                                                                                                                                                          |
| `status`           | Print the ledger snapshot as a compact status table with active subagents, blockers, open decisions, and suggested next steps; add the ASCII task DAG only when the list is long or has parallel/blocked branches. See [reference/ledger.md](reference/ledger.md#status-rendering). |
| `?`                | List these shorthands.                                                                                                                                                                                                                                                              |

`status` and `?` are the shared core reused across the personal skills (see the `creating-skills` skill).

**Schedule from each task's resource profile and git mode.** Shared-tree writers serialize. Worktree writers may run concurrently only with independent files, interfaces and shared resources. Read-only research and tasks confined to an explicit external or scratch path are parallel-safe. Rebase, stack topology, integration and push remain orchestrator-owned and serialized. For an existing `gh stack`, load the `gh-stack` skill and follow [reference/git-modes.md](reference/git-modes.md#stacked-pr-output-for-large-tasks).

**Parallelism: a hard ceiling and a soft cap.** Two limits bound the whole orchestration tree, not only the root layer. The **hard ceiling** is the harness's own maximum (Copilot CLI: `subagents.maxConcurrency` and subagent depth in `/settings`), which you cannot exceed. The **soft cap** is yours: a resource- and budget-aware limit you keep at or below the ceiling and actively monitor. Use credit, latency and rate-limit budgets first. Inspect local load, memory and GPU only for tasks that run local builds, tests or inference. Keep one slot free for review or revision unless the user explicitly prioritizes all capacity. `cap=N` pins the soft cap for the session; absent it, default to a conservative few and adjust from what you observe.

Before the first delegation and every new scheduling wave, recompute capacity:

1. Read the harness ceiling, active-agent count, credit/rate-limit state and the resource profile of every ready task.
2. When tasks run local commands, inspect CPU/load, available memory, free disk on the repo/worktree filesystem and relevant GPU state. Typical probes are `nproc`, `uptime`, `free -h`, `df -h <path>` and `nvidia-smi` when present.
3. Set the soft cap below the first limiting resource and leave enough memory, disk and compute for the orchestrator and integration checks.
4. Start ready, compatible tasks until that cap is reached. When an agent finishes, recompute and immediately start the next compatible ready task.

Do not leave capacity idle while substantive compatible work is ready. Reserve a slot for review/revision only when a return is pending or review work is queued; otherwise use it and reserve capacity again at the next scheduling wave. Never start unnecessary tasks only to fill a slot.

**Use the available harness surface.** Recommend native user-facing modes when they improve the workflow, but call only tools exposed to the active agent. Choose `security-review` first for explicit vulnerability reviews, `code-review` for substantial diff review, `rubber-duck` for design challenge, and the other agent types per [reference/task-brief.md](reference/task-brief.md).

**Native plan mode is a manual toggle, not part of a shorthand.** A shorthand injects a turn and cannot switch the harness into plan mode, and plan mode's write gate would block the ledger and `todos` writes anyway (Copilot CLI: the gate is active while the session mode is `plan` and it propagates to subagents). So the useful chain is: enter native plan mode yourself (`/plan`) for the read-only decomposition and interface design, leave it, then run `decompose` and `next` in normal mode to persist and delegate. When `decompose` needs deep upfront thinking and plan mode is not active, recommend that the user enable it first rather than assuming it is on.

## Guardrails

- **Project orchestration wins.** If the repository or project documents its own coordination skill or workflow, follow it over this personal skill because it encodes repo-specific integration and review constraints this generic skill cannot know. If the applicable guidance is unclear or conflicts in a way you cannot reconcile, stop and ask the user rather than assuming.
- **Limit direct implementation in decomposed pipelines.** For the core feature-decomposition loop, your edits are limited to accept-with-nits (trivial, no behavior change, a few lines). Under the stateful workflow runtime, the main agent may own substantive writes when its profile and execution-mode rules select single-agent operation.
- **Review by evidence, not trust.** Cheaper models hallucinate; demand relevant check output. For code this is usually build/test/lint; for docs, skills, config, or prose, define and run deterministic structural checks yourself (linters, schema/link/anchor/format/frontmatter validators, or a small script). No evidence -> `needs-evidence`, not a code-change request. Treat subagent checks as preliminary evidence. The authoritative gate is the orchestrator's run on the integration tree with no active writers. Delegate review only when it needs substantial separate context. Resolve findings by evidence, never by vote.
- **Cross-validate cheaply.** Run narrow deterministic probes directly. Use at most one independent reviewer and two delegated probes per task, and only when each probe needs substantial context or a long-running command.
- **Converge on blocking findings.** Run reviewer/orchestrator rounds until no CRITICAL/HIGH/MEDIUM findings remain. LOW ends actioned, refuted or accepted-deferred and never consumes a round alone.
- **Challenge, do not only confirm.** At the right points, add a *contrarian* reviewer that argues against the change instead of checking it: is it needed, is there a simpler or more native path, does it add debt, is the claimed perf win actually measured. Deploy it on judgment-heavy, high-blast-radius, debt-prone, or hard-to-reverse decisions and on every perf claim. Give one contrarian a bounded set of named risks rather than spawning one per small decision. Arbitrate its findings by evidence like any other; a contrarian finding does not decide the outcome. See [reference/review-rubric.md](reference/review-rubric.md).
- **Budget before spawning.** In interactive mode, ask only when a real cost/scope choice exists. In unattended mode, use the supplied budget or a conservative cap and log it. Use `/limits --max-ai-credits` and `/usage` when available.
- **Isolate writers.** Serialize writers in a shared tree. Use one branch and worktree per writer for true parallel edits, with explicit ownership and cleanup. The orchestrator owns integration, rebases, stack topology and push. See [reference/git-modes.md](reference/git-modes.md).
- **Recover active work after a crash.** Every launch has a persisted brief, unique attempt name, agent ID, worktree and branch/base. A new session first reattaches to recorded agents; when reattachment is unavailable, it reconciles the worktree and resumes with a replacement only after the old attempt is marked orphaned. See [reference/ledger.md](reference/ledger.md).
- **Arbitrate reviewer findings.** Independent reviewer output is not automatically correct, even when labeled `BLOCKER`. Reconcile each finding with acceptance criteria and ledger decisions before acting; reject findings that contradict an explicit choice. Example: if the user deliberately kept a legacy spelling, do not send a worker to "fix" it because a reviewer flagged it.
- **Choose task size and parallelism carefully.** Default caps are \<=3-4 active subagents and \<=2-3 review rounds per task. Keep one slot free for review or a fix round. Nested delegation requires explicit authorization in the brief; otherwise the root orchestrator owns all delegation.
- **Same-agent revisions.** Reuse the live subagent for `request-changes` when possible; respawn only when its context is polluted or you're changing tier. See [reference/task-brief.md](reference/task-brief.md).
- **Commits are atomic and integration is serialized.** In a shared tree, the orchestrator stages explicit assigned paths and commits. In an isolated worktree, a subagent may commit only when its brief grants ownership. The orchestrator verifies identity, trailers, paths and SHA before integration.
- **Log autonomous decisions for end-of-run review.** In an unattended or autopilot run, every non-trivial decision you take on the user's behalf (a design choice, a layering or commit split, a scope cut, a default chosen where the brief was silent) goes into a decision log kept alongside the ledger, each entry recording the alternatives you rejected, the rationale, and the reversibility (easy while local and unpushed, costly once shared). Record the alternative, not just the choice, because the reversal cost is exactly what the user is judging. Present the full log at the end so the user can review each call with complete context and easily undo any while the work is still local.
- **Propagate active persona/style skills into briefs.** Subagents do not inherit loaded skills. Resolve precedence, read the applicable skill, then copy its current task-relevant constraints into each brief instead of embedding a possibly stale summary here. See [reference/task-brief.md](reference/task-brief.md).
- **Optimize delivery throughput.** Prefer **many small tasks in parallel** over a few large ones. Do not let one large subagent task delay many small ready tasks, and avoid large tasks whose review becomes a long back-and-forth. When a task is big or on the critical path, split it further, isolate its risky part, or raise its model tier, so overall feature delivery stays fast and independent tasks keep flowing.

## Improving this skill

Only maintain this skill when the user explicitly asks to improve or update the `orchestrating-agents` skill. Then capture validated refinements such as a better decomposition heuristic, a sharper review rubric, a reusable brief pattern, or a git-mode lesson. Keep changes concise, generalize away from the one-off task, and preserve the existing structure and progressive disclosure.

## Reference files

- **Persistent ledger: structure, status rendering, update cadence, bootstrap-from-disk** - [reference/ledger.md](reference/ledger.md)
- **Subagent task brief + structured return contract** - [reference/task-brief.md](reference/task-brief.md)
- **Review rubric: evidence gate, bounded independent review, security dispatch, severity gating, convergence, verdicts, model/effort routing and budget** - [reference/review-rubric.md](reference/review-rubric.md)
- **Git execution modes: shared-tree mode, worktree fleets, snapshots and gh-stack ownership** - [reference/git-modes.md](reference/git-modes.md)
- **Stateful workflow runtime: domain profiles, active waiting, reconciliation, external mutations and completion** - [reference/workflow-runtime.md](reference/workflow-runtime.md)

## Examples

- "Be the orchestrator for this feature: split it up and delegate to subagents."
- "Coordinate a multi-agent pipeline to build X, you review each subagent's work."
- "Run this as a high-level agent that delegates to cheaper models and reviews."
- "Implement these independent tasks in separate worktrees, then integrate them."
- "Address this existing gh-stack with one worker per independent layer."
- "Run this external review-and-CI loop without waiting idle."
