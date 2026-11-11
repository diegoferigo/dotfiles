---
name: coordinating-sessions
description: >-
  Use for any coding, debugging or exploration session beyond a quick question
  that touches several files or steps, without the ledger of
  orchestrating-agents. The main agent plans and edits, a fast model does the
  mechanical reads and commands, and a stronger advisor is consulted before a
  non-trivial plan locks, after the same error twice and before declaring done.
  For many tasks, parallel writers, worktrees or crash recovery use
  orchestrating-agents.
---

# Coordinating sessions

The default for sessions that are more than a quick question and less than a pipeline: cheap models for routine turns, the strongest model only for the big calls, and no ledger. The main agent stays the single writer and the decision maker.

## Contents

- [Roles](#roles)
- [Delegate mechanical work to the fast model](#delegate-mechanical-work-to-the-fast-model)
- [Delegate a bounded task](#delegate-a-bounded-task)
- [Consult the advisor](#consult-the-advisor)
- [When to switch to orchestrating-agents](#when-to-switch-to-orchestrating-agents)

## Roles

| role       | does                                                                 | Copilot CLI                                      | Claude Code                       |
| ---------- | -------------------------------------------------------------------- | ------------------------------------------------ | --------------------------------- |
| main agent | talks to the user, plans, edits code, decides                        | session model                                    | session model                     |
| fast model | mechanical work only: read, search, run a command, report            | `explore` and `task` agents                      | Explore / Bash subagents          |
| advisor    | independent judgment on the big calls, read-only                     | `advisor` agent                                  | `--advisor` / `/advisor`          |
| strong     | a bounded task that needs judgment, delegated to the strongest model | `general-purpose` agent with the strongest model | a subagent on the strongest model |

Models are configured per tool, not here: Copilot in `~/.copilot/settings.json` (`subagents.agents`), Claude Code with `advisorModel`.

## Delegate mechanical work to the fast model

The fast model is an executor, not a thinker. Give it a task with no decision in it and an exact thing to report:

- Exploration that would take more than about five reads or searches: ask for paths, symbols and short quotes. Launch independent searches in parallel.
- Build, test or lint runs: ask for the command, the result and the first error.
- Mechanical lookups and summaries: grep, listing, reading a long log.

Never give it an ambiguous task, a design choice, a diagnosis or an edit that needs judgment. The fast model reports, the main agent decides. Verify a claim that drives an edit by reading the file. A known file, a single edit or a quick question: do it directly.

## Delegate a bounded task

Delegate an edit only when it is small, has a clear interface and does not overlap the files you are changing. Keep at most two or three in flight, each on a different set of files. The main agent does the routine ones itself or hands them to a subagent on the session model. Use the strong model for a bounded task that needs deep judgment, such as a hard debugging hypothesis, a subtle algorithm or a risky refactor of one module.

- Give a self-contained brief: goal, files it may touch, acceptance check, and the constraints from the active repo and persona skills. Subagents do not inherit loaded skills.
- Review each result by evidence before you accept it: read the diff and the command output, not the summary.
- Track the tasks in the session task list the tool provides. A file or a note is not needed.
- You commit and integrate. Run the full check yourself after the last task returns.

## Consult the advisor

1. **Before a non-trivial plan locks**, after exploring and before the first edit.
2. **After the same error twice**: it must not be a third variation of the same attempt.
3. **Before declaring the work done**, with the evidence of the checks that ran.

Skip it for a trivial change, a question or a one-line fix.

The advisor may not see the conversation, so pass a self-contained brief: the goal, the plan or diff, what was tried, the evidence (command and first error, or the check summary) and one question. Do not ask it to implement. It is read-only by design: it may run commands only to read state or to check a claim (`git diff`, tests, linters, searches).

Apply the answer: adopt findings that prevent a real problem, set aside the ones that add complexity without benefit, and say which in one line. Consulting is best effort: the instructions are followed by the main agent, not enforced. If the advisor says stop, stop and report to the user. When your own evidence contradicts a finding, say so instead of following it blindly.

## When to switch to orchestrating-agents

Load `orchestrating-agents` instead when any of these holds: more than about four tasks, parallel writers that need their own worktrees, a stack of pull requests, a review or CI loop that must survive a crash, or a plan you want written to a ledger.
