# Large generated / lockfile ("fake binary") files

## Contents

- [Core rules: treat lockfiles as binary](#core-rules-treat-lockfiles-as-binary)
- [Rebasing a stack where EVERY layer has its own lock commit](#rebasing-a-stack-where-every-layer-has-its-own-lock-commit)

## Core rules: treat lockfiles as binary

Files like `pixi.lock` (also `poetry.lock`, `Cargo.lock`, `package-lock.json`, `*.pb`, vendored blobs) are huge machine-generated artifacts. They diff line-by-line but a three-way merge is meaningless, treat them like **binary**:

- **Keep the lockfile in its own single, standalone commit**, never spread lock changes across layers. Every other layer's diff stays readable and all merge pain is isolated to one commit.

- **If there is exactly one commit updating the lockfile, keep it as the last commit of the branch (the head/tip), and keep it there after every rebase or reorder.** With the lock commit at the tip, the post-rebase recovery is always the same trivial two-step: regenerate the lockfile from source, then `git commit --amend --no-edit` (a squash into the tip). No `gh stack modify`, no interactive rebase, the final commit always ends up carrying the correct, freshly generated lockfile.

- **Regenerate; never hand-merge.** The lockfile derives from a source of truth (`pixi.toml`), so a manual conflict resolution is never authoritative.

- **On a rebase/reorder conflict in the lockfile:** don't reconcile hunks. Pick either side to clear it, continue, then **regenerate from source and squash** the regeneration back into the lock commit:

  ```bash
  # conflict reported in pixi.lock during gh stack rebase
  git checkout --ours pixi.lock     # content is thrown away anyway
  git add pixi.lock
  gh stack rebase --continue        # finish the cascade

  pixi lock                         # regenerate from the rebased pixi.toml
  git add pixi.lock
  git commit --amend --no-edit      # squash regen into the single lock commit
  # (works directly because the lock commit is kept at the tip; if it isn't,
  #  fold the regen in via `gh stack modify` and move it back to the tip)
  ```

- Optionally add `pixi.lock -diff merge=ours` (or `binary`) in `.gitattributes` so git stops trying to merge it, but the regenerate-and-squash flow is what guarantees a correct lockfile.

- Because the final lockfile is regenerated from the final `pixi.toml`, the tree still matches the target; verify with `git diff --quiet <TARGET>` (regenerate again if a nondeterministic lock produced noise).

## Rebasing a stack where EVERY layer has its own lock commit

A single `gh stack rebase` cascades through all layers in one go, so you only get to resolve conflicts, you never get a chance to *regenerate* the lock before the next layer is replayed on top of it. Each layer then inherits a lockfile that is inconsistent with its own `pixi.toml`, and the conflicts compound upward.

**Rebase one layer at a time instead**, using `--downstack` from each branch (it is a no-op for the layers below that are already correct):

```bash
gh stack bottom
for each layer bottom → top:
    gh stack rebase --downstack          # rebases only up to the current branch
    # on conflict (always pixi.lock):
    git checkout --theirs pixi.lock      # keep OUR version, see note below
    git add pixi.lock
    gh stack rebase --continue
    pixi lock                            # regenerate against THIS layer's pixi.toml
    git add pixi.lock && git commit --amend --no-edit
    gh stack up
```

- **Use `--theirs`, not `--ours`, during a rebase conflict.** In a rebase the sides are swapped: `--ours` is the new base, `--theirs` is the commit being replayed. Taking `--ours` can make the lock commit *empty*, and git then drops it, you lose the commit that the amend-regenerate step needs. `--theirs` keeps it non-empty; the content is thrown away by `pixi lock` anyway.
- Check the regenerated diff (`git diff --stat pixi.lock` before amending). A small diff means the resolution was already close; a large one is still fine, the solve is authoritative, but a *huge* one is a hint that the conflict resolution had picked the wrong side.
- **`pixi lock`'s `+ (conda) <pkg>` output after a rebase onto a *newer* trunk is EXPECTED, not a bug, it is trunk's own new dependencies being absorbed.** When a regen shows unfamiliar packages appearing/disappearing (e.g. a whole cluster like `kivy`/`sdl2_*`/`portaudio` from a new "add tools" PR on trunk), do **not** assume the split/rebase dropped or corrupted them. **Verify against trunk before reacting:** the package's presence in your regenerated lock must match `origin/main`'s lock, not your branch's *pre-rebase* lock (which predates trunk's advance):
  ```bash
  grep -c '<pkg>' pixi.lock                         # your regenerated lock
  git show origin/main:pixi.lock | grep -c '<pkg>'  # trunk, should MATCH
  git show refs/backup/rebase/<branch>:pixi.lock | grep -c '<pkg>'  # pre-rebase (may differ)
  git log --oneline -S'<pkg>' <old-boundary>..origin/main -- pixi.lock  # which trunk commit introduced it
  ```
  If your lock matches `origin/main` and the diff is explained by a trunk commit, the regen is correct. A mismatch *with trunk* is the real red flag.
- `gh stack rebase --continue` (and `git rebase --continue`) may open `$EDITOR` and fail in a non-interactive shell with `error: there was a problem with the editor 'nano'`. Prefix with `GIT_EDITOR=true` to accept the existing message.
- **After `gh stack rebase --downstack --continue`, the message `All branches in stack rebased locally with main` is misleading, `--downstack` still only rebases up to the *current* branch.** Do not trust the banner; verify with `gh stack view --json` (upper layers keep their old SHAs) and `git merge-base --is-ancestor <lower> <upper>` per pair. The upper layers are intentionally left at their old tips so you regenerate their locks one at a time as you `gh stack up`, which is exactly what this per-layer loop wants.
