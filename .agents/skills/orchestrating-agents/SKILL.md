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
- [Keeping the human informed](#keeping-the-human-informed)
- [Stateful workflow runtime](#stateful-workflow-runtime)
- [Quick start](#quick-start)
- [Recover active work](#recover-active-work)
- [Command shorthands](#command-shorthands)
- [Guardrails](#guardrails)
- [Improving this skill](#improving-this-skill)
- [Reference files](#reference-files)
- [Examples](#examples)

Act as the **orchestrator**: plan, delegate, review, and integrate. In a decomposed implementation pipeline, keep direct implementation to tiny nits. A stateful workflow may assign its single writer to the main agent when the execution-mode rules select single-agent operation.

If the repo or project provides its own orchestration workflow, follow that first; this personal skill fills gaps.

When skills compose, the domain skill owns consent and completion semantics, this skill owns scheduling, execution ownership and journaling, and a tool skill such as `gh-stack` owns exact commands and recovery. The runtime never broadens domain consent.

Two disciplines make this work:

1. **Delegate implementation and review by evidence.** Read diffs, summaries, and test output instead of whole files; direct coding consumes the context budget this pattern protects.
2. **Keep durable memory outside the target repository.** The ledger supports compaction recovery by default; claim cross-session recovery only when it contains the complete persisted state defined in `ledger.md`.

## The core loop

```
1. PLAN     Decompose the feature into independent, verifiable tasks.
            Define interface contracts, acceptance criteria, and the task graph.
            Write epics (goal, definition of done) and leaf tasks with parent,
            deps, priority and write_set to `tasks.toml`, next to the ledger.
            ([reference/ledger.md](reference/ledger.md))
            Read the `check` warnings for look-alike tasks before adding one.
2. DELEGATE Run `scripts/ledger_graph.py <ledger-dir>/tasks.toml next --rw N --ro N --budget N --caps-note "<reason>"`, start
            exactly its `start` list ([reference/task-graph.md](reference/task-graph.md)).
            Re-read `tasks.toml` before every `next`: a priority a human set is
            authoritative, so never rewrite the file from memory.
            Print `answered` (the new human answers) and `awaiting_human` every round
            (`next --awaiting`) and never stall on it: a task that waits on a human
            is blocked with a `human:` blocker, the rest of the graph keeps
            flowing, and the human answers from the TUI. Write the question, the
            evidence and the options in `<ledger-dir>/inbox/<id>.md` first,
            unless the choice is trivial; both are in the human's conversation
            language (ids, commands, paths and code stay as they are; commits, PR
            text, docs and briefs stay in English).
            Spawn a subagent per task with a self-contained brief
            ([reference/task-brief.md](reference/task-brief.md)), in parallel at
            each useful tree level (respecting the shared concurrency cap).
3. REVIEW   When a subagent returns, judge by evidence against acceptance
            criteria and emit one verdict; for non-code tasks, require
            deterministic structural-check output. Always use a separate
            read-only reviewer subagent, scaled in tier to the risk;
            you still arbitrate and issue the verdict. ([reference/review-rubric.md](reference/review-rubric.md))
              approve            -> commit the task (commits are serialized,
                                   [reference/git-modes.md](reference/git-modes.md)), release the subagent
                                   (let it go idle), mark done
              accept-with-nits   -> YOU apply the tiny nits, re-run the fast
                                   check, commit; release the subagent
              needs-evidence     -> run/request the missing check, no code change
              request-changes    -> send fixes to the SAME live subagent, repeat
              reject-and-respec  -> re-scope the task, respawn
4. INTEGRATE Merge dependent tasks, run the full build/test/lint suite (or a
            structural check for non-code artifacts) with no active writers,
            then a final review. This serialized run, not the subagents'
            self-reported checks, is the authoritative green gate.
5. LEDGER   Update the ledger after EVERY significant event (never batch it).
```

**Never wait for the human to ask about status or parallel work.** After every agent return, and before every final reply of a turn, run `next` and start what it prints, or say in one line why it started nothing. Every closing reply ends with a `Parallel:` line copied from that `next` output: the started ids, or the skip reason. The same rule is a standing constraint in [reference/compaction.md](reference/compaction.md#standing-constraints-file).

**Titles.** A task title says the outcome in about 60 characters (`check` warns above that), without what the row already shows. Details go in `briefs/<id>.md` ([reference/task-graph.md](reference/task-graph.md#briefs-and-titles)).

The end to end process, from the plan to the resume, is in [reference/process.md](reference/process.md).

## Keeping the human informed

Subagent output is invisible to the human unless you print it. Print it in the chat, in the conversation language, in the same turn you act on it.

- **Every return:** print the agent, the model tier and effort, and its findings or summary (shortened, with ids, anchors and evidence) before you arbitrate. Then print your verdict per finding (accepted, rejected, deferred) with a one-line reason.
- **Every brief:** print one line per delegation: the task, the agent, the tier and effort, and why.
- **Plan recap:** in a long loop, print a short recap at each phase change and after about every 5 returns: the goal, the task graph with status, what is running, the open decisions and the next step. Also after a compaction or a resume.
- Do not paste whole transcripts: the ledger holds the full record.

## Stateful workflow runtime

For review/CI loops and other event-driven workflows, use the same orchestration engine through [reference/workflow-runtime.md](reference/workflow-runtime.md). The domain skill supplies a workflow profile (units, gather sources, classification, dependencies, completion, reopen rules, consent). This skill owns scheduling, active waiting, agents, resources, git isolation, durable state and crash recovery.

**Start parallel-safe work after planning.** Launch independent read-only, external-repo and isolated-worktree tasks promptly. In shared main-tree mode only one writer runs at a time; other slots may run read-only work. Keep one slot free for review or a revision unless the user prioritizes all capacity ([reference/git-modes.md](reference/git-modes.md)).

**Tooling map (Copilot CLI):** slash commands such as `/plan`, `/fleet` and `/review` are user-facing controls, not callable tools; spawn through the agent tool. Persist the full brief and a pending agent-registry row before launch, then record every returned agent ID immediately ([reference/harness-map.md](reference/harness-map.md#agent-types-and-native-plan-mode)).

**Choosing the engine.** Use the manual ledger by default. Dynamic workflows (`run_dynamic_workflow`) are experimental and can consume many credits: start one only when the user asks.

## Quick start

1. **Scope & plan.** Restate the goal, then decompose into tasks that are each a coherent unit with its own checks, recorded with dependencies, contracts and acceptance criteria in the ledger and `tasks.toml`. Size tasks as **S** (focused), **M** (multi-file or judgment-heavy) or **L** (must be decomposed); **XL/XXL** are planning signals, not tasks. Never delegate L or larger as one unit. Consider a stacked-PR output for a large sequential deliverable ([reference/git-modes.md](reference/git-modes.md)). Plan the zoom-out tasks now (`read_only`, one after about a third of the work and one before integration) ([reference/planning.md](reference/planning.md)).
2. **Define done first.** For each task write explicit acceptance criteria and, where possible, the tests, *before* delegating. Review then becomes binary.
3. **Delegate ready tasks.** For each task with all deps `done`, spawn a subagent using the brief template: the **fast tier** for ordinary or mechanical tasks, the **smart tier** directly for judgment-heavy, ambiguous or hard-to-reverse ones. Escalate per the escalation policy when a round proves under-powered. Nested delegation is off unless the root brief authorizes it with a sub-cap, permitted agent types and isolated write sets.
4. **Review on return.** Require the structured return, route the artifact to a separate reviewer scaled in tier to the risk (skipping it needs a one-line ledger reason) and emit exactly one verdict.
5. **Iterate.** For `request-changes`, reuse the same subagent (`write_agent` steers a running or idle one). Enforce the iteration cap; then take over or redefine the task.
6. **Integrate & finalize.** After merging, run the full suite or the defined structural check, then a final integration review. Mark a task done only when green evidence is in the ledger.
7. **Keep the ledger current.** Record every new task or follow-up in `tasks.toml` immediately, with dependencies.

## Recover active work

A session that resumes work, after a crash, a context compaction or in a new session, rebuilds its state from the ledger, `tasks.toml`, the briefs, the inbox and the agent registry, never from the chat. Start with `scripts/ledger_graph.py tasks.toml resume`, and read the constraints file named in the ledger header before anything that acts. The re-entry flow, the digest that `resume` prints and the rules to survive a compaction are in [reference/compaction.md](reference/compaction.md).

Then reconcile every task in a running status that resolves a worktree, before starting anything new:

1. Run `git worktree list --porcelain` to check that the worktree is still there, and recreate it with `wt switch <branch> --no-cd` when it is missing.
2. Run `git -C <worktree> status --short` (changes mean work in progress) and `git -C <worktree> log <base>..HEAD --oneline` (commits ahead mean the task is ready for review).
3. Review what is there, continue from that state, and update `tasks.toml` and the ledger.
4. Spawn a new agent on that worktree only after the old attempt is proven unable to write, per the orphan rules in [reference/ledger.md](reference/ledger.md#crash-safe-state-transitions).

The worktree and the branch live in `tasks.toml` ([reference/task-runtime.md](reference/task-runtime.md#worktrees)); the agent registry keeps the agent id, tier, round, verdict and commit of each attempt. Track an idle writer by agent id, so that `write_agent` can steer it after a compaction. Compact on purpose at a clean point (no arbitration half done, every verdict and agent id written), before the context is full.

## Command shorthands

Invoked as `/orchestrating-agents <verb>[!] [text] [cap=N]`: the text after the verb is this turn's input and the verb is its first token; with no verb, it runs `?`. `!` on a verb escalates the subagent tier (fast to smart). These wrap the loop above, they are not a new mechanism, and each expansion defers to the host harness's native features where they exist.

- `decompose <goal>`: run the decomposition and persist it (contracts, acceptance criteria, the ledger and `tasks.toml`, the only plan). These are writes, so it runs in normal mode. It runs `scripts/ledger_graph.py <tasks.toml> check` on the result and prints its answer.
- `deleg <task>`: spawn one subagent (fast tier) with the brief template, acceptance criteria and the structured-return contract.
- `deleg! <task>`: the same, smart tier, for a judgment-heavy, ambiguous or hard-to-reverse task.
- `next [cap=N]`: delegate ready tasks within the soft cap, reserving one slot for review/revision unless all capacity was requested. `cap=N` is `--rw N` for `scripts/ledger_graph.py <tasks.toml> next` and bounds the writers only, so pass `--ro` too. It prints the ids it started, from `start`, and the `awaiting_human` questions.
- `judge <task-id>`: judge the returned artifact by evidence and emit one verdict; route it to the independent reviewer (skipped only with a ledger reason) plus the extra gate of the layer's risk ([reference/planning.md](reference/planning.md#extra-gates)), then arbitrate. It prints the verdict and the findings.
- `integrate`: merge the `done` tasks, run the full suite with no active writers, do the final review, update the ledger.
- `status`: print the ledger snapshot as a compact status table with active subagents, blockers, open decisions and next steps; add the ASCII task DAG only when the list is long or branched ([reference/ledger.md](reference/ledger.md#status-rendering)).
- `tree [--all] [--epic ID]`: run `scripts/ledger_graph.py <tasks.toml> tree` and print the output as is. `--all` or `--epic ID` opens the hidden done tasks and idle epics.
- `board`: run `scripts/ledger_graph.py <tasks.toml> board` and print the columns as is. A human at a terminal opens `scripts/ledger_board.py <tasks.toml>`, a TUI where only the human sets a priority and answers a blocker ([reference/task-graph.md](reference/task-graph.md#the-board-tui)).
- `resume`: run `scripts/ledger_graph.py <tasks.toml> resume`, then reconcile every task listed in `next.running` as in [Recover active work](#recover-active-work) before starting anything. It prints the epics, `next`, the stacks and the reconciliation per task.
- `replan [new.toml]`: run the `replanner` role ([reviewers/replanner.md](reviewers/replanner.md)), which writes `tasks.proposed.toml` next to `tasks.toml` (or use the given file), then `scripts/ledger_graph.py <tasks.toml> compare <new.toml>`, then the independent `critic` review. It prints what changed and the findings. Nothing is applied: accept at a plan boundary or through a human blocker. `compare` fails with the ids when the proposal changes or removes a `done`, `in_progress` or `in_rev` task: fix the proposal, never the ledger. A pending request in `replan-requests/` (`inbox` lists it) names the `scope` (`remaining`, or `epic` with its `epic` id) and an optional `note` for the replanner; once its proposal is compared, move the request file to `replan-requests/done/`.
- `zoom [scope]`: spawn one read-only agent for a zoom out of the work so far, with the contrarian pass on hard-to-reverse decisions ([reference/planning.md](reference/planning.md#what-a-zoom-out-does)). It prints findings with a severity and proposes plan changes without applying them.
- `inbox`: run `scripts/ledger_graph.py <tasks.toml> next --awaiting` and print `awaiting_human`: id, priority, question (or, for a task marked for human review, the `review` command) and the `inbox/<id>.md` path when it exists, then `replan_requests`: the request file, its scope, epic and note, as a pending request to answer with `replan` (an entry with an `error` is an invalid file: say so, do not delete it), then `feedback`: for each task the human sent comments on from hunk, the file, the task and its comments (`path`, `line`, `text`), each one a finding already decided by the human, to send to the writer of the task as the process says (an entry with an `error` is an invalid file: say so, do not delete it). It does not answer: the human answers from the board TUI (`e`), the orchestrator never runs `answer` or `set` ([reference/task-graph.md](reference/task-graph.md#plan-and-execution)).
- `?`: list these shorthands and what each prints.

`status` and `?` are the shared core reused across the personal skills (see the `creating-skills` skill). There is no shorthand for `set`, `answer`, `epics`, a per-verb model or effort, or a remote run: `set` and `answer` are the human's path from the board TUI, and a model and an effort per task are the optional `model` and `effort` keys of `tasks.toml`.

**Schedule from each task's resource profile and git mode.** Shared-tree writers serialize. Worktree writers run concurrently only with independent files, interfaces and resources. Read-only research and tasks confined to an external or scratch path are parallel-safe. Rebase, stack topology, integration and push stay orchestrator-owned and serialized. For an existing `gh stack`, load the `gh-stack` skill ([reference/git-modes.md](reference/git-modes.md#stacked-pr-output-for-large-tasks)).

**Parallelism: a hard ceiling and a soft cap.** The hard ceiling is the harness's own maximum; the soft cap is yours, at or below it. Before the first delegation and every new scheduling wave, run `nproc`, `uptime`, `free -h` and `df -h <path>`, then `next --rw <writers> --ro <read-only agents> --budget <weight> --caps-note "<reason>"` (exit 2 without them). Keep one slot free for review or revision unless the user prioritizes all capacity. Start exactly what the script printed; narrow it only through `next --pick <ids>`. `set <id> status done` prints the next start list (`--no-next` skips it). The full procedure is in [reference/parallelism.md](reference/parallelism.md#hard-ceiling-soft-cap-and-the-capacity-recompute).

**Use the available harness surface.** Call only tools exposed to the active agent. Choose `security-review` first for explicit vulnerability reviews, the `correctness` role (`~/.copilot/agents/correctness-reviewer.agent.md`) for substantial diff review, and `rubber-duck` for a cheap second opinion ([reference/harness-map.md](reference/harness-map.md#agent-types-and-native-plan-mode)).

**Native plan mode is a manual toggle, not part of a shorthand.** A shorthand cannot switch the harness into plan mode, and its write gate would block the ledger writes. Enter plan mode yourself (`/plan`) for the read-only decomposition, leave it, then run `decompose` and `next` in normal mode.

## Guardrails

The full text of each rule is in [reference/guardrails.md](reference/guardrails.md).


- **Project orchestration wins.** Follow the repository's own coordination workflow over this skill. If the guidance conflicts and you cannot reconcile it, stop and ask the user.
- **Limit direct implementation.** In a decomposed pipeline your edits are limited to accept-with-nits. The stateful runtime may assign substantive writes to the main agent in single-agent mode.
- **Review by evidence, not trust.** Demand relevant check output; for docs, skills, config or prose, run deterministic structural checks yourself. No evidence -> `needs-evidence`. Subagent checks are preliminary: the authoritative gate is your run on the integration tree with no active writers. Resolve findings by evidence, never by vote.
- **Cross-validate cheaply and converge.** Run narrow probes directly; one independent reviewer at a time per task, at most two delegated probes. Run rounds until no CRITICAL/HIGH/MEDIUM findings remain; LOW ends actioned, refuted or accepted-deferred.
- **Challenge and simplify.** Add a *contrarian* reviewer on judgment-heavy, high-blast-radius or hard-to-reverse decisions and on every perf claim, and a *simplifier* as the last review of a non-trivial code change ([reference/review-rubric.md](reference/review-rubric.md)).
- **Arbitrate reviewer findings.** Reviewer output is not automatically correct, even when labeled `BLOCKER`: reconcile it with the acceptance criteria and ledger decisions, and reject what contradicts an explicit choice.
- **Budget before spawning.** Ask only when a real cost or scope choice exists. Unattended, use the supplied budget or a conservative cap and log it.
- **Isolate writers.** Serialize writers in a shared tree; one branch and worktree per writer for parallel edits. The orchestrator owns integration, rebases, stack topology and push ([reference/git-modes.md](reference/git-modes.md)).
- **Recover active work after a crash.** Every launch has a persisted brief, unique attempt name, agent ID, worktree and branch/base. Reattach to recorded agents first, and replace one only after the old attempt is marked orphaned ([Recover active work](#recover-active-work)).
- **Choose task size and parallelism carefully.** Default caps are \<=3-4 active subagents and \<=2-3 review rounds per task, with one slot free for review or a fix round. Nested delegation needs explicit authorization in the brief.
- **Commits are atomic and integration is serialized.** The orchestrator stages explicit paths and commits; a subagent in an isolated worktree commits only when its brief grants it. Verify identity, trailers, paths and SHA before integration.
- **Log autonomous decisions.** In an unattended run, record every non-trivial decision taken for the user in a decision log next to the ledger (rejected alternatives, rationale, reversibility), and present it at the end.
- **Propagate persona and style skills into briefs.** Subagents do not inherit loaded skills ([reference/task-brief.md](reference/task-brief.md)).
- **Optimize delivery throughput.** Prefer many small tasks in parallel over a few large ones.

## Improving this skill

Only maintain this skill when the user explicitly asks to improve or update it. Then capture validated refinements (a decomposition heuristic, a review rubric, a brief pattern, a git-mode lesson), concisely and generalized away from the one-off task, preserving the structure and progressive disclosure.

## Reference files

- **Process:** plan to resume, gates by risk, human questions, zoom outs, replanner - [reference/process.md](reference/process.md)
- **Pi sessions:** resume a task with a Pi session key - [reference/pi-sessions.md](reference/pi-sessions.md)
- **Task graph:** `tasks.toml`, `deps`, epics, priority, the `next`, `resume`, `tree` and `board` script, waiting on a human, several sessions - [reference/task-graph.md](reference/task-graph.md)
- **Board and tree visual spec:** symbols, colors, keys, overlays, inbox layout - [reference/board-tui.md](reference/board-tui.md)
- **Parallelism:** footprint, exclusive, overlap, machine caps, waves, capacity recompute - [reference/parallelism.md](reference/parallelism.md)
- **Task runtime:** worktrees, `cleanup`, stacks, event-driven loops, event log, attempt events - [reference/task-runtime.md](reference/task-runtime.md)
- **Remote runs:** a subagent on another machine, per-user machine map, code over `git push`, DONE and ACK, session marker, down machine as a human blocker - [reference/remote.md](reference/remote.md)
- **GitHub mapping:** keys against Issues and Projects, iteration versus wave - [reference/github-mapping.md](reference/github-mapping.md)
- **File formats:** versions of `tasks.toml`, migration - [reference/formats.md](reference/formats.md)
- **Persistent ledger:** structure, status rendering, update cadence - [reference/ledger.md](reference/ledger.md)
- **Surviving compaction:** persist before launch, constraints file, re-entry flow and digest - [reference/compaction.md](reference/compaction.md)
- **Subagent task brief and structured return** - [reference/task-brief.md](reference/task-brief.md)
- **Review rubric:** evidence gate, reviewers by risk, severity gating, verdicts, model/effort routing - [reference/review-rubric.md](reference/review-rubric.md)
- **Guardrails in full** - [reference/guardrails.md](reference/guardrails.md)
- **Reviewer roles and findings format:** `~/.copilot/agents/simplifier-reviewer.agent.md`, `~/.copilot/agents/contrarian-reviewer.agent.md`, `~/.copilot/agents/correctness-reviewer.agent.md`, `~/.copilot/agents/critic-reviewer.agent.md`, `~/.copilot/agents/perfectionist-reviewer.agent.md`, [reviewers/replanner.md](reviewers/replanner.md), [reviewers/findings.md](reviewers/findings.md)
- **Rendering findings:** local report and review of another person's PR - [reference/rendering-findings.md](reference/rendering-findings.md)
- **Harness map:** native capabilities, reviewer roles on Copilot CLI and Pi, agent types, plan mode - [reference/harness-map.md](reference/harness-map.md)
- **Git modes:** shared tree, worktree fleets, snapshots, gh-stack ownership - [reference/git-modes.md](reference/git-modes.md)
- **Planning practice:** zoom-outs, extra gates, epics and sub-epics, narrow write sets - [reference/planning.md](reference/planning.md)
- **Stateful workflow runtime:** domain profiles, active waiting, reconciliation, completion - [reference/workflow-runtime.md](reference/workflow-runtime.md)

## Examples

- "Be the orchestrator for this feature: split it up and delegate to subagents."
- "Coordinate a multi-agent pipeline to build X, you review each subagent's work."
- "Run this as a high-level agent that delegates to cheaper models and reviews."
- "Implement these independent tasks in separate worktrees, then integrate them."
- "Address this existing gh-stack with one worker per independent layer."
- "Run this external review-and-CI loop without waiting idle."
