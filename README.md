# :hammer_and_wrench: dotfiles

<img src="https://github.com/user-attachments/assets/ca97b3e4-67ee-4a12-a5f9-00921a449395" alt="Two line starship prompt: local, ssh and docker" width="100%">

Personal dotfiles that set up my shell, tools and agent environment on any machine, including machines that are not mine.

- **Bare and sparse git clone.**
  - Files live directly in `$HOME`, with no symlinks, following the [bare repository approach](https://www.atlassian.com/git/tutorials/dotfiles).
  - A sparse checkout keeps repository-only files (tests, CI, docs) out of `$HOME`.
- **Tools with [pixi global](https://pixi.prefix.dev/latest/global_tools/introduction/).**
  - CLI tools are installed per user, without root.
- **Encrypted secrets.**
  - API keys and credentials are stored `age`-encrypted in the same repository.
  - They are applied only on request.
- **Agent skills.**
  - Skills are versioned and deployed like any other dotfile.
- **Ephemeral shells.**
  - `--shell` checks everything out into a temporary `$HOME`.
  - A shared machine (Jetson, robot, container) gets my prompt, tools and API keys.
  - Nothing is left in the real `$HOME` when the shell exits, only a shared package cache.
- **Safe lifecycle.**
  - Conflicting files are backed up.
  - `dotfiles --update` and `dotfiles --uninstall` handle updates and removal.

## :rocket: Bootstrap

### From a URL (zero local clone required)

```bash
curl -fsSL https://raw.githubusercontent.com/diegoferigo/dotfiles/main/bootstrap | bash
```

This will:

1. Install [pixi](https://pixi.sh) if not already present
2. Download `.local/bin/dotfiles` and run it via its `pixi exec` shebang
3. Clone the bare repo into `~/.dotfiles`
4. Check out tracked dotfiles directly into `$HOME` (backing up any conflicts)
5. Install the tools listed in `TOOLS` via [`pixi global`](https://pixi.prefix.dev/latest/global_tools/introduction/)
6. Report encrypted dotfiles that can be applied separately

### From a local clone

```bash
git clone https://github.com/diegoferigo/dotfiles.git
cd dotfiles
./bootstrap
```

### Try them in an ephemeral shell

To try the dotfiles on a machine that is not yours, `--shell` checks them out into a temporary `$HOME` and starts a shell in it, e.g. in a container:

```bash
docker run --rm -it ubuntu:latest bash -c '
  apt-get update -qq && apt-get install -y -qq curl ca-certificates >/dev/null &&
  curl -fsSL https://raw.githubusercontent.com/diegoferigo/dotfiles/main/bootstrap | bash -s -- --shell'
```

- Existing files are not touched. Exiting removes the temporary `$HOME`, also on `SIGHUP` and `SIGTERM`.
- Several shells can run at the same time. They share the package cache in `~/.cache/diegoferigo-dotfiles` and the logins of `gh`, `gcloud` and `rattler-build`, which are deleted when the last shell ends.
- Run `dotfiles secrets apply` inside the shell to load the encrypted secrets.
- `--no-cache` keeps the packages in the temporary `$HOME` too, and `--branch <name>` (`GITHUB_BRANCH` for `bootstrap`) tries a branch.

> [!WARNING] Secrets and logins are plaintext while the shell runs, and any process of the same user can read them. On a shared account use only scoped and revocable tokens.

Note: to delete the package cache, remove its directory when no shell is running.

## :gear: Managing dotfiles after bootstrap

The `dotfiles` command (checked out to `~/.local/bin/dotfiles`) wraps git against the bare repo. `main` is protected, so a change goes through a branch and a pull request:

```bash
dotfiles git switch main                        # start from main
dotfiles --update                               # make sure it is up to date
dotfiles git switch -c update-starship   # new branch
dotfiles git status
dotfiles git diff
dotfiles git add ~/.config/starship.toml
dotfiles git commit -m "Update starship config"
dotfiles git push -u origin update-starship   # then open the pull request on GitHub
```

After the pull request is merged, go back to `main` and update:

```bash
dotfiles git switch main
dotfiles --update
dotfiles git branch -d update-starship   # delete the merged branch
```

Other useful commands: `dotfiles git log --oneline`, `dotfiles git show HEAD`.

An agent can do this for you with the bundled [`working-on-dotfiles`](.agents/skills/working-on-dotfiles/SKILL.md) skill.

## :arrows_counterclockwise: Update

Pull the latest changes and re-apply dotfiles:

```bash
dotfiles --update
```

Public files are updated independently from encrypted files. Run `dotfiles secrets status` after an update and apply changes explicitly. Use `dotfiles --update --with-secrets` to update both in one interactive run.

## :lock: Encrypted dotfiles

Encrypted sources are tracked under `secrets/home/` and map directly below `$HOME`:

```text
secrets/home/.ssh/config.d/rai.conf.age -> ~/.ssh/config.d/rai.conf
```

One shared age identity is protected by a high-entropy passphrase stored in a password manager. Its encrypted wrapper is distributed through the repository, so machines do not need separate private-key provisioning. Bootstrap and update leave secrets untouched unless explicitly requested.

```bash
dotfiles secrets init
dotfiles secrets encrypt ~/.ssh/config.d/rai.conf
dotfiles secrets status
dotfiles secrets apply
dotfiles --update --with-secrets
dotfiles secrets change-passphrase
```

See [`secrets/README.md`](secrets/README.md) for setup, authoring, deployment, conflict handling, recovery, and key rotation.

## :wastebasket: Uninstall

Remove all checked-out dotfiles and restore any backed-up originals:

```bash
dotfiles --uninstall
```

Uninstall removes unchanged decrypted files and restores their original backups. The tracked encrypted identity follows the normal public-dotfile lifecycle. A legacy plaintext identity is never removed.

## :label: Notes

- Compatible with [GitHub Codespaces](https://docs.github.com/en/codespaces/setting-your-user-preferences/personalizing-github-codespaces-for-your-account): select this repository as your dotfiles repository and Codespaces runs `bootstrap` in every new codespace. Run `dotfiles secrets apply` in the first shell, it asks for the passphrase.
- Requires only `pixi` on the host; all Python dependencies are resolved on-the-fly via the shebang.
- `DOTFILES_REPO`, `DOTFILES_DIR`, `BACKUP_DIR` environment variables can override defaults.
