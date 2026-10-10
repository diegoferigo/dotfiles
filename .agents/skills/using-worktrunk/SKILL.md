---
name: using-worktrunk
description: Creates, finds and removes git worktrees with worktrunk (`wt`) instead of `git worktree add`, the built-in worktree tools or ad-hoc directories. Use whenever the task needs a separate checkout or a worktree, for example to work on another branch in parallel, to isolate an experiment, to review a PR or to run a subagent on its own branch, even if the user does not say worktrunk.
---

# Using worktrunk for git worktrees

Worktrunk (`wt`) creates every worktree in `<repo>/.worktrees/<branch>` and runs the configured setup hooks (for control_suite, the gitignored files a build needs). `git worktree add` and the built-in worktree commands skip those hooks, so use `wt` for any new worktree.

Create a worktree only when the task needs one. Do not create one for a change that fits the current checkout.

## Create

```bash
wt switch --create <branch> --no-cd                  # new branch from the default branch
wt switch --create <branch> --base <base> --no-cd
wt switch pr:<number> --no-cd                        # a pull request branch
```

- Pass `--no-cd`: an agent shell has no `wt` shell function, so the shell would not follow anyway. The command prints the worktree path; run later commands with `cd <path>` or `git -C <path>`.
- Use the usual branch prefix of the repository (for example `diegoferigo/<topic>`). Slashes become `-` in the directory name.
- Do not pass `--no-hooks`: the hooks are the point. If a hook fails, report the error instead of working around it.

## Find and reuse

```bash
wt list                       # worktrees, branches and status
wt list --format json         # same, for parsing
wt switch <branch> --no-cd    # existing worktree, prints its path
```

Check `wt list` before creating one and reuse the existing worktree of a branch.

## Remove

```bash
wt remove <branch> --yes
```

Remove only worktrees you created in this task, and only when the user asked or the work is merged. Never add `--force` (removes uncommitted changes) or `-D` (deletes an unmerged branch): without them `wt remove` fails safely.

## Limits

- `wt` is not installed everywhere (`command -v wt`). If it is missing, tell the user and ask before falling back to `git worktree add <repo>/.worktrees/<branch>`.
- A worktree created by Copilot CLI itself (`/worktree`, `/fork worktree`, `/move`, `--worktree`) lands in the same directory but runs no worktrunk hooks. Prefer `wt` so the setup runs.
