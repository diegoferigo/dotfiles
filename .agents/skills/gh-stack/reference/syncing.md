# Updating a local stack after the remote was rebased

**Decision gate: if the *remote* branches were updated elsewhere, STOP, do not run `gh stack sync`/`rebase`/`push`.** Those rebase your *stale local* onto trunk and push it, clobbering the cleaner remote rebase or manufacturing needless conflicts. The remote is authoritative; make local match it instead. Validated against **v0.1.0**.

This applies when someone hit the PR-UI **Rebase Stack** button, a teammate re-pushed, a bottom PR merge auto-rebased the rest, or you rebased on another machine. Your local branches are then *stale*: they carry the same logical commits at old SHAs, and `git rev-list --left-right --count <branch>...origin/<branch>` shows both sides non-zero (diverged, not fast-forwardable).

## Contents

- [Why the obvious commands are wrong here](#why-the-obvious-commands-are-wrong-here)
- [Correct procedure](#correct-procedure)

## Why the obvious commands are wrong here

- **`gh stack checkout <n>` does NOT force-update divergent branch tips.** It reconciles stack *structure* (PR membership/order) only, so when the grouping already matches it prints `✓ Local stack matches remote, switching to branch` and leaves every stale tip untouched. It is not the tool for pulling in a remote rebase.
- **`gh stack sync`/`rebase`/`push` are wrong here**, they rebase your stale local onto trunk and push, clobbering the remote rebase or manufacturing conflicts.

## Correct procedure

Fast and deterministic once `git fetch` shows the divergence:

```bash
git fetch origin --prune

# 1. Confirm there is NO local-only work: every branch's diff vs its remote must
#    be empty OR only trunk-derived changes the remote rebase already absorbed.
#    (A non-trunk diff means you have unpushed local work, stop and rebase it in
#    with `gh stack rebase`/`sync` instead of resetting.)
for b in <bottom> <l2> <l3> <top>; do
  echo "== $b =="; git diff --stat "$b" "origin/$b"
done

# 2. Backup the current local tips (cheap insurance; recover with `git update-ref`).
for b in <bottom> <l2> <l3> <top>; do git update-ref "refs/backup/premerge/$b" "$b"; done

# 3. Fast-forward trunk, then hard-align every stack branch to its remote.
git switch main && git merge --ff-only origin/main
for b in <bottom> <l2> <l3>; do            # every branch EXCEPT the one checked out
  git update-ref "refs/heads/$b" "origin/$b"
done
git switch <top> && git reset --hard "origin/<top>"   # working tree is clean

# 4. Verify: every branch (and main) must report `0  0`.
for b in <bottom> <l2> <l3> <top>; do
  printf '%-45s ' "$b"; git rev-list --left-right --count "$b...origin/$b"
done
gh stack view --json        # confirm SHAs now match the remote
```

`git update-ref` resets the non-checked-out branches without switching to each one; only the currently checked-out branch needs `reset --hard` (and only when the working tree is clean). The stack's local tracking/order is unchanged, so no `link`/`submit` is needed afterwards. Recover any branch with `git update-ref refs/heads/<b> refs/backup/premerge/<b>`.
