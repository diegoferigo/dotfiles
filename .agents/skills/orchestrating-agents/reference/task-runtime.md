# Worktrees, stacks, event loops and the event log

The runtime side of `tasks.toml`: where a task runs, how a stack of pull requests is read from the file, how an event-driven loop uses the same file and what the event log records. The file format is in [task-graph.md](task-graph.md).

## Contents

- [Worktrees](#worktrees)
- [Stacks](#stacks)
- [Event-driven loops](#event-driven-loops)
- [Event log](#event-log)
- [Attempt events](#attempt-events)

## Worktrees

`worktree` (a path) and `branch` (a branch name) say where a task runs. Put them on the epic when a group of tasks shares one worktree, on the leaf otherwise. A leaf without them inherits the ones of its nearest ancestor epic, and `next` resolves that for every entry of `start`. The orchestrator writes them when it creates the worktree, so another harness can reattach to the same work from the file alone.

The script never runs git when it schedules and never checks that the path or the branch exists. The keys are harness agnostic by design: they record what was created, nothing else. Only `cleanup` runs git, and `next` and `resume` call it to count candidates.

Create fewer worktrees. A read-only task never gets one. A writer gets one only when it runs in parallel with another writer. A doc-only chain on the same files reuses one worktree and one branch. Integrate early: merge or rebase a done and reviewed branch into the stack at the next plan boundary.

`cleanup` reports instead of guessing, as text, in three classes:

- `remove`: every task done, the branch an ancestor of `--tip` (default the `Stack tip: <ref>` line of `ORCHESTRATION.md`, else `HEAD` of the main checkout) and a clean tree. It comes with its `wt remove <branch>` command.
- `keep`, with the reason: an open task, uncommitted changes, commits the tip lacks, the main checkout, the current directory or a parent of it.
- `orphan`: the ledger names a worktree or branch that git no longer matches.

Worktrees the ledger never names are not listed. It changes nothing without `--apply`, and `--repo DIR` picks the git repository to inspect (default `.`). `--apply` runs `wt remove --foreground -- <branch>` for the `remove` class only, classifies each entry again right before its removal, prints one line per result, appends it to `ORCHESTRATION.md`, goes on after a refusal and exits 1 at the end.

The orchestrator runs `cleanup --apply` itself: it acts only on worktrees the ledger names with a `worktree` key, never removes a `keep`, never touches a worktree the ledger did not create, only reports an `orphan`, never uses `rm -rf`, and relies on `wt remove`, which refuses a dirty tree and unmerged commits. `next` (the `cleanup` key, only when there are candidates) and `resume` (a `CLEANUP` line) count the `remove` candidates and warn above 8 (`cleanup --max-removable N` changes the threshold of the report). Run `cleanup` at the end of a stack, after the push.

Convention when a worktree is created: name the branch after the epic id when the group shares it, after the task id otherwise, then record the path the tool reports (`wt list` for worktrunk). To reattach to work started by an earlier session, follow "Recover active work" in [SKILL.md](../SKILL.md#recover-active-work).

## Stacks

`stack` is an optional key of an epic: a non-empty name or a positive number (the number of the stack, or of its first PR). `check` refuses it on a task, an empty name, `0`, a boolean and a list. A leaf takes the stack of its nearest ancestor epic that sets one.

There is no per-task layer key. One task is one layer (one `gh stack` branch), so its `write_set` is the files of the layer and its `branch` is the layer. The file order of the leaves is the order of the layers, bottom first.

`resume` adds a `STACKS` section: one entry per stack, in file order, each with its layers. A layer is a leaf of the stack that resolves a `branch`, with its `id`, `status`, `worktree` and `branch`. A leaf that resolves no branch, or that sits under no stack, is not listed. Two leaves of a stack may not resolve the same branch, and `check` refuses it, so a stacked epic does not set `branch` (set it on each leaf). The `stack` is always read as a string, so `12` and `"12"` are the same stack, and `resume` reports the name as a string.

`check` also reports `warnings` (and still exits 0) for a task that nests more than 8 levels below its top epic, the sub-issue limit of GitHub ([Keys and GitHub mapping](github-mapping.md#keys-and-github-mapping)). The key is absent when there is nothing to warn about.

## Event-driven loops

A loop (a review or CI loop, see [workflow-runtime.md](workflow-runtime.md)) runs on the same file. [task-graph.md](task-graph.md) owns the work: the epics, leaves, `deps`, `write_set`, blockers and the epic gate. [workflow-runtime.md](workflow-runtime.md) owns the events and the poll state.

1. One epic per scope (for example a stack). One child epic per unit (for example a PR layer) with its `worktree` and `branch`. Leaves inherit them.
2. One leaf per round of work, named `<unit>-r<N>`, with the files it edits in that worktree as `write_set`. A loop never reopens a `done` leaf: it appends the next round as a new `pending` leaf.
3. One standing read-only leaf per unit, `<unit>-watch`, with `status = "blocked"` and `blocker = "event: <what>, <watermark or SHA>"`, for example `event: copilot review requested on 1a2b3c4`. Nothing lists it in `deps`, so a late event blocks no other unit. `answer` refuses it.
4. Only the orchestrator wakes a watch leaf, after each gather. New findings: append `<unit>-r<N+1>` and keep the watch leaf blocked with the new `event:` text. Settled: keep it blocked with `event: settled on <sha>`.
5. `next` lists the open ones in `awaiting_event` (`{id, event}`). While it is not empty, do not end the turn: sleep for the poll time of the unit table and gather again. The cadence belongs to the domain profile.
6. `N` counts the rounds that addressed findings. A cascade after a restack is not a round. The cap is the domain profile's, else the one of [review-rubric.md](review-rubric.md). After the last allowed round, set the watch leaf to `human: <question>` with an `inbox/<id>.md` file, so it reaches `awaiting_human`.
7. Convergence is the definition of done of the scope epic, the profile's completion predicate. After a final gather, set every watch leaf `done`, record the evidence, then set the epic `done`. Activity after that is a new run.
8. Restack, push, reply and review requests are orchestrator operations, never leaves. A holistic review is a read-only leaf `<scope>-holistic-h<N>` appended once every unit is provisionally settled, with no `deps`.

The `write_set` overlap check ignores `worktree` and `branch` on purpose: a same-file fix in two layers runs one at a time, which limits restack conflicts.

## Event log

`events/<task-id>.jsonl`, next to `tasks.toml` and outside the repository, records what happened to a task. One JSON object per line with `ts` (UTC ISO), `task`, `event` (for example `status`, `agent`, `commit`, `verdict`) and `detail`. It is append only and never rewritten, so a new field is backward compatible. A reader ignores a field it does not know.

`ledger_graph.py <tasks.toml> event <id> <event> "<detail>"` appends one line. `ledger_graph.py <tasks.toml> event <id>` prints the log as JSON. `resume` shows the last event of each running task as `last=`.

### Attempt events

The orchestrator writes `launch` when it starts an attempt (sync or background) and `return` when it ends, for every attempt. The detail is either a JSON object or space separated `key=value` pairs; any other text is free-form and still renders. Fields: `mode` (sync or background), `agent`, `agent_name`, `model`, `effort`, `host`, `session` (orchestrator session id), `pane` (herdr pane id, the handle of a background attempt), `pid`, `worktree`, `branch`, `round`, `verdict`, `commit`. Example: `event t1 launch "mode=background agent=a1 model=m host=box pane=p7 round=1"`.

`host` may hold a label of the per-user machine map ([remote.md](remote.md)); `check` warns when it looks like a host name (a dot or an IP address). The `remote` event is validated by `event`: `state` (`running`, `done` or `acked`, required), `machine` (a map label) and `path` (the run directory). `resume` prints a REMOTE section with the last `remote` state per task and flags `state=running` as needing a human check.

`heartbeat` may be written by the orchestrator or the pane. `resume` prints `attempt=alive|finished|orphan` plus `host` and `pane` for each running task: finished after a `return`, alive while the last `launch` or `heartbeat` is under 30 minutes old, else orphan. An orphan is reported, never auto-resumed: follow [ledger.md](ledger.md). The overlay History and `resume` show one line per event with the fields separated by dots.

The log only records. It never vetoes: `set <id> status done` does not read it and is never refused because of it.
