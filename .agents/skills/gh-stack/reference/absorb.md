# Folding fixes into lower layers with git absorb

`git absorb` places each staged hunk into the commit that last touched those lines, so one command fixes several lower layers at once. It is the faster alternative to `git commit --fixup` plus `git rebase -i --autosquash` (see [reordering-and-conflicts.md](reordering-and-conflicts.md)) when a change spans more than one layer. Validated against `gh stack` v0.1.0 and `git-absorb`.

## Contents

- [When to use it](#when-to-use-it)
- [Procedure](#procedure)
- [If a rebase stops](#if-a-rebase-stops)

## When to use it

- The current branch is a stack layer, not the trunk.
- The change belongs to commits of one or several lower layers.
- The hunks are staged (`git add -p` or `git add <file>`).
- `git absorb --version` works. If `git-absorb` is not installed, use the fixup and autosquash flow instead.

Absorb can only amend a commit that already touched the same lines. New files and brand new lines have no target commit, so they stay staged. Put them in the right layer by hand.

## Procedure

1. Take a safety branch and record the existing backups. `--update-refs` rewrites every branch that points at a rewritten commit, including `backup/*` branches, so they would silently move to the new history.

   ```bash
   git branch backup/<branch>-$(date +%Y%m%d-%H%M%S)
   git for-each-ref refs/heads/backup/ > backup-refs.txt
   ```

   Keep `backup-refs.txt` outside the repository, or delete it when done.

2. Find the base. Absorb looks at 10 commits by default, so pass the merge-base with the trunk to cover the whole stack. Read the trunk from the stack and fall back to the remote trunk when the local branch is missing.

   ```bash
   trunk=$(gh stack view --json | jq -r .trunk)
   git rev-parse --verify -q "$trunk" >/dev/null || trunk=origin/$trunk
   base=$(git merge-base HEAD "$trunk")
   ```

3. Absorb and rebase. `GIT_SEQUENCE_EDITOR=true` stops absorb from opening an editor, and `--update-refs` moves the lower layer branches with their commits.

   ```bash
   GIT_SEQUENCE_EDITOR=true git absorb --and-rebase --base "$base" -- --update-refs
   ```

4. Check `git status`. Commit or stash any leftover staged or unstaged change before the next step, or the rebase fails with `index contains uncommitted changes`.

5. Rebase the layers above the changed one. Use `--no-trunk`: `--upstack` is not enough when the current layer is not the top.

   ```bash
   gh stack rebase --no-trunk
   ```

6. Verify, then publish when the workflow allows it. Every branch must report `needsRebase` false.

   ```bash
   gh stack view --json | jq -c '.branches[] | [.name, .needsRebase]'
   gh stack push
   ```

7. Restore the backup branches that step 3 moved: for each line of `backup-refs.txt` whose branch now points elsewhere, run `git branch -f <branch> <recorded-sha>`. Compare with `git for-each-ref refs/heads/backup/`.

## If a rebase stops

A conflict in the step 3 rebase is plain git: resolve it and run `git rebase --continue`, or run `git rebase --abort` to go back. A conflict in the step 5 `gh stack rebase --no-trunk` is tracked by gh stack: resolve it and run `gh stack rebase --continue`, or run `gh stack rebase --abort`. In both cases restore the backup branches as in step 7, and use the safety branch from step 1 to return to the previous state if needed.
