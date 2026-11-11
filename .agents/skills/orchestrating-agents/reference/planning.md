# Planning practice: zoom outs, gates and epics

How a plan is shaped before it is scheduled: where the zoom-out reviews go, which gates a layer gets beyond the default ones, when a new epic buys parallelism, and how to keep write sets disjoint and avoid a task that blocks many others. The vocabulary (`tasks.toml`, epics, `deps`, `write_set`, `read_only`) is in [task-graph.md](task-graph.md), and the reviewer roles are in [review-rubric.md](review-rubric.md).

## Contents

- [Zoom outs](#zoom-outs)
- [What a zoom out does](#what-a-zoom-out-does)
- [Extra gates](#extra-gates)
- [Epics and parallelism](#epics-and-parallelism)
- [Disjoint write sets and bottlenecks](#disjoint-write-sets-and-bottlenecks)
- [Model and effort per task](#model-and-effort-per-task)

## Zoom outs

Every plan carries zoom-out tasks, written while planning and not added later when something already feels wrong.

- One after about a third of the work. When the work is long, one after each group of about three layers or three tasks instead.
- One at the end, before any push or integration.
- One extra after a task of high risk: hard to reverse, a public surface, security, or data loss.

A zoom out is a task in `tasks.toml` like any other:

```toml
[[task]]
id = "z1"
title = "Zoom out after the parser layer"
status = "pending"
read_only = true
deps = ["t3", "t4", "t5"]
```

- `read_only = true`: it writes nothing, so it conflicts with no write set and counts against `ro`.
- `deps`: every task of the group it reviews.
- It is a ledger task: the tasks planned after it depend on it, so its findings reach them before they start. A zoom out nobody waits for is decoration.
- It may propose changes to the plan: add, drop, re-scope or re-order tasks. The orchestrator applies them and records the change in the ledger next to the finding that caused it.

## What a zoom out does

The question is whether the whole thing still goes where it should, not whether one layer is correct. The per-layer review already answered that.

1. Compare the work so far against the original goal, read as a whole, including what is already `done`.
2. Run a contrarian pass on the decisions that are hard to reverse, with the role prompt in `~/.copilot/agents/contrarian-reviewer.agent.md`.
3. Use the result for real: run the CLI, open the TUI, follow the documented steps end to end, as a user and not as a test.
4. List the shortcomings, the wrong turns, and the direction changes the work took without a decision.
5. The orchestrator decides what changes in the plan and records the decision in the ledger. The zoom out proposes, it does not replan.

The output is a short findings list with a severity per finding ([../reviewers/findings.md](../reviewers/findings.md)), not a rewrite and not a new plan. Severity gates it like any other review ([review-rubric.md](review-rubric.md#severity-gating)): CRITICAL, HIGH and MEDIUM block integration, LOW does not.

## Extra gates

The default gates of a task are `correctness` and, past about a hundred changed lines, `simplifier`. Beyond those, add one gate chosen from the risk the layer carries: security for input handling, files, subprocesses or the network, `perfectionist` for a public surface (documentation, a file format, a CLI), `contrarian` for a design choice or for a measurement to defend. The contrarian gate takes the slot it shares with the simplifier, so the cap of three roles per task still holds and the routing table does not change; the rows and the tie-breaks are in [review-rubric.md](review-rubric.md#extra-gates-by-layer-risk).

## Epics and parallelism

Before adding a task to an epic, ask whether a new epic, or a sub-epic, buys parallelism.

- The tasks of one epic usually share a worktree and a branch ([task-runtime.md](task-runtime.md#worktrees)), and often the same files, so they run one after the other.
- An epic has no write set of its own: the scheduler reads the write sets of the leaves. So the leaves of two epics run at the same time when their write sets are disjoint, each epic in its own worktree, and when a free `rw` or `ro` slot and enough budget are left ([parallelism.md](parallelism.md)).
- So a task that writes other files than its siblings belongs to its own epic, not to the epic whose description happens to mention it.
- An epic can hold a sub-epic: a `parent` only has to be an epic, at any depth (GitHub stops at 8 levels, see [github-mapping.md](github-mapping.md#keys-and-github-mapping)). Use one to split a long epic into groups that can run in parallel.
- A far epic may have no tasks yet: break it down when the work gets close, and expect `0/0` in the `tree` until then ([task-graph.md](task-graph.md#epics-priority-and-milestone)).
- An epic is never scheduled, so it carries no order of its own. `milestone` set on it is inherited by the leaves that have none, `priority` is read per leaf, and the rest of the order is in [task-graph.md](task-graph.md#choosing-the-next-tasks): `priority`, `milestone`, how many dependent leaves that are not done the task unblocks, then the file order.

Do not create an epic to hold a single task.

- A task with no `parent` is valid. Leave it floating until a second task joins it.
- Create an epic when two or more tasks share a worktree or a goal.
- A process step (a final zoom out, a push, a release, a gate over the whole graph) or a task that waits on a human floats at the root unless it is part of the epic's definition of done, so it does not keep the epic open. The opposite holds too: a root leaf that every leaf of one epic waits for or serves belongs to that epic.
- Promote a floating task when it grows: add `type = "epic"` to that same block, keep its `id`, and create the sub-tasks with `parent = "<that id>"`. Move `write_set` or `read_only` down to the sub-tasks, because an epic needs neither. `check` tolerates one of the two left behind on an epic, where it means nothing, and refuses only both at once.

Keep `write_set` narrow, because a scheduler parallelizes only what the write sets allow:

- Name the files the task writes (`src/parser.py`, `tests/test_parser.py`), not the directory that holds them (`src/**`). Overlap is compared on the literal prefix before the first glob character, so a wide glob collides with everything under it.
- A wide write set is not a safety measure. It serializes every sibling that touches anything under it, including tasks that would never conflict.
- A task that cannot name the files it writes is not decomposed yet. Split it until it can.

## Disjoint write sets and bottlenecks

Plan so that tasks collide as little as possible. A plan where one file or one task gates most of the work runs one task at a time, whatever the slot limits are. The replanner looks for the same things in its method step 2 ([../reviewers/replanner.md](../reviewers/replanner.md#method)), and the planner should not need it to find them.

Split the work by file or module, so the write sets are disjoint. Tasks with disjoint write sets run in parallel and tasks that share a file run one after the other, so every shared file costs a round. Prefer tasks that only write documentation or that only create new files, because they cannot collide with the rest. This also keeps one task, one layer: the write set of the task is the files of its layer ([git-modes.md](git-modes.md#stacked-pr-output-for-large-tasks)).

Do not add a `deps` edge only because two tasks share a file. The write-set conflict already makes the second one wait ([parallelism.md](parallelism.md)), so the edge adds nothing and hides the real reason. Keep `deps` for output that a task needs, and leave the shared file visible in the write sets.

One task must not block many others. When more than about 3 open tasks wait on one task, directly or through a shared file, split the file or move the work to a new file before planning more tasks on it. A refactor that splits the file is its own task and goes first, and the replanner only reports the file, it does not fold the split into the plan ([../reviewers/replanner.md](../reviewers/replanner.md#rules)). In one real plan about 10 tasks waited on a single task through two shared files. `check` reports it as the `blocks N` warning, and the next section says what to do with it.

### Replan before scheduling a bottleneck

When `next` would start a task that `check` reports as holding up more than the limit of open tasks (`blocks N`), run a replan first for that task, and start the rest of the `next` list meanwhile. The replanner can run while writers are active, because it only proposes and works on tasks that have not started. It proposes to split the task, narrow its write set or set `overlap`, and the orchestrator applies the accepted proposal at a plan boundary. A task that is already `in_progress` is not replanned.

A long-running writer is a risk for the same reason, because everything behind it waits for it to finish. Give it a time or turn budget and ask for a checkpoint commit when a usable part is done, so the dependents that need only that part can start from the checkpoint. The checkpoint is a commit of the same layer, so the layer rule of [git-modes.md](git-modes.md#stacked-pr-output-for-large-tasks) is unchanged: one task, one layer.

Schedule the review gates of a long task so that they do not hold the next wave. A gate waits only for the layers that use the API it reviews, so a task that does not use that API does not depend on the gate. The planner and the replanner apply the same budget and checkpoint advice when they split a task that ran long.

## Model and effort per task

The planner and the replanner assign `model` and `effort` to each task in `tasks.toml` ([task-graph.md](task-graph.md#the-file)) and apply the routing table of [review-rubric.md](review-rubric.md#agent-model-and-reasoning-effort-routing). For every model family, use the newest version when it is at least as good as the older one on quality, cost and speed. For Claude that is `claude-opus-5.5` and `claude-sonnet-5.5`, never version 5. Do not propose an older version. When a model is unavailable and a comparable one replaces it, log the substitution in the ledger.

`high` effort is for planning, exploration and open-loop implementation only, because it makes a task much slower. Once the plan is known, delegate at `low` or `medium` by the uncertainty left.
