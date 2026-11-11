# Guardrails in full

The short list is in [SKILL.md](../SKILL.md#guardrails). This file keeps the full text of each rule.

## Contents

- [Precedence and ownership](#precedence-and-ownership)
- [Review](#review)
- [Delegation and capacity](#delegation-and-capacity)
- [Git and recovery](#git-and-recovery)
- [Logging, personas and throughput](#logging-personas-and-throughput)

## Precedence and ownership

- **Project orchestration wins.** If the repository or project documents its own coordination skill or workflow, follow it over this personal skill because it encodes repo-specific integration and review constraints this generic skill cannot know. If the applicable guidance is unclear or conflicts in a way you cannot reconcile, stop and ask the user rather than assuming.
- **Limit direct implementation in decomposed pipelines.** For the core feature-decomposition loop, your edits are limited to accept-with-nits (trivial, no behavior change, a few lines). Under the stateful workflow runtime, the main agent may own substantive writes when its profile and execution-mode rules select single-agent operation.

## Review

- **Review by evidence, not trust.** Cheaper models hallucinate; demand relevant check output. For code this is usually build/test/lint; for docs, skills, config, or prose, define and run deterministic structural checks yourself (linters, schema/link/anchor/format/frontmatter validators, or a small script). No evidence -> `needs-evidence`, not a code-change request. Treat subagent checks as preliminary evidence. The authoritative gate is the orchestrator's run on the integration tree with no active writers. Resolve findings by evidence, never by vote.
- **Cross-validate cheaply.** Run narrow deterministic probes directly. Use one independent reviewer at a time per task (tier scaled to risk, rounds follow the findings, reuse or respawn per the review rubric) and at most two delegated probes, and only when each probe needs substantial context or a long-running command.
- **Converge on blocking findings.** Run reviewer/orchestrator rounds until no CRITICAL/HIGH/MEDIUM findings remain. LOW ends actioned, refuted or accepted-deferred and never consumes a round alone.
- **Challenge, do not only confirm.** At the right points, add a *contrarian* reviewer that argues against the change instead of checking it: is it needed, is there a simpler or more native path, does it add debt, is the claimed perf win actually measured. Deploy it on judgment-heavy, high-blast-radius, debt-prone, or hard-to-reverse decisions and on every perf claim. Give one contrarian a bounded set of named risks rather than spawning one per small decision. Arbitrate its findings by evidence like any other; a contrarian finding does not decide the outcome. See [review-rubric.md](review-rubric.md).
- **Simplify before you push.** For a code change of non-trivial size or risk, run a strict *simplifier* reviewer (a contrarian specialised on deletion) as the last review, and again only when a round adds new elements. It takes the contrarian slot, not an extra one. It makes the author justify every added element and reports only removals and substitutions with evidence. See "Simplifier review" in [review-rubric.md](review-rubric.md).
- **Arbitrate reviewer findings.** Independent reviewer output is not automatically correct, even when labeled `BLOCKER`. Reconcile each finding with acceptance criteria and ledger decisions before acting; reject findings that contradict an explicit choice. Example: if the user deliberately kept a legacy spelling, do not send a worker to "fix" it because a reviewer flagged it.

## Delegation and capacity

- **Budget before spawning.** In interactive mode, ask only when a real cost/scope choice exists. In unattended mode, use the supplied budget or a conservative cap and log it. Use `/limits --max-ai-credits` and `/usage` when available.
- **Choose task size and parallelism carefully.** Default caps are \<=3-4 active subagents and \<=2-3 review rounds per task. Keep one slot free for review or a fix round. Nested delegation requires explicit authorization in the brief; otherwise the root orchestrator owns all delegation. The capacity procedure is in [parallelism.md](parallelism.md#hard-ceiling-soft-cap-and-the-capacity-recompute).
- **Same-agent revisions.** Reuse the live subagent for `request-changes` when possible; respawn only when its context is polluted or you're changing tier. See [task-brief.md](task-brief.md).

## Git and recovery

- **Isolate writers.** Serialize writers in a shared tree. Use one branch and worktree per writer for true parallel edits, with explicit ownership and cleanup. The orchestrator owns integration, rebases, stack topology and push. See [git-modes.md](git-modes.md).
- **Recover active work after a crash.** Every launch has a persisted brief, unique attempt name, agent ID, worktree and branch/base. A new session first reattaches to recorded agents; when reattachment is unavailable, it reconciles the worktree and resumes with a replacement only after the old attempt is marked orphaned. See [Recover active work](../SKILL.md#recover-active-work) and [ledger.md](ledger.md).
- **Commits are atomic and integration is serialized.** In a shared tree, the orchestrator stages explicit assigned paths and commits. In an isolated worktree, a subagent may commit only when its brief grants ownership. The orchestrator verifies identity, trailers, paths and SHA before integration.

## Logging, personas and throughput

- **Log autonomous decisions for end-of-run review.** In an unattended or autopilot run, every non-trivial decision you take on the user's behalf (a design choice, a layering or commit split, a scope cut, a default chosen where the brief was silent) goes into a decision log kept alongside the ledger, each entry recording the alternatives you rejected, the rationale, and the reversibility (easy while local and unpushed, costly once shared). Record the alternative, not just the choice, because the reversal cost is exactly what the user is judging. Present the full log at the end so the user can review each call with complete context and easily undo any while the work is still local.
- **Propagate active persona/style skills into briefs.** Subagents do not inherit loaded skills. Resolve precedence, read the applicable skill, then copy its current task-relevant constraints into each brief instead of embedding a possibly stale summary here. See [task-brief.md](task-brief.md).
- **Optimize delivery throughput.** Prefer **many small tasks in parallel** over a few large ones. Do not let one large subagent task delay many small ready tasks, and avoid large tasks whose review becomes a long back-and-forth. When a task is big or on the critical path, split it further, isolate its risky part, or raise its model tier, so overall feature delivery stays fast and independent tasks keep flowing.
