---
name: working-with-advisor
description: >-
  Runs a normal coding or exploration session with a split of roles: the main
  agent writes the code, a fast model explores and runs commands, and a stronger
  advisor is consulted at three moments (before a non-trivial plan locks, after
  the same error twice, before declaring done). Use whenever a session involves
  non-trivial coding, debugging or repo exploration and no larger decomposition
  is needed. For splitting a feature into tasks use orchestrating-agents.
---

# Working with an advisor

Cheap models for routine turns, the strongest model only for the big calls. This is the default for ordinary sessions, not an orchestration: for a feature split into many tasks use `orchestrating-agents`.

## Roles

| role         | does                                                    | Copilot CLI     | Claude Code              |
| ------------ | ------------------------------------------------------- | --------------- | ------------------------ |
| main agent   | talks to the user, edits code, decides                  | session model   | session model            |
| fast explore | reads many files, maps the repo, finds call sites, docs | `explore` agent | an Explore subagent      |
| fast runner  | runs builds, tests and linters, reports the result      | `task` agent    | a Bash-capable subagent  |
| advisor      | independent judgment on the big calls, read-only        | `advisor` agent | `--advisor` / `/advisor` |

Models are configured per tool, not here: Copilot in `~/.copilot/settings.json` (`subagents.agents`), Claude Code with `advisorModel`.

## When to delegate to the fast model

- Exploration that would take more than about five reads or searches: delegate it, run independent searches in parallel and ask for paths, symbols and short quotes.
- Long build, test or lint output: delegate it and ask for the command, the result and the first error.
- Small tasks, a known file or a single edit: do it directly.

The fast model reports, the main agent decides. Verify a claim that drives an edit by reading the file.

## When to consult the advisor

1. **Before a non-trivial plan locks**, after exploring and before the first edit.
2. **After the same error twice**: it must not be a third variation of the same attempt.
3. **Before declaring the work done**, with the evidence of the checks that ran.

Skip it for a trivial change, a question or a one-line fix.

## How to consult

Pass a self-contained brief, because the advisor may not see the conversation: the goal, the plan or diff, what was tried, the evidence (command and first error, or the check summary) and one question. Do not ask it to implement.

Apply the answer: adopt findings that prevent a real problem, set aside the ones that add complexity without benefit, and say which in one line. If the advisor says stop, stop and report to the user.
