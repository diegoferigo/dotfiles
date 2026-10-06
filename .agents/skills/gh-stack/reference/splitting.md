# Splitting one big branch into a stack (soft-reset method)

Use this to repartition the commits/files of an existing branch into small, logical PRs. Decide the layers first, see [stack-design.md](stack-design.md), then carve them here.

## Contents

- [The single most important discipline: a per-layer gate](#the-single-most-important-discipline-a-per-layer-gate)
- [Plan first (no git yet)](#plan-first-no-git-yet)
- [Per-layer gate](#per-layer-gate)
- [Two base choices](#two-base-choices)
- [Fork-point local-only setup](#fork-point-local-only-setup)
- [Steps](#steps)
- [Key facts for splitting](#key-facts-for-splitting)

## The single most important discipline: a per-layer gate

**Split incrementally behind a per-layer gate.** Building the whole stack and only then validating the top lets errors (misplaced code, wrong-layer changes, non-self-sufficient layers) pile up where they are expensive to unwind and hard to self-correct. Instead: plan the layers up front, then add ONE layer at a time bottom-up and *verify it before starting the next*, fixing any problem on the layer that owns it immediately, never defer a fix upward.

## Plan first (no git yet)

Write the ordered layer list and map every changed file, or hunk, when a file splits across layers, to exactly one layer, lowest = most foundational. Keep this map visible; it is the contract the per-layer gate checks against. A file's *final* content is committed exactly once; the union of all layers must equal the full diff with no overlap.

## Per-layer gate

After each `gh stack add`, before moving on, confirm on that branch:

1. **scope**, its diff vs its base contains only that layer's intended files *and hunks* (`git diff <base>..HEAD --stat`, then skim the actual hunks: a misplaced include or stray blank line is cheap to fix now, costly in review);
2. **self-sufficient**, it builds/lints on its own base, not relying on a hack that only exists higher up;
3. **invariant**, union of all layers' files still equals the full diff, no overlap.

If any check fails, correct it on that branch before adding the next layer.

## Two base choices

- **(a) base on the fork point** to keep the final tree byte-identical (best when the head must match exactly, or for offline review), you then retarget to the real trunk before pushing (see [trunk-retargeting.md](trunk-retargeting.md)). Because a bare SHA base can break commands that expect `origin/<trunk>`, use the fake-ref procedure below;
- **(b) base directly on the real trunk** (`main`) when you intend to submit and merge, simpler to push, but the tree absorbs any trunk advance.

Steps below show (a); for (b) just use the trunk in place of `<FORK_POINT>`.

## Fork-point local-only setup

When the stack is based on a fork-point SHA, give that SHA a local branch name and a matching local remote-tracking ref before `gh stack init`. Use `<base-name>` as the working branch that temporarily holds the full diff. This keeps the head byte-identical while satisfying commands that materialise `origin/<base-name>`:

```bash
git switch -c <base-name> <FORK_POINT>
git update-ref refs/remotes/origin/<base-name> <FORK_POINT>   # local only, no network
gh stack init --base <base-name> <scope>/layer-1
```

Use `gh stack rebase --no-trunk` while staying local-only so the cascade aligns stack branches without rebasing onto the moving upstream trunk. Before pushing, retarget the stack to the real trunk with [trunk-retargeting.md](trunk-retargeting.md).

## Steps

```bash
# 0. Record the target tree so you can verify identical head at the end.
git rev-parse HEAD                 # e.g. <TARGET> = current tip of the big branch

# 1. Move to the fork point with all changes preserved in the working tree.
#    Base the stack on the SAME commit the big branch forked from, so the final
#    head is byte-identical (rebasing onto a newer trunk changes the tree).
git switch -c <base-name> <FORK_POINT>    # FORK_POINT = git merge-base <branch> <trunk>
git restore --source=<big-branch> --staged --worktree .   # bring in all final file contents
# (equivalently: start on the big branch and `git reset --soft <FORK_POINT>`)

# 2. Initialize the stack's bottom branch. For a fork-point base, create the
#    local remote-tracking ref first; for a real trunk, use the trunk name.
git update-ref refs/remotes/origin/<base-name> <FORK_POINT>   # fork-point/local-only only
gh stack init --base <base-name-or-trunk> <scope>/layer-1

# 3. For each logical group, bottom-up, stage only that group's files and add a
#    layer, then RUN THE PER-LAYER GATE before the next one:
git restore --staged .                       # clear the index
git add <group-1 paths...>
gh stack add -m "Group 1 summary" <scope>/layer-1     # (or commit then it's on current)
git diff --stat <base>..HEAD                  # gate: only this layer's files+hunks?
# build/lint this layer on its own base; fix here if wrong, THEN continue
# ...repeat: stage next group, `gh stack add -m "..." <scope>/layer-N`, re-gate

# 4. Multiple commits in ONE PR: after `gh stack add`, just `git commit` again
#    on the same branch (no new `gh stack add`). Useful to keep e.g. a lockfile
#    in its own commit within the same PR. If there is a single lockfile commit,
#    keep it as the LAST commit of the whole stack (the head), even after
#    rebases, so it can always be regenerated + `git commit --amend`ed in place.
git add pixi.lock && git commit -m "Update lockfile"

# 5. Verify the final head is identical to the original branch.
gh stack top
git diff --quiet <TARGET> && echo "IDENTICAL TREE" || echo "TREES DIFFER"
```

## Key facts for splitting

- A file's *final* content is committed exactly once (in whichever layer owns it). The union of all layers' files must equal the full diff, with no overlap.
- Because splitting only repartitions the same final file contents, the top of the stack has the **same tree** as the original branch, verify with `git diff --quiet <TARGET>`.
- Order layers by dependency: a new header/symbol must be in the same or a lower layer than its first user (otherwise intermediate layers won't build).
- Lockfiles/large generated files get special handling, see [lockfiles.md](lockfiles.md).
