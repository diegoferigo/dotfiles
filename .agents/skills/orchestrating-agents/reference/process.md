# The end to end process

The real sequence of an orchestrated run, from the plan to the resume, in the order it happens. Each step names the script command or the reference that holds the detail, so this file stays a map. `ledger_graph.py <tasks.toml> <command> --help` is the contract of every command ([task-graph.md](task-graph.md#choosing-the-next-tasks)).

## Contents

- [1. Plan in the ledger](#1-plan-in-the-ledger)
- [2. Titles](#2-titles)
- [3. Floating tasks and epics](#3-floating-tasks-and-epics)
- [4. Next, with the caps](#4-next-with-the-caps)
- [5. A worktree per epic](#5-a-worktree-per-epic)
- [6. The writer, then the gates by risk](#6-the-writer-then-the-gates-by-risk)
- [7. Arbitration](#7-arbitration)
- [8. Amend and sync](#8-amend-and-sync)
- [9. Questions for the human](#9-questions-for-the-human)
- [10. Restructuring a stack](#10-restructuring-a-stack)
- [11. Zoom outs and the replanner](#11-zoom-outs-and-the-replanner)
- [12. Resume](#12-resume)
- [13. The turn ends with a parallel line](#13-the-turn-ends-with-a-parallel-line)

## 1. Plan in the ledger

`tasks.toml` is the only plan, next to the ledger (`ORCHESTRATION.md`). The native `todos` of the harness are not mirrored and nothing syncs them ([ledger.md](ledger.md#the-native-todos)). The planner writes epics (goal, definition of done) and leaf tasks with `parent`, `deps`, `priority` and `write_set`, then runs `check`. The `check` warnings are the first review: a task that holds up too many others, a nesting deeper than 8 levels, a missing brief, a long title and two open tasks that look alike. Read them before adding a task.

The planner and the replanner also give every task a `model` and an `effort`, with the rule of [planning.md](planning.md#model-and-effort-per-task): the newest version of a family when it is at least as good on quality, cost and speed, and `high` effort only for planning, exploration and open-loop work.

## 2. Titles

A title is human friendly and says the outcome in about 60 characters, so the TUI is readable at a glance.

- About 60 characters, human friendly: the outcome, readable in the TUI at a glance.
- Do not repeat what the row already shows: the id, the layer number, the status, the epic name.
- Gates read "Gate: <roles>". Keep a commit SHA only when it is the result.
- Details go in `briefs/<id>.md`, never in the title.
- `check` warns above 60 characters.

## 3. Floating tasks and epics

An epic is a section header: it is done when its leaves are done, and it has no status of its own to maintain. Add a leaf at any time, under the epic it belongs to, as a floating task: it needs only an id, a title, a `parent`, a `write_set` and `deps` when it has real ones. Prefer a new epic or sub-epic over a wider one when the write sets are disjoint ([planning.md](planning.md#epics-and-parallelism)). A task parked on purpose is `deferred`: `next` never starts it, and `tree` shows it dim and last. Edit the file with the file tools, never rewrite it from memory (a human may have set a priority), and run `check` after every edit.

## 4. Next, with the caps

`scripts/ledger_graph.py <tasks.toml> next --rw N --ro N --budget N --caps-note "<reason>"` prints `start`, the tasks to start now, and `skipped` with the reason for the rest (`no free slot`, `no budget left`, a write set held by another task, an exclusive resource). The flags describe the machine, the file describes the work ([parallelism.md](parallelism.md)). Start exactly `start`. Two tasks that set `overlap = "hunks"` may share a `write_set` when they run in separate worktrees. `next --awaiting` prints only `awaiting_human` and `awaiting_event` and needs no caps; `next` also prints `awaiting_human`. When a task ends, `set <id> status done` writes the status and prints the next start list, so the loop does not depend on remembering to run `next`.

## 5. A worktree per epic

An epic or sub-epic carries the `worktree` and `branch` that its leaves inherit. Create it with `wt switch --create <branch> --no-cd`, never `git worktree add`, and write both keys in `tasks.toml` before the first leaf starts ([task-runtime.md](task-runtime.md#worktrees)). Writers in separate worktrees run in parallel when their write sets and shared resources are disjoint. In one tree, writers serialize ([git-modes.md](git-modes.md)). `cleanup` lists the worktrees and the branches that can go, and removes them only with `--apply`.

## 6. The writer, then the gates by risk

The brief is persisted before the launch (`briefs/<id>.md` and a registry row), then one writer runs it ([task-brief.md](task-brief.md)). The writer runs the fast checks and returns the structured result. The gates follow, and they are separate read-only agents, never the writer: `correctness` always, `simplifier` past about a hundred changed lines, and one extra gate by the risk of the layer: security, `perfectionist` or `contrarian` ([planning.md](planning.md#extra-gates), [review-rubric.md](review-rubric.md)). Deterministic checks (tests, lint, `check`) come before any model review.

## 7. Arbitration

The orchestrator reads the findings by evidence and issues one verdict per task: `approve`, `accept-with-nits`, `needs-evidence`, `request-changes` or `reject-and-respec`. Every finding ends accepted, rejected or deferred, with a reason. Only CRITICAL, HIGH and MEDIUM block. A change request goes to the same live agent, up to the round cap. The orchestrator commits an approved task (commits are serialized), then runs `set <id> status done` and starts the list it prints ([review-rubric.md](review-rubric.md)).

## 8. Amend and sync

A worker amends only the commit its brief names, and never after the review of the pull request started: after that, a fix is a new commit on the owning layer, and the layers above it are restacked. A rewrite of a lower layer sets the `done` tasks above it back to `pending` until their gate passes again on the new base ([git-modes.md](git-modes.md)). Rebases, topology changes and pushes belong to the orchestrator and are serialized. Pushing, opening a pull request and any other external action need the user's consent.

## 9. Questions for the human

A task that needs an answer only the human can give goes through a blocker and the inbox. Finish what the task can do without the answer. Write `inbox/<id>.md` (question, context, options, recommendation), then mark the leaf `blocked` with `blocker = "human: <question>"` ([task-graph.md](task-graph.md#waiting-on-a-human)). The `human:` blocker and the inbox file are written in the human's conversation language (Italian for Diego); ids, commands, paths and code stay as they are, and commits, PR text, docs and briefs stay in English. The rest of the graph keeps flowing. `next` prints `awaiting_human` every round. The human answers from the board TUI (`e`), and only then does the task become `pending` again with `answer` set. The orchestrator never runs `answer` or `set <id> priority`, and `set <id> status` refuses a task blocked on a human. A task in `in_rev` that the human marked for review (`human_review = true`, key `h` in the board) also shows in `awaiting_human`, with its `review` command instead of a question: do not set it to `done` or merge it until the human clears the mark ([task-graph.md](task-graph.md#human-review-gate)).

## 10. Restructuring a stack

When the deliverable is a stack of pull requests, the orchestrator is the only owner of the topology. Split, merge, reorder or insert a layer only when the new shape is clearly more reviewable, with the commands of the `gh-stack` skill and the steps in [git-modes.md](git-modes.md#restructuring-a-stack). After any rewrite, set the `done` tasks above the rewritten layer back to `pending` and rerun their gates.

## 11. Zoom outs and the replanner

Plan the zoom-out tasks up front (`read_only`): one after about a third of the work and one before integration ([planning.md](planning.md#zoom-outs)). A zoom out reads the work so far and the hard-to-reverse decisions, and returns findings with a severity. The `replanner` role ([../reviewers/replanner.md](../reviewers/replanner.md)) reshapes the plan: it writes `tasks.proposed.toml`, `compare` shows the added, removed and changed tasks, the waves and the simulated rounds before and after, refuses with the ids a proposal that changes or removes a `done`, `in_progress` or `in_rev` task, and the `critic` reviews it. A human can ask for a replan from the board with `R`, which writes a request in `replan-requests/` that `inbox` lists, and the orchestrator then runs `replan` and moves the request to `replan-requests/done/`. Review comments written in hunk reach the writer the same way: the board saves them as `feedback/<id>.json`, the human sends them with `f`, `inbox` lists them as `feedback`, the orchestrator sends each comment to the writer of the task as a finding already decided, with the path and line, and moves the file to `feedback/done/<id>.<UTC time>.json`, then the task goes back to review as for any other finding. Nothing is applied until a plan boundary or a human blocker.

## 12. Resume

After a crash, a compaction or in a new session, start with `scripts/ledger_graph.py <tasks.toml> resume`, read the constraints file, reconcile every running task against `git worktree list`, `git status` and `git log`, and then run `next` ([compaction.md](compaction.md#re-entry-flow), [ledger.md](ledger.md#bootstrap-from-disk-protocol)). A task with a Pi session resumes it by the `session` key ([pi-sessions.md](pi-sessions.md)). Never trust the chat over the ledger and git.

## 13. The turn ends with a parallel line

Never wait for the human to ask about status or parallel work. After every agent return, and before every final reply of a turn, run `next` and start what it prints, or say in one line why it started nothing. Every closing reply ends with a `Parallel:` line copied from that `next` output: the started ids, or the skip reason.
