# General stack lessons

This file keeps only cross-repository lessons that are not command procedures. For exact commands and recovery, use the dedicated references:

- reorder or middle insertion: [reordering-and-conflicts.md](reordering-and-conflicts.md);
- remote branches rewritten elsewhere: [syncing.md](syncing.md);
- base merged into trunk: [trunk-retargeting.md](trunk-retargeting.md);
- generated lockfiles: [lockfiles.md](lockfiles.md);
- version-specific or superseded behavior: [legacy.md](legacy.md).

## Split incrementally

Design the layer order and expected file/hunk ownership before creating branches. Add one layer, verify it on its own base, then continue. A layer is ready only when:

- its diff contains only its intended concern;
- it builds and tests without relying on a higher layer;
- its public contract is sufficient for direct consumers;
- the cumulative top tree equals the intended feature plus documented mechanical changes.

Review hunks, not only file lists. Mechanical additions can land in the correct file but the wrong layer.

## Derive scope from the real base

Use the stack's recorded fork point or parent, not a moving upstream branch, to derive each layer's files and commit range. Independent upstream changes can make an unchanged file look modified and create empty or misplaced layers.

After every history rewrite, inspect each range explicitly:

```bash
git log --oneline <parent>..<layer>
git diff --stat <parent>...<layer>
```

Stop if a layer gained a lower layer's old tip or another unexpected commit. Prefer amend or the canonical stack tooling over reset-and-rebuild rewrites, which make old range boundaries easy to replay accidentally.

## Verify every layer in its own environment

The top branch passing does not prove lower PRs are self-contained. Run the applicable build, tests and lint on every layer using that checkout's dependency and toolchain definition. Launch environment-managed commands from outside a previously activated environment so switching branches cannot reuse stale dependencies.

If a lower layer only works because a higher layer introduces global build infrastructure, move the required contract or dependency into the earliest layer that consumes it.

## Preserve rollback before mutation

Before amend, rebase, reorder, restack or force-push:

1. record every local and remote head;
2. create backup refs for commits not safely reachable elsewhere;
3. snapshot server membership and PR bases for topology changes;
4. journal the exact operation and explicit leases.

Proceed only when the prior state can be restored without reconstructing it from conversation history. Keep backups until the remote result and required checks are verified.

## Recheck review surfaces after rewrites

A lower-layer change can alter every upper PR diff even when their branch names stay the same. After a cascade:

- verify each PR base and head;
- re-run layer checks;
- re-read affected PR descriptions;
- reopen stale assessments;
- run the holistic merge-base-to-top review before convergence.

Keep fixes in the earliest layer that owns their contract. Do not duplicate a lower fix in an upper layer merely to satisfy a comment attached there.
