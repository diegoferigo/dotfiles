---
name: working-on-dotfiles
description: >-
  How to change Diego's dotfiles repo (diegoferigo/dotfiles): where the checkout
  lives, branch and test flow, keeping AGENTS.md in sync, opening the draft PR
  and propagating a merged change with `dotfiles --update`. Use whenever the user
  asks to edit, add or fix a dotfile, shell snippet in .bashrc.d, git or gh
  config, a TOOLS entry, a tracked setting, the dotfiles script or its tests, or
  to propagate dotfiles changes, even if they do not name this skill.
---

# Working on the dotfiles repo

The repo is checked out to `$HOME` by `.local/bin/dotfiles` as a bare repo in
`~/.dotfiles`. `AGENTS.md`, `tests` and the other development files are
sparse-excluded, so they are not in `$HOME`: on a machine with no regular clone
they are missing. Read `AGENTS.md` first, it is the source of truth for the
architecture, the tests and the tracked files: from `~/git/dotfiles` when it
exists, otherwise with `dotfiles git show HEAD:AGENTS.md`. This skill only covers
how Diego wants changes made.

## Flow

1. Work in the main checkout `~/git/dotfiles`, not in a worktree. If it does not
   exist, create it with `GH_TOKEN=$(gh auth token -u diegoferigo) gh repo clone
   diegoferigo/dotfiles ~/git/dotfiles` (never use `~/.dotfiles` or `$HOME` as the
   work tree). Start from a fresh branch: `git fetch && git switch -c diegoferigo/<slug> origin/main`.
   Never edit the tracked files in `$HOME` directly: they are copies, and the
   next `dotfiles --update` overwrites them.
2. Make one logical change per commit. Commit before running the tests: they
   clone from `HEAD`.
3. Run, in the checkout, `pixi run lint`, `pixi run check`, `pixi run hooks` and
   `pixi run test`. Report the commands and the summary lines, not "tests pass".
4. For a bug fix, prove the new test catches it: put the previous
   `.local/bin/dotfiles` (`git show origin/main:.local/bin/dotfiles`) in place and
   confirm the new test fails, then restore the file.
5. Keep `AGENTS.md` in sync with the code, the tests and the tracked files (key
   components table, test list, the file tree and the Git configuration table).
6. Push and open the draft PR only when asked, following the
   `acting-as-diegoferigo` skill. The repo is personal: use the `diegoferigo`
   account (`GH_TOKEN=$(gh auth token -u diegoferigo) gh ...`) even when another
   account is active. Sections: "What change is being made", "Why this change is
   being made", "Tested".
7. After Diego merges, update the checkout (`git switch main && git pull`), run
   `dotfiles --update`, then delete the local branch.

## Rules that are easy to get wrong

- A new tool goes in `TOOLS` in `.local/bin/dotfiles` and in the `TOOLS` row of
  `AGENTS.md`. `dotfiles --update` installs only what is in `TOOLS`: a tool added
  by hand with `pixi global` is not propagated, and a tool removed from the list is
  not uninstalled.
- Never skip a backup in `checkout_to_home`: rollback and uninstall restore only
  the files listed in `backed_up`. A backup whose content equals the incoming file
  is made but not reported (`_same_content`).
- Tracked files outside the sparse-excluded set (see `SPARSE_TRACKED_EXCLUDES`)
  land in `$HOME`. Development files such as `tests`, `AGENTS.md` and `pixi.toml`
  must stay excluded.
- Keep tracked config machine-neutral: no accounts, tokens, host names or
  `allowedUrls` entries. `~/.gitconfig` and `~/.copilot/config.json` stay local.
- Secrets live in `secrets/` as age ciphertext. Never print or commit a
  plaintext value, and never echo one in a command output.
- `dotfiles --update` only reports encrypted sources. Decrypting needs the age
  passphrase, so `dotfiles secrets apply` (and `secrets init`,
  `secrets change-passphrase`) is a manual command that Diego runs himself in his
  terminal: an agent cannot supply the prompt. Do not try to run it. Ask Diego to
  run it only when a secret change actually needs it (a new or changed `*.age`
  source, or `dotfiles secrets status` reporting a pending one), say which command
  and why, and carry on with the rest meanwhile. After `--update`, run
  `dotfiles secrets status` (it never decrypts or prints content) and ask only if
  it shows something to apply.
- A change to a setting that a tool rewrites itself (for example
  `~/.copilot/settings.json`) must be copied back into the repo and committed,
  otherwise `dotfiles --update` restores the tracked version.

## Checks specific to a snippet in `.bashrc.d`

Load it in a clean interactive shell before committing:
`bash -ic 'type <name>; complete -p <command>'`. Guard optional tools with
`[[ -n $(type -t <tool>) ]]` like the existing snippets, and put tool init and
completion in `.bashrc.d/tools`.
