# Pi sessions: resume, create and fork

How to restore a Pi subagent after a restart, how to create a session with a known id, and how to fork a session from an earlier point to steer it. For starting a Pi subagent in the first place, see Pi in [harness-map.md](harness-map.md). The optional `session` key of a task in [task-graph.md](task-graph.md) stores the session to resume. This page does not define the key.

## What was checked

Everything below was run with Pi 1.4.0 (`pi --version`) over RPC (`pi --mode rpc`), without a model call. The test session was a small JSONL file written by hand in the format of the [session format](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/session-format.md): one header, two user prompts and two assistant replies. A real model reply was never requested, so anything that needs one is marked `unverified`. Each step is marked `verified` or `unverified`. The flags are listed by `pi --help`, and the RPC commands are in the [RPC commands](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-commands.md) reference.

## Where sessions live

By default Pi stores sessions under `~/.pi/agent/sessions/--<cwd>--/`, one directory per working directory, with files named `<timestamp>_<session-id>.jsonl` (`verified`: the directories exist and are named after the working directory). `--session-dir <dir>` stores and looks up sessions in `<dir>` instead (`verified`). `--session-dir` takes the highest precedence, above the `PI_CODING_AGENT_SESSION_DIR` variable and the `sessionDir` setting (stated in the [sessions doc](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/sessions.md), not tested).

A session belongs to the working directory it was created in (`verified`). Resuming it from another directory with `--session` does not load it: Pi prints `Session found in different project: <cwd>` and asks `Fork this session into current directory? [y/N]`. Without a terminal this aborts. Always start the subagent in the same worktree that created the session. This is also why the session id alone is not enough to resume: record the worktree, and `--session-dir` when it is not the default.

`--session` also accepts a session file path, and `switch_session` over RPC loads a file path from any directory without the prompt (`verified`). The header keeps the original `cwd`. Which directory Pi then uses for tools is `unverified`.

## Create a session with a known id

```sh
cd <worktree> && pi -p --session-id <id> --session-dir <dir> "<prompt>"
```

`--session-id <id>` uses the exact id, and creates the session when it does not exist.

- `verified`: `pi --mode rpc --session-id spike-one --session-dir <dir>` starts, prints `No project session found with id 'spike-one'; creating a new session with that id.` on stderr, and `get_state` returns `sessionId` equal to `spike-one` and a `sessionFile` ending in `_spike-one.jsonl`.
- `verified`: right after start the session file is not on disk, so do not check for the file before the first prompt has run. That it appears after the first message is `unverified`.
- `unverified`: the print-mode command above, because it needs a model reply.

Choose the id yourself (for example the task id plus a counter) and store it in the `session` key, so the id is known before the process starts and survives a crash. Without `--session-id` Pi picks a random UUID, which `get_state` returns as `sessionId` (`verified`).

## Resume after a restart

```sh
cd <worktree> && pi -p --session <id|path> --session-dir <dir> "<next prompt>"
```

`--session` takes a file path or an id. A partial UUID also works according to `pi --help` (`unverified`).

- `verified`: `pi --mode rpc --session <id> --session-dir <dir>` finds the session, and `get_state` returns its `sessionId`, its `sessionFile` and `messageCount` 4 for the test session, so the history was loaded.
- `verified`: the lookup in another directory asks about forking, see above.
- `verified`: over RPC, `{"type":"switch_session","sessionPath":"<file>"}` loads another session into a running process, and `get_state` then returns the loaded session.
- `unverified`: that the model answers with the loaded history in print mode.

`pi --continue` resumes the latest session of the working directory (`unverified`). Prefer `--session` with a recorded id, because the latest session may be another one.

## Fork from an earlier point

Fork is an RPC feature. Start Pi in RPC mode on the session, list the user messages that can be forked from, and fork from one of them.

```sh
cd <worktree> && pi --mode rpc --session <id> --session-dir <dir>
```

Send one JSON object per line on stdin, and read one JSON object per line from stdout. Match responses to commands with an `id`, because responses are not guaranteed to arrive in order.

```json
{"id":"1","type":"get_fork_messages"}
{"id":"2","type":"fork","entryId":"<entryId from the first response>"}
{"id":"3","type":"get_state"}
```

- `verified`: `get_fork_messages` returned both user prompts as `{"entryId":"...","text":"..."}`.
- `verified`: `fork` with the `entryId` of the second prompt returned `{"text":"Second prompt","cancelled":false}`. The process then runs in a new session: `get_state` returned a new random `sessionId`, a new `sessionFile` in the same directory, and `messageCount` 2, so the chosen prompt and everything after it are not in the fork.
- `verified`: the new file starts with a header that has `parentSession` set to the original file, and the original file is unchanged.
- `unverified`: a model reply from the fork.

To steer a subagent that went the wrong way: fork from the user message just before the wrong turn, then send a corrected message with the `prompt` command. The `text` returned by `fork` is the original prompt, to edit and resend (`unverified`: the `prompt` after a fork, because it needs a model). Record the new session id from `get_state` in the `session` key, because the original session is not the one to resume any more. The `steer` command is a different thing: it queues a message into a run that is still going, and it does not go back in the history (documented, not tested).

`pi --fork <id|path>` forks before the session starts and `/fork` does the same in the interactive UI. Both are `unverified`.

### Extensions can ask for a confirmation

With the extensions of the author's global Pi configuration loaded (not part of Pi itself), `fork` first sends an `extension_ui_request` with `"method":"select"`, the title `Fork from entry <entryId>?` and the options `Yes, create fork` and `No, stay in current session`. The fork happens only after the client answers (`verified`):

```json
{"type":"extension_ui_request","id":"<uuid>","method":"select","title":"Fork from entry a0000003?","options":["Yes, create fork","No, stay in current session"]}
{"type":"extension_ui_response","id":"<uuid>","value":"Yes, create fork"}
```

The dialog does not appear with `--no-extensions` (`verified`). An RPC client must either pass `--no-extensions` or answer every `select`, `confirm`, `input` and `editor` request, as described in the [RPC extension UI](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/rpc-extension-ui.md) reference. The other `extension_ui_request` records (`setStatus`, `notify`, `setWidget`, `setTitle`) need no answer and can be ignored.

## Summary

| step                                             | status       |
| ------------------------------------------------ | ------------ |
| `--session-id` creates a session with that id    | `verified`   |
| the session file appears after the first message | `unverified` |
| `--session` resumes with the history (RPC)       | `verified`   |
| `--session` from another directory prompts       | `verified`   |
| `--session-dir` selects the storage directory    | `verified`   |
| `switch_session` to a file path                  | `verified`   |
| `get_fork_messages` and `fork` with `entryId`    | `verified`   |
| a model reply after resume or fork               | `unverified` |
| `pi -p` with `--session-id` or `--session`       | `unverified` |
| `--fork`, `--continue`, partial UUID             | `unverified` |
