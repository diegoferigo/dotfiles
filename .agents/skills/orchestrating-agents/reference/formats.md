# File formats and changes

The ledger has two files, and the format of `tasks.toml` is versioned. This file lists every version and every breaking change, so a session that started on an older skill knows what to do.

## Versions

| File                                 | Version | Where it is written                  |
| ------------------------------------ | ------- | ------------------------------------ |
| `tasks.toml`                         | 1       | the top-level `format = 1` key       |
| `ORCHESTRATION.md` (Markdown ledger) | none    | the `Task graph:` line of its header |

`tasks.toml` is the only source of the version. The header line of `ORCHESTRATION.md` only says where the graph is.

## Rules

- `scripts/ledger_graph.py` reads only the format it knows. Any other value is an error, and the message says to stop and not write. An old script that meets a newer file therefore stops, and an old session must not edit a file it does not know.
- A change that adds an optional key that no command needed before does not change the version. The key is only read where it exists, and a file without it keeps its meaning. An older script that meets the new key stops with `unknown key`, which is a refusal, not a silent misread.
- A change of a meaning, of a required key or of a default is breaking. It bumps the version and is listed below with how to migrate.
- Unknown keys are errors, so a misspelled key is never silently ignored.
- A migration is done by hand, or by an agent, on a copy: copy `tasks.toml` to `tasks.toml.bak-<date>`, rewrite it, run `check`. There is no `migrate` command until a second version exists.
- An old `SKILL.md` that tells the agent to edit a task table in the Markdown ledger is the one case a script cannot stop. `ledger.md` says not to create such a table.

## Event log

`events/<task-id>.jsonl`, next to `tasks.toml`, has no version. Each line is a JSON object with `ts`, `task`, `event` and `detail`. Lines are only appended, and a reader ignores unknown fields, so adding a field is not a breaking change. See [task-runtime.md](task-runtime.md#event-log).

## Changelog

### Format 1

First version. Keys of a `[[task]]` block: `id`, `type` (`task` or `epic`), `title`, `status`, `priority`, `milestone`, `parent`, `deps`, `write_set`, `read_only`.

Later optional keys, added without a version bump because no command needed them before and a file without them keeps its meaning: the strings `blocker` and `answer` (a question for the human and its reply, see [task-graph.md](task-graph.md#waiting-on-a-human)), the strings `worktree` and `branch` (where the task runs, see [task-runtime.md](task-runtime.md#worktrees)), and `footprint` (a positive integer) and `exclusive` (a list of names), the weight of a task and the resources it takes alone, with the header caps `rw`, `ro` and `budget` (see [parallelism.md](parallelism.md)), and the string `done_at` (ISO 8601 with an offset, stamped by `set <id> status done` and removed when the task leaves `done`; `check` warns about a done task without it).

Before this version the graph was a Markdown table under `## Task graph` in `ORCHESTRATION.md`. To migrate such a ledger, write one `[[task]]` block per row, keep `id`, `title`, `deps`, `parent`, `priority`, `milestone`, `status` and `write set` (as `write_set`), set `type = "epic"` on every row that has children, drop the other columns (they belong to the Active agent registry), and delete the table.
