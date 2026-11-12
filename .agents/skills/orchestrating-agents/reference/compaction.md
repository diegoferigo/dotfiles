# Surviving context compaction

The orchestrator is a long-lived chat, and its context is the first limit it reaches. A real run failed there: after a long session, new subagent launches were refused with "prompt budget 200858 > 200000". The model was fine, the session context was full. This file says how to lose as little as possible when the context is compacted, or fails to fit, and how to re-enter.

## Contents

- [Principles](#principles)
- [What is persisted before a launch](#what-is-persisted-before-a-launch)
- [Standing constraints file](#standing-constraints-file)
- [Keeping the context small](#keeping-the-context-small)
- [Compacting on purpose](#compacting-on-purpose)
- [Idle writers](#idle-writers)
- [Re-entry flow](#re-entry-flow)
- [The re-entry digest](#the-re-entry-digest)

## Principles

1. **The ledger is the source of truth, not the chat.** After a compaction the orchestrator rebuilds its state from `tasks.toml`, the briefs, the inbox and the agent registry alone. Anything that lives only in the conversation is lost, so write it down when it happens ([ledger.md](ledger.md#update-cadence)).
2. **Persist before you act.** Write the state that a launch or a decision needs before the launch or the decision, never after.
3. **Keep the chat small.** The chat holds the digest and what a human needs to follow. The detail lives in files.
4. **Compact when you choose to**, at a clean point, not when launches start to fail.

In this file, compaction is the compaction of the conversation context. The archiving of a large ledger file is a different thing, described in [ledger.md](ledger.md#compaction-for-multi-day-runs).

## What is persisted before a launch

Before an agent starts, these are on disk:

| What                    | Where                                                                                                                                                        |
| ----------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| the complete brief      | the ledger section "Task briefs", or the file `briefs/<task-id>.md` next to `tasks.toml`; this location is a convention of this document, no script reads it |
| the worktree and branch | `worktree` and `branch` of the task in `tasks.toml`                                                                                                          |
| the agent id            | the row of the agent registry in the ledger, written as `pending` before the launch and replaced by the real id right after                                  |
| tier, effort, write set | the same registry row                                                                                                                                        |

The agent id is the one thing that exists only after the launch, so write it in the same turn the harness returns it. A launch whose id was never written is an orphan after a compaction: find it from the worktree ([SKILL.md](../SKILL.md#recover-active-work)).

Every arbitration verdict and every decision is written to the ledger with its reason, in the turn it is taken: the verdict, the finding ids it covers, the evidence it rests on and the alternative it rejected. A verdict that is only in the chat is lost at the next compaction and gets arbitrated a second time, possibly with another outcome.

## Standing constraints file

The rules that must hold for the whole run live in one file next to the ledger, `constraints.md`, and not in a summary of the chat. A summary drops them silently. The header of the ledger names the file:

```markdown
Constraints: constraints.md
```

The file lists, one per line, with the date and who gave the rule:

- the user's rules for this run (for example what may be pushed, what needs consent, the language of the artifacts);
- the tier and effort policy and the caps (writers, read-only agents, budget);
- the no-push, no-merge and draft-only rules in force;
- any rule that came from a correction by the user;
- the standing rule to never wait for the human to ask about status or parallel work: after every agent return, and before every final reply of a turn, run `next` with the caps and start what it prints, or say in one line why it started nothing, and end every closing reply with a `Parallel:` line copied from that `next` output (the started ids, or the skip reason).

Add a line the moment the user states a rule, and when a rule ends, strike it with the date instead of deleting it. After a compaction, read this file before anything else that acts.

## Keeping the context small

- **Subagents return short, structured results.** Use the return contract of [task-brief.md](task-brief.md#structured-return-contract) and ask for a summary, not a transcript.
- **Long output goes to files.** Test logs, diffs, review findings and research notes are written to a file in the scratch directory of the ledger, and the return carries the path and a few lines. The orchestrator opens the file only when it needs the detail.
- **The chat gets only the digest.** After a return, print what [SKILL.md](../SKILL.md#keeping-the-human-informed) asks for, shortened. Do not paste whole outputs into the chat or into a brief: point to the file.
- **Briefs reference, they do not copy.** A brief names the files and the ledger sections it needs instead of embedding them.

## Compacting on purpose

Waiting for a failed launch is the worst moment: the launch cannot carry its brief, and the state may be half written. Watch the size of the context and compact early, at a clean point.

- **When.** Use the context size the harness shows, when it shows one, and compact well before the limit. Without a figure, take the plan recap that SKILL.md already asks for after about every 5 returns as the moment to check. A long run that has produced many returns, or reads large files into the chat, is the case to watch.
- **A clean point** is one where nothing is half done:
  - no arbitration is in progress: every returned result has its verdict in the ledger;
  - every launched agent has its id in the registry, and no row is `pending` for a launch that happened;
  - `tasks.toml` statuses, the registry and "Next actions" match what is true now;
  - no `prepared` or `running` operation is open in the operation journal.
- **How.** Bring the ledger to that state, then compact with a note that names the ledger path, the `tasks.toml` path and the constraints file. When the harness has no manual compaction, end the session and start a new one: the re-entry flow below is the same.
- **If launches already fail** with a prompt-budget error, do not retry the same launch. Write the missing state to the ledger, compact or restart, and re-enter.

## Idle writers

A writer that returned and may need a follow-up (a `request-changes` round) is still useful, because it keeps the context of its task. Track it by agent id in the registry with the status `idle`, next to its worktree, so that `write_agent` can steer it after a compaction. If the harness no longer knows the id, mark the row `stale`, take the orphan route of [ledger.md](ledger.md#crash-safe-state-transitions) and respawn from the persisted brief.

## Re-entry flow

Run this after a compaction, in a new session, or whenever the context feels degraded:

1. Run `scripts/ledger_graph.py <tasks.toml> resume`. It prints the re-entry digest below: the running tasks with their agent id, worktree and branch, what can start, what is blocked, the last decisions and the first next action.
2. Read the constraints file named in the ledger header.
3. Read "Next actions" and the last decisions of the ledger, and the questions in `inbox/`.
4. Reconcile every running task and every non-terminal registry row, as in [Recover active work](../SKILL.md#recover-active-work): the worktree, its status and commits, and the agent id.
5. Print the plan recap to the human, then continue with `scripts/ledger_graph.py <tasks.toml> next --rw N --ro N --budget N --caps-note "<reason>"`.

The full protocol with the checks against git and the remote is [Bootstrap-from-disk](ledger.md#bootstrap-from-disk-protocol).

## The re-entry digest

`resume` prints this digest, as text and not JSON, for a person entering the session. The JSON of `next` is still there for scripts. The ledger is the `ORCHESTRATION.md` next to `tasks.toml`; without it, `resume` still prints the graph and marks the ledger as missing. A missing constraints file is flagged `MISSING, ask the human`, an undecodable ledger is marked `(unreadable)`, and reading the tasks from stdin has no ledger, so the digest says so. Blockers, questions and `waits on` lists are cut to 100 characters. A registry row that is `pending`, `stale` or has no id prints `agent=?`. A decision is printed as written in its bullet, cut to 100 characters.

The digest is plain text, short, in this order, one task per line:

```text
RESUME <slug>  tasks=<path>  ledger=<path>
CONSTRAINTS <path of the constraints file>  (read it before acting)
READY (<n>)
  <id>  <priority>  unblocks <n>  <worktree>
RUNNING (<n>)
  <id>  agent=<agent id>  <worktree>  <branch>
BLOCKED (<n>)
  <id>  human: <question>
  <id>  blocked: <blocker>
  <id>  waits <dep id>[, <dep id>] (dep)
DECISIONS (last <n>)
  <date>  <task id>  <verdict>: <reason>
NEXT <the first action of "Next actions">
```

Where each part comes from:

| Part        | Source                                                                                                                                                                                                                                                                                                     |
| ----------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| READY       | `next.start`, the leaves that can start now under the caps of the header                                                                                                                                                                                                                                   |
| RUNNING     | `next.running` for the ids, worktrees and branches, and the agent registry of the ledger for the agent ids                                                                                                                                                                                                 |
| BLOCKED     | `next.awaiting_human` for the questions (an entry with `review` and no `question` is a task marked for human review, not a blocker), the ids of `next.blocked` that are not in `awaiting_human` with the `blocker` of the task in `tasks.toml`, and `next.waiting` for the tasks that wait on a dependency |
| DECISIONS   | the last entries of "Decisions & rationale" of the ledger                                                                                                                                                                                                                                                  |
| CONSTRAINTS | the `Constraints:` line of the ledger header                                                                                                                                                                                                                                                               |
| NEXT        | the first line of "Next actions"                                                                                                                                                                                                                                                                           |

A running task with no agent id in the registry is printed with `agent=?`: that is the orphan case to reconcile first. The digest never replaces the reading of the ledger, it says what to open.
