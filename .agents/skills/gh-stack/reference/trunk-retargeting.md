# Retargeting the trunk of a stack

Stacking on top of someone else's open PR branch (or any non-default trunk), and moving back to the default branch afterwards. Validated against **v0.1.0**, re-verify against your installed version using the precedence policy in [commands.md](commands.md#installed-version-precedence).

## Contents

- [Before pushing: retarget a not-yet-submitted stack](#before-pushing-retarget-a-not-yet-submitted-stack)
- [Retargeting the trunk of an ALREADY-SUBMITTED stack](#retargeting-the-trunk-of-an-already-submitted-stack)
- [Do not add the other person's PR to your stack](#do-not-add-the-other-persons-pr-to-your-stack)
- [Moving the trunk back once the base PR is merged](#moving-the-trunk-back-once-the-base-pr-is-merged)

## Before pushing: retarget a not-yet-submitted stack

To rebase onto a *different* branch than the configured trunk (e.g. when `main` is temporarily broken and you want to stack on a soon-to-merge branch):

```bash
gh stack unstack --local
gh stack init --base <newbase> <branches…>
```

Two gotchas observed with gh-stack v0.0.8:

- **`init --base` only *adopts/records* the new trunk, it does NOT rebase the branches onto it.** You must run `gh stack rebase` afterwards to actually cascade onto the new trunk (that is where trunk-vs-stack conflicts surface and get resolved). The tree changes only at the `rebase` step.
- **Pass `<newbase>` as a plain branch name WITHOUT the `origin/` prefix** (e.g. `--base abrandemuehl/feature`, not `--base origin/abrandemuehl/feature`). gh stack internally prepends `origin/`; giving `origin/…` yields `origin/origin/…` and `gh stack rebase` fails with `could not create local trunk branch … Not a valid object name`.

Then resolve any conflicts and `submit`.

## Retargeting the trunk of an ALREADY-SUBMITTED stack

This needs both a **local** trunk change and a **server-side** base change on the bottom PR:

```bash
# 1. LOCAL: re-adopt the branches under the new trunk (plain name, no origin/).
gh stack unstack --local
gh stack init --base <newtrunk> <bottom> <l2> <l3> <top>   # bottom → top
gh stack rebase                                            # actually cascades

# 2. SERVER: the bottom PR's base cannot be edited while it is a stack member
#    (`gh pr edit --base` → "Cannot change the base branch because the pull
#    request is part of a stack"). So tear the grouping down first.
gh api repos/{o}/{r}/stacks?pull_request=<bottom#> --jq '.[].number'
gh stack unstack <stack#>
gh pr edit <bottom#> --base <newtrunk>
gh stack submit --auto        # NOT `gh stack link`, see below
```

- **`gh stack link` does not read gh-stack's local trunk tracking.** With no `--base` it targets the repo default branch and reports `✓ Updated base branch for PR #<bottom> to main`, silently undoing a custom-trunk retarget. `gh stack submit` *does* honour the locally configured trunk, so use `submit` whenever the stack has a non-default trunk. (`gh stack link --base <trunk>` can set a non-default trunk when driving stacks statelessly, but it re-stacks the PR, so it is not the tool for fixing an existing local-tracked stack here.)
- `gh stack submit --auto` is safe here **only because the order is unchanged and every PR already exists** (no new PR is created, so no draft/ready state and no base moves *down* the stack). If the order also changed, follow the push → guard → `gh pr create` → `link` procedure in [reordering-and-conflicts.md](reordering-and-conflicts.md) instead.

## Do not add the other person's PR to your stack

Do **not** run `gh stack link <their#> …`. That rewrites *their* PR's base, makes merging yours merge theirs bottom-up, and collides with whatever tool they use (Graphite, jj, ghstack…). Pointing your trunk at their *branch* leaves their PR completely untouched, verify with `gh api repos/{o}/{r}/stacks?pull_request=<their#> --jq 'length'` → `0`.

Living on someone else's branch is inherently unstable: if they force-push (e.g. a Graphite restack) your stack goes stale and your bottom PR shows a dirty diff until you re-run `gh stack rebase` + `gh stack push`. When their PR merges, their branch is deleted and GitHub re-targets your bottom PR to the default branch, then move the trunk back (below).

## Moving the trunk back once the base PR is merged

Same recipe in reverse (`--base main`), **plus one extra care**: if the base PR was **squash-merged**, its branch tip is *not* an ancestor of the default branch, so a plain `gh stack rebase` can pick a too-old merge-base and replay the old trunk commit into your bottom layer as a duplicate of the squash commit. Set the boundary explicitly on the bottom branch, then cascade normally:

```bash
git rev-parse origin/<oldtrunk>                       # OLD_TRUNK_SHA
git merge-base --is-ancestor <OLD_TRUNK_SHA> origin/main \
  && echo "merge commit"  || echo "squash-merged"

gh stack unstack --local
gh stack init --base main <bottom> <l2> <l3> <top>
git switch <bottom>
git rebase --onto origin/main <OLD_TRUNK_SHA>    # excludes the trunk commit
# then per-layer: gh stack up && gh stack rebase --downstack
```

Verify afterwards that the top's tree differs from the pre-rebase top by *exactly* the trunk's advance, and that no layer gained a commit:

```bash
# must equal: git diff --stat <OLD_TRUNK_SHA> origin/main
git diff --stat <backup-top> <top>
# per layer: same commit count as before
git log --oneline <base>..<branch>
```

Then `gh stack push`, `gh stack unstack <stack#>`, `gh pr edit <bottom#> --base main`, `gh stack submit --auto`.
