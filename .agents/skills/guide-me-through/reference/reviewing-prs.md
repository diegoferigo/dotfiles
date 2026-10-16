# Understanding a PR or a stack

## Gather

```bash
gh pr view <n> --json title,body,baseRefName,headRefName,files,closingIssuesReferences
gh pr diff <n>
gh pr checks <n>
```

Read the description and linked issues first. Branch names can be stale or missing locally (and a fork head never exists): run `git fetch origin <baseRefName>` and `git fetch origin pull/<n>/head`, then use `origin/<baseRefName>` and `FETCH_HEAD` (or the PR's `headRefOid`). For the "before" view prefer `git show origin/<base>:<path>` or `gh pr diff` over a checkout; use a temporary worktree (`using-worktrunk` skill) only when the code must be run or navigated, never by switching the user's branch.

### Stack detection

Given `PR n`, find out whether it is one layer of a stack before zooming out:

- Down: check whether `baseRefName` is the head branch of another open PR (`gh pr list --head <baseRefName>`), or whether `gh stack view` shows the branch.
- Up: `gh pr list --base <headRefName> --json number,headRefName` finds the PRs stacked on this one.
- Enumerate all layers by repeating both until trunk and the top. Zoom out on `git diff origin/<bottom-base>...<top-head>` (fetch the heads first).
- If unsure whether the user means the whole stack or one layer, ask once.

### Large PRs

Do not run `gh pr diff` on a huge PR. Start with the file list and line counts: `gh pr view <n> --json files` (capped at about 100 files, unverified), and when it returns 100 entries use `gh api repos/{owner}/{repo}/pulls/<n>/files --paginate` or `git diff --stat origin/<base>...<head>`. Then:

- Classify by path and list without reading: lockfiles, generated code (`*_pb2.py`, `generated/`), vendored code, snapshots, pure renames.
- Read the core files fully; summarise the rest by group.
- Above roughly 1000 changed lines, delegate per-directory mapping to read-only `explore` subagents.
- If the description does not match the code (draft, WIP, force-pushed stack), say so and trust the code.

## Pre-PR context

The main focus. On the base branch, for each touched area:

- What it does today, in one paragraph, from the code (not from the PR text).
- Who calls it (grep callers) and what it calls.
- Which tests cover it and what they assume.
- Why it exists: `git log -- <path>`, `git blame` on the touched lines, linked issues.

## The PR in context

- State what the PR changes in one sentence, then which part is the core and which is mechanical (renames, generated files, formatting).
- Read the core change first, then the rest. Tests in the diff are the spec for the new behavior.
- Say what the PR leaves unchanged that a reader might expect to change.
- Report understanding, not a verdict. List doubts under "open questions"; for review findings, hand off (see `SKILL.md`, Not for).

## Stacks

- Zoom out (pre-PR context, what the feature does): treat the whole stack as one change. The stack boundaries do not matter here; use the base of the bottom layer and the head of the top layer.
- Then go layer by layer, bottom (closest to trunk) first. Each layer's diff is relative to the layer below. For each layer: what it adds, what it assumes from the layers below, what the layers above rely on.
- Before calling something missing or wrong in a layer, check whether a later layer changes it.
- Final recap: explain how the feature was split into layers and why (one line per layer, plus the dependency between layers), so the user can plan a practical review order.
- Navigation with `gh stack up|down|top|bottom`: see the `gh-stack` skill.

## Caveats

- GitHub's docs only say each layer shows its own diff. "Review bottom-up" is this skill's convention, not a documented rule.
- A diff hides unchanged code that interacts with the change: open the surrounding file, not only the hunk.
