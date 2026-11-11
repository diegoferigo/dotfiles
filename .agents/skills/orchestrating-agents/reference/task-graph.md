# Task graph: dependencies, parallelism and GitHub mapping

The task graph is `tasks.toml`, next to the ledger ([ledger.md](ledger.md)). This file says how it is written, how its edges are read, how the next tasks are chosen and how a human steers it. The format versions are in [formats.md](formats.md).

## Contents

- [The file](#the-file)
- [Briefs and titles](#briefs-and-titles)
- [Edges and the ready set](#edges-and-the-ready-set)
- [Epics, priority and milestone](#epics-priority-and-milestone)
- [Choosing the next tasks](#choosing-the-next-tasks)
- [Bottlenecks](#bottlenecks)
- [Waiting on a human](#waiting-on-a-human)
- [Human review gate](#human-review-gate)
- [Replan requests](#replan-requests)
- [Review feedback](#review-feedback)
- [Plan and execution](#plan-and-execution)
- [Several sessions](#several-sessions)
- [Gaps](#gaps)
- [The board TUI](#the-board-tui)
- [The inbox view](#the-inbox-view)
- [Other files](#other-files)

## The file

```toml
format = 1

[[task]]
id = "E1"
type = "epic"
title = "Reviewable stack"
status = "pending"
priority = "P1"
milestone = "m1-stack"

[[task]]
id = "t1"
title = "Extract the roles"
status = "done"
parent = "E1"
write_set = ["src/roles/**"]

[[task]]
id = "t2"
title = "Write the tests"
status = "pending"
parent = "E1"
deps = ["t1"]
write_set = ["tests/roles/**"]
```

- `status`, `id` and `title` are required. `type` is `task` (default) or `epic`. `priority` defaults to `P2`.
- Every task needs a `write_set`, or `read_only = true` when it writes nothing. A missing write set is an error, because an unknown write set would let two writers start on the same file. An epic needs neither.
- The optional string keys are `blocker` and `answer` ([Waiting on a human](#waiting-on-a-human)), `worktree` and `branch` ([Worktrees](task-runtime.md#worktrees)) and `stack` on an epic ([Stacks](task-runtime.md#stacks)). `blocker` is only valid with `status = "blocked"`, and `check` refuses it anywhere else.
- The optional parallelism keys are `footprint` (a positive integer, `1` by default), `exclusive` (a list of non-empty names) and `overlap = "hunks"` (a task that may share files with another one in a separate worktree), see [parallelism.md](parallelism.md). The optional key `needs` is a list of non-empty abstract strings such as `["gpu"]` (never a machine or host name); `next` carries it in the `start` entry and [remote.md](remote.md) maps it to a machine. The optional header keys are `rw`, `ro` and `budget` (positive integers), the caps of the machine that runs the work.
- The optional `model` (one copilot model id, for example `claude-opus-5.5`) and `effort` (`low`, `medium`, `high` or `xhigh`) keys are a preference for the agent that runs the task. The header may set a default per role in the tables `[model]` and `[effort]` (`implementer = "claude-sonnet-5.5"`). `check` validates the shape only. A model is not a requirement: when it is unavailable the orchestrator uses a comparable model, never a weaker one for a hard task, and logs the substitution. `next` reports the keys in its `start` entry.
- The optional `done_at` key is the time a task finished, ISO 8601 with an offset like the lines of `steering.log`. `set <id> status done` writes it and a change away from `done` removes it. `check` rejects a malformed value and warns about a done task without one (an older task). The Done column of the board, `board` and `tree --all` list done tasks newest first by `done_at`, and the ones without it after them in reverse file order. The task overlay shows it.
- The optional `human_review` key is a boolean that only the human sets, from the board with `h` (after a confirmation), on a task that is not done. A task in `in_rev` with it is a review gate: `next --awaiting` lists it with its hunk command, and `set <id> status done` refuses a marked task until the human clears the mark in the board (clearing deletes the line and does not mark the task done). `check` rejects a non-boolean value and warns about a done task that still has the mark.
- The optional `session` key is a Pi session id or path that resumes the work of the task, and `session_dir` is the `--session-dir` it was made with ([pi-sessions.md](pi-sessions.md)). Both are strings, `session_dir` needs `session`, and `check` validates the shape only. `next` reports them in its `start` and `running` entries and `resume` prints them after the worktree.
- Unknown keys, unknown statuses and a format other than the one the script reads are errors.
- Edit the file with the file tools (or a Python snippet), never with `sed`. After every edit run `check`: it refuses the file on any error, and the other commands refuse it too.
- The runtime state of an attempt (agent, tier and effort, reviewer, round, verdict, commit) is not here. It lives in the Active agent registry of the ledger. The worktree and the branch of a task are the exception: they are here, and the registry may list the worktree of an attempt too.

## Briefs and titles

A brief is `briefs/<id>.md` next to `tasks.toml`. It is a convention, not a key: the objective, the write set, the acceptance and the decisions with the why. `check` warns (and still exits 0) for each leaf that is not done and has a problem with its brief: an `in_progress` leaf with no brief, a ready leaf with no brief, and a brief with no `Acceptance` heading. It does not look for a brief when the file comes from stdin.

A brief replaces a long title. Title guidelines:

- About 60 characters, human friendly: the outcome, readable in the TUI at a glance. `check` warns above 60.
- Do not repeat what the row already shows: the id, the layer number, the status, the epic name.
- Gates read "Gate: <roles>". Keep a commit SHA only when it is the result.
- Details go in `briefs/<id>.md`, never in the title.

`check` also warns (exit 0; `tree` does not print it) about two open leaves that may be duplicates: the words of id and title (no stop words) have a Jaccard of 0.5 or more, or both list the same `write_set` entry and share a title word (a shared `.md` doc counts only when two or more entries are shared). The titles must share at least two significant words (not a stop word, at least 4 characters). A pair where both tasks set `overlap = "hunks"`, or where one lists the other in `deps`, is not reported. An optional `similar = ["other-id"]` on one of the two silences the pair.

## Edges and the ready set

- The only stored edge is `deps`: the ids a task is blocked by. This is GitHub's `blocked by`.
- `blocking` is the inverse of `deps`. Compute it, never store it. A stored inverse drifts.
- A task is blocked while any id in `deps` is not `done`. `blocked` in `status` is for an external blocker (a decision, a failed operation), not for an unfinished dependency.
- The ready set is every leaf with `status` pending, all `deps` done and no epic above it with status `blocked`.
- `status = "deferred"` parks a leaf on purpose (low priority, costs credits): not now, and nobody is awaited, unlike `blocked`. A deferred leaf is never ready, never in `start` or `skipped`, never running and holds no slot or write set, and it holds nothing up (no `blocks N`). A task that depends on it waits as usual, and `explain` names it in `blocked_by`. It counts as open for the progress of its epic, and the `tree` shows it (dim). `resume` and `epics` leave deferred leaves out of every list. Move it back with `set <id> status pending`.
- `check` finds a missing id, a parent that is not an epic and a cycle. A cycle is a planning error: stop and replan.

## Epics, priority and milestone

- **Epic:** a block with `type = "epic"`. It is never delegated and never scheduled, even without children. A task with a `parent` is a child, and the leaves are the tasks that are not epics. The `parent` may be another epic, so epics nest at any depth and the leaves of an epic are the ones of its whole subtree.
- An epic has a goal, details and a definition of done in the ledger's `Epics` section ([ledger.md](ledger.md)). The definition of done is checkable: a command, a diff property or a review verdict.
- All leaves done is necessary, not sufficient. An epic is `done` only after its acceptance check ran and the result is in the evidence log. The `epics` command reports `children_done`, the orchestrator sets `done`.
- An epic far in the future may stay not broken down on purpose: its leaves are added when the work gets close. The `tree` shows it with an empty bar and `0/0`, and that is normal, not a defect.
- A `deps` entry on an epic is met only when the epic itself is `done`, so the acceptance check gates the dependents. A child inherits the `deps` of its epic.
- **`priority`:** `P0` to `P3`, same names as a GitHub priority field. It is the first sort key of the next-task selection. The human or the orchestrator may change it at any time: edit the value and run `next` again.
- **`milestone`:** a delivery gate, for example the first reviewable stack or ready to merge. Any task, epic or floating, may set it. Set it on the epic: leaves without one inherit it. It is only a tie-break after `priority` and the state (names sort alphabetically, so use `m1-...`, `m2-...`). It does not block anything. A GitHub milestone groups issues with one due date and progress: this is the closest equivalent.
- **Display order:** the `tree` and the board sort epics and floating tasks by running first, then priority, then ready before waiting, then milestone, then file order (`board-tui.md`).

## Choosing the next tasks

Do not pick tasks by reading the file. Run the script. It reads `tasks.toml` (or `-` for stdin) and prints JSON, except `tree`, `board`, `resume` and `cleanup`:

```bash
scripts/ledger_graph.py <ledger-dir>/tasks.toml next --rw 2 --ro 3 --budget 6 --caps-note "load 1.2/14, 11 GiB free"
```

`ledger_graph.py <tasks.toml> <command> --help` is the contract: it lists the flags, every output key, the exit codes (0 ok, 1 a failed `cleanup`, 2 an invalid file, id or value) and an example of every command, so an agent never reads the script. This file does not repeat the output keys.

The shebang runs it through `pixi exec` with Python 3.12 and `rich`. `python3 scripts/ledger_graph.py ...` works only when `python3` is 3.11 or newer and `rich` is installed. On Pi call it through bash with the full path under `~/.agents/skills/orchestrating-agents/scripts/`, and put the full path of `pixi` in front when it is not on the `PATH` of a non-interactive shell.

### Commands

- `check`: the format, ids, keys, statuses, priorities, write sets, parents and deps are valid, with no cycle. Exit 2 and a list of errors otherwise. It also lists `warnings`: one line for each task that holds up more than `--max-blocked` open tasks (default 3, see [Bottlenecks](#bottlenecks)), for a nesting deeper than 8 levels ([Stacks](task-runtime.md#stacks)) for a done task that is still marked `human_review` and for the briefs ([Briefs and titles](#briefs-and-titles)): an `in_progress` or ready leaf without `briefs/<id>.md`, and a brief without an `Acceptance` heading.
- `ready`: leaves with status `pending` and nothing left to wait for, in priority order.
- `next [flags]`: what to start now. `start` is ordered, its write sets are disjoint from the running tasks and from each other, and each entry has its `read_only`, `footprint` and resolved `worktree` and `branch`. `skipped` carries a reason: `no free slot`, `no free read-only slot`, `no budget left`, `budget reserved`, `exclusive resource` or `write set conflict`. It also lists `running`, `waiting` (the unmet blockers), `blocked`, `awaiting_human` (`{id, question}` for a human blocker, `{id, review}` with the hunk command for an `in_rev` task marked `human_review`) and `awaiting_event` (`{id, event}`, a blocker that starts with `event:`). With `--awaiting` it also prints `replan_requests` ([Replan requests](#replan-requests)) and `feedback` ([Review feedback](#review-feedback)). The flags are `--rw N` (`--slots N` is its alias), `--ro N`, `--budget B`, `--caps-note`, `--pick a,b`, `--awaiting` and `--peek`; what they mean is in [parallelism.md](parallelism.md).
- `next --pick a,b`: the same, with `start` reduced to those ids. The list may not be empty, and the ids must be a subset of what `next` would start, otherwise exit 2 and the message says which id cannot start and what `next` starts instead. It is the re-validation of a choice made outside the script.
- `explain <id>`: status, wave, unmet blockers, what the task blocks, whether it is ready, the open leaves it holds up (`holds_up`) and a `warning` past `--max-blocked`.
- `epics`: leaves done per epic and `children_done`.
- `resume`: for a new session, a text digest and not JSON, from `ORCHESTRATION.md` next to `tasks.toml`: READY and RUNNING under the caps of the header (each RUNNING line ends its branch with the worktree state: `missing`, or `clean`/`dirty N` and `ahead N` of the base, `unknown` when git fails), BLOCKED, the last 3 DECISIONS, the CONSTRAINTS file to read (flagged when missing), the first NEXT action and the STACKS. The format is in [compaction.md](compaction.md#the-re-entry-digest).
- `tree [--all] [--epic <id>] [--width N] [--color always|never]`: the epics and their tasks as a colored text tree that fits the terminal, one row per task. An italic title marks a task that starts as soon as it can, and a dim one waits. Done tasks and finished epics are hidden unless `--all`; `--epic <id>` shows one subtree. It prints no warnings: `check` is the only place that does. The symbols, colors and the legend are in [The board TUI](#the-board-tui).
- `cleanup [--apply]`: which worktrees and branches the ledger names can go, as text, in three classes: `remove`, `keep` and `orphan`. It changes nothing without `--apply`. `--tip` and `--repo DIR` pick the tip branches are compared with and the git repository, and `--max-removable N` warns above N remove candidates. The rules, and what `--apply` does, are in [Worktrees](task-runtime.md#worktrees).
- `board`: the leaves in the columns of a GitHub Project board (Todo, In progress, Done), as plain text. A card shows what blocks it, the question it asks the human and its branch, `blocks N` past `--max-blocked` and `waits <id> (files)` when `next` holds a ready card back on a `write_set` (`waits <ids> (dep)` for the deps). A card that waits on a human comes first in its column.
- `compare <new.toml>`: what a replanned file changes, for the replanner role: `added`, `removed` and `changed` ids (`write_set`, `deps` and `exclusive` compare as sets), `header`, then `before` and `after` with the `waves`, the `ready` set, the `gates`, the simulated `rounds` and the `stuck` leaves (they never start, for example behind a human blocker); the keys are in `compare --help`. The first argument is the old file, the one after `compare` is the new one, and a new file that `check` would refuse exits 2. It also exits 2 when the new file changes or removes a task that is `done`, `in_progress` or `in_rev` in the old one, with an error per id that names the status and the changed keys; a replan only adds tasks and changes `pending`, `deferred` and `blocked` ones.
- `machine <id> [--map <file>]`: for a task with `needs`, the `machine` label and `transport` that the per-user map picks (the first machine whose `needs` cover all of the task's), or `null` for both when none matches. The map is the `--map` file or `$LEDGER_MACHINE_MAP`. Read-only: it opens no connection and never prints an ssh target. See [remote.md](remote.md).
- `set <id> priority <P0..P3>`: a human write. It refuses a file that `check` would refuse, an unknown id, any field other than `priority` and `status`, and a result that differs from the file by more than that one priority. The block keeps its comments, key order, layout and line endings, and one line (timestamp, id, old and new priority) is appended to `steering.log` next to the file. `--no-next` is refused for a priority (exit 2).
- `set <id> status <pending|in_progress|done|blocked|deferred>`: the orchestrator write, with the same mtime guard, layout and `steering.log` line (`<id> status <old> -> <new>`). It refuses an unknown status or id, a task blocked on a human (only `answer` frees it), `done` on a task marked `human_review` (only the human clears the mark, in the board) and a result that differs from the file by more than that status and, when it leaves `blocked`, the `blocker`. After a change to `done`, `blocked`, `deferred` or `pending`, which free capacity, it prints `limits`, `start` and `skipped` of `next` for the state after the write. It has no cap flags: caps come from the header of `tasks.toml`, and without them `limits` is all null, so the orchestrator runs `next --rw .. --ro ..` itself before starting. `in_progress` and `--no-next` print only the change. Setting the status a task already has writes nothing and still prints `next`.
- `answer <id> "<text>"`: the other human write. It answers a task blocked on a human: `status = "pending"`, `blocker` removed, `answer` written. It refuses a task that is not blocked on a `human:` blocker, an answer with a control character other than a tab or a line feed (a carriage return is refused), an answer with a line separator (U+2028, U+2029), and a result that differs from the file by more than that task's status, blocker and answer. The answer may span several lines: a line feed is written escaped (`\n`), so the `answer` key stays on one line of the file, and every one-line display of it (`resume`) shows the lines joined by a space. Same protections as `set`.
- `register <id> --title T --parent P [--deps ...] [--write-set ...] [--read-only] [--model M] [--effort E] [--brief-from FILE|-]`: for a task created mid-conversation. It appends the task as `in_progress` (after the same validation as `check`; an invalid task leaves the file untouched and exits 2), writes `briefs/<id>.md` (the file or stdin, else an empty `Change`), built from the skeleton of `task-brief.md` (the gate first, then the title, `Change`, `Acceptance`, `Constraints` with the write set, and `Return`) and prints the ledger gate: `[ledger:<id>]`, the brief path and the instruction `First action: grep tasks.toml for id "<id>" and check status in_progress and briefs/<id>.md exists; otherwise return only NO LEDGER TASK: <what is missing> and do nothing else.` Paste that output at the start of the `task` prompt, so an agent launched without a ledger task stops. The text is the constant `LEDGER_GATE` in the script. `next` does not print it: use `register` for an ad-hoc task and paste the same gate for a task of `start`.
- `attach <id> <agent-id>`: records the agent that runs the task, the same as `event <id> agent <agent-id>`.
- `event <id> [<event> "<detail>"]`: appends one line to `events/<id>.jsonl`, or without an event prints the log. It refuses an unknown id and stdin. See [Event log](task-runtime.md#event-log).

Order of `start`: `priority`, then `milestone`, then the number of pending leaves the task unblocks (more first), then the file order. Statuses `running`, `in_progress`, `in_rev` and `returned` hold their write set, their footprint and their `exclusive` names. A `read_only` task never conflicts on a write set and is counted against `ro`. Write set overlap compares the normalized literal path prefix before the first glob character, so it errs on the safe side.

Run `check` after every edit of the file and `next` at every scheduling wave, then start exactly `start`. An orchestrator may narrow that list with judgment the script does not have (a credit budget, an agent that is about to return), and `--pick` is how that choice is checked: it re-runs the selection and refuses any id the script would not start ([SKILL.md](../SKILL.md#command-shorthands)). Never start a task from `skipped` or `waiting` by judgment: change the file (a priority, a write set, a dependency) and run `next` again. `watch -n2 scripts/ledger_graph.py tasks.toml board` follows the board live.

## Bottlenecks

One task must never block many others. A leaf that is not done holds up a leaf that is not done when that leaf waits on it through `deps`, directly or not. It also holds up the `pending` leaves that share a path in `write_set`, and the leaves that wait on those through `deps`. For a shared path the task that counts is the running one, or the one `next` would start first when neither runs, so two tasks that share files are not each counted against the other. A pending leaf that overlaps two running tasks is held by both. Two leaves that both set `overlap = "hunks"` in separate worktrees do not hold each other up. A `blocked` or running leaf that shares a path is not a write_set hold, but it counts through `deps` when it waits on the task.

`check` and `explain` warn when a task holds up more than `--max-blocked` open tasks (default 3), and list the held ids with the reason (`deps` or `write_set`). `board` shows `blocks N` on that task, with the same limit, and `tree` shows it for every task with not started tasks behind it, with no limit. The warning does not change the exit code. A bottleneck is a planning smell: split the task, or narrow the write sets, and run `check` again. The replanner proposes the split ([reviewers/replanner.md](../reviewers/replanner.md)).

## Waiting on a human

In autopilot the orchestrator never waits idle on a human. When a task needs an answer only the human can give:

1. Finish whatever that task can do without the answer, first.
2. Write the context in `inbox/<task-id>.md` first, unless the choice is trivial, so the human reads the evidence and the options in the inbox view.
3. Mark the **leaf** `blocked` and write `blocker = "human: <question>"`. Never the epic: a blocked epic blocks all its leaves.
4. Keep calling `next`. The rest of the graph keeps flowing, and only the tasks that depend on the blocked one wait.
5. Print the `awaiting_human` list of `next --awaiting` to the human every round, so the question is in front of them.
6. When `start` is empty and `awaiting_human` is not, say which questions are open and end the turn, unless `awaiting_event` is not empty: then an event is still pending, so follow [Event-driven loops](task-runtime.md#event-driven-loops) instead. With no `event:` blocker open there is nothing left to do and nothing to poll.
7. The human answers from the board TUI with `e`, or with `answer <id> "<text>"`. The task becomes `pending` again, `blocker` is removed and `answer` holds the reply.
8. When that task starts, its brief includes `answer`.

```toml
[[task]]
id = "t7"
title = "Pick the retry policy"
status = "blocked"
blocker = "human: retry three times or fail fast?"
write_set = ["src/retry.py"]
```

`blocked` without a `human:` blocker keeps its meaning: an external blocker the orchestrator owns (a failed operation, a decision it takes itself, an `event:` it waits for). Only a `human:` blocker shows up in `awaiting_human` and can be answered with `answer`. Only an `event:` blocker shows up in `awaiting_event`.

### Human review gate

A task can also wait on a human review without a question. The human marks the task in the board (`h`, then a confirmation) and the key `human_review = true` appears in its block. When that task is in `in_rev`, `next --awaiting` lists `{id, review}` in `awaiting_human`, with the same hunk command as the board, and the board, `board` and the `Blockers` badge count it as awaiting you. The orchestrator prints that entry every round like a question, does not call `set <id> status done` (it is refused) and does not merge the task. The human reviews the diff, then clears the mark in the board (`h` again); the orchestrator sees the key gone on its next read and continues. `resume` lists only the entries with a `question`.

### Replan requests

The human can ask the orchestrator to replan, from the tree of the board (`R`, then a confirmation and an optional one-line note). The board never runs the replanner: it writes one file, `replan-requests/<UTC time>.toml` (for example `20260102T150405Z.toml`), next to `tasks.toml`, and changes nothing else (not `tasks.toml`, not `steering.log`). The file is written once, atomically and without replacing another one, after the same stale-file guard as the other writes. Its keys:

- `scope`: `remaining` (the default: every task that is not `done`) or `epic` (the open tasks under the epic the human spotlit, offered only when an epic row is spotlit).
- `epic`: the id of the epic, only for the scope `epic`.
- `note`: an optional one-line text from the human, for the replanner.

`next --awaiting` lists the pending files as `replan_requests`: `{request, scope, epic, note}` oldest first, or `{request, error}` for a file that is not valid TOML, and the `inbox` shorthand prints them. The orchestrator runs `replan` as usual, then moves the handled file to `replan-requests/done/`, which `replan_requests` does not list. The past is frozen in both scopes: a task that is `done`, `in_progress` or `in_rev` is never changed or removed by a proposal, and `compare` enforces it. The scope is a request to the replanner, not a check: `compare` does not tell whether a proposal stays inside the epic.

### Review feedback

The human reviews the diff of a task in `in_rev`, or steers a task in `in_progress`, in hunk (`hunk diff`, opened from the command of the board) and writes inline comments there. While the board runs, it asks every `in_progress` or `in_rev` leaf that has a worktree for its comments every 2 seconds, with `hunk session comment list --repo <worktree> --type user --json`, in a worker thread with a 5 second timeout. No session, a missing hunk, a timeout or an answer that is not that export is silent: nothing is saved and the board never waits for it. Otherwise the comments are saved as a draft, `feedback/<task-id>.json` next to `tasks.toml`: `{"ready": false, "comments": [...]}`, where each comment is the object hunk wrote (`noteId`, `filePath`, `newRange`, `body`, `author`, ...). The draft mirrors the comments of the session while the session has some. An empty list never wipes it, so a hunk session that was just opened keeps the draft.

The human sends the draft with `f` (after a confirmation, cancel is the default), which sets `"ready": true` after the same stale-file guard as the other writes and refuses a task that is neither `in_progress` nor `in_rev`, a task without a draft and a draft that is sent already. Nothing else is written: not `tasks.toml`, not `steering.log`. A sent file is never overwritten by the poll. The human restores the draft in a new hunk session with `F` (after a confirmation), which pipes `{"comments": [{filePath, newLine, summary, author}]}` to `hunk session comment apply --repo <worktree> --stdin`; the comments come back as notes of an agent, not editable ones, and the board writes nothing. The restore is **unverified** against a real hunk: only the help of hunk 0.23.0 was read, no live session was available, so what hunk does with a line that no longer exists is not known.

`next --awaiting` lists the files that are `ready` as `feedback`: `{task, file, comments: [{path, line, text}]}` (`line` is `newRange[0]`), or `{task, file, error}` for a file that is not valid or has a comment without `filePath`, `newRange` or `body`. A draft is never listed. The orchestrator reads each comment as a finding the human already decided, sends it to the writer of the task as such, and moves the handled file to `feedback/done/<task-id>.<UTC time>.json` (for example `r1.20260102T150405Z.json`), which `feedback` does not list. The poll leaves out of a new draft the comments whose `noteId` is in a file of `feedback/done/`, because hunk keeps listing them. Sending the comments does not keep the task awaiting: the `human_review` mark still decides that.

## Plan and execution

While planning, the orchestrator may create and edit anything in `tasks.toml`. During execution it only moves tasks forward: it writes the `status` of the tasks it runs, and their `blocker`, `worktree` and `branch`. It never rewrites the file from memory.

The human steers only from the board TUI, and only `priority`, `answer`, the `human_review` mark, the `deferred` to `pending` transition, the replan requests and the review feedback. The orchestrator never runs `set <id> priority` or `answer`: they are the human's path, used by the board and by the tests. The orchestrator marks its tasks with `set <id> status done` (or `blocked`, `pending`), so what to start next is always in the answer in front of it, and it does not need to remember to run `next`.

## Several sessions

A long feature can span several independent sessions, and a clean session spends fewer tokens than a compacted one. The ledger and `tasks.toml` live outside the sessions, so a new session starts with `resume` and reconciles every task in a running status that resolves a worktree, following "Recover active work" in [SKILL.md](../SKILL.md#recover-active-work), before it starts anything new.

- One orchestrator session writes a ledger at a time. Writes take an exclusive `flock` on `tasks.toml.lock`, so two writers never interleave, but two orchestrators still plan against different views of the work. The lock serialises the script and TUI writers (`set`, `answer`); a direct hand edit of `tasks.toml` does not take it.
- Close a session at the boundary of an epic or a wave, with every task `done` or `pending`. If a task is still running, record its `worktree` and `branch` in `tasks.toml` and its agent in the registry first.
- For work big enough to need several orchestrators in parallel, use one ledger per epic.

## Gaps

- Views (table, board, roadmap) cannot be created with `gh project`. Automations (auto-add, auto-archive, close on merge) are UI only.
- Issue fields are in public preview and are probably writable only with `gh api`. Scope `project` is needed for `gh project`.
- GitHub has no ready-set query: the ready set is computed here, from the `blocked by` edges.
- The script writes only the priority, the status and the answer to a human blocker, with `set` and `answer` or from the board. It holds an exclusive `flock` on a `tasks.toml.lock` sidecar from the read to the rename, writes through a unique fsynced temporary file, and guards every write with a compare and swap on the mtime. The lock only binds the script and the board: an editor that does not take it can still race.

## The board TUI

`scripts/ledger_board.py <ledger-dir>/tasks.toml` is a terminal window on the same file, with the tree, the board (columns, or with `c` the text overview) and Blockers, the inbox of the questions that wait on you. The symbols, colors, connectors, card borders (the card of the task `next` would start first shows `next` in its title border, `next · P1`), the overlay layout, the colored pick menus and the footer are in [board-tui.md](board-tui.md), the visual spec of the tree and the board.

- It shows the current state of the file only: no history. It polls the file every second, reads it again when it changed and keeps the view and the selected question. `r` reads at once. It does not read while a `p` or `e` prompt is open.
- A click on a task, or `o` or `enter`, opens the overlay of the task with its brief, its history and the keys that apply: `p` for the priority, `s` for the state, `e` to answer a question, `y` to copy the hunk command of a running task or one in review (a copy icon on its row does the same, see [board-tui.md](board-tui.md#review-command)). The `p` and `s` menus have a border in the color of the highlighted option.
- `h` marks the spotlit task (tree), the selected card (board) or the open overlay task for human review, or clears the mark, after a confirmation. It writes only the `human_review` line ([Human review gate](#human-review-gate)).
- `R` in the tree asks the orchestrator to replan, after a confirmation menu (cancel is the default) that offers `remaining`, and the open tasks of the spotlit epic when the spotlit task is an epic, then an optional note. It writes only a new file in `replan-requests/` ([Replan requests](#replan-requests)).
- `f` sends the saved hunk comments of the spotlit task (tree), the selected card (board) or the open overlay task, after a confirmation. `F` applies them to the hunk session of the worktree again. The board polls hunk for them ([Review feedback](#review-feedback)).
- Only a human sets a priority, from here or with `set`, moves a task between `deferred` and `pending` (`s`), and answers a blocker, from here or with `answer`. The board writes no other field and never rewrites the file from memory: it edits the `priority` line, or the `status`, `blocker` and `answer` lines of the task it unblocks.
- A write is refused when the file changed since the view was loaded (and a write typed in an overlay, when the file changed since the overlay was drawn), so reload and type it again. The mtime is checked once more just before the rename, which narrows but does not close the window between the read and the write.
- The priority a human types is authoritative, and the orchestrator re-reads `tasks.toml` before every `next`, because the order may have changed under it.

## The inbox view

The third view of the board, Blockers, lists the tasks that wait on you, most urgent first. A row opens the overlay of the task, where the question, its context and the keys `e` (answer) and `p` (priority) are. The context file is a convention, not a key of `tasks.toml`: it is `inbox/<task-id>.md`, in the folder of the file the board shows. Nothing else reads it, `ledger_graph.py` ignores it, and it stays on disk after the answer as a record. The agent does not read it back: the reply it needs is `answer`.

The agent writes it before it marks the task blocked, whenever the choice is not trivial. The `human:` blocker text and the inbox file are written in the human's conversation language (Italian for Diego), so he reads and answers faster; ids, commands, file paths and code stay as they are. The sections (`Question`, `Context`, `Options` with what happens, cost, risk and reversibility of each, `Recommendation`, always present and on its own heading, and `check` warns when an inbox file of a task waiting on a human has none) and an example are in the inbox part of `board-tui.md`.

## Other files

- [parallelism.md](parallelism.md): `footprint`, `exclusive`, `overlap`, the caps `rw`, `ro` and `budget`, the `next` flags and waves.
- [task-runtime.md](task-runtime.md): worktrees, `cleanup`, stacks, event-driven loops, the event log and attempt events.
- [github-mapping.md](github-mapping.md): the key to GitHub mapping, iteration versus wave and what is unverified.
