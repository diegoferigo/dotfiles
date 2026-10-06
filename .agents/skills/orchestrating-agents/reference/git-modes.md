# Git execution modes

## Contents

- Choosing an execution mode
- Shared main-tree mode
- Committing: who commits, safely
- Non-git snapshot fallback
- Stacked-PR output for large tasks
- Worktree fleet mode
- Crash recovery for git operations

## Choosing an execution mode

Default to the main working tree with one writer at a time. Use a worktree fleet when at least two substantial implementation tasks have independent write sets, the repository can be bootstrapped per worktree, and the integration or stack order is known. Existing worktrees do not imply fleet mode; inventory them and treat worktrees not created for this run as occupied and read-only.

The host may expose user-facing `/fleet` and `/worktree` commands without exposing worktree creation as an agent-callable tool. Use native isolation when the current harness makes it callable. Otherwise, create worktrees only when the user requested worktree/fleet execution or repository instructions authorize it. If neither applies, stay in shared main-tree mode.

Choose a fleet only when its parallel-work benefit clearly exceeds setup and integration cost. In a boundary case, stay in main-tree mode.

## Shared main-tree mode

Only one subagent may write the shared working tree at a time. Parallel subagents are limited to read-only investigation, external repositories, or DRAFT artifacts written to explicit session paths. Disjoint file lists do not make concurrent writes safe: editors still share one index, untracked files, generators and repository-wide tools.

Before delegation, record `git status --short`, the current branch and HEAD in the ledger. The assigned write set must not overlap pre-existing dirty paths, and the index must be clean before a writer starts; otherwise use an isolated worktree or stop. Give the writer an explicit write set. When it returns, compare the diff with that baseline and reject changes outside its write set.

Targeted checks during the edit are useful signals. The authoritative gate is the orchestrator's full integration check after the writer stops and the tree is has no active writer.

## Committing: who commits, safely

In shared main-tree mode, the orchestrator commits. Uncommitted changes do not carry authorship metadata, so never infer ownership from their path or content. Compare against the recorded baseline, stage only the task's explicit paths, and inspect the staged diff:

```bash
git add -- <assigned-path>...
git diff --cached --stat
git diff --cached
git commit
```

Never use `git add -A`, `git commit -a`, or pathless staging in a dirty shared tree. Do not use interactive staging to separate overlapping writers; serialize those tasks instead.

In worktree mode, a subagent may commit only inside its isolated worktree and only when its brief explicitly grants commit ownership. The brief must include the expected git identity, commit-message convention and forbidden trailers. Before integration, the orchestrator verifies the changed paths and:

```bash
git log -1 --format='%an <%ae>%n%B'
```

All rebase, commit splitting, stack topology and push operations remain serialized under the orchestrator. Record every accepted SHA in the ledger.

## Non-git snapshot fallback

Git commits are the default safety net. When the target is not a git repo, or the work must not be committed, take a scoped snapshot before mutation under the absolute session artifact path supplied in the brief. Exclude build trees, environments, datasets and other large generated content. Record the path and contents in the ledger, and remove it only after the authoritative gate passes.

## Stacked-PR output for large tasks

When the task is large, the user may want a GitHub stack as the deliverable. Planning and building local branches does not grant consent to open, update or submit PRs. Before any externally visible stack action, load the `gh-stack` skill and require the same explicit consent as any push or PR operation.

- Plan the intended stack order up front (dependencies define the sequence).
- Assign each change to the earliest layer whose responsibility and public contract require it. A finding attached to an upper PR may belong in a lower layer; record that decision and mark every rewritten upper dirty.
- Implement independent pieces in isolated task worktrees; after approval, the orchestrator places each commit onto the correct ordered branch.
- If the stack is already managed by `gh stack`, the orchestrator is the sole topology owner. Under this runtime, workers edit, verify and optionally commit; the orchestrator performs topology mutations, rebases and pushes. Record topology and every pre-round branch head before delegating, using the inspection commands from the `gh-stack` skill.
- Integrate dependency-ready layers as workers return instead of waiting for the whole fleet. A layer is streamable only when it already contains the settled parent head and needs no rebase or restack. Publish it with the exact branch-scoped Git push from `gh-stack`, never a multi-ref stack command.
- Incremental publication never changes grouping or PR bases. Reconcile server membership, parent ancestry, local/remote heads and the explicit lease first. If the next operation needs rebase, restack, `push`, `sync`, `submit`, `link`, `unstack`, reorder or would move any active worker branch, it is not streamable and remains queued until the normal serialized operation is safe.
- Releasing a branch means its owning worker has committed or deliberately discarded only its run-owned changes and reported a clean handoff. An unreleasable or unexpectedly dirty worktree blocks the restack; never force remove it.
- A lower-layer rewrite dirties every rewritten upper layer. Verify and push the complete moved chain bottom-up; do not let workers push individual layers.
- Decide commit policy before delegation. A worker may amend only the exact layer commit named in its brief; otherwise it creates a new task commit. After return, compare the branch's commit range with the recorded baseline before any restack.
- Never improvise stack topology to repair stale local state. Follow the `gh-stack` skill's sync and recovery procedures.
- Keep each stack entry small and self-consistent (task-sized) so review stays fast and one large entry does not delay the chain.

## Worktree fleet mode

Each writer gets one dedicated branch and worktree. The orchestrator records the absolute path, branch, base SHA, assigned write set and cleanup ownership before spawning it. A worktree not created for this run is read-only.

Before parallel execution:

1. inspect existing worktrees with `git worktree list --porcelain` and record the initial dirty state of every tree involved;

2. for a `gh stack`, record one topology-owner worktree. First check where the installed `gh stack` keeps its state: run `git rev-parse --git-dir` in the worktree and look for the state file there. If the state and its lock are in the worktree-private git dir (in v0.1.0 they are), no other worktree runs `gh stack` and the owner is not removed until recovery is done;

3. confirm branch/task ownership and dependency order;

4. allocate an absolute scratch path and worktree path per task. Put worktrees in `<repo-root>/.worktrees/<slug>` (kebab-case task slug, no branch prefix or slash) when the user's global git ignore covers `.worktrees/`. The user's Copilot `/worktree` setting starts from the remote default branch, so never rely on it for a stack layer or any task that must build on another branch: pass the explicit base SHA or branch to `git worktree add`, as below;

5. identify shared resources such as build caches, ports, databases, GPUs, generated files and lockfiles;

6. create only the missing run-owned worktrees:

   ```bash
   git worktree add -b <task-branch> <absolute-worktree-path> <base-sha>
   # For an existing branch that is not checked out elsewhere:
   git worktree add <absolute-worktree-path> <branch>
   ```

7. bootstrap each worktree according to repository instructions before checks.

During parallel execution, one writer owns each worktree. Tasks with overlapping files or interfaces remain serial even across worktrees. Shared compute and services obey the resource profile. A worker cannot create another writer unless its brief explicitly authorizes nested delegation, gives a sub-cap and assigns separate worktrees/write sets.

Before changing from fleet to main-tree mode, stop all writers, judge and integrate or explicitly reject every returned change, then verify and remove only clean run-owned worktrees. Record the transition and new baseline before the main agent writes.

When a worker returns:

1. verify its branch, commit range and changed paths against the ledger;
2. run the task-specific checks in that worktree;
3. record the verdict and SHA;
4. if dependencies are stable, detach or remove only worktrees whose branches the next integration can move, then integrate immediately;
5. otherwise queue the verified commit without blocking unrelated work;
6. run the authoritative full suite on the final integration tree with no active writers.

Do not delete a worktree that contains uncommitted changes. Cleanup is limited to the exact worktree paths created and recorded for this run:

```bash
git -C <absolute-worktree-path> status --short
git worktree remove <absolute-worktree-path>
```

For ordinary branch integration, workers commit on task branches and the orchestrator applies those commits to the integration branch in dependency order (normally `git cherry-pick <sha>`), stopping on any conflict. For an existing `gh stack`, do not cherry-pick blindly across layers: assign each finding to its owning layer first, let one worker edit that layer branch, and integrate the lowest ancestry-stable layer with the branch-scoped push defined by `gh-stack`. Higher workers may continue in isolated worktrees until their dependencies settle. A layer needing ancestry repair waits for the normal serialized restack. A conflict, unexpected branch move or topology mismatch is a stop condition, not permission to try `link`/`unstack` or another topology verb.

## Crash recovery for git operations

Use the operation journal in [ledger.md](ledger.md) for every git or stack mutation. Record current branch heads, worktree paths and remote SHAs before the operation, then record the intended result.

Before a server topology mutation, also record the server stack ID, ordered PR membership, every PR base ref and merge state using `gh-stack` inspection commands. For a multi-step regroup, journal each step separately. If unstacking applied but relinking is not verified, recovery is blocked until the recorded membership and current server state are reconciled.

Also journal the topology-owner worktree and its private stack-state backup. After a crash, resume stateful `gh stack` commands only from that owner. If it no longer exists, reconcile server membership first and deliberately recreate local tracking through `gh-stack`; never assume another worktree inherited it.

After a crash, inspect before acting:

```bash
git status --short --branch
git worktree list --porcelain
git log --oneline --decorate -n 20
```

- **Worktree creation:** if the path and branch already match the prepared operation, mark it applied. If only one exists or points elsewhere, block and report; do not remove or recreate it automatically.
- **Commit/cherry-pick/rebase:** use `git status` and the journaled before/after SHAs to determine whether the operation completed or stopped on a conflict. Continue or abort only when the operation ID and recorded ownership match.
- **`gh stack`:** compare the current topology and every layer head with the recorded state using `gh-stack`. Follow its lock and recovery procedure; never bypass a lock or invent a topology repair.
- **Push:** compare the remote head with the recorded pre-push and intended post-push SHA. If it equals the intended SHA, record success. If it equals the pre-push SHA, retry only with the recorded explicit lease. Any third value is a concurrent update and blocks the operation.
- **Cleanup:** remove a run-owned worktree only after its commit is integrated, the integration result is verified, and the ledger records both facts.

Never restart a mutation only because its command output was lost. Reconcile the observable state first.
