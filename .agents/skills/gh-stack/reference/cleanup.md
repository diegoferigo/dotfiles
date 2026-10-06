# Cleaning up local branches after a stack settles

Long-lived stacks accumulate temporary work branches and backups. Cleanup is destructive, so first decide whether local or remote state is authoritative.

## Decision gate

1. Fetch the remote and compare every stack branch with its remote.
2. If any remote tip was rewritten elsewhere and there is no local-only work, **do not run `gh stack sync`, rebase, push, link or edit `.git/gh-stack`**. Follow [syncing.md](syncing.md) to back up local tips and align them to the authoritative remote.
3. If local-only work exists, stop cleanup and integrate it deliberately before deleting any branch.
4. Use native reconciliation only when branch tips are not divergent. `checkout` may restore tracking; `sync` is limited to ordinary non-divergent reconciliation.

This gate takes precedence over every cleanup step below.

## Cleanup procedure

1. Record the current topology, server grouping, branch heads and dirty state in the operation journal.

2. Snapshot local refs under the ledger artifact path, using its stable external location for a cross-session run, never `/tmp`:

   ```bash
   git for-each-ref --format='%(objectname) %(refname:short)' refs/heads/ \
     > <durable-ledger-artifact>/branch-snapshot.txt
   ```

3. Identify the canonical branches from the reconciled stack state. Never infer authority from a stale `.git/gh-stack` file.

4. Delete only explicitly recorded run-owned duplicates and obsolete backup refs. Never touch unrelated branches.

5. Keep one named pre-split backup until the complete stack has merged and the authoritative state is reachable remotely.

6. Verify branch tips, PR bases and server grouping after cleanup.

Directly editing `.git/gh-stack` is a last-resort recovery action, not routine cleanup. Before doing it, use the durable ledger artifact directory to back up the file, confirm no `gh stack` process holds the lock, and follow the applicable recovery reference. Never delete the only ref that still reaches a force-pushed old tip.
