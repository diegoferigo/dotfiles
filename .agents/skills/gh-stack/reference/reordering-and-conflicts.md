# Reordering, restructuring & conflict resolution

## Contents

- Restructuring with the TUI (drop/fold/insert/reorder/rename)
- Reorder hazard: the auto-merge trap
- Submitting after a reorder or middle insertion (mandatory procedure)
- Restacking after amending a lower layer
- Inserting a layer in the middle of an already-submitted stack
- Non-interactive reorder fallback
- Non-interactive insertion fallback
- Non-interactive drop fallback (peel a layer off)
- Resolving conflicts anywhere in the stack
- Commit hygiene that keeps layers movable

## Restructuring with the TUI

`gh stack modify` opens an interactive TUI to **drop, fold, insert, reorder, and rename** branches. Prefer it over manual rebases.

```bash
gh stack modify
#   Drop   : remove a branch      Fold: merge into an adjacent branch
#   Insert : add a branch         Reorder: move a branch up/down
#   Rename : rename a branch
# Stage operations, then Ctrl+S to apply them atomically.
```

- A reorder triggers a cascading rebase; resolve conflicts with `gh stack modify --continue` (or `--abort` to restore the original stack).
- After restructuring an unsubmitted stack, use the normal non-interactive submit form. If any affected PR already exists and its order or base changed, follow the mandatory procedure below instead; do not submit that stack.
- **TUI needs `origin/<trunk>` to exist.** It materialises the trunk from `origin/<base>`; a stack based on a **bare fork-point SHA that was never pushed** aborts with `could not create local trunk branch <sha>…`. With a real trunk base (`main`) this never happens. For the local-only variant, fake the ref first:
  ```bash
  git branch -f <base-name> <FORK_SHA>
  git update-ref refs/remotes/origin/<base-name> <FORK_SHA>   # local, no network
  gh stack unstack --local
  gh stack init --base <base-name> <b_bottom> ... <b_top>
  ```
  (`gh stack rebase --no-trunk` sidesteps the same limit for cascades but cannot reorder: only the TUI reorders.)

## Reorder hazard: the auto-merge trap

**Reordering two adjacent PRs can silently auto-merge the lower one.** GitHub marks a PR *Merged* the instant its base branch already contains its head commit (there is nothing left to merge). A reorder that re-points the lower PR's base onto the branch above it, while that upper branch still holds the lower PR's commit, trips exactly this: the lower PR closes as *Merged* into the upper branch instead of the order changing. It is then **unrecoverable in place** (merged PRs cannot reopen; a grouping containing merged members is not safely recreatable after teardown).

Guardrails whenever a base ref is about to change (reorder, `gh stack modify`, or a manual `gh pr edit --base`):

```bash
# STOP if this prints success, <new-base> already contains <pr-head>,
# so retargeting will auto-merge the PR:
git merge-base --is-ancestor <pr-head> <new-base> && echo "WILL AUTO-MERGE"
```

- Push the **reordered branch contents first** (so no branch contains a lower-layer head it shouldn't), *then* let bases update, or **recreate** the moved layer as a fresh branch/PR.
- A base is safe only when it is a proper ancestor of the head that **lacks** the head commit (base strictly *below* head).
- If a version of `gh stack` starts reordering without ever transiently pointing a base at its own head, this whole hazard may disappear, re-verify against the current version using the precedence policy in [commands.md](commands.md#installed-version-precedence).

## Submitting after a reorder or middle insertion (mandatory procedure)

**Choose the route first.**

- **A human can drive a terminal (preferred, documented by GitHub):** ask them to run `gh stack modify` (insert, reorder, Ctrl+S), then `gh stack submit --auto`. GitHub documents this as the route that pushes, updates the bases of existing PRs and replaces the old stack. Run the guards in step 1 and step 3 below before the human confirms with Ctrl+S, and verify the result with the `stacks?pull_request=` call and `gh pr view` per member. `--continue` and `--abort` are non-interactive, so an agent can resolve a conflict stop.
- **No TTY (agent only):** use the procedure below. It is the heavier, documented fallback ("unstack, rearrange, `link`").

Unverified on installed v0.2.0: whether `modify` + `submit` on an already-submitted stack ever points a base at its own head (the trap below). The guards cost nothing, so keep them on both routes. Verified in practice on v0.2.0: `gh pr edit --base` and `link` fail while the PR is a stack member, and a push that makes a PR head contained in its base marks it merged.

**STOP. Do not run `gh stack submit` on a submitted stack whose order changed.** GitHub marks a PR as *Merged* (and deletes its branch) the instant its base branch already contains its head commit. **This is silent, irreversible, and destroys the PR**: the branch is gone, the review history is closed as merged, and you must recreate the layer under a new PR number.

It bites in two ways:

1. `gh stack modify` reorder + `gh stack push`, when a lower PR's base gets moved onto a branch that still holds its commit.
2. **`gh stack submit` after a reorder or a middle insertion.** `submit` creates every *new* PR against the stack's previous top base, not against the branch below it in the new order. After a full-stack rebase that base contains everything below it, so each newly created middle PR is born already merged. Existing PRs whose base moves *down* the stack die the same way.

**Whenever the order of an already-submitted stack changes or a layer is inserted below an existing PR, do not run any form of `gh stack submit`.** Use this single procedure:

```bash
# 0. Journal the server grouping and every PR edge before mutation.
gh api 'repos/{owner}/{repo}/stacks?pull_request=<member#>' \
  > <durable-ledger-artifact>/stack-before.json
for pr in <bottom#> <...> <top#>; do
  gh pr view "$pr" \
    --json number,state,baseRefName,headRefName,headRefOid,autoMergeRequest,mergeStateStatus
done > <durable-ledger-artifact>/pr-edges-before.json

# STOP unless every member is OPEN, outside the merge queue and has auto-merge
# disabled. A retained queued/auto-merge member prevents a complete regroup.

# 1. BEFORE ANY PUSH, guard every CURRENT server edge against the PROSPECTIVE
#    state. A push moves a PR's head and/or its base, so check the local tip of
#    the PR's own branch (what it will have after the push) against BOTH the
#    server tip and the local tip of its current base branch. If either contains
#    the head, GitHub marks the PR merged at the first push that makes it true,
#    and a merged PR cannot be reopened. Checking only the current server head
#    misses the usual case: a layer was inserted, so the new local base contains
#    the layer's new head.
while read -r pr head_branch base_branch; do      # from pr-edges-before.json
  for base in "origin/$base_branch" "$base_branch"; do
    if git merge-base --is-ancestor "$head_branch" "$base"; then
      echo "UNSAFE CURRENT EDGE: PR #$pr ($head_branch) is contained in $base"
      exit 1
    fi
  done
done < <current-edges-list>
# If a PR is unsafe, do not push. First `unstack` the server grouping (step 5),
# retarget that PR to trunk or to a safe base, then push.

# 2. Push branches only after every current edge passes. `push` is non-atomic:
#    journal results and reconcile partial success before continuing.
gh stack push

# 3. Guard EVERY intended final pair, for existing and missing PRs alike. Run it
#    with LOCAL refs before step 2 as well (replace `origin/` with the local branch),
#    so an unsafe final pair is caught before anything is pushed.
#    `--is-ancestor <head> <base>` succeeding means "creating/retargeting this
#    PR will instantly mark it merged". It must FAIL for every pair.
for pair in "layer-1:main" "layer-2:layer-1" "layer-3:layer-2"; do
  head=origin/${pair%%:*}; base=origin/${pair##*:}
  if git merge-base --is-ancestor "$head" "$base"; then
    echo "UNSAFE FINAL EDGE: $head is already contained in $base"
    exit 1
  else
    echo "safe: $head -> $base"
  fi
done

# 4. Create each missing PR yourself, explicitly against the branch BELOW it.
gh pr create --draft --head <layer-N> --base <layer-N-1> --title … --body-file …

# 5. If an old all-open grouping exists, unstack that exact server stack.
#    This is a journaled operation; do not infer its number from stale local state.
#    Never use the no-argument form here: with no remote ID in the local state it
#    only drops local tracking and the later `pr edit --base` / `link` fail.
gh stack unstack <server-stack#>

# 6. Link the complete chain. `link` sets bases bottom-up in the right order.
gh stack link <bottom#> … <top#>
for pr in <bottom#> <...> <top#>; do
  gh pr view "$pr" --json number,state,baseRefName,headRefName,headRefOid
done
# Confirm the new server grouping lists every member in order.
gh api 'repos/{owner}/{repo}/stacks?pull_request=<top#>' \
  --jq '.[0] | [.number, ([.pull_requests[].number] | join(","))] | @tsv'
```

When only some branches changed and the user has confirmed the force-push (ask before any force-push), step 2 may be replaced by one branch-scoped `git push --force-with-lease=<branch>:<recorded-remote-sha> origin <branch>:refs/heads/<branch>` per changed branch, plus a plain push for the new branch. Run the step 1 and step 3 guards first.

Persist the command outputs in the operation journal. If the process stops after `unstack`, do not rerun the whole procedure: reconcile the recorded membership, current PR edges and branch heads, then resume only the missing link/verification step.

If a PR is already dead, do not try to reopen it: GitHub refuses to reopen a PR that it considers merged. Recreate the layer under a new branch name (the old branch name is usually deleted and reusing it confuses the stack), open a fresh draft PR, and reference the dead PR number in its body.

## Restacking after amending a lower layer

After `git commit --amend` (or any rewrite) of a lower layer, do not run `git rebase <lower-layer>` on the layer above: git replays the OLD version of the amended commit too, because it no longer matches by patch id, and the stale duplicate silently re-adds what the amend removed. Rebase with the old tip as the exclusive base instead, and keep the old tip until it is done:

```bash
old=$(git rev-parse <lower-layer>)         # BEFORE amending
# ... amend <lower-layer> ...
git rebase --onto <lower-layer> "$old" <upper-layer>
git log --oneline <lower-layer>..<top>     # each commit appears once; no duplicate subjects
```

Afterwards compare `git diff <lower-layer> <top> -- <paths the amend touched>`: it must show only the upper layers' own changes. `gh stack rebase` and `git absorb --and-rebase -- --update-refs` do this correctly; the manual `rebase <branch>` form does not.

## Inserting a layer in the middle of an already-submitted stack

Inserting a layer in the MIDDLE of an already-submitted stack **always needs the mandatory procedure above**, including grouping journal, branch-only push, ancestry guards, explicit draft PR creation, `unstack` and full-chain `link`. GitHub's stack grouping is append-at-top only, and the base of a PR cannot be changed while it is a stack member. `submit` creates the new PR against the wrong previous top base and cannot update the PR above it. Re-running it does not repair the chain. Plan for the full canonical sequence from the start after local branches reach target order.

## Non-interactive reorder fallback

The reorder has no scriptable flag. Without an interactive terminal, reproduce it with git + re-adopt, safe **only** when layers' file sets are disjoint:

```bash
# Rebase each branch onto its NEW parent, using its OLD parent as the exclusive
# base:  git rebase --onto <NEW_PARENT> <OLD_PARENT> <branch>
# Go bottom→top in the TARGET order; the just-rebased branch becomes the next
# NEW_PARENT. Disjoint files ⇒ no conflicts. (bash var names can't contain '-';
# map branch names to safe ids or use plain SHAs.)

gh stack unstack --local                                    # drop local tracking
gh stack init --base <FORK> <b_bottom> <b_2> ... <b_top>    # re-adopt new order
```

Verify: the **top tree must be byte-identical** before/after (`git rev-parse <top>^{tree}`), since you only permuted independent commits. Then re-lint every branch: the new intermediate prefixes never existed before.

## Non-interactive insertion fallback

Inserting a layer between two existing branches has no scriptable flag either (`gh stack modify` is TUI-only). Reproduce it with git, going bottom-up, when the new layer's files are disjoint from the layers above:

```bash
# New layer <NEW> goes between <BELOW> and <ABOVE>.
git switch -c <NEW> <BELOW>
# ...apply and commit the change...
git rebase --onto <NEW> <BELOW> <ABOVE>          # replay <ABOVE> on the new layer
git rebase --onto <ABOVE> ORIG_HEAD <TOP>        # replay every further layer
gh stack unstack --local
gh stack init --base <trunk> <b_bottom> … <NEW> … <b_top>
```

`ORIG_HEAD` after each rebase is the previous tip of the branch just replayed, which is exactly the exclusive base needed for the next one. Verify that the top tree changed by the new layer's diff only:

```bash
git diff --stat <old-top-tree> <new-top-tree>
```

Then follow the mandatory server procedure above. Do not submit first and repair afterwards; use the journaled `unstack` + full-chain `link` sequence described in [Inserting a layer in the middle of an already-submitted stack](#inserting-a-layer-in-the-middle-of-an-already-submitted-stack).

## Non-interactive drop fallback (peel a layer off)

Dropping a branch has no scriptable flag either. The common case is peeling the **top/leaf** off to merge it on its own later: it needs **no rebase**, since the branches below are untouched. Re-adopt the smaller set without it:

```bash
git switch <new_top>                          # the layer just below the leaf
gh stack unstack <stack#>                      # clears GitHub + local grouping for ALL members
gh stack init --base <trunk> <b_bottom> … <new_top>   # re-adopt without the leaf
gh stack submit --auto                         # same order, recreates the kept all-open subset
```

- The dropped PR keeps its branch, head and **base** (still pointing at its old parent), and simply leaves the stack. Do **not** retarget it to `<trunk>` while the lower layers are unmerged: that base ref is below its head, so the retarget would balloon its diff. Leave the base alone; once the lower layers merge, GitHub auto-retargets the orphan to `<trunk>` and it merges separately.
- Use `gh stack unstack --local` (not the plain form) when you only want to drop local tracking and leave the GitHub-side navigation untouched.
- Verify the leaf is gone from the group but intact as a branch:
  ```bash
  gh stack view                                          # leaf no longer listed
  git rev-parse --short <leaf> origin/<leaf>             # both still at the old head
  gh pr view <leaf-pr> --json state,baseRefName          # OPEN, base unchanged
  ```

Dropping a layer from the **middle** does need a rebase, exactly like the reorder fallback: `git rebase --onto <below> <layer> <above>` (then cascade the rest onto it), followed by `unstack --local` + `init` of the reduced set. Same disjoint-files caveat and top-tree verification apply.

## Resolving conflicts anywhere in the stack

Conflicts surface during a cascading `gh stack rebase` or a `gh stack modify` reorder. Both pause and expose the same git-native loop:

```bash
gh stack rebase                 # or: gh stack modify (then reorder in the TUI)
# ... conflict in some layer ...
git status                      # conflicted files (the layer being replayed)
git add <resolved paths>
gh stack rebase --continue      # or: gh stack modify --continue
# ...repeat per conflicting layer as the cascade proceeds...
gh stack rebase --abort         # or --abort: fully restore the pre-op state
```

- The cascade rebases **each layer onto the one below it in order**; resolve layer by layer, always `git add` then `--continue`.
- Prefer `--abort` over unwinding manually when a resolution goes wrong.
- Narrow the blast radius: `gh stack rebase --upstack` from the first affected branch, or `--downstack` up to it.
- Conflicting hunks are always the overlap between a layer and the layers below it, same file/lines edited in two layers is where conflicts appear (see commit hygiene to avoid them).

## Commit hygiene that keeps layers movable

Reorder/fold/drop are painless only when layers don't overlap:

- **One concern per commit, ideally one subsystem/directory.** Disjoint file sets ⇒ zero conflicts when reordering.
- **Never touch the same file (better: the same lines) in two layers.** If a file genuinely needs two concerns, keep both in one layer or split hunks with `git add -p` and accept they're coupled.
- **Put definitions below their users.** Shared headers/types/symbols go in a lower layer than anything referencing them, so lower layers build standalone.
- **Prefer additive, self-contained changes low in the stack** (new files/symbols, compat shims), they rarely conflict and reorder freely.
- **Each layer must build and lint on its own base**; keep formatting/lint fixes in the same commit as the code they touch.
- **No "fixup a previous layer" commits**, amend the right layer via `gh stack modify` (fold) or interactive rebase.
- **Use `git add -p` / path-scoped `git add`** to carve a big tree into clean, single-purpose commits.
- **Descriptive, self-contained messages**, a reordered commit should still make sense out of its original position.
