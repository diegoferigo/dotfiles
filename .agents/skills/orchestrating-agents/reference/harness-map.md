# Harness map

How the orchestrator uses the native features of a harness, and how each reviewer role of [../reviewers/](../reviewers/) is run on a given harness. The role prompts are plain Markdown and the findings format is plain text, so a harness only needs a way to start a read-only agent with a role prompt and the diff. The Pi command was run once with Pi 1.3.0 (see Pi). What was not checked is marked `unverified`.

## Roles and mechanisms

| role            | Copilot CLI                                                                                       | Pi                                                         | Other harnesses                                |
| --------------- | ------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- | ---------------------------------------------- |
| `correctness`   | `general-purpose` agent with the role prompt in the brief. Fallback: built-in `code-review` agent | role prompt through the command below                      | role prompt as the body of a custom agent file |
| `critic`        | `rubber-duck` agent with the role prompt in the brief                                             | role prompt through the command below                      | as above                                       |
| `contrarian`    | `general-purpose` or `rubber-duck` agent with the role prompt in the brief                        | role prompt through the command below                      | as above                                       |
| `simplifier`    | `general-purpose` agent with the role prompt in the brief                                         | role prompt through the command below                      | as above                                       |
| `perfectionist` | `general-purpose` agent with the role prompt in the brief                                         | role prompt through the command below                      | as above                                       |
| security        | built-in `security-review` agent. No neutral role                                                 | no neutral role, `unverified` whether Pi has an equivalent | not covered                                    |

The `correctness` role is the default because a built-in agent keeps its own system prompt, which cannot be replaced: the brief can add the findings format and the role rules, but not remove what the built-in forbids (style, naming and documentation findings, for example). The orchestrator may fall back to the built-in `code-review` agent when a quick review is enough and the format and the zoom out are not needed, or when the user asks for it. The built-in agents of Copilot CLI are defined in `definitions/*.agent.yaml` inside the installed CLI package. Their prompts are not copied here: the roles in this directory are written separately and add the findings format and the severity mapping of this skill.

## Pi

Pi has no native subagents. The recommended way is to start another `pi` process through the shell, in print mode with a read-only tool set and the role prompt appended to the system prompt. The flags are documented in the [Pi CLI reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md). The command below was run once with Pi 1.3.0, on the contrarian role and a small diff, and it returned findings in the format of `findings.md`. The other roles are `unverified` on Pi.

```sh
git diff <base>...HEAD | pi --print --tools read,grep,find,ls --append-system-prompt ~/.agents/skills/orchestrating-agents/reviewers/<role>.md "Review this diff and return findings in the format of ~/.agents/skills/orchestrating-agents/reviewers/findings.md."
```

`--tools read,grep,find,ls` leaves out the tools that edit files and run commands, so a reviewer started this way cannot run a build or a test. The orchestrator runs the checks a role needs (the `correctness` role needs a build and the tests) and passes their output in the prompt. MCP tools are not removed by `--tools` unless an entry starts with `mcp__` (stated in the CLI reference linked above, not tested here).

Pi discovers `AGENTS.md` and `CLAUDE.md` ([CLI reference](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/cli.md)) and skills ([skills doc](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/docs/skills.md)).

### Subagents on Pi

Run the subagent from the worktree of its task: Pi reads `AGENTS.md` from the working directory, checked on Pi 1.3.0 with two directories, one with an `AGENTS.md` (the agent returned its secret word) and one without (it returned `NONE`). Pass the brief as a file and ask for the result in a file under the ledger directory, because the processes share no conversation. `--no-session` makes a reviewer ephemeral; `--session-dir`, `--session-id`, `--session` and `--fork` select where a session is stored. Pi lists the global skills of `~/.agents/skills` (checked with `--print`), but a skill under a project `.agents/skills` in a fresh git repository was not listed, so project skills are `unverified`.

Herdr panels for the subagents are a Pi-only option: the orchestrator opens a pane per subagent in its worktree and reads the result file. Copilot CLI does not use herdr here.

## Capabilities with no equivalent outside Copilot CLI

These are not required by the role prompts. The orchestrating skill uses them when they exist and falls back to the manual ledger when they do not:

- dynamic workflows (`run_dynamic_workflow`);
- the session SQL database for todos;
- background agents that are addressed by id (`write_agent`, `read_agent`);
- the automatic choice of a contrasting model for the `rubber-duck` agent.

On Pi the orchestrator keeps its state in the ledger file, runs reviewers one at a time through the command above, and keeps the iteration cap of [review-rubric.md](review-rubric.md). Model and reasoning effort stay out of the role prompts: they belong to the harness configuration.

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
