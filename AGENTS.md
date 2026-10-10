# AGENTS.md — Guide for AI Agents

This file is the authoritative reference for AI agents working on this repo. Read it before making changes.

> 🔄 **Keep this file in sync.** AGENTS.md is part of the definition of done. Whenever you add, rename, or change a feature, CLI flag, method, sparse-checkout rule, test, or behaviour, update the relevant sections here **in the same change** so this document never drifts from the code. Before finishing any task, re-read AGENTS.md and verify it still matches what you implemented.
>
> Keep this guide at the architectural level. Document the repository structure, public CLI, invariants, workflows, and test strategy that an agent needs in order to change the project safely. Do not catalog leaf-level personal configuration such as individual aliases, PATH entries, prompt modules, key bindings, or application preferences unless they affect the bootstrap architecture or require a non-obvious maintenance rule.

______________________________________________________________________

## Project Overview

Personal dotfiles managed with a **bare git repo** pattern:

```
git --git-dir=~/.dotfiles --work-tree=~
```

Files live directly in `$HOME` — no symlinks. The `dotfiles git` command (see below) wraps this.

The bootstrap system is intentionally **two-layer**:

| File                  | Role                                                                                                                                                                                                                                                         |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bootstrap`           | Thin bash shim: ensures pixi is installed, downloads the Python script if running from a URL, then execs it. Runs under `set -u`, so `BASH_SOURCE[0]` is read guarded (`${BASH_SOURCE[0]:-}`) because it is unset when piped from stdin (`curl ... \| bash`) |
| `.local/bin/dotfiles` | Full Python logic. Uses a **smart shebang** (`pixi exec`) so it needs zero pre-installed Python dependencies                                                                                                                                                 |

______________________________________________________________________

## Repo Structure

```
.
├── .local/bin/dotfiles   # Main Python script (also a dotfile — checked out to ~/.local/bin/)
├── .local/libexec/dotfiles/lazy-tools # Python (stdlib, pixi exec shebang) lazy installer for tools pixi cannot provide (hunk), run by `dotfiles --update`
├── .local/libexec/dotfiles/herdr-setup.sh # Copilot hook and agent skill of herdr, run by `dotfiles --update` after the tools
├── .bashrc.environment.sh # Environment-only payload sourced by the first ~/.bashrc block
├── .bashrc.dotfiles.sh   # Source of the managed block injected into the user's ~/.bashrc
├── .bashrc.d/            # Bash snippet directory, sourced by the injected block
├── .config/starship.toml # Starship prompt config
├── .config/herdr/config.toml # herdr settings (default shell bash: herdr can start sh, which prints the bash prompt escapes literally; keys for the worktrunk plugin actions)
├── .config/worktrunk/config.toml # worktrunk user config: worktrees in `<repo>/.worktrees/<branch>`, control_suite `pre-start` hook
├── .config/environment.d/999-pixi.conf # Puts ~/.pixi/bin on the graphical session PATH
├── .config/git/          # Shared git config; work.gitconfig is included for work-org remotes
├── .copilot/settings.json # Copilot CLI user preferences (models, worktree location)
├── .copilot/copilot-instructions.md # Global Copilot CLI custom instructions (language, voice, honesty, plan mode, consent, commits)
├── .copilot/hooks/       # Copilot CLI hooks (herdr-session-title: Copilot session name -> herdr pane title; worktree-guard: denies `git worktree add` when wt is installed)
├── .github/skills/code-review/ # Repo-scoped review skill for high-signal code reviews
├── .agents/skills/       # Agent skills checked out to ~/.agents/skills (working-on-dotfiles, gh-stack, orchestrating-agents, creating-skills, opening-zed, opening-hunk, using-worktrunk)
├── .pixi/                # Workspace-local pixi config (NOT checked out to HOME)
├── .nanorc               # nano config
├── .pre-commit-config.yaml # Local hooks: ruff, pyright, shellcheck, tombi, mdformat (NOT checked out to HOME)
├── pyproject.toml        # Tool config: mdformat, pyright, pytest, ruff, tombi (NOT checked out to HOME)
├── secrets/              # Sparse-excluded age ciphertext
├── bootstrap              # Bash bootstrap shim (NOT checked out to HOME)
├── pixi.toml             # Dev environment + tasks (NOT checked out to HOME)
├── tests/
│   ├── conftest.py       # Fixtures + subprocess helpers
│   ├── helpers.py        # Shared helpers for the unit-test modules
│   ├── test_unit_backup.py # Unit: bootstrap, backups, uninstall, tracked skills, retire_untracked
│   ├── test_unit_update.py # Unit: update, tool install, autostash, local-commit guards
│   ├── test_unit_bashrc.py # Unit: Bashrc block parsing, injection and removal
│   ├── test_unit_secrets.py # Unit: encrypted dotfiles, identity, sparse-index authoring
│   ├── test_unit_sparse_checkout.py # Unit: sparse-checkout rules and index marking
│   ├── test_unit_helpers_cli.py # Unit: small CLI-facing helpers such as describe_error
│   ├── test_unit_fonts.py # Unit: optional pinned Nerd Font install command
│   ├── test_clone.py     # Integration: clone, sparse checkout config
│   ├── test_checkout.py  # Integration: checkout, rollback, git passthrough, CLI errors
│   └── test_shell.py     # Ephemeral shell: throwaway HOME, cache, concurrent sessions, cleanup on signals
└── AGENTS.md             # This file
```

Files excluded from sparse checkout (never appear in `$HOME`): `.devcontainer`, `.github`, `.pixi`, `.pytest_cache`, `.ruff_cache`, `.vscode`, `tests`, `.gitattributes`, `.gitignore`, `.pre-commit-config.yaml`, `pyproject.toml`, `.shellcheckrc`, `AGENTS.md`, `bootstrap`, `LICENSE`, `pixi.lock`, `pixi.toml`, `README.md`, `secrets`

Notable: `.local/bin/dotfiles` is **not excluded** — it is checked out as a dotfile to `~/.local/bin/dotfiles`.

> ℹ️ **`~/.bashrc` is intentionally NOT tracked.** The repo never ships a `.bashrc`. Instead, `Bashrc.inject()` merges two managed blocks into whatever `~/.bashrc` the user already has. The environment-only block is prepended before Ubuntu's non-interactive early return and contains only a guarded source of `~/.bashrc.environment.sh`. The interactive block embeds `~/.bashrc.dotfiles.sh` and remains appended. This keeps the user's own `.bashrc` untouched outside the blocks and keeps `dotfiles git status` completely clean after bootstrap.

> ⚠️ **Caveat on rename**: `.local/bin/dotfiles` was previously `bootstrap.py` at the repo root. It was renamed and moved so that it is checked out to `~/.local/bin/` on bootstrap, making it available on `$PATH` as `dotfiles`. Keep this in mind when updating sparse-checkout rules or if tests reference old paths.

______________________________________________________________________

## Development Setup

All tasks run via `pixi`. No manual pip/venv needed.

```bash
pixi run test        # Run the full pytest suite
pixi run lint        # ruff check
pixi run check       # pyright .local/bin/dotfiles tests/
pixi run hooks       # Run all pre-commit hooks (ruff, pyright, shellcheck, tombi, mdformat)
pixi run toml        # tombi format && tombi lint, TOML is managed by tombi
pixi run md          # mdformat every tracked .md file in the repository
```

Markdown paragraphs are one line each, mdformat enforces this.

Dependencies are `"*"` by design. `pixi.lock` fixes the versions; add a hand-written lower bound only when a feature depends on one.

Checks are run by hand with the pixi tasks above. Do not install git hooks: `pre-commit install` writes into the shared git directory, which every worktree of the checkout uses.

**Always run `lint` and `check` before committing code changes.**

Comments and docstrings describe the current code and its intent. Do not leave notes about previous behavior, removed alternatives or tool versions (for example "no upgrade fallback" or "removed in pixi 0.78"); that belongs in the commit message.

GitHub secret scanning and push protection are enabled on the public repository. `.github/workflows/secret-scan.yml` also runs Gitleaks against full history on every push, pull request, and manual dispatch. Keep `fetch-depth: 0`; a shallow checkout can miss a secret that was committed and removed later. Intended ciphertext under `secrets/**/*.age` is not broadly allowlisted: add only narrow, reviewed exclusions if a verified false positive appears.

> ⚠️ **Critical caveat for agents**: The pytest suite clones from `HEAD` via `git clone --bare`, not from the working tree. **Changes to `.local/bin/dotfiles` must be committed before running tests** or the tests will run against the old version and produce misleading results (e.g. new features appear broken, new sparse-checkout rules are not applied). Commit first, then test.

______________________________________________________________________

## Python style

- Comments explain why, not what.
- Keep comments high-signal: start with an uppercase letter, end with a full stop, keep one sentence per full stop, and stay in plain ASCII.
- Use short signpost comments only where a longer routine needs help to show its steps.
- Prefer descriptive names over short clever ones.
- When a name carries a non-obvious unit, spell the unit out in the identifier.
- Prefer keyword-only arguments in new code, or in code you touch anyway, when a call site would otherwise hide the meaning or unit of a bare value. Do not change existing call signatures only for style.
- Prefer guard clauses and shallow nesting over wrapping the main path in extra `if` blocks.
- Prefer comprehensions and generator expressions over accumulator loops when they stay clear.
- Prefer top-down module order in new code, or in code you touch anyway: put the public entry points first, and keep module-level private helpers near the bottom. In the existing single-file script, keep helpers next to the feature they serve unless you are already refactoring that area.
- In new modules, or in modules you refactor heavily, group private helpers under a `# Private helpers` banner at the bottom. Do not move existing helpers in bulk only to satisfy this rule.
- In new code, or in code you touch anyway, put one blank line after a function or method docstring before the body. Do not add blank lines in bulk only to satisfy this rule.
- Give non-trivial private helpers complete docstrings, not just public functions.
- Keep docstrings plain and readable. Do not use reST markup, rendered-doc syntax, or special Args/Returns sections in this repo.
- Use blank lines as semantic separators inside a function body.
- For tests, in new code or in tests you touch anyway, prefer a one-line docstring per test, a small number of high-signal cases, and private helpers at the bottom. Do not add docstrings in bulk only to satisfy this rule. Tests clone committed HEAD, so commit before running them (see Testing Architecture).

______________________________________________________________________

## Architecture: `.local/bin/dotfiles`

### Shebang

```python
#!/usr/bin/env -S pixi exec --spec git --spec gitpython --spec rich -- python
```

`pixi exec` creates a temporary isolated env on-the-fly with the listed packages. Requires only `pixi` in `PATH` — no system Python, no virtualenv.

### Key components

| Symbol                                                        | Description                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| ------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TOOLS`                                                       | List of packages to install via `pixi global install`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `SPARSE_CHECKOUT`                                             | gitignore-style rules written to `~/.dotfiles/info/sparse-checkout`, built from `SPARSE_TRACKED_EXCLUDES` (tracked dev files) plus `SPARSE_UNTRACKED_GUARDS` (gitignored paths kept out of HOME in case they are ever re-added, e.g. `.vscode`)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `RollbackStack`                                               | Ordered list of `(description, callable)` pairs; executed in reverse on any exception                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `Bashrc`                                                      | Namespace for `~/.bashrc` injection: prepends a compact block that sources `~/.bashrc.environment.sh`, embeds `~/.bashrc.dotfiles.sh` in the appended interactive block, and updates/removes both idempotently. Never reads a tracked `.bashrc` (there is none)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `DotfilesRepo`                                                | Dataclass: clone, configure sparse checkout, checkout to HOME with proactive backup. Refuses to `rmtree` a non-bare dir on `--overwrite-git-dir` (`_looks_like_bare_repo` guard). On `--overwrite-git-dir` it keeps the previous manifest (`previous_manifest`, written back after the clone) and the digests of the previously deployed files (`previous_digests`, read before the old repo is deleted). The bootstrap then reuses the recorded backup dir, passes the previous files that are still as deployed as `managed` (an edited one is a normal conflict and is backed up), passes the secret targets as `forbidden`, rolls back by rewriting the previous content of managed files, and retires previous files the new checkout no longer has |
| `DotfilesRepo._sparse_worktree`                               | Context manager: checks out a treeish's sparse set into a throwaway work-tree with an isolated `GIT_INDEX_FILE`; yields `(worktree_path, files)` where `files` is what git actually wrote (the effective sparse set)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `DotfilesRepo._copy_into_home`                                | Copies included files from the throwaway work-tree into HOME; only listed files are written, so untracked user files (e.g. `~/.bashrc`) are never deleted                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `DotfilesRepo._populate_index`                                | `read-tree --reset HEAD` (no `-u`, no work-tree deletion) then `_mark_skip_worktree` on sparse-excluded files, plus `_mark_assume_unchanged` on the ones the user already has in HOME (path collisions, e.g. their own `~/.gitattributes`), so `dotfiles git status` stays clean and `commit -a` never stages spurious deletions or the user's own content                                                                                                                                                                                                                                                                                                                                                                                               |
| `DotfilesRepo._mark_skip_worktree` / `_mark_assume_unchanged` | Thin wrappers over `_update_index_flag` for `--skip-worktree` / `--assume-unchanged`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| `DotfilesRepo._update_index_flag`                             | Best-effort `update-index <flag>`: on a non-zero batch it retries per file and warns about the paths git refuses to mark, so an index-marking hiccup never aborts (and rolls back) a completed checkout                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| `DotfilesRepo.checkout_to_home`                               | Returns `(backed_up, checked_out)`. Backs up only genuine user conflicts (skips `managed` files, never overwrites an existing backup; identical files are still backed up). A conflict whose path already has a backup and differs from both the backup and the incoming file is moved to a `.local` name via `_unique_local_backup` instead of being overwritten. Copies from the temp work-tree, then populates the shared index                                                                                                                                                                                                                                                                                                                       |
| `write_manifest()`                                            | Writes `~/.dotfiles/manifest.json` with UTC timestamp, backup_dir, backed_up, checked_out, while preserving encrypted deployment metadata                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| `notify_backups()`                                            | Rich-formatted warning listing backed-up files, leaving out backups identical to the file now in HOME (via `_same_content`). `update()` passes only backups missing from the previous manifest, so earlier ones are not re-reported                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `find_pixi()`                                                 | Locates pixi binary (`~/.pixi/bin/pixi` → PATH fallback)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                 |
| `describe_error()`                                            | Turns an exception into a descriptive message: for a `subprocess.CalledProcessError` (which stringifies to just the command and exit code) it unpacks the captured git stderr/stdout, so a failure no longer shows a bare 'returned non-zero exit status 128'. Used at every top-level error print                                                                                                                                                                                                                                                                                                                                                                                                                                                       |
| `install_tools()` / `installed_tools()`                       | `pixi global install <tool>` for each tool in TOOLS that is not already a pixi global environment (`pixi global list --json`; an unreadable list installs every tool)                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `install_nerd_fonts()` / `install_nerd_font()`                | Explicit local-only command: downloads the pinned Nerd Font Mono releases into `~/.local/share/fonts/`, verifies each archive's size and SHA-256 before extraction, installs only the allowlisted mono weights plus the license into each target family directory, then refreshes `fc-cache` when available. Bootstrap, `--update` and `--shell` never call it                                                                                                                                                                                                                                                                                                                                                                                           |
| `run_ephemeral_shell()`                                       | `--shell`: bootstraps into a throwaway `HOME` below `<cache>/diegoferigo-dotfiles/run/<pid>-<id>/home`, runs `bash` there and deletes the run directory on exit, SIGHUP and SIGTERM. Each session has its own run directory, so several shells can run at once. See "Ephemeral shell"                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `install_tools_or_warn()`                                     | Runs `install_tools()` after a bootstrap or a successful `--update`, OUTSIDE the rollback-guarded section: a tool failure only warns and never changes the exit status. Honors `--skip-tools` / `DOTFILES_SKIP_TOOLS`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `install_lazy_tools()` / `install_lazy_tools_or_warn()`       | Runs `~/.local/libexec/dotfiles/lazy-tools install` right after the pixi tools, for tools with no conda-forge package (hunk). Same rules as `install_tools_or_warn`: outside the rollback, warn only, honors `--skip-tools`. The script only installs what is missing: it upgrades or downgrades a managed install only to an explicit `HUNK_VERSION` pin, and leaves an external install alone. It records managed installs under `$XDG_STATE_HOME/dotfiles/lazy-tools/`; `status` and `uninstall` are manual                                                                                                                                                                                                                                           |
| `setup_herdr()` / `setup_herdr_or_warn()`                     | Runs `~/.local/libexec/dotfiles/herdr-setup.sh` right after the lazy tools, with the same rules (outside the rollback, warn only, honors `--skip-tools`). The script does nothing without herdr. It runs `herdr integration install copilot` unless `herdr integration status` reports it as current, rewrites `~/.agents/skills/herdr/SKILL.md` from `herdr --skill` when the content changed, and, when `wt` is installed, installs the worktrunk plugin (`devashish2203/herdr-worktrunk`) at the commit pinned in `worktrunk_plugin_ref` unless `herdr plugin list` already shows it (changing the pin updates every machine). The hook and the skill follow the installed herdr version, so they are generated and never tracked                     |

| `uninstall()` | Reads manifest.json, removes checked-out files, restores backups, removes the `.bashrc` block, removes `~/.dotfiles`, and lists any file left in the backup dir (released originals, `.local` copies). Guarded: aborts (unless `--force`) if a tracked dotfile in HOME has uncommitted edits, which removal would drop | | `_git_head_sha()` / `_fetch_remote_tip()` / `_warn_update_branch_mismatch()` | Update helpers: resolve HEAD sha; fetch the current branch's remote tip (`git clone --bare` leaves `remote.origin.fetch` empty, so a plain fetch only moves `FETCH_HEAD`, never `refs/heads/*`) and return it via `FETCH_HEAD`; warn if the checked-out branch is not the remote default | | `_current_branch()` / `_local_modifications()` / `_discarded_commits()` | Update helpers: current branch name (to move its ref to the remote tip and to restore on abort), tracked dotfiles in HOME modified vs a base sha (the autostash set), and local commits advancing to the remote tip would drop | | `_snapshot_worktree_files()` / `_remote_changed_paths()` / `_reapply_stashed()` / `_unique_local_backup()` / `_notify_autostash()` | Autostash helpers: snapshot HOME content of locally-modified tracked files before the re-checkout; list paths the pull changed; after the re-checkout ignore edits already identical to the incoming file, write untouched edits back, and park genuinely divergent edits in a `.local` backup (never clobbering the pristine bootstrap backup); report what was kept or parked | | `_report_pending_loss()` / `_confirm_override()` | Print the local changes about to be discarded, then prompt `[y/N]` (default no); `--force` short-circuits to yes, a non-interactive shell to no | | `_deployed_digests()` / `_matches_digest()` | SHA-256 of each path's blob at a commit via `git cat-file --batch` (non-blobs and errors are left out), and whether a HOME file is a regular file with that digest | | `_retire_untracked()` / `_notify_untracked()` | Files in the previous `checked_out` that are no longer checked out (on `--update` and on a re-bootstrap): one whose content matches its last deployed digest is removed from HOME and its original is moved back from the backup dir; any other one (edited or unverifiable) stays and becomes the user's, with its original left in the backup. Rollback rewrites the removed bytes and moves the original back. `update()` runs it after `Bashrc.inject`, right before `write_manifest`; report both | | `update()` | Rollback-guarded: fetch the current branch's remote tip and fast-forward the local branch ref to it (a bare clone sets no fetch refspec, so `--update` must move the ref itself), re-apply sparse rules, re-checkout dotfiles (reusing the previous manifest's `checked_out` as the `managed` set), re-inject the `.bashrc` block, update manifest; detects no-op ("Already up to date"). Uncommitted edits to tracked files are autostashed (snapshotted before and compared with the incoming files; converged edits need no action, while divergent collisions are parked in the backup dir). The only destructive case left is a local commit absent from the remote: it is listed and dropped only on confirm or `--force` | | `discover_secrets()` / `_secret_target()` | Read `secrets/home/**/*.age` directly from Git objects and map them below HOME. Sources and targets that escape the fixed layout or overlap the bare repo, backup directory, or age identity metadata are rejected | | `secret_status()` | Reports age, identity, source, manifest, and target state without decrypting or printing content | | `init_secret_identity()` | Generates or imports one shared identity, passphrase-encrypts it through `age`, writes the public recipient, and stages both tracked metadata files | | `_secret_identity()` | Asks `age` to unlock the tracked encrypted identity into a temporary mode `0600` file, falling back to a safe legacy plaintext identity only when the wrapper is absent | | `change_secret_passphrase()` | Unlocks and re-wraps the same identity, atomically replaces the encrypted wrapper, and stages it without re-encrypting secret sources | | `encrypt_secret()` / `_stage_sparse_blob()` | Map a regular HOME file to `secrets/home/<path>.age`, encrypt through the tracked recipient, and stage the Git blob without materializing sparse-excluded ciphertext in HOME | | `apply_secrets()` | Explicitly reconciles ciphertext and plaintext. It unlocks the identity once, decrypts and validates every source first, protects local edits, then atomically deploys and records one target at a time so interrupted runs are resumable | | `_remove_orphaned_secret()` | Removes unchanged plaintext whose ciphertext disappeared, restores a pristine backup, and rejects local edits unless forced | | `_uninstall_secrets()` | Removes unchanged managed plaintext, restores pristine backups, and never removes the age identity |

The pre-interactive environment payload must remain silent and non-interactive. It runs for remote protocol shells used by `scp`, `sftp`, and `rsync`, where stdout output or prompts corrupt the protocol stream.

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
      → install_tools: pixi global install for each tool in TOOLS not yet installed
      → install_lazy_tools: libexec/dotfiles/lazy-tools install (hunk via its upstream installer; only an explicit `HUNK_VERSION` pin changes an installed version)
      → setup_herdr: libexec/dotfiles/herdr-setup.sh (herdr hook for Copilot CLI, the herdr skill and the worktrunk plugin)
          (OUTSIDE the rollback guard — a tool failure only warns, dotfiles stay)
      → report encrypted sources and the explicit `dotfiles secrets apply` command,
        or apply them when `--with-secrets` was requested
```

### Sparse checkout / skip-worktree

The checkout intentionally **never** runs `read-tree -u` directly against HOME. That would flip skip-worktree bits AND delete sparse-excluded files already in HOME (e.g. the user's `~/.bashrc`). Instead:

1. `_sparse_worktree` checks out the sparse set into a throwaway work-tree using an isolated `GIT_INDEX_FILE`, and lists what git actually wrote (the effective set).
2. `_copy_into_home` copies only those included files into HOME.
3. `_populate_index` primes the shared index with `read-tree --reset HEAD` (no `-u`) and marks sparse-excluded tracked files `--skip-worktree` via `_mark_skip_worktree`. It then marks the sparse-excluded paths the user already has in HOME `--assume-unchanged` via `_mark_assume_unchanged` (see the collision note below).

> Note: recent git (≥ 2.53) already hides sparse-excluded files from `git status` via `core.sparseCheckout=true`; the explicit `--skip-worktree` marking keeps behaviour correct on older git (e.g. 2.34) too.

> ⚠️ **User-file collisions**: a sparse-excluded tracked file can share its path with a file the user already owns, because the bare-repo work-tree is `$HOME`. The real case is `~/.gitattributes`: the repo tracks a root `.gitattributes` for GitHub Linguist (extensionless `.local/bin/dotfiles` highlighted as Python, `pixi.lock` marked generated), it is sparse-excluded so it never deploys, but the user's own `~/.gitattributes` sits at the same path. Modern git (≥ 2.53) refuses to set `skip-worktree` on a path that is present and differs from the index, so the file would show as modified forever. `_populate_index` falls back to `--assume-unchanged` for those collisions, which keeps `dotfiles git status` clean and leaves the user's own content untouched (`commit -a` never stages it).

> ⚠️ **Best-effort marking**: the index marking is a nicety on top of `core.sparseCheckout`, so `_update_index_flag` never lets it abort a completed checkout. If the batch `update-index` returns non-zero (a real bootstrap once died with exit 128 here, tearing everything down via rollback), it retries file by file and reports the paths git refuses to mark as a warning instead of raising.

### Rollback

`RollbackStack` is populated as mutations happen:

1. After clone → push "remove dotfiles dir"
2. After checkout → push "restore backed-up files and remove checked-out dotfiles"
3. After both `.bashrc` blocks are injected → push "restore .bashrc" (restores the complete pre-injection content)

On any unhandled exception, all pushed actions execute in reverse order. `install_tools` (through `install_tools_or_warn`) runs **outside** the rollback-guarded section, so a transient tool failure only warns and never undoes an otherwise-successful dotfiles install. `--update` runs it too, after `update()` returned 0 and only then: a tool added to `TOOLS` reaches an existing machine on its next update. The update that first brings the new script still runs the old in-memory code, so the tool appears on the following `--update`.

`update()` is likewise rollback-guarded: it captures the pre-pull work-tree and `~/.bashrc`, and on failure restores the work-tree (via `_sparse_worktree` at the old sha + `_copy_into_home` + `_populate_index`) and then the `.bashrc`.

### Update: fast-forward, autostash, commit guard

`--update` fetches and fast-forwards the local branch, preserves uncommitted edits automatically, and only asks before dropping a local commit.

- **Fast-forward.** `git clone --bare` leaves `remote.origin.fetch` empty, so a plain `git fetch` moves only `FETCH_HEAD`, never `refs/heads/*`. `update()` therefore fetches the current branch explicitly (`_fetch_remote_tip`), then moves `refs/heads/<branch>` to that tip itself with `update-ref`. HEAD is not moved until after the commit guard, so an abort leaves the repo untouched.
- **Autostash.** `_local_modifications(dotfiles_dir, home, base_sha)` lists tracked dotfiles in HOME that differ from the pre-fetch HEAD (so remote-only changes are not mistaken for user edits). Their content is snapshotted with `_snapshot_worktree_files` before the re-checkout and re-applied by `_reapply_stashed` after it. An edit already identical to the incoming file is treated as converged and needs no backup. An edit to a file the pull did not touch is written straight back; a genuinely divergent edit that collides with a pulled change is not merged (the incoming version wins in HOME and the user's edit is parked via `_unique_local_backup`, which never clobbers the pristine bootstrap backup at `backup_dir/rel`). `_notify_autostash` reports what was kept or parked.
- **Commit guard.** `_discarded_commits(dotfiles_dir, kept, dropped)` lists local commits reachable from the pre-fetch sha but not the remote tip. `_confirm_override(force)` prompts `Override local changes and lose them? [y/N]` (default no); `--force` answers yes, a non-interactive shell answers no and asks for `--force`. Only these dropped commits still need consent; uncommitted edits never trigger the prompt.

`uninstall()` keeps the plain local-change guard: it deletes tracked dotfiles (the backup dir only holds the pristine pre-bootstrap copy), so it lists what would be lost and prompts before proceeding.

### Ephemeral shell

`dotfiles --shell` (or `bootstrap --shell`) gives a shell with these dotfiles on a machine that is not yours, writing only below `$XDG_CACHE_HOME/diegoferigo-dotfiles` (default `~/.cache/diegoferigo-dotfiles`):

- `run/<pid>-<id>/home`: the session `HOME`, a normal bootstrap with the tools installed. Removed when the shell exits, and on SIGHUP (dropped ssh) and SIGTERM.
- `cache/`: the rattler and pixi package caches, shared by all sessions and kept between them, on the same filesystem as `run/` so packages are hard-linked. `--no-cache` puts it in the run directory instead. Remove the directory by hand when no session is running.
- `pixi-home/`: the pixi binary that `bootstrap --shell` installs when none is found, with `PIXI_NO_PATH_UPDATE=1` so no shell profile is edited. It also points the rattler and pixi caches to `cache/`, so the `pixi exec` of the shebang does not write to the host's own cache; running the script directly leaves that cache alone.

The session environment unsets `PIXI_HOME`, the `XDG_*` config, data and state variables and the activation variables of the shebang's `pixi exec` (`PIXI_ENVIRONMENT_NAME`, `CONDA_PREFIX`, `CONDA_SHLVL`), which would show up in the prompt, and points `HOME`, `XDG_CACHE_HOME`, `RATTLER_CACHE_DIR` and `PIXI_CACHE_DIR` below the run directory or cache. `_SHARED_AUTH` lists the tool logins kept below `cache/` (`gh`, `gcloud`, `rattler`) and the variable that points the tool at them (`GH_CONFIG_DIR`, `CLOUDSDK_CONFIG`, and `RATTLER_AUTH_FILE` for the `credentials.json` file of rattler-build and pixi). The running shells share them, so a `gh auth login` in one is visible in the others. They hold plaintext tokens, so the last shell to end (under a lock on `base/.lock`) deletes them, and the next `--shell` deletes them when no live run is left (SIGKILL, power loss). To share another tool's login, add a row there. A run killed without a chance to clean up (SIGKILL, power loss) is removed by the next `--shell`: the directory name starts with the owning pid. Secrets are not applied automatically: run `dotfiles secrets apply` inside the shell, which writes plaintext below the throwaway `HOME`. The `dotfiles` function in `.bashrc.d/bare` sources `secrets.sh` into the current shell after a successful `secrets apply`. When stdin is not a terminal (`curl | bash`) the shell gets `/dev/tty` instead, otherwise it would read EOF and exit at once. `ssh` takes the home directory from the passwd entry, not `$HOME`.

### Encrypted dotfiles

Encrypted sources are repository-only files with a deterministic mapping:

```text
secrets/home/<relative-path>.age -> $HOME/<relative-path>
```

`secrets` is sparse-excluded. Discovery uses `git ls-tree`, `git rev-parse`, and `git show` against the bare repository, so ciphertext never needs to appear in HOME. The shared private identity is passphrase-encrypted at `~/.config/dotfiles/age/identity.txt.age`; its public recipient is tracked at `~/.config/dotfiles/age/recipients.txt`. A legacy plaintext identity at `~/.config/dotfiles/age/identity.txt` remains supported, must have no group or world permissions, is used only when the encrypted identity is absent, and is never removed.

Bootstrap and update do not apply secrets by default. They report the tracked source count and leave decryption to `dotfiles secrets apply`, keeping the public lifecycle independent from secret prerequisites and failures. `--with-secrets` explicitly unlocks and applies them after a successful public bootstrap or update.

Explicit apply unlocks the encrypted identity once into a temporary mode `0600` file, decrypts every source before mutation, rejects public-dotfile, manager-state, and identity-metadata collisions, checks deployed plaintext hashes for local edits, and creates pristine backups only once. A first deployment records ownership before replacement; managed updates record the new hash immediately after replacement. A failure can leave earlier targets applied, but re-running the command resumes from the recorded state.

Manifest entries contain only the Git blob SHA and plaintext SHA-256. Removing a ciphertext makes its entry orphaned; explicit apply removes unchanged plaintext and restores its original backup. Modified orphaned plaintext requires `--force`.

`status` classifies targets as current, stale, missing, unmanaged, modified, or orphaned. It does not require the identity and never decrypts. Uninstall uses the recorded plaintext hash, so it also works without the identity.

`dotfiles secrets init` creates and stages the encrypted identity and recipient. `dotfiles secrets change-passphrase` decrypts the wrapper with the old passphrase, re-encrypts the same identity with the new passphrase, and stages only the wrapper. Neither command commits or pushes.

`dotfiles secrets encrypt <path>` performs the inverse deterministic mapping for a regular file below HOME. It encrypts through the tracked public recipient, which must match its committed and indexed bytes. It writes the resulting blob through a locked temporary index, restores its skip-worktree bit, and atomically replaces the shared index only after both operations succeed. It refuses any unresolved repository conflict or a staged change to the same ciphertext. During a rebase, `--resolve` may replace the matching unmerged ciphertext from the selected plaintext only when it is the sole unresolved path; the materialized conflict file and empty `$HOME/secrets` directories are removed. Encrypted bytes are never merged.

`update()` refuses a dirty index before fetching or resetting it. This protects new ciphertext and identity metadata staged by the authoring commands; the user must commit or unstage them first.

#### Updating a secret from a PR branch (agents)

Never print a secret value, not even partially. Show only variable names, lengths or JWT metadata (for example `exp`), and redact values with `sed -E 's/=.*/=<redacted>/'` when displaying a plaintext file.

1. Edit the deployed plaintext in HOME (for example `~/.config/dotfiles/secrets.sh`). When copying a value from another file, pipe it straight into the target without echoing it.

2. In a regular clone or worktree of the repo, check that the tracked recipient matches `~/.config/dotfiles/age/recipients.txt`, then re-encrypt with the same armored format as `dotfiles secrets encrypt`:

   ```bash
   age -a -R .config/dotfiles/age/recipients.txt \
     -o secrets/home/.config/dotfiles/secrets.sh.age \
     ~/.config/dotfiles/secrets.sh
   ```

3. Before pushing, check that the diff touches only `.age` files and that neither the diff nor the commit message contains any value or value prefix (count matches with `grep -c -F`, never print them). Then run gitleaks on the new commits, which needs no install:

   ```bash
   pixi exec --spec go -- go run github.com/zricethezav/gitleaks/v8@latest \
     git --log-opts="origin/main..HEAD" --redact --no-banner .
   ```

4. Decryption needs the identity passphrase, so the agent cannot test it. After the merge, the user runs `dotfiles --update && dotfiles secrets apply`, and `dotfiles secrets status` reports `current`. Until then, `status` reports the edited plaintext as `modified`.

The current design intentionally uses one shared identity and one high-entropy passphrase across trusted machines. Per-machine identities, password-manager CLI integration, passphrase caching, templates, Bash loading, Fish, direnv, and systemd integration are out of scope.

______________________________________________________________________

## Agent skills

`.agents/skills/working-on-dotfiles/SKILL.md` tells agents how to change this repo (checkout, branch, tests, PR flow). It is checked out to `~/.agents/skills/` on every machine, so edit it here and propagate it with `dotfiles --update`. Keep it about workflow only: the architecture stays in this file.

The skill repeats some behavior documented here (the `dotfiles` commands, the update flow, `SPARSE_TRACKED_EXCLUDES`, `TOOLS`, secrets). When the sparse tracked excludes change, keep development-only files such as `pixi.toml` and `pyproject.toml` out of `$HOME` in both places. It must stay in sync: a PR that changes any of that updates the skill in the same PR, and a PR that edits the skill checks it against this file. Every tracked `.agents/skills/*/SKILL.md` is covered by the deployment tests in `tests/test_unit_backup.py`, and every tracked file under a `scripts/` directory must be deployed executable.

`.agents/skills/gh-stack/` is the skill for stacked pull requests with the `gh stack` extension. Its `reference/commands.md`, `reference/stack-design.md` and `reference/troubleshooting.md` keep blocks mirrored word for word from `github/gh-stack` between `upstream:begin` and `upstream:end` markers and reformatted by mdformat, so update them by replacing the block content and running `pixi run md`, not by hand edits of wording.

`.agents/skills/orchestrating-agents/` is the skill for decomposing a feature into tasks, delegating them to subagents and reviewing each result, plus crash-safe review and CI loops. Its `reference/` files hold the task brief, ledger, review rubric, git modes and workflow runtime notes.

`.agents/skills/creating-skills/` is the skill for authoring, structuring and validating agent skills (`SKILL.md`). It ships a `reference/examples.md` and a `templates/SKILL.md.template`.

When you review a change to any agent skill under `.agents/skills/` (a new skill, an edit, or a review of someone else's PR), load `creating-skills` and use it as the quality bar: frontmatter and description, `SKILL.md` length, progressive disclosure, tables of contents, links and consistent terms. Report deviations from it as findings.

`.agents/skills/opening-zed/` opens Zed from an agent session. `scripts/open-zed.sh` runs `zed` locally and, over SSH, prints the `zed ssh://` command to paste on the laptop. Opening it without copy and paste (a reverse SSH hop or a socket forwarded through the SSH connection) is a TODO in the script.

`.agents/skills/opening-hunk/` covers the diff-only review. `scripts/hunk-command.sh` never runs hunk, a TUI: it prints the command to paste in a new terminal, `cd <path> && hunk diff` locally or `ssh -t user@host '...'` over SSH, with the host taken from `SSH_CONNECTION` or `HUNK_REMOTE_HOST`.

`.agents/skills/using-worktrunk/` tells agents to create, find and remove worktrees with `wt` (`wt switch --create <branch> --no-cd`) instead of `git worktree add`, so the worktrunk hooks run. It is advisory; the `worktree-guard` hook enforces it.

Dotfiles manages only these skills under `~/.agents/skills/`: the other skills there are not tracked. A local file at the same path is moved to the backup directory on the first update.

`.github/skills/code-review/` is a repo-scoped review skill for this checkout only. It is not deployed to `~/.agents/skills/`.

## Prompt font

The starship prompt uses Nerd Font glyphs (the git branch icon and the arrow tip), so it expects the terminal to use a Nerd Font Mono family.

No font binaries are tracked in git. Install the pinned local families explicitly with `dotfiles fonts install`, which downloads JetBrainsMono Nerd Font Mono into `~/.local/share/fonts/JetBrainsMonoNerdFontMono/` and FiraCode Nerd Font Mono into `~/.local/share/fonts/FiraCodeNerdFontMono/` on the current machine only.

On the first `dotfiles --update` after the tracked FiraCode files are removed upstream, `update()` retires the released path only when its digest still matches the last deployed tracked file and then restores the original from `~/.dotfiles_backup` if one exists, otherwise it leaves any unverifiable or edited file in place as the user's file. This is implemented in `.local/bin/dotfiles` at `_retire_untracked()` and exercised by `tests/test_checkout.py::test_update_keeps_a_file_untracked_with_rm_cached`.

After that first update, run `dotfiles fonts install`, then `fc-cache -f`, then restart the terminal or editor: terminals and Zed load fonts at startup.

Selecting the font in the terminal profile is a dconf setting and is not tracked. The `.uuid` files that fontconfig writes into the font directories are generated state: never commit them. Write glyphs above the Basic Multilingual Plane (for example U+F062C) literally, and Private Use Area glyphs inside the BMP (for example U+F0DA) as `\uXXXX` escapes in TOML, because some editors drop them.

## Copilot CLI settings

`.copilot/settings.json` holds the user preferences of the Copilot CLI (default and subagent models, footer, `worktreePathTemplate`, `worktreeBaseRef`). The CLI rewrites this file itself when a setting changes, so copy the updated file back into the repo and commit it, or `dotfiles --update` will restore the tracked one. Keep it machine-neutral: no `allowedUrls` entries and no accounts. The sibling `~/.copilot/config.json` is managed state (login, trusted folders, caches) and is never tracked.

`.copilot/copilot-instructions.md` holds the global custom instructions, loaded in every session on every machine. Keep it short and free of repo-specific rules: the repo's own instructions win on conflicts. It must not contradict the tracked skills (for example `orchestrating-agents` owns the delegation policy).

`.copilot/hooks/` tracks only `herdr-session-title.{py,json}` and `worktree-guard.{py,json}`. `worktree-guard` is a `preToolUse` hook that denies a `git worktree add` run through the bash tool and points to `wt switch --create <branch> --no-cd`. It denies only when `wt` is installed, and `DOTFILES_ALLOW_GIT_WORKTREE=1` bypasses it. Copilot treats a crashing `preToolUse` command as a denial, so the script swallows every error and the JSON launcher ends with `|| true`. `herdr-session-title` copies the session name from `workspace.yaml` to the herdr pane title (shown on split pane borders) and is a no-op outside herdr. It only runs on session events, so the title can be stale or missing for panes opened before the hook and after `/rename`. The sidebar therefore shows `terminal_title_stripped` (`.config/herdr/config.toml`), the OSC title Copilot keeps current itself. The JSON calls the script through `$HOME`, so it stays machine-neutral. Never track `herdr-agent-state.sh` (herdr rewrites it on updates) or `orca.json` (generated by Orca with absolute paths).

## Git configuration

One machine uses two GitHub accounts, so the identity (commit email and token) is chosen per repo from its remotes, not from the active `gh` account:

| File                         | Tracked | Content                                                                                                                                                                                                                                                                    |
| ---------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `.config/git/config`         | yes     | Shared settings, personal identity as default, one `includeIf hasconfig:remote.*.url:...` per work org                                                                                                                                                                     |
| `.config/git/work.gitconfig` | yes     | Work email and credential helper for `diegoferigo-rai`                                                                                                                                                                                                                     |
| `.config/gh/config.yml`      | yes     | gh settings (protocol, aliases: `co` and the `gh stack` families view, sync, add, submit, navigation, composites and `st-prune`, which deletes old `backup/` branches after asking; the full list is in the YAML comments). Never track `hosts.yml`: it lists the accounts |
| `.config/git/attributes`     | yes     | Global attributes: routes every language mergiraf supports to the `mergiraf` merge driver                                                                                                                                                                                  |
| `.config/git/ignore`         | yes     | Global ignore: `.worktrees/`, so worktrees created inside any repo never show up as untracked                                                                                                                                                                              |
| `~/.gitconfig`               | **no**  | Machine-specific settings: signing, whatever tools write with `git config --global`                                                                                                                                                                                        |

- Git reads `~/.gitconfig` last, so it must not set `user.email` or credential helpers: they would override the per-org identity.
- To add a work org, add one more `includeIf` block. It matches any remote, not only `origin`.
- The helpers call `gh auth token -u <user>` through `~/.pixi/bin/gh`, so `gh` must stay in `TOOLS`.
- `includeIf hasconfig` needs git >= 2.36. Older git ignores it silently and uses the personal identity everywhere. `git` is not in `TOOLS` on purpose; GUI clients find `~/.pixi/bin` through `.config/environment.d/999-pixi.conf`, whose `999-` prefix must sort after Ubuntu's `99-environment.conf`, which resets `PATH`.
- Never track tokens: they live in the `gh` keyring.
- `ghp` (in `.bashrc.d/functions`) runs `gh` as `diegoferigo` for one command, without switching the active account.
- Merges use `mergiraf` (syntax-aware) through the `[merge "mergiraf"]` driver in `.config/git/config` and the rules in `.config/git/attributes`, so `mergiraf` must stay in `TOOLS`. Regenerate the rules with `mergiraf languages --gitattributes` after a mergiraf upgrade. Disable it for one command with `mergiraf=0 git <command>`. Repo `.gitattributes` rules (e.g. `pixi.lock merge=binary`) take precedence over the global file.
- `merge.conflictStyle = zdiff3` needs git >= 2.35.
- `~/.gitattributes` is not a global attributes file for git: in HOME it is only this repo's own `.gitattributes`.

## Worktrunk

`wt` comes from `TOOLS`. `.config/worktrunk/config.toml` sets a global `worktree-path` of `{{ repo_path }}/.worktrees/{{ branch | sanitize }}`, the same location as `worktreePathTemplate` in `.copilot/settings.json` and covered by the global ignore. Per-repository settings go under `[projects."github.com/<owner>/<repo>"]` in that file, which scopes a hook to one repository and needs no approval, unlike a committed `.config/wt.toml`. Never track `~/.config/worktrunk/approvals.toml`.

The worktrunk plugin for herdr is installed by `herdr-setup.sh`, and `.config/herdr/config.toml` binds its pickers (`prefix+shift+g` replaces herdr's built-in new-worktree key, which runs no worktrunk hooks). Its action ids come from `herdr plugin action list`.

`.bashrc.d/tools` evaluates `wt config shell init bash` so `wt switch` can change the shell directory. Never run `wt config shell install`: it writes into `~/.bashrc`, which the managed blocks own.

______________________________________________________________________

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

# Shell with the dotfiles in a throwaway HOME (see "Ephemeral shell")
dotfiles --repo-uri https://github.com/user/dotfiles.git --shell
dotfiles --repo-uri ... --shell --no-cache   # also delete the package cache on exit

# Clone a branch instead of the remote default (bootstrap sets it from GITHUB_BRANCH)
dotfiles --repo-uri https://github.com/user/dotfiles.git --branch my-branch

# Clone from a local checkout but keep following another repo (bootstrap sets it from the
# checkout's origin when CODESPACES=true)
dotfiles --repo-uri file:///path/to/checkout --origin-uri https://github.com/user/dotfiles.git

# Pull latest changes, re-apply sparse-checkout, re-checkout dotfiles
dotfiles --update
dotfiles --update --with-secrets

# Install the optional local Nerd Font Mono families on this machine only
dotfiles fonts install
dotfiles fonts install jetbrains-mono
dotfiles fonts install fira-code

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
- `DOTFILES_SKIP_TOOLS` — when set, skip the pixi global tool install on bootstrap and `--update` (same as `--skip-tools`)
- `DOTFILES_BRANCH` — default `--branch`
- `DOTFILES_ORIGIN` — default `--origin-uri`: URL set as `origin` of the clone instead of the source
- `CODESPACES` (read by `bootstrap` only) — when `true`, a local-checkout bootstrap passes that checkout's `origin` URL as `--origin-uri`
- `GITHUB_BRANCH` (read by `bootstrap` only) — branch to download the script from, and to clone when downloading it

______________________________________________________________________

## Testing Architecture

### Two test tiers

| Tier        | Files                                                                                                                                                                                       | Mechanism                            | Speed          |
| ----------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------ | -------------- |
| Unit        | `helpers.py`, `test_unit_backup.py`, `test_unit_update.py`, `test_unit_bashrc.py`, `test_unit_secrets.py`, `test_unit_sparse_checkout.py`, `test_unit_helpers_cli.py`, `test_unit_fonts.py` | Direct module import via `importlib` | ~0.05s/test    |
| Integration | `test_clone.py`, `test_checkout.py`, `test_shell.py`                                                                                                                                        | Subprocess + pixi exec shebang       | ~0.6–1.6s/test |

### Fixtures (`tests/conftest.py`)

| Fixture / Helper                          | Scope    | Description                                                                                                                                                                          |
| ----------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `fake_home`                               | function | Isolated `$HOME` in a tempdir, seeded with the regular dotfiles from `/etc/skel` (directories skipped: CI runners keep multi-GB toolchains there), with the pixi binary symlinked in |
| `git_daemon_url`                          | session  | Starts a real `git daemon` serving a bare clone on a random port; yields `git://127.0.0.1:<port>/dotfiles`                                                                           |
| `repo_uri`                                | function | Parametrized: `local` (`file://<REPO_ROOT>`) and `git-daemon`; covers both bootstrap use cases                                                                                       |
| `dotfiles_module`                         | session  | Loads `.local/bin/dotfiles` as a Python module via `importlib` (no subprocess); used by the `test_unit_*` modules                                                                    |
| `run_bootstrap(home, uri, *args)`         | —        | Subprocess helper: runs dotfiles with `--repo-uri` injected, `PIXI_HOME` + `PIXI_CACHE_DIR` preserved                                                                                |
| `run_dotfiles(home, *args, unset_env=())` | —        | Subprocess helper: arbitrary args, no `--repo-uri` injection; `unset_env` removes specific env vars                                                                                  |

**Cache preservation**: `run_bootstrap` and `run_dotfiles` explicitly set `PIXI_HOME` and `PIXI_CACHE_DIR` to the real user values, preventing `pixi exec` from treating the fake `$HOME` as a cold cache on every subprocess call.

**Tool install skipped**: both helpers set `DOTFILES_SKIP_TOOLS=1` so the subprocesses do not run `pixi global install` for the tools (the `--update` tests in `test_unit_update.py` stub `install_tools` instead). This keeps the tests fast and, crucially, avoids exhausting the CI runner disk with a global env per tool on every bootstrap invocation.

### Two bootstrap scenarios under test

| Param        | URI                               | Simulates                                                      |
| ------------ | --------------------------------- | -------------------------------------------------------------- |
| `local`      | `file:///path/to/repo`            | User cloned the repo and runs `./bootstrap` manually           |
| `git-daemon` | `git://127.0.0.1:<port>/dotfiles` | User runs `curl .../bootstrap \| bash` (fetches from a server) |

### Test files

- **`helpers.py`**: shared direct-call bootstrap, git, fake-age, sparse-checkout and Bashrc helpers reused by the unit modules
- **`test_unit_backup.py`**: bootstrap backups and manifests, tracked-skill and skill-script deployment, uninstall safeguards, overwrite guard, and `_retire_untracked`
- **`test_skill_scripts.py`**: `open-zed.sh` and `hunk-command.sh` with a fake `ps` and `hunk` (local and SSH output, port, IPv6, quoting, hostile host override)
- **`test_worktree_guard.py`**: the worktree guard hook (denied and allowed commands, other tools, both payload shapes, no wt, the escape variable, the launcher never failing)
- **`test_herdr_session_title.py`**: the herdr session title hook against a fake herdr socket (control characters stripped, an empty name clears the title)
- **`test_herdr_setup.py`**: the `herdr-setup` bash script against a fake `herdr` (hook install, idempotence, skill rewrite, bad skill output, partial failure, no herdr, worktrunk plugin install at the pinned ref, idempotence, replacement of another ref, no `wt`)
- **`test_lazy_tools.py`**: the `lazy-tools` Python script, imported and called directly against a fake `file://` installer (install and record, no upgrade, external install, pin, failing installers including a missing curl, stale record, uninstall, status, the file:// gate), plus one subprocess test for the exit codes and the https-only curl options
- **`test_unit_update.py`**: `--update`, tool install, sparse reconfiguration, autostash behavior, local-commit guard, and related helpers
- **`test_unit_bashrc.py`**: `Bashrc.read_blocks`, injection ordering, create-if-missing behavior, idempotent replacement, and block removal
- **`test_unit_secrets.py`**: encrypted-source path validation, identity setup and rotation, sparse-index secret authoring, apply/uninstall flows, orphan handling, and backup-directory consistency
- **`test_unit_sparse_checkout.py`**: stale sparse-checkout detection, declared guards, best-effort skip-worktree marking, and user-file collision handling
- **`test_unit_helpers_cli.py`**: `describe_error` formatting for `CalledProcessError` and plain exceptions
- **`test_unit_fonts.py`**: `dotfiles fonts install`, pinned-archive verification, allowlisted extraction for both prompt fonts, CLI selection, and missing `fc-cache`
- **`test_shell.py`**: cache base follows `XDG_CACHE_HOME`, the session environment redirects the per-user paths, the sweep removes only runs of dead processes, a shell runs in a throwaway `HOME` with the dotfiles and leaves no run behind (with and without the cache), the shell exit status is returned, `--branch` clones the requested branch, and two concurrent sessions each clean up on SIGHUP and SIGTERM
- **`test_clone.py`**: bare repo created, sparse-checkout file content and rules, untracked files hidden, fails without `--overwrite-git-dir`, succeeds with it, a linked worktree is a valid local source, bootstrap shim piped from stdin has no `BASH_SOURCE` unbound-variable error
- **`test_checkout.py`**: dotfiles placed in HOME, sparse exclusions respected (dev files absent, `.local/bin/dotfiles` present), explicit encrypted apply after bootstrap without an identity, passphrase-unlocked bootstrap and update, ciphertext update preserving stale plaintext until explicit apply, rollback on clone failure, missing `--repo-uri` exits non-zero, git passthrough (`log`, `status`), `git status` hides sparse-excluded files and stays fully clean, a pre-existing user `~/.gitattributes` is not reported as modified, the pre-interactive environment exposes Pixi and decrypted Bash secrets before an Ubuntu-style early return without duplicating PATH, update after bootstrap, update preserves an existing `.bashrc`, update autostashes an uncommitted edit, update keeps an autostashed edit visible in `git status`, update removes a file that is no longer tracked and restores its original (or just removes it without one), keeps a modified one with its original in the backup, saves a re-tracked user file next to an earlier backup as `.local`, keeps a file untracked with `git rm --cached`, a re-bootstrap with `--overwrite-git-dir` does not back up its own files, backs up an edited one, and restores the original of a file the new checkout no longer has, and uninstall lists the backups it does not restore

> ⚠️ **Agent note**: When adding or renaming tracked files, update the sparse-checkout assertions in `test_clone.py` and `test_checkout.py` accordingly. Remember to commit changes before running tests — the git-daemon fixture and `file://` URI both clone from `HEAD`, not the working tree.
