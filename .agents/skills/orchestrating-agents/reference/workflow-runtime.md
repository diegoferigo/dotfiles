# Stateful workflow runtime

## Contents

- When to use it
- Workflow profile
- Runtime loop
- Scheduling during waits
- Execution mode and agent routing
- Durable state and recovery
- External mutations
- Completion and stop conditions

## When to use it

Use this runtime when orchestration is driven by repeated external events rather than a one-shot implementation graph: review and CI loops, deployment reconciliation, issue triage, batch processing, or another gather-classify-act workflow. The domain skill supplies policy and API semantics; this skill owns scheduling, agents, durable state, isolation, evidence and crash recovery.

The runtime also works with no subagents. A small single-unit workflow can stay on the main agent while using the same state machine and ledger. Load the domain skill and this skill together when the domain profile asks for this runtime.

## Workflow profile

Before the first mutation, persist a profile that covers these concerns, either in one mapping table or in clearly named profile sections:

| field                 | definition                                                          |
| --------------------- | ------------------------------------------------------------------- |
| scope                 | stable identity of every unit the workflow governs                  |
| unit state            | domain fields, watermarks and allowed states                        |
| gather                | read-only sources queried on every reconciliation                   |
| classify              | rules that map gathered evidence to unit state                      |
| ready work            | bounded read-only and write tasks derived from state                |
| dependencies          | ordering or invalidation between units                              |
| serialized operations | topology, integration, push or API writes owned by the orchestrator |
| completion predicate  | observable conditions that settle each unit and the workflow        |
| reopen rules          | events that invalidate a settled unit                               |
| stop conditions       | decisions, limits or unsafe states that require human input         |
| consent               | exact external mutations authorized for this run                    |

Keep domain semantics in the domain skill. Do not copy its API commands, watermark meanings or completion predicate into this runtime.

In a composed workflow, the domain profile owns consent and completion, this runtime owns scheduling and execution ownership, and the tool skill owns exact mutation commands and recovery. A lower layer may narrow authority but never broaden it.

## Runtime loop

Run one canonical loop for single-unit and multi-unit workflows:

1. **Recover.** Bootstrap from the ledger. Reconcile incomplete operations, active agents, git state and external state before scheduling anything new.
2. **Gather.** Query every unit in scope, including settled units covered by a reopen rule. Gathering is read-only and may run in parallel.
3. **Classify.** Compare observations with persisted watermarks. Derive current unit state and ready work before advancing any watermark that could hide unhandled activity.
4. **Schedule.** Build or refresh `todos` and dependencies. Recompute resource capacity and start compatible ready work up to the soft cap.
5. **Execute and judge.** Run writers under the selected git mode. Judge every return by evidence and reuse the same worker for corrections when practical.
6. **Integrate.** As soon as a ready unit has no active writer and its dependencies are stable, serialize its commit, topology, push and API writes. Do not wait for unrelated isolated writers. The final authoritative suite still runs with no active writers anywhere.
7. **Reconcile.** Gather again, verify intended results from observable IDs, SHAs and statuses, then update watermarks and completion state.
8. **Continue or stop.** Repeat while work or expected external events remain. Stop only on the profile's completion or stop conditions.

Do not create separate pipelines for a single unit and a collection. The collection scheduler applies the same per-unit primitive with dependency and invalidation rules.

## Scheduling during waits

Waiting is a scheduling state, not an idle state. While any unit expects an external event:

1. Poll the profile's gather sources with moderate backoff and within API rate limits.
2. Reclassify new events immediately, including events that reopen settled units.
3. Fill available capacity with useful compatible work: triage, read-only code tracing, reproduction, prior-art or versioned-API research, verification planning, or independent writes already justified by gathered evidence.
4. Re-gather after a helper returns and before starting a write, because the external state may have changed.
5. When no safe work remains, sleep with backoff and poll again. Do not invent speculative edits merely to keep an agent busy.

Use a bounded read-only helper only when the investigation needs substantial separate context. Choose its agent type through `task-brief.md`, persist the brief and registry row before launch, and treat its output as evidence rather than authority. A helper cannot mutate git, external state or workflow topology. Convert it into a normal writer task only through the execution-mode and ownership rules.

Prioritize work that can unblock the critical path. A domain profile may define a stricter order, but new human decisions and failures normally precede speculative preparation.

Process returns as a stream, not a batch. Judge a worker immediately, persist its commit and evidence, and advance every dependency-ready unit while other isolated workers continue. Queue only units whose dependencies can still invalidate them.

## Execution mode and agent routing

Use `git-modes.md` as the only source for main-tree, worktree, commit and stack mechanics. Default to the main agent and one writer. Choose a worktree fleet only when at least two substantial ready writes have independent ownership, files, interfaces and resources, and per-worktree setup is practical. Existing worktrees never imply fleet mode.

Assign every write to its owning unit and integration point before delegation. One branch or unit has one writer. Serialize cross-unit changes or keep them on the main agent when ownership is ambiguous.

Choose agent type, model tier and reasoning effort through `task-brief.md` and `review-rubric.md`. Route from the hardest coupled reasoning in the task, not from diff size or a domain severity label. A domain profile may identify additional high-risk shapes, but it must not duplicate the generic routing table.

Every brief includes:

- unit identity and current watermarks;
- allowed files and exact ownership;
- worktree, branch/base and baseline;
- dependencies and integration point;
- resource profile;
- acceptance criteria and checks;
- domain constraints copied from the active skill;
- forbidden topology, push and external API operations.

## Durable state and recovery

Use `ledger.md` for the ledger, active-agent registry and operation journal. Extend it with the workflow profile and a domain-appropriate unit table. A generic shape is:

| unit | identity | observed version | cursors | state | changed | attempts | pending event | blocker |
| ---- | -------- | ---------------- | ------- | ----- | ------- | -------- | ------------- | ------- |

The observed version is the domain's immutable reconciliation key, such as a commit SHA, deployment revision or batch generation. Persist raw returned IDs for external writes when the API provides them.

For a single-unit, single-agent run, keep the representation compact: the unit row, decisions, evidence and operation journal are sufficient. Do not create an active-agent registry when no agent is launched. The same recovery invariants still apply to external and git mutations.

The ledger must let a fresh session reconstruct:

- the complete workflow profile and consent;
- every unit, dependency, watermark and reopen rule;
- current capacity, active agents and worktree ownership;
- every prepared or running mutation;
- the next gather and scheduling actions.

On restart, never trust a stale `waiting`, `settled` or successful state. Reconcile it against the current external version and gathered events. Standing consent survives crash recovery only for the same recorded workflow scope and owner, until completion or explicit revocation. A changed scope, repository owner or topology plan requires fresh consent.

**Do not assume one `sessionEnd` hook per run.** In Copilot CLI `-p` and piped runs the hook fires once per completed agent turn, not at shutdown (1.0.78); since 1.0.92 Stop-hook continuations of one prompt count as one turn. A hook or loop that treats `sessionEnd` as the end of the run is wrong ([changelog](https://github.com/github/copilot-cli/blob/main/changelog.md)).

## External mutations

Treat every push, reply, resolution, review request, deployment action or other API write as a journaled external mutation with an idempotent recovery pattern:

1. Persist the operation ID, consent basis, unit, preconditions, expected version, request identity and recovery query.
2. Execute it once.
3. Reconcile through a read endpoint and returned IDs, not only the command exit status.
4. Mark it verified, blocked or safe to retry.

After a crash, repeat only when reconciliation proves the mutation did not apply. If the observed version changed concurrently, block and re-gather instead of applying an operation prepared for stale state.

Git and topology mutations additionally follow `git-modes.md`. Exact topology commands and recovery come only from the domain's topology skill.

## Completion and stop conditions

A workflow completes only when every unit satisfies the profile's observable completion predicate after a final gather, no required operation is merely `prepared` or `running`, and no active writer remains.

Stop and report instead when:

- consent does not cover the next external mutation;
- a topology, ownership or hard-to-reverse decision is ambiguous;
- the observed state conflicts with the operation journal;
- a round, cost, rate or resource limit is exhausted;
- repeated work does not converge;
- the domain profile declares the state unrecognizable or unsafe.

Report the exact unit, observed version, blocker and safest next action.
