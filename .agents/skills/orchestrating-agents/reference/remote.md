# Remote runs: a subagent on another machine

How the orchestrator runs a task on another machine of the user's stack (a workstation with many cores or a GPU) while the ledger stays on the orchestrator's machine and carries no host name. This page is a procedure for the orchestrator. `tasks.toml` has an optional `needs` key and the scripts validate the `remote` event and read the per-user map below (read-only: `ledger_graph.py <tasks.toml> machine <id>`); nothing in the repo starts anything remotely. The only machine-related data in the ledger are the `needs` of a task and events (see [Ledger entries](#ledger-entries)). The file format is in [task-graph.md](task-graph.md), the event log in [task-runtime.md](task-runtime.md#event-log).

## Contents

- [What was checked](#what-was-checked)
- [Rules](#rules)
- [Choose the machine and the transport](#choose-the-machine-and-the-transport)
- [The per-user map](#the-per-user-map)
- [Code travels by git](#code-travels-by-git)
- [Run the agent](#run-the-agent)
- [Async completion: DONE and ACK](#async-completion-done-and-ack)
- [Ledger entries](#ledger-entries)
- [Session marker](#session-marker)
- [Per-orchestration herdr session](#per-orchestration-herdr-session)
- [Down machine: a human blocker](#down-machine-a-human-blocker)
- [Limits](#limits)

## What was checked

Every claim is marked `verified` (with the command) or `unverified`. The probe ran from the orchestrator's machine against one remote host, written `<host>` here, which is an `ssh` alias that works with `BatchMode`. On `<host>`: Copilot CLI 1.0.105 and herdr 0.9.5, no `pi`, no `wt`, no `~/.local/state/orchestration`. Probe files lived in a scratch directory under the home of `<host>`, deleted at the end. The two Copilot runs left two sessions behind (see [Session marker](#session-marker)).

Not run on purpose: `herdr machine add`, any herdr command that changes state (`agent start`, `pane rename`, a session), and any install.

## Rules

- A task never runs on a machine silently. The machine comes from the per-user map, never from the repo or the ledger.
- No host name in the repo, in `tasks.toml`, in a brief or in the ledger. Use the label of the map entry, and `<host>` in docs.
- The ledger and the briefs stay on the orchestrator's machine. The brief travels as a file in the run directory. The remote agent never edits the ledger.
- The branch travels by `git push` over `ssh` to a clone on the remote machine, never to GitHub. No `gh` account is needed there.
- A down or unusable machine becomes a human blocker. It never falls back to a local run.
- Never install anything on a shared machine without asking. The most the orchestrator may ask to install there is `pixi`, in `~`. Never bootstrap the user's dotfiles on a machine that does not have them.

## Choose the machine and the transport

Pick the machine from the `needs` of the task (abstract strings such as `["gpu"]`, see [task-graph.md](task-graph.md)), the `Compute` line of its resource profile ([task-brief.md](task-brief.md)) and the per-user map. `ledger_graph.py <tasks.toml> machine <id> --map <file>` prints the label and transport the map picks, or `null` for both when none matches; it opens no connection.

Transports, in order of preference. The per-user map says which one a machine supports:

1. **herdr session.** The agent runs in a pane of a herdr session on the remote machine (`herdr --machine <label>`). Needs herdr on the remote machine. Pi and Copilot are both supported by `herdr agent start --kind` (`verified` in `ssh <host> herdr agent start --help`: `copilot` and `pi` are in the list of kinds). Starting an agent through herdr was not run: `unverified`.
2. **A local subagent over `ssh`.** A local subagent runs commands on the remote machine through `ssh` and returns the structured result like any local agent. Needs only `ssh`. Used when herdr is missing, and for short commands (sync mode).
3. **Copilot over `ssh`.** `ssh <host> "cd <run-dir> && bash -lc 'copilot -p ...'"` (see [Run the agent](#run-the-agent)). Needs Copilot on the remote machine.
4. **A human blocker**, when none works.

Before launching, check the machine:

- `herdr machine status <label> --json` prints `status` and `error` per saved machine without prompting for a login (`verified`: `"status": "reachable"` for the saved machine).
- `ssh -o BatchMode=yes <host> true` for the `ssh` transports. `verified` that `ssh <host>` runs commands without a prompt.

Modes. Sync: the orchestrator waits for the local subagent, which uses `ssh` and returns when the commands end. Use it for short commands. Async: an agent works on the remote machine and writes its report to a path there, and the orchestrator does not wait ([Async completion](#async-completion-done-and-ack)). Use it for long runs and heavy builds.

## The per-user map

A file outside the repo and the ledger maps a need to a machine. This page does not fix its path: pass it with `--map` or set `LEDGER_MACHINE_MAP`. Only `machine` reads it, and it uses `needs` and `transport`; the first machine, in file order, whose `needs` cover the task's wins. The format is:

```toml
[machine.gpu-box]
needs = ["gpu"]
ssh = "<ssh alias>"
transport = "herdr"
kind = "own"
remote_root = "~/orchestration"
```

- `label` (the table name) is the only name that enters the ledger.
- `ssh` is the alias or target. It never leaves the file.
- `transport` is `herdr` or `ssh`.
- `kind` is `own` (the user's dotfiles are there: herdr, copilot, pi, git, gh, pixi) or `shared` (assume at most `pixi`; ask before the first `pixi` install in `~`; everything else through `pixi exec` or a scratch directory under `~`).
- `remote_root` is the directory under which the run directories are created.

A saved herdr machine is bound to one session. `herdr machine list` locally shows `id`, label, ssh target, session and state per line (`verified`): the existing entry for the host is bound to the `default` session.

## Code travels by git

The remote machine has a receiving repository, and one clone per task as the working directory. All of this was run (`verified`) with `git push` over `ssh`, no remote service:

1. Create the receiving repository once: `ssh <host> "git init -q <remote_root>/<ledger>/clone"`. It needs one commit on its own branch before the first checkout, so that the branch is checked out and the pushes below never target it.
2. Push the task branch to a ref that is not checked out: `git push <host>:<remote_root>/<ledger>/clone HEAD:refs/heads/orch/<task-id>` prints `[new branch]`. This sent the whole history of the repository to an empty `git init` repository.
3. Pushing to the branch that is checked out in that repository is refused: `[remote rejected] ... (branch is currently checked out)`. Never push to the checked-out branch.
4. The working directory is a clone per task: `git clone -q -b orch/<task-id> <receiving-clone> <remote_root>/<ledger>/<task-id>/repo`, then `git checkout -b <task-branch>`, work and commit there. The commit identity must be set in the clone (`git config user.name` and `user.email`); a global identity on the remote machine was not checked.
5. Results come back as commits: `git fetch <host>:<remote_root>/<ledger>/<task-id>/repo <task-branch>` prints the new commit in `FETCH_HEAD`. The orchestrator then integrates it as any other branch ([git-modes.md](git-modes.md)). Workers never push, so this fetch is the only way a result reaches the orchestrator.

Why a clone and not a `git worktree`: the `worktree-guard` hook of the orchestrator's machine denies any bash command that contains `git worktree add`, also inside an `ssh` command (`verified`: `Denied by preToolUse hook: Create worktrees with worktrunk ...`), and the remote machine had no `wt`. A remote worktree is not used.

## Run the agent

Sync, a Copilot run over `ssh` (`verified`, Copilot CLI 1.0.105):

```sh
ssh <host> "cd <run-dir> && bash -lc 'copilot -p \"Read brief.md in the current directory and follow it exactly.\" --allow-all-tools --trust-folder -s -n \"[orch:<ledger>/<task-id>] <title>\"'"
```

- `bash -lc` is needed: `ssh <host> 'command -v copilot'` finds nothing, because `~/.local/bin` is on the `PATH` of a login shell only (`verified`).
- The login worked without a prompt, the command exited 0 and `-s` printed only the answer. The run read `brief.md`, wrote `report.md` and then created `DONE`, as the brief said (`verified`).
- `--allow-all-tools` is required in non-interactive mode (stated by `copilot --help`). Whether `--trust-folder` is needed was not tested. `--model <model>` and `--reasoning-effort <level>` exist in `copilot --help`, for the optional `model` and `effort` keys of the task (not run).
- Copy the brief into the run directory before the run. Copying a file from the remote machine with `scp <host>:<path> <local>` was verified; the reverse direction and the brief on stdin were not run (`unverified`).
- Do not use `scp ... -` as "stdout": it writes a local file named `-`.
- The prompt must tell the agent to write its report to `<run-dir>/report.md`, then create `DONE` last, and never to edit the ledger.

Async through herdr (`unverified`, taken from `herdr agent start --help` and `herdr agent prompt --help`): in a pane of the session at a shell prompt, `herdr agent start <name> --kind copilot --pane <pane-id> -- <agent args>`, then `herdr agent prompt <name> "<text>" --wait`. Extra arguments after `--` go to the agent, which is how `-n` would carry the [marker](#session-marker). `herdr --machine <label> pane list` works from the orchestrator's machine and prints JSON (`verified`).

For Pi: `pi` was not found on the remote machine (`ssh <host> "bash -lc 'command -v pi'"` printed nothing), so Pi on a remote machine is `unverified`, including its session key, which is per working directory ([pi-sessions.md](pi-sessions.md)).

## Async completion: DONE and ACK

Four steps, pull only. No heartbeat and no push channel. All four steps were run against the remote machine (`verified`); a laptop that suspends during the run was not tested (`unverified`).

1. The remote agent writes its report in `<run-dir>/report.md`, then creates `DONE` last: `touch <run-dir>/DONE`.
2. The orchestrator, awake or on `resume`, runs `ssh <host> ls <run-dir>/DONE`. It exits 2 while the file is absent and 0 when it exists. When it exists, copy the report with `scp` and fetch the branch (`git fetch` as above), then record both in the ledger.
3. The orchestrator creates `ACK`: `ssh <host> touch <run-dir>/ACK`.
4. The remote work directory is removed only when `ACK` exists: `test -e <run-dir>/ACK && rm -rf <run-dir>/repo`. Before `ACK` it exits 1 and removes nothing. The orchestrator runs it over `ssh` right after step 3.

A run with no `DONE` after a long time becomes a human blocker (see below).

## Ledger entries

The ledger holds what is needed to reattach after a compaction or a restart, as events of the task. `event <id> <name> "<detail>"` takes any event name and `key=value` details, and `resume` prints the last one as `last=`. The `remote` event is validated (see below).

- `event <id> launch "mode=background agent=<name> host=<label> worktree=<run-dir> pane=<pane-id>"`, with the fields of [Attempt events](task-runtime.md#attempt-events). `host` carries the label of the map entry, never an ssh target; `check` warns when it looks like a host name.
- `event <id> remote "state=running|done|acked machine=<label> path=<run-dir>"` for each step. `state` is required and `machine` must be a label, not a host name; `event` refuses anything else. The newest event is the state: `resume` lists it in REMOTE, and flags `state=running` as needing a human check.

The remote state is the three notes of the design (machine, remote path, state). Nothing else is stored: the ssh target stays in the per-user map.

## Session marker

The session title of every remote agent starts with `[orch:<ledger>/<task-id>]`, so a remote run is recognised at a glance and never mixed with the other sessions of the remote machine. The marker holds no host name.

Copilot, `verified`:

- `-n "[orch:<ledger>/<task-id>] <title>"` stores `name: '[orch:probe/t1] probe'` and `user_named: true` in `~/.copilot/session-state/<session-id>/workspace.yaml` of the remote machine.
- List only the marked sessions: `ssh <host> 'grep -l "^name: .\[orch:" ~/.copilot/session-state/*/workspace.yaml'`. For one ledger use `"^name: .\[orch:<ledger>/"`. It listed exactly the two probe sessions, while the directory holds other, unmarked sessions. Avoid an apostrophe in a title: YAML doubles it.
- `copilot sessions` only has `import`, and `--resume <name>` matches a name exactly. Listing through them was not run.

herdr (`unverified`): the pane title is `title` in the JSON of `herdr --machine <label> pane list` (`verified` for the key; no marked pane exists yet). The hook `herdr-session-title` copies the Copilot session name to the pane title and is present on the remote machine (`verified`, `ls ~/.copilot/hooks`), but a pane started with `-n` was not run. `herdr pane rename <pane-id> <label>` exists in its help and sets the title by hand (not run). To list only marked panes: filter the `title` that starts with `[orch:`.

Pi: `pi --name <name>` exists (local `pi --help`); on the remote machine it is `unverified` (no `pi`).

The probe sessions `[orch:probe/t1] probe` and `[orch:probe/t2] brief file` stay in `~/.copilot/session-state` of the remote machine. Delete them there by hand if unwanted.

## Per-orchestration herdr session

Use a herdr session named after the ledger (`orch-<ledger>`), so its panes are isolated and can be stopped and deleted when the orchestration ends: `herdr session stop` and `herdr session delete`, never the `default` session. All of this is `unverified`, except where marked.

- `herdr session list` on the remote machine lists its sessions (`verified` over `ssh <host> herdr session list`: one row, `default`, with name, status, directory and socket).
- `herdr --machine <label> session list` is refused: `error: session list is not an API-backed machine command` (`verified`). Run the session commands over `ssh <host> herdr ...`.
- A saved machine is bound to one session. `herdr machine add <ssh-target> --label <label> --remote-session <name>` creates one (from `herdr machine add --help`, not run). It may ask for an approval in an interactive terminal, so it is a setup step for the user, not for the agent.

## Down machine: a human blocker

When the machine check or the first command fails, or a run has no `DONE` after a long time:

1. Do not run the task locally and do not pick another machine without the map saying so.
2. Mark the leaf `blocked` with `blocker = "human: remote machine <label> unavailable: <first error>"`, write the evidence in `inbox/<task-id>.md` and follow [Waiting on a human](task-graph.md#waiting-on-a-human).
3. Quote the command and the first error, for example the output of `herdr machine status <label> --json`.

## Limits

- No heartbeat, so a stuck remote run is only found by a `DONE` that never appears.
- Per-machine slots and budget do not exist. The caps of `next` are measured on the orchestrator's machine and do not measure the remote one ([parallelism.md](parallelism.md)). To run one task at a time on a machine, give the tasks the same `exclusive` name, for example `remote-<label>` ([parallelism.md](parallelism.md)): two tasks that share a name never run together. The `write_set` still lists the repo paths the branch changes.
- A long command on the remote machine needs a timeout and a log file in the run directory. A local subagent over `ssh` has no interactive tools.
- The remote agent has no ledger and no GitHub access by design. Anything that needs the ledger or a push is done by the orchestrator.
- The Copilot login on the remote machine is the one of that machine. Whether it is the account the user wants was not checked.
