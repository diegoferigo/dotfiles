# AGENTS.md — Guide for AI Agents

This file is the authoritative reference for AI agents working on this repo.
Read it before making changes.

> 🔄 **Keep this file in sync.** AGENTS.md is part of the definition of done. Whenever you add,
> rename, or change a feature, CLI flag, method, sparse-checkout rule, test, or behaviour, update
> the relevant sections here **in the same change** so this document never drifts from the code.
> Before finishing any task, re-read AGENTS.md and verify it still matches what you implemented.
>
> Keep this guide at the architectural level. Document the repository structure, public CLI,
> invariants, workflows, and test strategy that an agent needs in order to change the project
> safely. Do not catalog leaf-level personal configuration such as individual aliases, PATH
> entries, prompt modules, key bindings, or application preferences unless they affect the
> bootstrap architecture or require a non-obvious maintenance rule.

---

## Project Overview

Personal dotfiles managed with a **bare git repo** pattern:

```
git --git-dir=~/.dotfiles --work-tree=~
```

Files live directly in `$HOME` — no symlinks. The `dotfiles git` command (see below) wraps this.

The bootstrap system is intentionally **two-layer**:

| File | Role |
|---|---|
| `bootstrap` | Thin bash shim: ensures pixi is installed, downloads the Python script if running from a URL, then execs it. Runs under `set -u`, so `BASH_SOURCE[0]` is read guarded (`${BASH_SOURCE[0]:-}`) because it is unset when piped from stdin (`curl ... \| bash`) |
| `.local/bin/dotfiles` | Full Python logic. Uses a **smart shebang** (`pixi exec`) so it needs zero pre-installed Python dependencies |

---

## Repo Structure

```
.
├── .local/bin/dotfiles   # Main Python script (also a dotfile — checked out to ~/.local/bin/)
├── .bashrc.environment.sh # Environment-only payload injected first into ~/.bashrc
├── .bashrc.dotfiles.sh   # Source of the managed block injected into the user's ~/.bashrc
├── .bashrc.d/            # Bash snippet directory, sourced by the injected block
├── .config/starship.toml # Starship prompt config
├── .byobu/.tmux.conf     # tmux config
├── .nanorc               # nano config
├── secrets/              # Sparse-excluded age ciphertext
├── bootstrap              # Bash bootstrap shim (NOT checked out to HOME)
├── pixi.toml             # Dev environment + tasks (NOT checked out to HOME)
├── tests/
│   ├── conftest.py       # Fixtures + subprocess helpers
│   ├── test_unit.py      # Fast unit tests (direct module calls, no subprocess)
│   ├── test_clone.py     # Integration: clone, sparse checkout config
│   └── test_checkout.py  # Integration: checkout, rollback, git passthrough, CLI errors
└── AGENTS.md             # This file
```

Files excluded from sparse checkout (never appear in `$HOME`):
`.devcontainer`, `.github`, `.pixi`, `.pytest_cache`, `.ruff_cache`, `.vscode`,
`tests`, `bootstrap`, `LICENSE`, `pixi.lock`, `pixi.toml`, `README.md`,
`.pre-commit-config.yaml`, `.shellcheckrc`, `AGENTS.md`, `secrets`

Notable: `.local/bin/dotfiles` is **not excluded** — it is checked out as a dotfile to `~/.local/bin/dotfiles`.

> ℹ️ **`~/.bashrc` is intentionally NOT tracked.** The repo never ships a `.bashrc`.
> Instead, `Bashrc.inject()` merges two managed blocks into whatever `~/.bashrc` the user
> already has. The environment-only block is built from `~/.bashrc.environment.sh` and is
> prepended before Ubuntu's non-interactive early return. The interactive block is built from
> `~/.bashrc.dotfiles.sh` and remains appended. This keeps the user's own `.bashrc` untouched
> outside the blocks and keeps `dotfiles git status` completely clean after bootstrap.

> ⚠️ **Caveat on rename**: `.local/bin/dotfiles` was previously `bootstrap.py` at the repo root.
> It was renamed and moved so that it is checked out to `~/.local/bin/` on bootstrap,
> making it available on `$PATH` as `dotfiles`. Keep this in mind when updating sparse-checkout
> rules or if tests reference old paths.

---

## Development Setup

All tasks run via `pixi`. No manual pip/venv needed.

```bash
pixi run test        # Run the full pytest suite
pixi run lint        # ruff check .local/bin/dotfiles tests/
pixi run check       # pyright .local/bin/dotfiles tests/
pixi run hooks        # Run all pre-commit hooks (ruff, pyright, shellcheck)
```

**Always run `lint` and `check` before committing code changes.**

> ⚠️ **Critical caveat for agents**: The pytest suite clones from `HEAD` via `git clone --bare`, not
> from the working tree. **Changes to `.local/bin/dotfiles` must be committed before running tests**
> or the tests will run against the old version and produce misleading results (e.g. new features
> appear broken, new sparse-checkout rules are not applied). Commit first, then test.

---

## Architecture: `.local/bin/dotfiles`

### Shebang

```python
#!/usr/bin/env -S pixi exec --spec git --spec gitpython --spec rich -- python
```

`pixi exec` creates a temporary isolated env on-the-fly with the listed packages.
Requires only `pixi` in `PATH` — no system Python, no virtualenv.

### Key components

| Symbol | Description |
|---|---|
| `TOOLS` | List of packages to install via `pixi global install` (starship, bat, eza, fzf, fd-find, zoxide, difftastic, age) |
| `SPARSE_CHECKOUT` | gitignore-style rules written to `~/.dotfiles/info/sparse-checkout`, built from `SPARSE_TRACKED_EXCLUDES` (tracked dev files) plus `SPARSE_UNTRACKED_GUARDS` (gitignored paths kept out of HOME in case they are ever re-added, e.g. `.vscode`) |
| `RollbackStack` | Ordered list of `(description, callable)` pairs; executed in reverse on any exception |
| `Bashrc` | Namespace for `~/.bashrc` injection: builds an environment block from `~/.bashrc.environment.sh` and prepends it, builds the interactive block from `~/.bashrc.dotfiles.sh` and appends it, and updates/removes both idempotently. Never reads a tracked `.bashrc` (there is none) |
| `DotfilesRepo` | Dataclass: clone, configure sparse checkout, checkout to HOME with proactive backup. Refuses to `rmtree` a non-bare dir on `--overwrite-git-dir` (`_looks_like_bare_repo` guard) |
| `DotfilesRepo._sparse_worktree` | Context manager: checks out a treeish's sparse set into a throwaway work-tree with an isolated `GIT_INDEX_FILE`; yields `(worktree_path, files)` where `files` is what git actually wrote (the effective sparse set) |
| `DotfilesRepo._copy_into_home` | Copies included files from the throwaway work-tree into HOME; only listed files are written, so untracked user files (e.g. `~/.bashrc`) are never deleted |
| `DotfilesRepo._populate_index` | `read-tree --reset HEAD` (no `-u`, no work-tree deletion) then `_mark_skip_worktree` on sparse-excluded files, plus `_mark_assume_unchanged` on the ones the user already has in HOME (path collisions, e.g. their own `~/.gitattributes`), so `dotfiles git status` stays clean and `commit -a` never stages spurious deletions or the user's own content |
| `DotfilesRepo._mark_skip_worktree` / `_mark_assume_unchanged` | Thin wrappers over `_update_index_flag` for `--skip-worktree` / `--assume-unchanged` |
| `DotfilesRepo._update_index_flag` | Best-effort `update-index <flag>`: on a non-zero batch it retries per file and warns about the paths git refuses to mark, so an index-marking hiccup never aborts (and rolls back) a completed checkout |
| `DotfilesRepo.checkout_to_home` | Returns `(backed_up, checked_out)`. Backs up only genuine user conflicts (skips `managed` files, never overwrites an existing backup), copies from the temp work-tree, then populates the shared index |
| `write_manifest()` | Writes `~/.dotfiles/manifest.json` with UTC timestamp, backup_dir, backed_up, checked_out, while preserving encrypted deployment metadata |
| `notify_backups()` | Rich-formatted warning listing backed-up files |
| `find_pixi()` | Locates pixi binary (`~/.pixi/bin/pixi` → PATH fallback) |
| `describe_error()` | Turns an exception into a descriptive message: for a `subprocess.CalledProcessError` (which stringifies to just the command and exit code) it unpacks the captured git stderr/stdout, so a failure no longer shows a bare 'returned non-zero exit status 128'. Used at every top-level error print |
| `install_tools()` | `pixi global install <tool>` for each in TOOLS; falls back to `pixi global upgrade` if already installed. Runs OUTSIDE the rollback-guarded section (a tool failure must not undo a successful install) |
| `uninstall()` | Reads manifest.json, removes checked-out files, restores backups, removes the `.bashrc` block, removes `~/.dotfiles`. Guarded: aborts (unless `--force`) if a tracked dotfile in HOME has uncommitted edits, which removal would drop |
| `_git_head_sha()` / `_fetch_remote_tip()` / `_warn_update_branch_mismatch()` | Update helpers: resolve HEAD sha; fetch the current branch's remote tip (`git clone --bare` leaves `remote.origin.fetch` empty, so a plain fetch only moves `FETCH_HEAD`, never `refs/heads/*`) and return it via `FETCH_HEAD`; warn if the checked-out branch is not the remote default |
| `_current_branch()` / `_local_modifications()` / `_discarded_commits()` | Update helpers: current branch name (to move its ref to the remote tip and to restore on abort), tracked dotfiles in HOME modified vs a base sha (the autostash set), and local commits advancing to the remote tip would drop |
| `_snapshot_worktree_files()` / `_remote_changed_paths()` / `_reapply_stashed()` / `_unique_local_backup()` / `_notify_autostash()` | Autostash helpers: snapshot HOME content of locally-modified tracked files before the re-checkout; list paths the pull changed; after the re-checkout ignore edits already identical to the incoming file, write untouched edits back, and park genuinely divergent edits in a `.local` backup (never clobbering the pristine bootstrap backup); report what was kept or parked |
| `_report_pending_loss()` / `_confirm_override()` | Print the local changes about to be discarded, then prompt `[y/N]` (default no); `--force` short-circuits to yes, a non-interactive shell to no |
| `update()` | Rollback-guarded: fetch the current branch's remote tip and fast-forward the local branch ref to it (a bare clone sets no fetch refspec, so `--update` must move the ref itself), re-apply sparse rules, re-checkout dotfiles (reusing the previous manifest's `checked_out` as the `managed` set), re-inject the `.bashrc` block, update manifest; detects no-op ("Already up to date"). Uncommitted edits to tracked files are autostashed (snapshotted before and compared with the incoming files; converged edits need no action, while divergent collisions are parked in the backup dir). The only destructive case left is a local commit absent from the remote: it is listed and dropped only on confirm or `--force` |
| `discover_secrets()` / `_secret_target()` | Read `secrets/home/**/*.age` directly from Git objects and map them below HOME. Sources and targets that escape the fixed layout or overlap the bare repo, backup directory, or age identity metadata are rejected |
| `secret_status()` | Reports age, identity, source, manifest, and target state without decrypting or printing content |
| `init_secret_identity()` | Generates or imports one shared identity, passphrase-encrypts it through `age`, writes the public recipient, and stages both tracked metadata files |
| `_secret_identity()` | Asks `age` to unlock the tracked encrypted identity into a temporary mode `0600` file, falling back to a safe legacy plaintext identity only when the wrapper is absent |
| `change_secret_passphrase()` | Unlocks and re-wraps the same identity, atomically replaces the encrypted wrapper, and stages it without re-encrypting secret sources |
| `encrypt_secret()` / `_stage_sparse_blob()` | Map a regular HOME file to `secrets/home/<path>.age`, encrypt through the tracked recipient, and stage the Git blob without materializing sparse-excluded ciphertext in HOME |
| `apply_secrets()` | Explicitly reconciles ciphertext and plaintext. It unlocks the identity once, decrypts and validates every source first, protects local edits, then atomically deploys and records one target at a time so interrupted runs are resumable |
| `_remove_orphaned_secret()` | Removes unchanged plaintext whose ciphertext disappeared, restores a pristine backup, and rejects local edits unless forced |
| `_uninstall_secrets()` | Removes unchanged managed plaintext, restores pristine backups, and never removes the age identity |

### Bootstrap flow (happy path)

```
bootstrap
  → ensure pixi in PATH
  → download .local/bin/dotfiles (or use local copy)
  → exec .local/bin/dotfiles --repo-uri <URI>
      → DotfilesRepo.__post_init__: resolve URI, git clone --bare → ~/.dotfiles
          (guard: refuse to rmtree a non-bare dir when --overwrite-git-dir)
      → configure_sparse_checkout: write rules + set showUntrackedFiles=no
      → checkout_to_home: check out the sparse set into a throwaway work-tree,
          back up genuine user conflicts, copy included files into HOME
          (never deletes excluded files), then populate the shared index with
          skip-worktree bits on the excluded files
      → write_manifest: ~/.dotfiles/manifest.json (backed_up + checked_out)
      → notify_backups: rich output
      → Bashrc.inject: prepend the environment block and append the interactive
        block in the user's ~/.bashrc
      → (leave rollback-guarded section)
      → install_tools: pixi global install for each tool in TOOLS
          (OUTSIDE the rollback guard — a tool failure only warns, dotfiles stay)
      → report encrypted sources and the explicit `dotfiles secrets apply` command,
        or apply them when `--with-secrets` was requested
```

### Sparse checkout / skip-worktree

The checkout intentionally **never** runs `read-tree -u` directly against HOME.
That would flip skip-worktree bits AND delete sparse-excluded files already in
HOME (e.g. the user's `~/.bashrc`). Instead:

1. `_sparse_worktree` checks out the sparse set into a throwaway work-tree using an
   isolated `GIT_INDEX_FILE`, and lists what git actually wrote (the effective set).
2. `_copy_into_home` copies only those included files into HOME.
3. `_populate_index` primes the shared index with `read-tree --reset HEAD` (no `-u`)
   and marks sparse-excluded tracked files `--skip-worktree` via
   `_mark_skip_worktree`. It then marks the sparse-excluded paths the user already
   has in HOME `--assume-unchanged` via `_mark_assume_unchanged` (see the collision
   note below).

> Note: recent git (≥ 2.53) already hides sparse-excluded files from `git status` via
> `core.sparseCheckout=true`; the explicit `--skip-worktree` marking keeps behaviour
> correct on older git (e.g. 2.34) too.

> ⚠️ **User-file collisions**: a sparse-excluded tracked file can share its path with
> a file the user already owns, because the bare-repo work-tree is `$HOME`. The real
> case is `~/.gitattributes`: the repo tracks a root `.gitattributes` for GitHub
> Linguist (extensionless `.local/bin/dotfiles` highlighted as Python, `pixi.lock`
> marked generated), it is sparse-excluded so it never deploys, but the user's own
> `~/.gitattributes` sits at the same path. Modern git (≥ 2.53) refuses to set
> `skip-worktree` on a path that is present and differs from the index, so the file
> would show as modified forever. `_populate_index` falls back to `--assume-unchanged`
> for those collisions, which keeps `dotfiles git status` clean and leaves the user's
> own content untouched (`commit -a` never stages it).

> ⚠️ **Best-effort marking**: the index marking is a nicety on top of
> `core.sparseCheckout`, so `_update_index_flag` never lets it abort a completed
> checkout. If the batch `update-index` returns non-zero (a real bootstrap once died
> with exit 128 here, tearing everything down via rollback), it retries file by file
> and reports the paths git refuses to mark as a warning instead of raising.

### Rollback

`RollbackStack` is populated as mutations happen:
1. After clone → push "remove dotfiles dir"
2. After checkout → push "restore backed-up files and remove checked-out dotfiles"
3. After both `.bashrc` blocks are injected → push "restore .bashrc" (restores the
   complete pre-injection content)

On any unhandled exception, all pushed actions execute in reverse order.
`install_tools` runs **outside** the rollback-guarded section, so a transient tool
failure only warns and never undoes an otherwise-successful dotfiles install.

`update()` is likewise rollback-guarded: it captures the pre-pull work-tree and
`~/.bashrc`, and on failure restores the work-tree (via `_sparse_worktree` at the old
sha + `_copy_into_home` + `_populate_index`) and then the `.bashrc`.

### Update: fast-forward, autostash, commit guard

`--update` fetches and fast-forwards the local branch, preserves uncommitted
edits automatically, and only asks before dropping a local commit.

- **Fast-forward.** `git clone --bare` leaves `remote.origin.fetch` empty, so a
  plain `git fetch` moves only `FETCH_HEAD`, never `refs/heads/*`. `update()`
  therefore fetches the current branch explicitly (`_fetch_remote_tip`), then
  moves `refs/heads/<branch>` to that tip itself with `update-ref`. HEAD is not
  moved until after the commit guard, so an abort leaves the repo untouched.
- **Autostash.** `_local_modifications(dotfiles_dir, home, base_sha)` lists
  tracked dotfiles in HOME that differ from the pre-fetch HEAD (so remote-only
  changes are not mistaken for user edits). Their content is snapshotted with
  `_snapshot_worktree_files` before the re-checkout and re-applied by
  `_reapply_stashed` after it. An edit already identical to the incoming file is
  treated as converged and needs no backup. An edit to a file the pull did not
  touch is written straight back; a genuinely divergent edit that collides with
  a pulled change is not merged (the incoming version wins in HOME and the
  user's edit is parked via `_unique_local_backup`, which never clobbers the
  pristine bootstrap backup at `backup_dir/rel`). `_notify_autostash` reports
  what was kept or parked.
- **Commit guard.** `_discarded_commits(dotfiles_dir, kept, dropped)` lists
  local commits reachable from the pre-fetch sha but not the remote tip.
  `_confirm_override(force)` prompts `Override local changes and lose them?
  [y/N]` (default no); `--force` answers yes, a non-interactive shell answers no
  and asks for `--force`. Only these dropped commits still need consent;
  uncommitted edits never trigger the prompt.

`uninstall()` keeps the plain local-change guard: it deletes tracked dotfiles
(the backup dir only holds the pristine pre-bootstrap copy), so it lists what
would be lost and prompts before proceeding.

### Encrypted dotfiles

Encrypted sources are repository-only files with a deterministic mapping:

```text
secrets/home/<relative-path>.age -> $HOME/<relative-path>
```

`secrets` is sparse-excluded. Discovery uses `git ls-tree`, `git rev-parse`, and
`git show` against the bare repository, so ciphertext never needs to appear in
HOME. The shared private identity is passphrase-encrypted at
`~/.config/dotfiles/age/identity.txt.age`; its public recipient is tracked at
`~/.config/dotfiles/age/recipients.txt`. A legacy plaintext identity at
`~/.config/dotfiles/age/identity.txt` remains supported, must have no group or
world permissions, is used only when the encrypted identity is absent, and is
never removed.

Bootstrap and update do not apply secrets by default. They report the tracked
source count and leave decryption to `dotfiles secrets apply`, keeping the public
lifecycle independent from secret prerequisites and failures. `--with-secrets`
explicitly unlocks and applies them after a successful public bootstrap or
update.

Explicit apply unlocks the encrypted identity once into a temporary mode `0600`
file, decrypts every source before mutation, rejects public-dotfile,
manager-state, and identity-metadata collisions, checks deployed plaintext
hashes for local edits, and creates pristine backups only once. A first
deployment records ownership before replacement; managed updates record the new
hash immediately after replacement. A failure can leave earlier targets
applied, but re-running the command resumes from the recorded state.

Manifest entries contain only the Git blob SHA and plaintext SHA-256. Removing a
ciphertext makes its entry orphaned; explicit apply removes unchanged plaintext
and restores its original backup. Modified orphaned plaintext requires
`--force`.

`status` classifies targets as current, stale, missing, unmanaged, modified, or
orphaned. It does not require the identity and never decrypts. Uninstall uses the
recorded plaintext hash, so it also works without the identity.

`dotfiles secrets init` creates and stages the encrypted identity and recipient.
`dotfiles secrets change-passphrase` decrypts the wrapper with the old
passphrase, re-encrypts the same identity with the new passphrase, and stages
only the wrapper. Neither command commits or pushes.

`dotfiles secrets encrypt <path>` performs the inverse deterministic mapping for
a regular file below HOME. It encrypts through the tracked public recipient,
which must match its committed and indexed bytes. It writes the resulting blob
through a locked temporary index, restores its skip-worktree bit, and atomically
replaces the shared index only after both operations succeed. It refuses any
unresolved repository conflict or a staged change to the same ciphertext.
During a rebase, `--resolve` may replace the matching unmerged ciphertext from
the selected plaintext only when it is the sole unresolved path; the
materialized conflict file and empty `$HOME/secrets` directories are removed.
Encrypted bytes are never merged.

`update()` refuses a dirty index before fetching or resetting it. This protects
new ciphertext and identity metadata staged by the authoring commands; the user
must commit or unstage them first.

The current design intentionally uses one shared identity and one high-entropy
passphrase across trusted machines. Per-machine identities, password-manager
CLI integration, passphrase caching, templates, Bash loading, Fish, direnv, and
systemd integration are out of scope.

---

## CLI Interface

```bash
# Bootstrap from a git remote
dotfiles --repo-uri https://github.com/user/dotfiles.git

# Bootstrap from a local clone (useful during development)
dotfiles --repo-uri file:///path/to/repo --overwrite-git-dir

# Bootstrap without installing the pixi global tools (used by the test suite)
dotfiles --repo-uri file:///path/to/repo --skip-tools

# Remove dotfiles and restore backups
dotfiles --uninstall

# Pull latest changes, re-apply sparse-checkout, re-checkout dotfiles
dotfiles --update
dotfiles --update --with-secrets

# Override local changes without prompting
# --update: drop local commits not on the remote (uncommitted edits are autostashed regardless)
# --uninstall: proceed even with uncommitted edits to tracked files
dotfiles --update --force
dotfiles --uninstall --force

# Inspect or deploy encrypted dotfiles
dotfiles secrets init
dotfiles secrets encrypt ~/.ssh/config.d/rai.conf
dotfiles secrets encrypt --resolve ~/.ssh/config.d/rai.conf
dotfiles secrets status
dotfiles secrets apply
dotfiles secrets apply --force
dotfiles secrets change-passphrase

# Run git against the bare dotfiles repo (works after bootstrap)
dotfiles git status
dotfiles git diff
dotfiles git add ~/.nanorc
dotfiles git commit -m "update nanorc"
dotfiles git log --oneline
dotfiles git --help       # shows git's own help
```

Environment variables:
- `DOTFILES_REPO` — default `--repo-uri`
- `DOTFILES_DIR` — override bare repo location (default: `~/.dotfiles`)
- `BACKUP_DIR` — override backup location (default: `~/.dotfiles_backup`)
- `DOTFILES_SKIP_TOOLS` — when set, skip the pixi global tool install (same as `--skip-tools`)

---

## Testing Architecture

### Two test tiers

| Tier | Files | Mechanism | Speed |
|---|---|---|---|
| Unit | `test_unit.py` | Direct module import via `importlib` | ~0.05s/test |
| Integration | `test_clone.py`, `test_checkout.py` | Subprocess + pixi exec shebang | ~0.6–1.6s/test |

### Fixtures (`tests/conftest.py`)

| Fixture / Helper | Scope | Description |
|---|---|---|
| `fake_home` | function | Isolated `$HOME` in a tempdir, seeded with the regular dotfiles from `/etc/skel` (directories skipped: CI runners keep multi-GB toolchains there), with the pixi binary symlinked in |
| `git_daemon_url` | session | Starts a real `git daemon` serving a bare clone on a random port; yields `git://127.0.0.1:<port>/dotfiles` |
| `repo_uri` | function | Parametrized: `local` (`file://<REPO_ROOT>`) and `git-daemon`; covers both bootstrap use cases |
| `dotfiles_module` | session | Loads `.local/bin/dotfiles` as a Python module via `importlib` (no subprocess); used by `test_unit.py` |
| `run_bootstrap(home, uri, *args)` | — | Subprocess helper: runs dotfiles with `--repo-uri` injected, `PIXI_HOME` + `PIXI_CACHE_DIR` preserved |
| `run_dotfiles(home, *args, unset_env=())` | — | Subprocess helper: arbitrary args, no `--repo-uri` injection; `unset_env` removes specific env vars |

**Cache preservation**: `run_bootstrap` and `run_dotfiles` explicitly set `PIXI_HOME` and
`PIXI_CACHE_DIR` to the real user values, preventing `pixi exec` from treating the fake `$HOME`
as a cold cache on every subprocess call.

**Tool install skipped**: both helpers set `DOTFILES_SKIP_TOOLS=1` so the subprocesses do not run
`pixi global install` for the tools (nothing in the suite asserts on them). This keeps the tests
fast and, crucially, avoids exhausting the CI runner disk with a global env per tool on every
bootstrap invocation.

### Two bootstrap scenarios under test

| Param | URI | Simulates |
|---|---|---|
| `local` | `file:///path/to/repo` | User cloned the repo and runs `./bootstrap` manually |
| `git-daemon` | `git://127.0.0.1:<port>/dotfiles` | User runs `curl .../bootstrap \| bash` (fetches from a server) |

### Test files

- **`test_unit.py`**: backup, no-backup-dir-without-conflicts, existing-`.bashrc` preserved,
  dev files excluded from HOME, manifest written, manifest records backed-up, rollback undoes
  checkout, uninstall (removes dotfiles / restores backups / removes `.bashrc` block / fails
  without manifest / aborts on local edits / `--force` overrides), `--overwrite` refuses a
  non-bare dir, update (fails without dotfiles dir / reconfigures sparse / preserves original
  backup / rollback restores `.bashrc` on failure / fast-forwards HEAD to the remote tip /
  autostashes an uncommitted edit / accepts a local edit already identical to
  the incoming file without creating a conflict backup /
  guards a local commit and drops it only with `--force`), autostash internals
  (`_reapply_stashed` writes an untouched edit back, parks a colliding edit, `_unique_local_backup`
  never clobbers the pristine backup, a local commit is dropped while an edit is preserved),
  local-change guard (`_confirm_override` force / non-interactive, `_discarded_commits` lists a
  dropped commit), `Bashrc.inject` (environment prepend / interactive append /
  create-if-missing / idempotent replacement), `remove_blocks` (removes both while preserving
  user content), sparse-checkout has no stale
  excludes and each guard is declared and untracked, skip-worktree marking survives an
  unmarkable path (warns instead of aborting), a pre-existing user file at a sparse-excluded
  path (`~/.gitattributes`) is hidden via `--assume-unchanged`, `describe_error` unpacks a
  `CalledProcessError` stderr and passes plain exceptions through; encrypted-source path
  validation and Git discovery, manager-state collision rejection, missing identity,
  passphrase-encrypted identity initialization and unlocking, passphrase rotation,
  direct sparse-index secret authoring and conflict rejection/resolution,
  resumable per-target deployment and manifest updates, mode `0600`, first-time backup and
  uninstall restoration, local-edit guard and `--force`, orphan removal, interruption recovery,
  and backup-directory consistency
- **`test_clone.py`**: bare repo created, sparse-checkout file content and rules, untracked files
  hidden, fails without `--overwrite-git-dir`, succeeds with it, bootstrap shim piped from stdin
  has no `BASH_SOURCE` unbound-variable error
- **`test_checkout.py`**: dotfiles placed in HOME, sparse exclusions respected (dev files absent,
  `.local/bin/dotfiles` present), explicit encrypted apply after bootstrap without an identity,
  passphrase-unlocked bootstrap and update, ciphertext update preserving stale plaintext until
  explicit apply, rollback on clone failure,
  missing `--repo-uri` exits non-zero,
  git passthrough (`log`, `status`), `git status` hides sparse-excluded files and stays fully
  clean, a pre-existing user `~/.gitattributes` is not reported as modified, the
  pre-interactive environment exposes Pixi and decrypted Bash secrets before an Ubuntu-style
  early return without duplicating PATH, update after bootstrap, update preserves an existing
  `.bashrc`, update autostashes an uncommitted edit, update keeps an autostashed edit visible
  in `git status`

> ⚠️ **Agent note**: When adding or renaming tracked files, update the sparse-checkout assertions in
> `test_clone.py` and `test_checkout.py` accordingly. Remember to commit changes before running
> tests — the git-daemon fixture and `file://` URI both clone from `HEAD`, not the working tree.

---

## What's Still TODO

- [ ] **Multi-machine / OS profiles**: template support for hostname/OS-specific dotfiles (à la chezmoi). Currently all machines receive identical files.
- [ ] **`dotfiles update`**: implemented as `dotfiles --update` (pull + re-apply sparse + re-checkout). Consider exposing as a subcommand instead of a flag for better discoverability.
- [ ] **`dotfiles add <file>`**: ergonomic shortcut to `dotfiles git add <file> && dotfiles git commit` for adding new dotfiles without knowing the bare-repo git syntax.
- [ ] **Post-checkout hooks**: support for `run_once_*` / `run_always_*` scripts that execute after checkout (e.g. install vim plugins, configure shell integrations).
- [ ] **pre-commit hooks**: `.pre-commit-config.yaml` exists with ruff, pyright, shellcheck hooks. Run `pre-commit install` once to install git hooks. Then use `pixi run hooks` to run all hooks against all files.
