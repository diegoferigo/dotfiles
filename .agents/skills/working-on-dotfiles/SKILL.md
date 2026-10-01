---
name: working-on-dotfiles
description: >-
  Guides agents changing Diego's dotfiles repo (diegoferigo/dotfiles): where the
  checkout lives, branch and test flow, keeping AGENTS.md in sync, opening the
  draft PR and propagating a merged change with `dotfiles --update`. Use whenever the user
  asks to edit, add or fix a dotfile, shell snippet in .bashrc.d, git or gh
  config, a TOOLS entry, a tracked setting, the dotfiles script or its tests, or
  to propagate dotfiles changes, even if they do not name this skill.
---

# Working on the dotfiles repo

The repo is checked out to `$HOME` by `.local/bin/dotfiles` as a bare repo in
`~/.dotfiles`. `AGENTS.md`, `tests` and the other development files are
sparse-excluded, so they are not in `$HOME`. Pick the workflow by the task:

- **Operate** (no clone needed): propagate merged changes, check the state, read
  the docs. Run `dotfiles --update`, `dotfiles secrets status` and
  `dotfiles git status|log|show`. Read `AGENTS.md` with
  `dotfiles git show HEAD:AGENTS.md`.
- **Small config change** (no clone needed): a change that only edits tracked
  files that exist in `$HOME` (for example a `.bashrc.d` snippet or a git config
  key) and needs no `AGENTS.md` update. Never use it for the files under
  `.agents/skills/`: they are documented in `AGENTS.md`. Run `dotfiles git switch main` (stop if it fails because of local changes) and
  `dotfiles --update`: the update follows the checked-out branch, so a stale
  feature branch would be updated instead of `main`. Then
  `dotfiles git switch -c diegoferigo/<slug>`, edit the file in `$HOME`,
  `dotfiles git add <file>` and `dotfiles git commit` (never `commit -a` with
  unrelated edits). Push and open the PR only when asked, as in step 6 below, with
  `dotfiles git push -u origin <branch>`. Afterwards switch back with
  `dotfiles git switch main` before the next `dotfiles --update`. Check
  `dotfiles git status` and the staged diff before committing.
- **Change** (needs the clone): anything that touches `.local/bin/dotfiles`, the
  tests, `AGENTS.md`, `TOOLS` or the tracked files table, because `tests`,
  `pixi.toml`, `pyproject.toml` and `AGENTS.md` are not in `$HOME` and the checks and the doc sync
  cannot run without them. Follow the flow below, and read `AGENTS.md` from the
  clone first: it is the source of truth for the architecture, the tests and the
  tracked files.

Do not leave a tracked edit in `$HOME` uncommitted. `dotfiles --update` snapshots
and reapplies it, but when the incoming change conflicts it keeps the incoming
version and parks the local edit under `~/.dotfiles_backup/...local`.

This skill only covers how Diego wants changes made.

## Flow for a change

1. Work in a regular clone of the repo, not in a worktree and never in
   `~/.dotfiles` or `$HOME`. Use the existing clone (usually `~/git/dotfiles`);
   if there is none, clone the URL printed by `dotfiles git remote get-url origin`.
   Start from a fresh branch:
   `git fetch && git switch -c diegoferigo/<slug> origin/main`.
2. Make one logical change per commit. Commit before running the tests: they
   clone from `HEAD`.
3. Run, in the checkout, `pixi run lint`, `pixi run check`, `pixi run hooks`,
   `pixi run md` and `pixi run test`. `pixi run md` formats every tracked `.md`
   file in the repo. Report the commands and the summary lines, not "tests pass".
4. For a bug fix, prove the new test catches it: temporarily replace
   `.local/bin/dotfiles` with `git show origin/main:.local/bin/dotfiles`, confirm
   the new test fails, then restore the file.
5. Keep `AGENTS.md` in sync with the code, the tests and the tracked files (key
   components table, test list, the file tree and the Git configuration table).
6. Push and open the PR only when asked, always as a draft. Check which `gh`
   account is active and that it can write to the repo. PR sections: "What change
   is being made", "Why this change is being made", "Tested".
7. After Diego merges, update the checkout (`git switch main && git pull`), run
   `dotfiles --update`, then delete the local branch.

## Rules that are easy to get wrong

- A new tool goes in `TOOLS` in `.local/bin/dotfiles` and in the `TOOLS` row of
  `AGENTS.md`. Bootstrap and `dotfiles --update` install it. If the tool is
  missing right after the first update that brings the new list, run
  `dotfiles --update` again. A tool removed from the list is never uninstalled.
- Never skip a backup in `checkout_to_home`: rollback and uninstall restore only
  the files listed in `backed_up`. A backup whose content equals the incoming file
  is made but not reported (`_same_content`).
- Tracked files outside the sparse-excluded set (see `SPARSE_TRACKED_EXCLUDES`)
  land in `$HOME`. Development files such as `tests`, `AGENTS.md`, `pixi.toml`
  and `pyproject.toml` must stay excluded.
- Keep tracked config machine-neutral: no accounts, tokens, host names or
  `allowedUrls` entries. `~/.gitconfig` and `~/.copilot/config.json` stay local.
- Secrets live in `secrets/` as age ciphertext. Never print or commit a
  plaintext value, and never echo one in a command output.
- `dotfiles --update` only reports encrypted sources. Decrypting needs the age
  passphrase (unless a plaintext identity is in use), so
  `dotfiles secrets apply` (and `secrets init`,
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

## Changing a tracked skill

Skills under `.agents/skills/` are tracked files, so they follow the change flow
above, with these steps:

1. `dotfiles git switch main` and `dotfiles --update` first, so the edit starts
   from the deployed version.
2. Edit the file in the clone, never in `~/.agents/skills`: the next update
   restores the tracked version.
3. Check the skill against the `Agent skills` section of `AGENTS.md`.
4. Run the checks. The deployment tests cover every tracked `SKILL.md`.
5. After the merge, run `dotfiles --update` and confirm that
   `~/.agents/skills/<name>/SKILL.md` has the new content.

Before tracking a skill that already exists in `~/.agents/skills`, `diff` the
local copy against the one to commit: the first update moves the local file to the
backup directory.

## Checks specific to a snippet in `.bashrc.d`

Load it in a clean interactive shell before committing:
`bash -ic 'type <name>; complete -p <command>'`. Guard optional tools with
`[[ -n $(type -t <tool>) ]]` like the existing snippets, and put tool init and
completion in `.bashrc.d/tools`.
