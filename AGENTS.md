# AGENTS.md — Guide for AI Agents

This file is the authoritative reference for AI agents working on this repo.
Read it before making changes.

> 🔄 **Keep this file in sync.** AGENTS.md is part of the definition of done. Whenever you add,
> rename, or change a feature, CLI flag, method, sparse-checkout rule, test, or behaviour, update
> the relevant sections here **in the same change** so this document never drifts from the code.
> Before finishing any task, re-read AGENTS.md and verify it still matches what you implemented.

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
| `bootstrap` | Thin bash shim: ensures pixi is installed, downloads the Python script if running from a URL, then execs it |
| `.local/bin/dotfiles` | Full Python logic. Uses a **smart shebang** (`pixi exec`) so it needs zero pre-installed Python dependencies |

---

## Repo Structure

```
.
├── .local/bin/dotfiles   # Main Python script (also a dotfile — checked out to ~/.local/bin/)
├── .bashrc.dotfiles.sh   # Source of the managed block injected into the user's ~/.bashrc
├── .bashrc.d/            # Bash snippet directory, sourced by the injected block
├── .config/starship.toml # Starship prompt config
├── .byobu/.tmux.conf     # tmux config
├── .nanorc               # nano config
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
`.pre-commit-config.yaml`, `.shellcheckrc`, `AGENTS.md`

Notable: `.local/bin/dotfiles` is **not excluded** — it is checked out as a dotfile to `~/.local/bin/dotfiles`.

> ℹ️ **`~/.bashrc` is intentionally NOT tracked.** The repo never ships a `.bashrc`.
> Instead, `Bashrc.inject()` merges a managed block (built from `~/.bashrc.dotfiles.sh`)
> into whatever `~/.bashrc` the user already has. This keeps the user's own `.bashrc`
> untouched apart from the block, and keeps `dotfiles git status` completely clean after a
> bootstrap (a tracked `.bashrc` would always show as ` M` because of the injected block).

> ⚠️ **Caveat on rename**: `.local/bin/dotfiles` was previously `bootstrap.py` at the repo root.
> It was renamed and moved so that it is checked out to `~/.local/bin/` on bootstrap,
> making it available on `$PATH` as `dotfiles`. Keep this in mind when updating sparse-checkout
> rules or if tests reference old paths.

---

## Development Setup

All tasks run via `pixi`. No manual pip/venv needed.

```bash
pixi run test        # Run the full pytest suite (~40s for 47 tests)
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
| `TOOLS` | List of packages to install via `pixi global install` (starship, bat, eza, …) |
| `SPARSE_CHECKOUT` | gitignore-style rules written to `~/.dotfiles/info/sparse-checkout`, built from `SPARSE_TRACKED_EXCLUDES` (tracked dev files) plus `SPARSE_UNTRACKED_GUARDS` (gitignored paths kept out of HOME in case they are ever re-added, e.g. `.vscode`) |
| `RollbackStack` | Ordered list of `(description, callable)` pairs; executed in reverse on any exception |
| `Bashrc` | Namespace for `~/.bashrc` injection: builds the managed block from `~/.bashrc.dotfiles.sh` and injects/removes it. Never reads a tracked `.bashrc` (there is none) — operates on the user's own file |
| `DotfilesRepo` | Dataclass: clone, configure sparse checkout, checkout to HOME with proactive backup. Refuses to `rmtree` a non-bare dir on `--overwrite-git-dir` (`_looks_like_bare_repo` guard) |
| `DotfilesRepo._sparse_worktree` | Context manager: checks out a treeish's sparse set into a throwaway work-tree with an isolated `GIT_INDEX_FILE`; yields `(worktree_path, files)` where `files` is what git actually wrote (the effective sparse set) |
| `DotfilesRepo._copy_into_home` | Copies included files from the throwaway work-tree into HOME; only listed files are written, so untracked user files (e.g. `~/.bashrc`) are never deleted |
| `DotfilesRepo._populate_index` | `read-tree --reset HEAD` (no `-u`, no work-tree deletion) then `update-index --skip-worktree` on sparse-excluded files so `dotfiles git status` stays clean and `commit -a` never stages spurious deletions |
| `DotfilesRepo.checkout_to_home` | Returns `(backed_up, checked_out)`. Backs up only genuine user conflicts (skips `managed` files, never overwrites an existing backup), copies from the temp work-tree, then populates the shared index |
| `write_manifest()` | Writes `~/.dotfiles/manifest.json` with UTC timestamp, backup_dir, backed_up, checked_out |
| `notify_backups()` | Rich-formatted warning listing backed-up files |
| `find_pixi()` | Locates pixi binary (`~/.pixi/bin/pixi` → PATH fallback) |
| `install_tools()` | `pixi global install <tool>` for each in TOOLS; falls back to `pixi global upgrade` if already installed. Runs OUTSIDE the rollback-guarded section (a tool failure must not undo a successful install) |
| `uninstall()` | Reads manifest.json, removes checked-out files, restores backups, removes the `.bashrc` block, removes `~/.dotfiles` |
| `_git_head_sha()` / `_warn_update_branch_mismatch()` | Update helpers: capture HEAD sha before/after pull; warn if the checked-out branch is not the remote default |
| `update()` | Rollback-guarded: pull from remote, re-apply sparse rules, re-checkout dotfiles (reusing the previous manifest's `checked_out` as the `managed` set), re-inject the `.bashrc` block, update manifest; detects no-op ("Already up to date") |

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
      → Bashrc.inject: merge the managed block into the user's ~/.bashrc
      → (leave rollback-guarded section)
      → install_tools: pixi global install for each tool in TOOLS
          (OUTSIDE the rollback guard — a tool failure only warns, dotfiles stay)
```

### Sparse checkout / skip-worktree

The checkout intentionally **never** runs `read-tree -u` directly against HOME.
That would flip skip-worktree bits AND delete sparse-excluded files already in
HOME (e.g. the user's `~/.bashrc`). Instead:

1. `_sparse_worktree` checks out the sparse set into a throwaway work-tree using an
   isolated `GIT_INDEX_FILE`, and lists what git actually wrote (the effective set).
2. `_copy_into_home` copies only those included files into HOME.
3. `_populate_index` primes the shared index with `read-tree --reset HEAD` (no `-u`)
   and marks sparse-excluded tracked files `--skip-worktree`.

> Note: recent git (≥ 2.53) already hides sparse-excluded files from `git status` via
> `core.sparseCheckout=true`; the explicit `--skip-worktree` marking keeps behaviour
> correct on older git (e.g. 2.34) too.

### Rollback

`RollbackStack` is populated as mutations happen:
1. After clone → push "remove dotfiles dir"
2. After checkout → push "restore backed-up files and remove checked-out dotfiles"
3. After `.bashrc` injection → push "restore .bashrc" (restores the pre-injection content)

On any unhandled exception, all pushed actions execute in reverse order.
`install_tools` runs **outside** the rollback-guarded section, so a transient tool
failure only warns and never undoes an otherwise-successful dotfiles install.

`update()` is likewise rollback-guarded: it captures the pre-pull work-tree and
`~/.bashrc`, and on failure restores the work-tree (via `_sparse_worktree` at the old
sha + `_copy_into_home` + `_populate_index`) and then the `.bashrc`.

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
  without manifest), `--overwrite` refuses a non-bare dir, update (fails without dotfiles dir /
  reconfigures sparse / preserves original backup / rollback restores `.bashrc` on failure),
  `Bashrc.inject` (append / create-if-missing / update-existing-block), `remove_block`
  (preserves surrounding lines / handles EOF), sparse-checkout has no stale excludes and each
  guard is declared and untracked
- **`test_clone.py`**: bare repo created, sparse-checkout file content and rules, untracked files
  hidden, fails without `--overwrite-git-dir`, succeeds with it
- **`test_checkout.py`**: dotfiles placed in HOME, sparse exclusions respected (dev files absent,
  `.local/bin/dotfiles` present), rollback on clone failure, missing `--repo-uri` exits non-zero,
  git passthrough (`log`, `status`), `git status` hides sparse-excluded files and stays fully
  clean, update after bootstrap, update preserves an existing `.bashrc`

> ⚠️ **Agent note**: When adding or renaming tracked files, update the sparse-checkout assertions in
> `test_clone.py` and `test_checkout.py` accordingly. Remember to commit changes before running
> tests — the git-daemon fixture and `file://` URI both clone from `HEAD`, not the working tree.

---

## What's Still TODO

- [ ] **Multi-machine / OS profiles**: template support for hostname/OS-specific dotfiles (à la chezmoi). Currently all machines receive identical files.
- [ ] **Secrets management**: no mechanism for private files (SSH keys, tokens). Needs integration with an encryption layer (age/gpg) or a secret manager (1Password CLI, Bitwarden CLI).
- [ ] **`dotfiles update`**: implemented as `dotfiles --update` (pull + re-apply sparse + re-checkout). Consider exposing as a subcommand instead of a flag for better discoverability.
- [ ] **`dotfiles add <file>`**: ergonomic shortcut to `dotfiles git add <file> && dotfiles git commit` for adding new dotfiles without knowing the bare-repo git syntax.
- [ ] **Post-checkout hooks**: support for `run_once_*` / `run_always_*` scripts that execute after checkout (e.g. install vim plugins, configure shell integrations).
- [ ] **pre-commit hooks**: `.pre-commit-config.yaml` exists with ruff, pyright, shellcheck hooks. Run `pre-commit install` once to install git hooks. Then use `pixi run hooks` to run all hooks against all files.
