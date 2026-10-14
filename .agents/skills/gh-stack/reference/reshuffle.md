# Reshuffling a submitted stack

Use this when an already-submitted, all-open stack under review must change shape without closing any PR: inserting or reordering layers, splitting a move from its generalisation, or regrouping so a reviewer sees a readable diff. It extends the mandatory procedure in [reordering-and-conflicts.md](reordering-and-conflicts.md) with the local rebuild, verification and publish order.

## Contents

- [When to reshuffle](#when-to-reshuffle)
- [Readable diff in a reshuffle](#readable-diff-in-a-reshuffle)
- [Phase 1: rebuild locally](#phase-1-rebuild-locally)
- [Phase 2: verify every layer](#phase-2-verify-every-layer)
- [Phase 3: journal and guard](#phase-3-journal-and-guard)
- [Phase 4: publish](#phase-4-publish)
- [Phase 5: realign local state and PR text](#phase-5-realign-local-state-and-pr-text)
- [Rollback](#rollback)
- [Gotchas](#gotchas)

## When to reshuffle

Reshuffle only when the new order has a clear review benefit. Review threads are not deleted: threads on rewritten commits become outdated. Possible losses: approvals (if stale reviews are dismissed), per-commit "viewed" state, links to unreachable SHAs, and the old stack number. Get explicit consent for the force-pushes and for the regrouping.

## Readable diff in a reshuffle

The goal and the rules (pure move first, no copies, mechanical changes apart, one concern per layer) are in [stack-design.md](stack-design.md#make-every-layers-diff-readable). In a reshuffle, apply them to the new layout, plus one rule of its own:

- Keep every PR non-empty: a layer whose diff vanishes after the reshuffle has to be dropped explicitly, not left open.

## Phase 1: rebuild locally

Build the new stack on throwaway local branches (for example `restack/<layer>`), never on the existing branch names, and never touch the remote.

- Pick a prefix that cannot collide with an existing branch (a branch named `wip` blocks every `wip/*`).
- Create the new layers with `git mv` and cherry-picks, then replay each existing layer with `git rebase --onto <new-parent> <old-parent> <layer>`, bottom to top. Each old tip is the exclusive base.
- A copy created from a remote-tracking ref (`git branch restack/x origin/x`) tracks `origin`. Create copies with `--no-track`, or run `git branch --unset-upstream` on every copy, so a plain `git push` cannot publish it.
- `git checkout <rev> -- <dir>` does not delete files absent in `<rev>`: use `git rm`.
- A layer fix that appears while rebuilding (for example a formatter diff) becomes a new commit on the layer that owns it, or folds into a commit that is new to this reshuffle. Never amend a commit that was under review. Then replay the layers above with the same `--onto` form.

## Phase 2: verify every layer

- The top tree must equal the old top tree, or differ only by the intended change: `git rev-parse <new-top>^{tree}` against the old one, and `git diff --no-ext-diff --stat <old-top> <new-top>` empty.
- Layers that must be unchanged keep their package tree identical to the old tip.
- Count commits against trunk and explain any difference.
- Run the linter on **every** layer range (`<trunk>..<layer>`), not only the top. A new intermediate prefix has never been formatted or built.
- Build and run the tests of every touched package at the top. Run the tests of downstream packages when a helper's behaviour changed. State plainly which packages have no tests and which layers were not built individually.
- In a worktree, a wrapper test command may print nothing and exit silently: run the test binaries or `ctest` directly.

## Phase 3: journal and guard

Before any remote mutation, with every member OPEN, outside the merge queue and with auto-merge off:

1. `git fetch`, then check each remote tip equals its recorded old tip.
2. Create a backup ref for each old tip (`refs/<backup-namespace>/<branch>`).
3. Save the server grouping (`gh api 'repos/{owner}/{repo}/stacks?pull_request=<n>'`) and, per PR, number, state, base, head branch and head SHA.
4. Map each new local branch to the remote branch it will overwrite, and save the old SHA for each lease.
5. Run the ancestry guard on **every** final pair (head against its new base) and on every pair that exists while the push is in flight: the head must not be contained in its current server base, nor in its new base. One `--is-ancestor` success means the PR will be marked merged and cannot be reopened.
6. Check which PR would become contained if its lower neighbour is pushed first (see the order in phase 4).

## Phase 4: publish

Ask before publishing. Use only plain Git and `gh pr`; never `gh stack push`, `sync` or `submit` here.

1. `gh stack unstack <server-stack#>` with the explicit number. This is non-destructive when every member is open. The stack gets a new number later.
2. Find every PR whose new head would be contained in its current server base once the other branches are pushed (the guard in phase 3). Push that PR's branch alone with its lease, retarget it with `gh pr edit <n> --base <new-base>`, and check that its diff holds the expected commits. Only then push the rest. Pushing the branches that contain it first marks it merged.
3. Fetch, re-run the guards against `origin/*`, then one `git push --atomic` with a `--force-with-lease=refs/heads/<branch>:<old-sha>` per overwritten ref and a plain create for each new branch. Expect many workflow runs.
4. Create each missing PR with `gh pr create --draft` against the branch below it, using the repository PR template.
5. `gh stack link <bottom#> ... <top#>` with the complete chain. It updates the bases of the existing PRs in the right order, so `gh pr edit --base` is not needed. If `link` rejects, fall back to `gh pr edit` per PR.
6. Verify: `gh pr view` for every PR (open, base, head SHA) and the stacks API for the member order.

Do not retarget a PR whose old head is already an ancestor of the new base before its branch is pushed: it is marked merged.

## Phase 5: realign local state and PR text

1. Point each local branch name at its rebuilt commit: `git branch -f <name> <restack-branch>`, `git reset --hard` in the worktree where the branch is checked out, and set the upstream.
2. `gh stack init` needs a branch checked out: switch off a detached HEAD first. Then `gh stack unstack --local` and `gh stack init --base <trunk> <names bottom first>`, and check the order with `gh stack view --json`.
3. Rewrite titles and descriptions that no longer describe the diff (new scope, test counts, removed items). Keep the repository template and do not add stack notes.
4. Old replies that quote commit SHAs now point at unreachable commits. Map old to new by subject (add manual entries for commits whose subject changed) and PATCH the replies you authored.
5. Reply on the thread that triggered the reshuffle, and leave other people's threads unresolved.
6. Check CI on the new heads. Re-request reviews only where a layer changed in content, not for an unchanged tree.

## Rollback

Valid only while no PR is marked merged. Push each `refs/<backup-namespace>/*` ref back with a lease on the new SHA, restore the old bases from the journal, close the new PR and delete its branch (with explicit consent), then `unstack` the new group and `link` the old chain.

## Gotchas

- `gh pr edit --base` and `gh stack link` fail with HTTP 422 while the PR is a stack member: `unstack <n>` first, never the no-argument form.
- `gh stack` cannot insert or reorder in place without a TTY; the sequence above is the fallback.
- A push that makes a PR's head contained in its base marks it merged, and a merged PR cannot be reopened.
- `git diff` may be wrapped by an external diff tool: use `--no-ext-diff` in scripts.
- Local tracking is cosmetic. The server grouping is the source of truth for order.
