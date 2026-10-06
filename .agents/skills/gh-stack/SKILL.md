---
name: gh-stack
description: >-
  Creates, manages, splits, reorders, and submits stacked pull requests with the
  GitHub `gh stack` CLI extension. Use whenever the user mentions gh stack,
  stacked PRs, a PR stack, splitting or breaking up a large branch or PR,
  non-interactive/agent use, exit-code recovery, conflict resolution,
  reordering, trunk retargeting, or lockfiles, even if they do not explicitly
  ask for this skill.
compatibility: >-
  Requires the GitHub CLI (`gh`) with the `github/gh-stack` extension, git, and a
  repository with stacked pull requests enabled.
metadata:
  author: diegoferigo
  version: "1.1.0"
  upstream: github/gh-stack@d4ab7ab47e5b3e3708a27c8c42abcdf4bc321419
---

# gh stack: Stacked Pull Requests

`gh stack` (extension `github/gh-stack`) creates and maintains a chain of small, dependent PRs. Bottom PRs merge first; every higher PR targets the branch below it. Use this skill whenever the work involves stacked PRs or splitting one large change into independently reviewable PRs. Native stacked pull requests are in public preview ([changelog](https://github.blog/changelog/2026-07-30-stacked-pull-requests-are-now-in-public-preview)). Set `GH_STACK_NO_UPDATE_NOTIFIER=1` in agent runs to silence update notices.

Docs: <https://gh.io/stacks> · Overview: <https://github.github.com/gh-stack/introduction/overview/>

## First decisions

1. Confirm setup and installed behavior: `gh stack --version` and `gh stack <cmd> --help` (`gh stack help <cmd>` is not per-command help).
2. If building a new stack, design the layers first: foundational/shared code lower, dependent code higher. Author every commit as one self-contained, single-concern unit so a layer stays splittable later, but default to small groups upfront: a lean PR clears the fixed, often-large per-review latency fast, so it merges sooner. See [reference/stack-design.md](reference/stack-design.md).
3. If splitting an existing large branch/PR, use the soft-reset method and the per-layer gate in [reference/splitting.md](reference/splitting.md).
4. If a fix belongs to commits of one or several lower layers, fold it in with `git absorb` ([reference/absorb.md](reference/absorb.md)) instead of fixup commits.
5. If changing order, inserting a middle layer, retargeting trunk, resolving conflicts, or handling generated lockfiles, load the matching reference before running commands.

## Cross-skill ownership

When another workflow skill uses `gh-stack`, ownership is split once:

- the domain skill decides **whether consent covers an external mutation**;
- the orchestrator decides **when and by whom it runs**, and journals it;
- `gh-stack` defines **preconditions, exact commands, verification and recovery**.

This skill never broadens consent granted by the active domain workflow. An active orchestrator may narrow delegation further than the standalone rules below.

## Non-interactive agent rules

`gh stack` changes behavior when stdout is a TTY. In agent or CI contexts, avoid prompt/TUI commands and use these forms:

| Use                                       | Avoid                                     |
| ----------------------------------------- | ----------------------------------------- |
| `gh stack view --json`                    | `gh stack view`                           |
| `gh stack submit --auto`                  | `gh stack submit` (editor TUI)            |
| `gh stack merge <target> --yes`           | `gh stack merge <target>` / `gh pr merge` |
| `gh stack init <branch>...`               | `gh stack init`                           |
| `gh stack add <branch>`                   | `gh stack add`                            |
| `gh stack checkout <target>`              | `gh stack checkout` (picker)              |
| `gh stack up` / `down` / `top` / `bottom` | `gh stack switch`                         |

`gh stack modify` is TUI-only. `submit` without `--auto` opens a TUI editor in a terminal, and `checkout` without an argument opens a stack picker. Agents should use the documented fallbacks in [reference/reordering-and-conflicts.md](reference/reordering-and-conflicts.md).

## Safety gates

- **Use one topology owner.** A worker never invents a topology change or uses a different verb after an error. In standalone use, the owner may delegate one exact command only when the active workflow allows it and the brief includes verified preconditions, expected output and a stop-on-deviation rule. When an orchestrator agent coordinates workers, the orchestrator executes every mutating `gh stack` command and push; workers edit, verify and optionally commit only.

- **Worktrees share one stack catalog (v0.2.0, Git 2.36+), but only the orchestrator changes the stack.** State lives in `<common-dir>/gh-stack`, so `rebase`, `sync` and `modify` work across worktrees and `--continue`/`--abort` run from any worktree. Workers (subagents) still never run `gh stack` commands that change the stack: the topology owner runs them from one worktree. See [reference/troubleshooting.md](reference/troubleshooting.md).

- **Streaming publication is branch-scoped Git, never `gh stack push`.** The latter pushes every active branch. When an active review workflow has proved one layer ancestry-stable and authorized its publication, the topology owner pushes only that ref with the journaled lease:

  ```bash
  git push --force-with-lease=<branch>:<recorded-remote-sha> \
    <remote> <local-ref>:refs/heads/<branch>
  ```

  Do not run `gh stack push`, `sync` or `submit` for this incremental step. It changes no grouping or PR base. Re-gather remote heads afterwards, and reconcile local tracking before the next stateful stack operation.

- **Never `unstack` to "fix" a stack, and never fuse separate stacks.** If the local `.git/gh-stack` looks stale or a PR seems to "belong to multiple stacks", do NOT reach for `unstack`/`link` to force everything into one group. Fetch and compare every local/remote tip first. If tips diverged because the remote moved, do not sync, rebase or push; follow [reference/syncing.md](reference/syncing.md). Use sync only for non-divergent ordinary reconciliation. Fusing distinct stacks throws away each stack's number and its web-UI grouping.

- **`unstack` on a stack that contains a merged PR is irreversible for the grouping.** GitHub keeps the merged member but drops the open ones, and you cannot recreate the group: `gh stack link` enforces base-ref chaining (each PR's base must equal the PR-below's head branch), and a merged bottom PR's head branch is deleted while its open successor correctly bases on trunk, so the link is rejected (HTTP 422). The web-UI view of "merged bottom + open successor" cannot be rebuilt. Do not `unstack` such a stack.

- **Retargeting a stacked PR to trunk after its base merges is the canonical maintenance procedure, not blanket consent.** Run it autonomously only when the active domain workflow or user instruction authorizes it. When authorized, and after journaling the server grouping, rebase the open successor of the merged base onto trunk (`git rebase --onto origin/<trunk> <old-base-tip>`, `--force-with-lease`) and regroup on GitHub via `gh stack unstack <open-server-stack#>` + `gh pr edit <bottom#> --base <trunk>` + `gh stack link --base <trunk> <b…>`. `unstack` on an **all-open** stack is non-destructive (grouping recreates under a new number, tips and bases untouched), so there is no destructive-vs-safe choice within the authorized procedure: pick the stack-preserving regroup. This is the topology owner's routine, not worker improvisation, and it is distinct from the merged-member hazard above. `gh pr edit --base` and `link`/`submit` all 422 ("PullRequest.base is invalid") while the PR is still a member, so `unstack` first. See [reference/trunk-retargeting.md](reference/trunk-retargeting.md).

- Run `gh stack merge` only on the user's explicit instruction to merge.

- New PRs should be drafts unless the user explicitly asks otherwise: use `gh stack submit --auto` and do not pass `--open`, because `--open` also marks existing PRs ready for review.

- When a repo has multiple remotes, set `git config remote.pushDefault origin` or pass `--remote <name>` to commands that support it. `checkout` and `trunk` have no `--remote` flag and rely on the config.

- After `gh stack sync`, verify with `gh stack view --json`; divergent local and remote stacks can print `Sync aborted` while exiting 0.

- If remote branches were rebased elsewhere, treat the remote as authoritative; do not `sync`/`rebase`/`push` stale local branches. Follow [reference/syncing.md](reference/syncing.md).

- If the order of an already-submitted stack changed or a layer was inserted below an existing PR, do not submit it. Follow [reference/reordering-and-conflicts.md](reference/reordering-and-conflicts.md) to snapshot membership, push branches, guard ancestry, create missing draft PRs, unstack the all-open old grouping and link the complete chain.

- For generated lockfiles, regenerate and squash into the dedicated lockfile commit; do not hand-merge hunks. See [reference/lockfiles.md](reference/lockfiles.md).

## Reviewing the whole stack

- **Per-layer web review is blind to the stack as a whole; add one deep review over the full range.** GitHub's automated PR review (Copilot web) and any per-PR reviewer only see each layer's own diff, whose base is the layer below, so nothing ever reads the feature end to end. That per-layer view structurally misses cross-layer defects: a fix landed in a lower layer that a higher layer silently re-breaks, an inconsistency between two layers, a term wired in one layer but consumed wrongly in another, or an invariant that only reads as wrong when the whole feature is seen at once. The blind spot is inherent to stacking, not a reviewer failing.
- Before submitting for review or merging, run one overall deep review across the full stack range in addition to the per-layer web reviews. Fetch the remote, verify the final PR heads, compute their merge-base with the gathered remote trunk and review `<merge-base-sha>...<top-headRefOid>`. Reconcile findings back into the owning layer (edit the layer, re-cascade the rebase upward, force-push), never by squashing layers together to make one reviewable diff.
- A long-running review workflow records this gate against the immutable merge-base/top-head pair. Any stack rewrite invalidates the record; rerun it before reporting convergence.

## Quick command map

| Goal                                         | Command                                                                      |
| -------------------------------------------- | ---------------------------------------------------------------------------- |
| Start a stack                                | `gh stack init [--base <trunk>] <branch>`                                    |
| Create/adopt layers                          | `gh stack init <b1> <b2> <b3>`                                               |
| Add a layer from the top                     | `gh stack add -m "msg" <branch>`                                             |
| View state                                   | `gh stack view --json`                                                       |
| Navigate                                     | `gh stack up` / `down` / `top` / `bottom` / `trunk`                          |
| Rebase                                       | `gh stack rebase [--no-trunk]` (`--no-trunk` skips the trunk fetch/rebase)   |
| Push branches only                           | `gh stack push`                                                              |
| Push + create/link PRs                       | `gh stack submit --auto`                                                     |
| Fetch/rebase/push/link                       | `gh stack sync`                                                              |
| Stateless API linking                        | `gh stack link [--base <trunk>] <b…>`                                        |
| Pull a remote stack locally                  | `gh stack checkout <n\|PR\|url>`                                             |
| Merge bottom-up                              | `gh stack merge <stack#\|PR#> --yes [--squash\|--rebase\|--merge]`           |
| Remove stack grouping                        | `gh stack unstack [<stack#>] [--local]`                                      |
| Peel the top layer off (merge it separately) | `unstack` + `init` the kept layers + `submit` (see reordering-and-conflicts) |

For preconditions, side effects, atomicity, exit codes, and the `view --json` schema, read [reference/commands.md](reference/commands.md).

## Command shorthands

Invoked as `/gh-stack <verb> [text]`; the verb is the input's first token, and with no verb it runs `?`. This skill exposes the shared core:

- `?`: list these shorthands and the primary actions: design a stack, split a branch, submit, reorder, resolve conflicts, retarget trunk.
- `status`: report the current stack from `gh stack view --json`: the layers, their order, each PR's target branch, and merge readiness.

## Reference map

Load only the relevant file; each reference is linked directly from here.

- [reference/commands.md](reference/commands.md): command semantics, setup, non-interactive forms, exit codes, JSON schema, source-of-truth/version policy.
- [reference/stack-design.md](reference/stack-design.md): choosing layer order, branch naming, and one-stack/one-story guidance.
- [reference/splitting.md](reference/splitting.md): splitting a big branch/PR into layers with a per-layer verification gate.
- [reference/reordering-and-conflicts.md](reference/reordering-and-conflicts.md): `modify`, reordering, middle insertion, auto-merge trap, fallbacks, conflicts.
- [reference/absorb.md](reference/absorb.md): folding staged fixes into the right lower layers with `git absorb`, then rebasing the layers above.
- [reference/syncing.md](reference/syncing.md): reconciling local branches after the remote stack was rebased or force-pushed elsewhere.
- [reference/trunk-retargeting.md](reference/trunk-retargeting.md): non-default or borrowed trunk, and moving back after the base PR merges.
- [reference/lockfiles.md](reference/lockfiles.md): generated/lockfile handling during stack rebases and reorders.
- [reference/troubleshooting.md](reference/troubleshooting.md): upstream recovery recipes for conflicts, divergence, stack locks, interrupted modify, worktrees.
- [reference/cleanup.md](reference/cleanup.md): pruning local stack branches and stale backups after a stack settles.
- [reference/lessons.md](reference/lessons.md): concise cross-repository planning, verification and rollback lessons.
- [reference/legacy.md](reference/legacy.md): pre-v0.1.0 workarounds kept only as history; use current commands first.

## Upstream mirror policy

The upstream skill from `github/gh-stack` is pinned at `d4ab7ab47e5b3e3708a27c8c42abcdf4bc321419` (skill v0.2.0, synced 2026-10-06). `reference/commands.md`, `reference/stack-design.md`, and `reference/troubleshooting.md` keep verbatim upstream blocks between `<!-- upstream:begin -->` / `<!-- upstream:end -->`; local notes live outside those markers. When behavior conflicts, prefer the installed command help and observed behavior, then the pinned upstream mirror, then local version-labelled exceptions, then legacy notes.
