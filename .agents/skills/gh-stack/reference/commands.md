<!--
UPSTREAM MIRROR, github/gh-stack : skills/gh-stack/references/commands.md
Pinned at commit 14fc42ed9b6c376a53b2f999f138d3bd26dac546 (skill v0.1.0, synced 2026-08-25).
Everything between upstream:begin/upstream:end is a faithful copy; to sync, replace
that block wholesale from upstream. Put local additions only under "Local notes".
-->

> **This file = upstream copy + local notes.** Everything between the `upstream:begin`/`upstream:end` markers is verbatim from upstream; local additions live under [Local notes](#local-notes) at the end.

<!-- upstream:begin -->

# Command behavior

`gh stack <command> --help` is authoritative for flags and arguments. (`gh stack help <command>` only prints the top-level help.) This file only covers behavior `--help` does not explain: preconditions, side effects, atomicity, and failure modes.

## Contents

- [init](#init)
- [add](#add)
- [push](#push)
- [submit](#submit)
- [link](#link)
- [sync](#sync)
- [rebase](#rebase)
- [view](#view)
- [checkout](#checkout)
- [unstack](#unstack)
- [merge](#merge)
- [Navigation](#navigation)

## init

Creates the stack and checks out the **last** branch in the list, so a single `init` can lay down the whole chain: `gh stack init auth api frontend`.

`init` processes branch arguments from bottom to top. Existing branches are adopted. If the first branch does not exist, it is created from the trunk; each later new branch is created from the branch immediately before it. There is no separate adopt mode — existence decides. `--base` selects a non-default trunk.

`init` also enables `git rerere`. Under a TTY the first run in a repo asks for confirmation; set `git config rerere.enabled true` beforehand to skip it.

## add

- **Must run from the top branch** of the stack (or the trunk when the stack is still empty). Anywhere else it exits **5** with `can only add branches on top of the stack`. Run `gh stack top` first.
- **Uncommitted changes carry over.** Without `-Am`, `add` does not touch the working tree, so staged and unstaged changes follow you onto the new branch. Commit or stash first for a clean start.
- **`add -Am` commits in place when the current branch has no commits yet** — for example immediately after `init` — instead of creating a branch. This is deliberate: the first layer usually needs its content before a second layer exists.
- `-A` and `-u` are mutually exclusive, and both require `-m`.

## push

Pushes every active (non-merged, non-queued) branch in one multi-ref push with per-branch `--force-with-lease`.

**Not atomic.** Some branches may update while another is rejected. A rejection means that branch moved on the remote; fix that branch and rerun — rerunning is safe and skips what already landed.

`push` never creates or updates pull requests. Use `submit` for that.

## submit

Pushes each active branch, then creates a PR for every branch that lacks one, basing it on the first non-merged ancestor, then links them into a Stack on GitHub.

- **Not atomic.** Branches are pushed sequentially with per-branch `--force-with-lease`. If a later push is rejected, earlier pushes and PR updates stand. Fix the rejection and rerun the same command.
- **A fully merged stack cannot be extended.** When every PR in the current stack is already merged, `submit` forks the remaining unmerged branches into a **new** stack rooted at the trunk and creates it on GitHub, leaving the merged stack untouched.
- **Title generation with `--auto`:** a branch with a single commit uses that commit's subject as the title and its body as the PR body. A branch with multiple commits humanizes the branch name (hyphens and underscores become spaces). There is no flag for a custom title or body; use `gh pr edit` afterwards.
- `--open` marks new *and existing* PRs ready for review; without it new PRs are drafts.
- Requires stacked PRs to be enabled on the repository. If not, `submit` exits **9** when non-interactive (under a TTY it offers to create ordinary unstacked PRs instead).

## link

Creates or updates a stack on GitHub **without any local tracking state**. This is the path for branches managed by another tool or living in another worktree — see `troubleshooting.md`.

- Arguments are given bottom to top. Each is a branch name or a PR number; a numeric argument is tried as a PR number first and falls back to a branch name.
- **A numeric first argument is treated as a stack number only when a stack with that number exists.** In that case the remaining arguments are appended to the top of that stack and you do not re-list its current PRs: `gh stack link 7 feature-c`. Arguments already in the stack are skipped; arguments belonging to a different stack are rejected.
- Branch arguments are pushed automatically (non-force, atomic). Missing PRs are created with auto-generated titles and correctly chained bases; existing PRs with a wrong base are corrected.
- Stack membership is **additive only** — `link` never removes a PR from a stack.

## sync

The routine command. Steps, in order:

1. **Fetch** from the remote.
2. **Reconcile with the GitHub stack.** PRs added to the stack on github.com are pulled down and appended locally. On divergence, aborts when non-interactive (see `troubleshooting.md`).
3. **Fast-forward the trunk.** Skipped when already current; warns when diverged.
4. **Cascade rebase when needed.** This runs if the trunk moved, a stack branch was fast-forwarded from its remote, or a branch no longer contains its expected parent. Merged PRs are handled automatically. On conflict, **all branches are restored** to their pre-rebase state and the command exits **3**.
5. **Push** all active branches, atomically.
6. **Refresh PR state** from GitHub.
7. **Sync the stack object** — link open PRs into a stack, additively. Only when two or more PRs exist. `sync` never opens PRs; that is `submit`.
8. **Prune** local branches for merged PRs, only when `--prune` is passed in a non-interactive environment.

## rebase

Pulls from the remote and cascade-rebases. Use it when `sync` reported a conflict or when you need to rebase only part of the stack.

- `--upstack` rebases from the current branch to the top. This is what you run after editing a lower layer.
- `--downstack` rebases from the trunk to the current branch.
- `--no-trunk` skips fetching and the trunk rebase entirely, aligning stack branches with each other only.
- `--continue` after staging resolutions; `--abort` restores every branch.
- A merged PR is detected automatically and replayed with `--onto` against the correct target, so a squash-merged parent does not produce spurious conflicts.
- Starting a rebase while one is in progress exits **7**.

## view

- `--json` writes the machine-readable payload to stdout. Its schema is in [Reading state with `view --json`](#reading-state-with-view---json).
- Bare `view` opens a full-screen TUI when stdout is a TTY, and prints static text when piped.
- `--short` prints a compact one-line-per-branch summary and never opens the TUI, but it is formatted for humans; parse `--json` instead.
- `view` refreshes PR state from GitHub as a side effect, best-effort — it does not fail when the API is unreachable.

## checkout

Accepts a stack number, PR number, PR URL, or branch name.

- A bare number resolves as a **stack number first**, then a PR number, then a branch name.
- Stack numbers, PR numbers, and PR URLs fetch from GitHub, pull the branches down, and set the stack up locally.
- A **branch name resolves against locally tracked stacks only** and never contacts GitHub. Use a stack or PR number to pull a stack that is not tracked locally.
- If a local stack already exists over those branches with a different composition, `checkout` cannot be forced past it. Run `gh stack unstack --local` first, then retry.
- `checkout` has no flags. It relies on `remote.pushDefault` when several remotes exist.

## unstack

Removes the stack **grouping** only. It never deletes pull requests or branches.

- With no argument it targets the active stack — the one containing the current branch — removing it on GitHub and locally.
- With a stack number it works from anywhere in the repository, tracked locally or not, via the API. Local tracking is also removed when present.
- `--local` removes local tracking only and never contacts GitHub. Combining `--local` with a stack number that is not tracked locally is an error.
- An unknown stack number exits **2**.

## merge

- Scope with an argument: pass a PR number to merge that PR and every unmerged PR below it in the stack, or pass a stack number to merge every unmerged PR in that stack.
- **All-or-nothing.** If any PR in that exact merge set cannot be merged, none are, and the reason is reported.
- The method comes from `--squash`, `--rebase`, `--merge`, or `--merge-method <method>`. Without one, the last-used method is reused.
- **The method applies per PR, not to the stack as a whole.** `--squash` squashes each merged PR/layer into its own commit as it lands bottom-up, so a 4-layer stack becomes 4 commits on the base branch. There is no mode that collapses the whole stack into a single combined commit. To get one commit for everything, collapse the stack first: retarget the top PR onto trunk (`gh pr edit <top> --base <trunk>`), squash-merge only it, then close the lower PRs. The web UI "Squash and merge stack" button is the same per-PR squash, not a whole-stack squash.
- Only basic PR state is checked before merging: open and not a draft. Bypassing merge requirements is not supported for stacks.
- **A merge queue on the base branch overrides everything.** The stack is added to the queue rather than merged; the queue chooses the method and any method flag you passed is ignored with a warning. Queued PRs are submitted together but land as the queue processes them, so they may merge in separate groups rather than all at once.
- `gh pr merge` cannot merge a stack. Always use `gh stack merge`.

## Navigation

`up`, `down`, `top`, `bottom`, and `trunk` are always non-interactive. `up` and `down` accept a count (`gh stack up 3`). Movement clamps at the stack bounds, and merged branches are skipped when navigating from an active branch, so `bottom` lands on the lowest *unmerged* branch.

`gh stack switch` is a selection menu with no non-interactive path. Use the commands above instead.

<!-- upstream:end -->

______________________________________________________________________

## Local notes

### Local notes contents

- [Installed-version precedence](#installed-version-precedence)
- [Prerequisites and setup](#prerequisites-and-setup)
- [Non-interactive command forms](#non-interactive-command-forms)
- [Multiple remotes](#multiple-remotes)
- [Command map](#command-map)
- [Exit codes](#exit-codes)
- [Reading state with `view --json`](#reading-state-with-view---json)
- [Reading server stack membership](#reading-server-stack-membership)
- [Related procedures](#related-procedures)

### Installed-version precedence

GitHub ships an official `gh stack` skill inside the tool's own repo. The files with `upstream:begin` / `upstream:end` markers are faithful mirrors of that skill, pinned at `14fc42ed9b6c376a53b2f999f138d3bd26dac546` (skill v0.1.0, synced 2026-08-25). Local procedures extend those mirrors. When sources conflict, prefer this order:

1. The installed version's actual behavior: `gh stack --version` and `gh stack <cmd> --help`.
2. The official skill at the pinned commit.
3. Locally reproduced exceptions, labelled with the version/date observed.
4. Legacy pre-v0.1.0 workarounds.

Prefer a native command or flag over a manual git/REST workaround when the installed version provides one. When behavior changes, update the affected note, bump its validation marker, and move superseded guidance to [legacy.md](legacy.md) instead of deleting the history.

### Prerequisites and setup

```bash
gh extension list | grep gh-stack       # confirm installed (github/gh-stack)
gh extension install github/gh-stack    # install if missing
gh stack --version

git config rerere.enabled true          # remember conflict resolutions (init sets this too)
git config remote.pushDefault origin    # REQUIRED if the repo has more than one remote
```

Before relying on a local workaround, run `gh stack --version` and `gh stack <cmd> --help`. If the installed version differs from a workaround's validation marker, treat the workaround as suspect and check for a native fix.

### Non-interactive command forms

`gh stack` branches on whether **stdout is a TTY**. Piped commands usually error cleanly or print static text; under a PTY the same command can open a prompt or full-screen TUI and block. In agent or CI contexts, use the explicit form instead of relying on TTY detection:

| Always run                          | Never run bare                            | Why                                                         |
| ----------------------------------- | ----------------------------------------- | ----------------------------------------------------------- |
| `gh stack view --json`              | `gh stack view`                           | opens a TUI under a PTY                                     |
| `gh stack submit --auto`            | `gh stack submit`                         | prompts for a title per new PR                              |
| `gh stack merge <target> --yes`     | `gh stack merge <target>` / `gh pr merge` | bare `merge` can prompt; `gh pr merge` cannot merge a stack |
| `gh stack init <branch>...`         | `gh stack init`                           | prompts for branch names                                    |
| `gh stack add <branch>`             | `gh stack add`                            | prompts for a name, fails even when piped                   |
| `gh stack checkout <target>`        | `gh stack checkout`                       | opens a selection menu                                      |
| `gh stack up`/`down`/`top`/`bottom` | `gh stack switch`                         | `switch` is menu-only                                       |
| -                                   | `gh stack modify`                         | TUI-only, no non-interactive path; agents must not run it   |

`view --json` writes JSON to stdout; status goes to stderr. Parse stdout and branch on exit codes, not stderr. `view --short` is human-formatted; parse `--json`.

Create new PRs as drafts unless the user says otherwise: use `gh stack submit --auto` and do not pass `--open`. `--open` marks new *and existing* PRs ready for review, so it can change an existing PR's human-chosen state. Restore with `gh pr ready <n> --undo` (draft) / `gh pr ready <n>` (ready) if you slip.

### Multiple remotes

Set `git config remote.pushDefault origin` during setup. When the repo has more than one remote, do not run `push`, `submit`, `sync`, `rebase`, or `link` bare: pass `--remote <name>` unless `remote.pushDefault` is configured. `checkout` and `trunk` have no `--remote` flag and require the config.

### Command map

| Goal                                                | Command                                                                                                                                     |
| --------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------- |
| Start a stack (bottom branch off trunk)             | `gh stack init [--base <trunk>] <branch>`                                                                                                   |
| Create a multi-layer stack at once                  | `gh stack init <b1> <b2> <b3>` (checks out the **last**)                                                                                    |
| Adopt existing branches into a stack                | `gh stack init <b1> <b2> ...` (existence decides; adopted automatically)                                                                    |
| Add a new layer (run from `top`)                    | `gh stack add -m "msg" <branch>` (stage first, or `-A`/`-u`)                                                                                |
| View the stack                                      | `gh stack view --json` (never bare)                                                                                                         |
| Navigate                                            | `gh stack up`/`down`/`top`/`bottom`/`trunk` (all non-interactive)                                                                           |
| Restructure (drop/fold/insert/reorder/rename)       | `gh stack modify` (TUI-only, see fallbacks)                                                                                                 |
| Rebase the whole stack on latest trunk              | `gh stack rebase`                                                                                                                           |
| Push all branches                                   | `gh stack push`                                                                                                                             |
| Push + create/link PRs                              | `gh stack submit --auto`                                                                                                                    |
| Fetch/rebase/push/link in one shot                  | `gh stack sync`                                                                                                                             |
| Link already-pushed branches (no local tracking)    | `gh stack link [--base <trunk>] <b…>`                                                                                                       |
| Pull a remote stack locally                         | `gh stack checkout <n\|PR\|url>`                                                                                                            |
| Merge a stack (or up to a PR) bottom-up, atomically | `gh stack merge <stack#\|PR#> --yes [--squash\|--rebase\|--merge]` (`--squash` yields one commit per PR/layer, not one for the whole stack) |
| Remove a stack grouping (keeps PRs/branches)        | `gh stack unstack [<stack#>] [--local]`                                                                                                     |

### Exit codes

Branch on these, not on stderr text:

| Code | Meaning                        | Recovery                                                                                                                                                                              |
| ---- | ------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 0    | Success                        | - (but see the `sync` caveat below)                                                                                                                                                   |
| 1    | Generic error                  | Read stderr                                                                                                                                                                           |
| 2    | Not in a stack / unknown stack | `gh stack init`, or `gh stack checkout <target>`                                                                                                                                      |
| 3    | Rebase conflict                | Resolve, `git add`, `gh stack rebase --continue` (or `--abort`)                                                                                                                       |
| 4    | GitHub API failure             | Check `gh auth status`, retry                                                                                                                                                         |
| 5    | Invalid arguments              | Fix the invocation; see `<cmd> --help`                                                                                                                                                |
| 6    | Disambiguation required        | Branch in several stacks → `checkout` a non-shared branch or pass a stack number; or several remotes with no auto-selectable push target → set `remote.pushDefault` / pass `--remote` |
| 7    | Rebase already in progress     | `gh stack rebase --continue` or `--abort`                                                                                                                                             |
| 8    | Stack file locked              | Another process holds `.git/gh-stack.lock`; wait ~5s and retry                                                                                                                        |
| 9    | Stacked PRs unavailable        | Not enabled on the repository; tell the user                                                                                                                                          |
| 10   | Modify recovery required       | `gh stack modify --abort`                                                                                                                                                             |

Caveat: exit 0 is not proof of success for `sync`. When local and remote stacks diverge, non-interactive `sync` prints both chains, makes no changes, and exits 0 with `Sync aborted`. After any `sync`, verify with `gh stack view --json` or look for that message; recovery is in [troubleshooting.md](troubleshooting.md).

### Reading state with `view --json`

`gh stack view --json` writes this schema to stdout:

```
trunk           string
currentBranch   string
branches[]      name, head, base, isCurrent, isMerged, isQueued, needsRebase
branches[].pr   number, url, state ("OPEN" | "MERGED" | "QUEUED"); absent when no PR exists
```

`base` is the saved SHA of the parent branch this branch was last known to contain; it may lag the parent's current tip. `needsRebase` is true when the parent's current tip is no longer an ancestor of the branch.

### Reading server stack membership

Local tracking can be stale. Query the server grouping containing a PR before any `unstack`, `link`, reorder or regroup:

```bash
gh api 'repos/{owner}/{repo}/stacks?pull_request=<pr-number>'
```

Persist the raw response in the operation journal. For every returned member, also record:

```bash
gh pr view <pr-number> \
  --json number,state,baseRefName,headRefName,headRefOid,autoMergeRequest,mergeStateStatus
```

Use `gh stack view --json` to inspect `isQueued`, and stop before regrouping if any member is merged, queued or has auto-merge enabled. The server response, not the local stack number, is authoritative for grouping identity.

### Related procedures

Deeper, battle-tested procedures that build on the behavior above live in sibling files:

- Splitting an existing branch into a stack: [splitting.md](splitting.md)
- Retargeting the trunk onto another PR/branch: [trunk-retargeting.md](trunk-retargeting.md)
- Reconciling a local stack after the **remote** was rebased elsewhere: [syncing.md](syncing.md)
- Reorder / middle-insert (the auto-merge trap and non-interactive fallbacks): [reordering-and-conflicts.md](reordering-and-conflicts.md)
- Lockfiles / large generated files during rebases: [lockfiles.md](lockfiles.md)
