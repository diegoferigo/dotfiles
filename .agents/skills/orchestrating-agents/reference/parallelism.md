# Parallelism: caps, footprint, exclusive and overlap

How the ready tasks of `tasks.toml` are run at once. The file format and the ready set are in [task-graph.md](task-graph.md). The flags and output keys of `next` are in `scripts/ledger_graph.py <tasks.toml> next --help`.

A `[[task]]` block says what the work needs, never what the machine affords. The same file is scheduled the same way on a laptop and on a workstation, and only the knobs outside the work description change: the header of the file for the machine that usually runs it, the flags of `next` for the machine that runs it now. A task never names a host, a core count or an agent slot.

Run at once the ready tasks that satisfy all of these:

1. Their `write_set` globs do not overlap ([git-modes.md](git-modes.md)), unless both tasks set `overlap = "hunks"` (see `overlap` below).
2. The caps have room: `rw` for the writers, `ro` for the read-only tasks, `budget` for the sum of the footprints.
3. They share no name in `exclusive`.

A ready task that fails 1, 2 or 3 waits for the one it conflicts with, without a new edge in `deps`: the conflict is temporary and the dependency is not real. Add a `deps` edge only when one task needs the output of the other.

## What the work declares

```toml
format = 1
rw = 2
ro = 3
budget = 6

[[task]]
id = "t9"
title = "Build the image"
status = "pending"
write_set = ["docker/**"]
footprint = 4
exclusive = ["docker-build-dir", "port-8080"]
```

- **`footprint`:** how heavy the task is, a positive integer, `1` by default. It is a relative weight, not a unit: a task that runs a full build weighs more than a task that edits a Markdown file.
- **`exclusive`:** free-form names of what the task takes alone, for example a build directory, a GPU, a port, a remote branch. Two tasks that share a name never run together. The names mean nothing to the script: it only compares them.
- **`overlap`:** `"hunks"` is the only value. It says the task shares files with others, but in separate regions, so two writers in separate worktrees can merge without a conflict or with an easy one. `next` ignores the `write_set` overlap of two tasks when both set it and neither runs without a worktree and the two worktrees, when known, differ (a pending task gets its own worktree when it starts). The default, no key, keeps the overlap a conflict. `exclusive`, `footprint` and the caps still apply. The pair is not a `write_set` hold for the bottleneck guard ([Bottlenecks](task-graph.md#bottlenecks)). The script does not merge: the orchestrator integrates the two branches one after the other in the order of [git-modes.md](git-modes.md), and a real conflict is a stop condition there. `check` lists each such open pair under `notes`, `explain` lists them as `overlaps_on_purpose`, and a `next` start that shares a file with a running or started task carries `overlaps`.
- **`read_only`:** a task that writes nothing. It never conflicts on a write set, and it is counted against `ro` instead of `rw`.

## What the machine affords

- **`rw`:** how many writers (tasks without `read_only = true`) may run at once.
- **`ro`:** how many read-only agents (reviewers, researchers) may run at once.
- **`budget`:** the largest sum of the footprints of the running plus the started tasks.
- The running tasks (statuses `running`, `in_progress`, `in_rev` and `returned`) count against the three caps and hold their `exclusive` names, so `next` fills only what is left.
- `next` requires the three flags `--rw`, `--ro` and `--budget` and exits 2 without them, listing the probes to run first: `nproc`, `uptime`, `free -h` and `df -h <path>`. The orchestrator picks the caps from the load it expects, the script does not compute them. It also requires `--caps-note "<reason>"` (for example "load 5.7/14, 11 GiB free, 2 gates expected") and appends one line to `<ledger-dir>/caps.log`: the ISO time, the three caps and the note. `next --pick` and the read-only commands need no note.
- `next --awaiting` prints only `awaiting_human` and `awaiting_event`, needs no caps and writes no log. An `awaiting_human` entry has a `question`, or a `review` command for a task marked `human_review`.
- `next` also prints `answered` (id, answer, time, status of the answers logged in `steering.log` since the last `next`, 24 hours on the first run) and then moves the `.next-seen` stamp (a `steering.log` offset, written after the output, with `next` as its only consumer) next to the ledger. `next --peek` prints it without moving the stamp.
- A cap that is nowhere is no cap. A flag of `next` overrides the header key of the same name, and `--slots` is an alias of `--rw`. A cap, in the header or on a flag, must be a positive integer: `next` exits 2 otherwise.
- A task heavier than the whole budget would never start, so it may start alone: it is picked only when nothing is running and nothing else was started in the same wave. Until then it keeps the wave for itself, and the lighter tasks below it are skipped with `budget reserved`, otherwise they would start again and again and it would starve. A task of a higher priority is ordered before it and still starts.
- The caps of the header are the only machine-dependent values in the file. Keep them out when the ledger is shared between machines and pass the flags instead.

## Rules that raise parallelism

- **Start the fix writer at once.** Start a fix writer on its own work branch as soon as its review is accepted. A `deps` edge means integrated, not needed to start.
- **Measure before capping.** Check `nproc`, the load and `free -g` before setting the caps. LLM agents are cheap locally and the test suite is the cost, so size the caps by the suite and say so in the `--caps-note`.
- **Run tests and hooks in the foreground.** An agent waits for them. A backgrounded run makes the agent return idle and costs minutes of orchestrator turns.
- **Verify per layer, then once.** Verify each layer of a stack in its own worktree, in parallel. Run the stack-wide suite once at the end.
- **Run the stack-wide contrarian early.** Run it on the layers that are already stable, not only at the very end.
- **Reuse idle reviewers.** Re-review a single `Address review` commit with the idle reviewer of that layer (`write_agent`), not a new one.

## Waves

Wave = the longest chain of `deps` below a task, plus one. Tasks in the same wave are candidates for parallel runs. A wave is derived, never stored. An epic waits for its own children, so its wave is the one it ends in: `tree` does not draw waves.

## Hard ceiling, soft cap and the capacity recompute

Two limits bound the whole orchestration tree, not only the root layer. The **hard ceiling** is the harness's own maximum (Copilot CLI: `subagents.maxConcurrency` and subagent depth in `/settings`), which you cannot exceed. The **soft cap** is yours: a resource- and budget-aware limit you keep at or below the ceiling and actively monitor. Use credit, latency and rate-limit budgets first. Inspect local load, memory and GPU only for tasks that run local builds, tests or inference. Keep one slot free for review or revision unless the user explicitly prioritizes all capacity. `cap=N` of the `next` shorthand pins the soft cap for the session; absent it, default to a conservative few and adjust from what you observe.

Before the first delegation and every new scheduling wave, recompute capacity:

1. Read the harness ceiling, active-agent count, credit/rate-limit state and the resource profile of every ready task.
2. When tasks run local commands, inspect CPU/load, available memory, free disk on the repo/worktree filesystem and relevant GPU state. Typical probes are `nproc`, `uptime`, `free -h`, `df -h <path>` and `nvidia-smi` when present.
3. Set the soft cap below the first limiting resource and leave enough memory, disk and compute for the orchestrator and integration checks.
4. Give that capacity to the script instead of applying it by hand: `scripts/ledger_graph.py <tasks.toml> next --rw <writers> --ro <read-only agents> --budget <weight> --caps-note "<reason>"`. Run the probes first: `next` exits 2 without the three caps and the note, and logs them to `<ledger-dir>/caps.log`. The flags describe this machine, `tasks.toml` only describes the work. Always pass `--ro` too: `--rw` bounds the writers only, so the writers plus the read-only agents stay at or below the harness ceiling only when both caps are set. Every cap is a positive integer.
5. Narrow the list only through an arbiter, when judgment the script lacks should reduce it (a credit budget, an agent about to return, a risky pair). Spawn one fast-tier agent with `start`, `running` and the caps, and require a subset of those ids, never a new one. Re-validate the answer with `next --pick <ids>` and start what it prints. On exit 2 the answer is invalid: drop it and start the list the script printed.
6. Start exactly what the script printed. When an agent finishes, recompute the caps and run `set <id> status done`, which writes the status and prints the next start list (`--no-next` skips it), then start that list.

Do not leave capacity idle while substantive compatible work is ready. Reserve a slot for review/revision only when a return is pending or review work is queued; otherwise use it and reserve capacity again at the next scheduling wave. Never start unnecessary tasks only to fill a slot.
