# Harness map

How the orchestrator uses the native features of a harness, and how each reviewer role of `~/.copilot/agents/*-reviewer.agent.md` is run on a given harness. The role prompts are Markdown files with YAML frontmatter and the findings format is plain text, so a harness only needs a way to start a read-only agent with a role prompt and the diff. An earlier form of the Pi command was run once with Pi 1.3.0 (see Pi). What was not checked is marked `unverified`.

## Roles and mechanisms

The five reviewer roles are Copilot custom agents, tracked in the dotfiles as `.copilot/agents/<role>-reviewer.agent.md` and deployed to `~/.copilot/agents/`. They work on their own, outside an orchestration, and the orchestrator spawns them by name under the rules of [review-rubric.md](review-rubric.md). They share the findings format of `reviewers/findings.md`; the `replanner` role stays in `reviewers/replanner.md`.

| role            | Copilot CLI                                                                                        | Pi                                                         | Other harnesses                                |
| --------------- | -------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------- |
| `correctness`   | custom agent `correctness-reviewer`. Fallback: built-in `code-review` agent                        | role prompt through the command below                      | role prompt as the body of a custom agent file |
| `critic`        | custom agent `critic-reviewer`, or `rubber-duck`                                                   | role prompt through the command below                      | as above                                       |
| `contrarian`    | custom agent `contrarian-reviewer`, or `rubber-duck`                                               | role prompt through the command below                      | as above                                       |
| `simplifier`    | custom agent `simplifier-reviewer`                                                                 | role prompt through the command below                      | as above                                       |
| `replanner`     | `general-purpose` agent with the role prompt in the brief, read-only, writes `tasks.proposed.toml` | role prompt through the command below                      | as above                                       |
| `perfectionist` | custom agent `perfectionist-reviewer`                                                              | role prompt through the command below                      | as above                                       |
| security        | built-in `security-review` agent. No neutral role                                                  | no neutral role, `unverified` whether Pi has an equivalent | not covered                                    |

The `correctness` role is the default because a built-in agent keeps its own system prompt, which cannot be replaced: the brief can add the findings format and the role rules, but not remove what the built-in forbids (style, naming and documentation findings, for example). The orchestrator may fall back to the built-in `code-review` agent when a quick review is enough and the format and the zoom out are not needed, or when the user asks for it. The built-in agents of Copilot CLI are defined in `definitions/*.agent.yaml` inside the installed CLI package. Their prompts are not copied here: the roles in `.copilot/agents/` are written separately and add the findings format and the severity mapping of this skill.

## Pi

Pi has no native subagents. The recommended way is to start another `pi` process through the shell, in print mode with a read-only tool set and the role prompt appended to the system prompt. The flags are documented in the [Pi CLI reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md). The command was run once with Pi 1.3.0, on the contrarian role and a small diff, with a plain Markdown role prompt, and it returned findings in the format of `findings.md`. The current form appends `~/.copilot/agents/<role>-reviewer.agent.md`, which starts with YAML frontmatter: that path and the other roles are `unverified` on Pi (Pi is not available here), and the frontmatter may need to be stripped first.

```sh
git diff <base>...HEAD | pi --print --tools read,grep,find,ls --append-system-prompt ~/.copilot/agents/<role>-reviewer.agent.md "Review this diff and return findings in the format of ~/.agents/skills/orchestrating-agents/reviewers/findings.md."
```

`--tools read,grep,find,ls` leaves out the tools that edit files and run commands, so a reviewer started this way cannot run a build or a test. The orchestrator runs the checks a role needs (the `correctness` role needs a build and the tests) and passes their output in the prompt. MCP tools are not removed by `--tools` unless an entry starts with `mcp__` (stated in the CLI reference linked above, not tested here).

Pi discovers `AGENTS.md` and `CLAUDE.md` ([CLI reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md)) and skills ([skills doc](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)).

### Subagents on Pi

Run the subagent from the worktree of its task: Pi reads `AGENTS.md` from the working directory, checked on Pi 1.3.0 with two directories, one with an `AGENTS.md` (the agent returned its secret word) and one without (it returned `NONE`). Pass the brief as a file and ask for the result in a file under the ledger directory, because the processes share no conversation. `--no-session` makes a reviewer ephemeral; `--session-dir`, `--session-id`, `--session` and `--fork` select where a session is stored. Pi lists the global skills of `~/.agents/skills` (checked with `--print`), but a skill under a project `.agents/skills` in a fresh git repository was not listed, so project skills are `unverified`.

To run the ready tasks in parallel, call `ledger_graph.py <ledger-dir>/tasks.toml next --rw N --ro N --budget N --caps-note "<reason>"` through the shell ([task-graph.md](task-graph.md)), with the full path of `pixi` when it is not on the `PATH` of a non-interactive shell, and start one `pi` process per task in `start`, each from its own worktree.

Herdr panels for the subagents are a Pi-only option: the orchestrator opens a pane per subagent in its worktree and reads the result file. Copilot CLI does not use herdr here.

## Capabilities with no equivalent outside Copilot CLI

These are not required by the role prompts. The orchestrating skill uses them when they exist and falls back to the manual ledger when they do not:

- dynamic workflows (`run_dynamic_workflow`);
- the session SQL database, for the harness's own notes (the ledger is `tasks.toml`, not a todos mirror);
- background agents that are addressed by id (`write_agent`, `read_agent`);
- the automatic choice of a contrasting model for the `rubber-duck` agent.

On Pi the orchestrator keeps its state in the ledger file, runs reviewers one at a time through the command above, and keeps the iteration cap of [review-rubric.md](review-rubric.md). Model and reasoning effort stay out of the role prompts: they belong to the `model` and `effort` keys of a task (and the header defaults per role), or to the harness configuration.

## Models

Pi uses the same Copilot model ids as Copilot CLI, for example `claude-opus-5.5`.

A model is a preference, not a requirement. Follow these steps:

1. Pick the model for the task. The default for Claude is `claude-opus-5.5` and `claude-sonnet-5.5`. Never use `claude-opus-5` or `claude-sonnet-5`: the 5.5 models were measured by Diego as faster and cheaper than 5.
2. For every family (Claude, GPT, Gemini, others), use the newest version when it is at least as good as the older one on quality, cost and speed. Never use the older one in that case.
3. Check the id against the models the harness lists. In Copilot CLI, the list is in the description of the `model` parameter of the agent tool. Do not assume an id that is not listed.
4. If the model is not listed or not available, use a comparable model, even from another family. Never use a weaker model for a hard task.
5. Write each substitution in one line in the ledger decision log: the wanted model, the model used and the reason.
6. The `contrarian` role uses a model of another family than the author, or a fresh context.

The ids the harness listed when this was written (the models change often, so check the list before each use):

| family        | newest id           | status                                                        |
| ------------- | ------------------- | ------------------------------------------------------------- |
| Claude Opus   | `claude-opus-5.5`   | measured, default for hard tasks                              |
| Claude Sonnet | `claude-sonnet-5.5` | measured, default for routine tasks                           |
| Claude Haiku  | `claude-haiku-5.5`  | listed, not measured                                          |
| GPT           | `gpt-6.1-sol`       | assumed to be at least as good as the older ids, not measured |
| Gemini        | `gemini-4-argon`    | assumed to be at least as good as the older ids, not measured |

The GPT line also lists `gpt-6-astra`, `gpt-6-luna`, `gpt-6-sol`, `gpt-5.6-*` and `gpt-5.3-codex`, and the Gemini line also lists `gemini-3.8-flash`. It is `unverified` whether the newest id of these families is the best choice for a given task: the suffix may be a tier, not a version. The harness also lists `kimi-k3`, `mai-code-1.1-flash` and `hydrafusion`, which are not compared here.

A script must not reject a model id that is newer than this table. Keep the table short and update it when the harness list changes.

## Native capabilities

Use the native feature of the harness when it meets the contract of this skill: a ledger the orchestrator can resume from, evidence for every verdict, and the consent rules. Deviate when it does not, write the reason in one line in the ledger decision log, and prefer the native feature again once the gap is closed. A native feature that is a user-facing mode (a slash command) is recommended to the user, not assumed callable.

| capability                | Copilot CLI                                                                         | Pi                                                        |
| ------------------------- | ----------------------------------------------------------------------------------- | --------------------------------------------------------- |
| parallel agents           | the agent tool in background mode. `/fleet` is a user-facing mode for one prompt    | start another `pi` through the shell, no native subagents |
| plan mode                 | `/plan`, toggled by the user. Its write gate blocks the ledger writes               | `unverified`                                              |
| code and design review    | `correctness` role in a `general-purpose` agent, built-in `code-review` as fallback | `correctness` role through the command above              |
| long scripted run         | `run_dynamic_workflow`, only when the user asks                                     | none known, `unverified`                                  |
| worktrees                 | not the built-in `/worktree`: it runs no setup hooks. Use `wt` (using-worktrunk)    | `wt`                                                      |
| past sessions and history | `session_store_sql`                                                                 | `unverified`                                              |

The worktree row is a deliberate deviation: the built-in feature misses the setup hooks of the repository.

## Agent types and native plan mode

Recommend native user-facing modes when they improve the workflow, but call only tools exposed to the active agent, and record the reason when you deviate from a native feature. Interactive slash commands such as `/plan`, `/fleet`, `/worktree`, `/review` and `/rubber-duck` are user-facing controls, not assumed callable tools: spawn through the available agent tool and select an agent type using [task-brief.md](task-brief.md). Prefer configured model defaults; override model or reasoning effort only when the user or active instructions require it. Persist the full brief and a pending agent-registry row before launch, then record every returned agent ID immediately. Choose `security-review` first for explicit vulnerability reviews, the `correctness` role of `~/.copilot/agents/correctness-reviewer.agent.md` for substantial diff review (the built-in `code-review` agent is the fallback), `rubber-duck` for a cheap, independent second review or design challenge, and the other agent types per [task-brief.md](task-brief.md).

Native plan mode is a manual toggle, not part of a shorthand. A shorthand injects a turn and cannot switch the harness into plan mode, and plan mode's write gate would block the ledger writes anyway (Copilot CLI: the gate is active while the session mode is `plan` and it propagates to subagents). So enter native plan mode yourself (`/plan`) for the read-only decomposition and interface design, leave it, then run `decompose` and `next` in normal mode to persist and delegate. When `decompose` needs deep upfront thinking and plan mode is not active, recommend that the user enable it first rather than assuming it is on.
