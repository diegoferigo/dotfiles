# :hammer_and_wrench: dotfiles

![][ps1]

Personal dotfiles managed with a **bare git repo** pattern — files live directly in `$HOME`, no symlinks.

[ps1]: https://user-images.githubusercontent.com/469199/124800077-85817480-df55-11eb-9bc8-b218fdd53d01.png

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
5. Install tools via `pixi global` (starship, bat, eza, fzf, fd, zoxide, difftastic, age)
6. Report encrypted dotfiles that can be applied separately

### From a local clone

```bash
git clone https://github.com/diegoferigo/dotfiles.git
cd dotfiles
./bootstrap
```

## :gear: Managing dotfiles after bootstrap

The `dotfiles` command (checked out to `~/.local/bin/dotfiles`) wraps git against the bare repo:

```bash
dotfiles git status
dotfiles git diff
dotfiles git add ~/.config/starship.toml
dotfiles git commit -m "update starship config"
dotfiles git log --oneline
dotfiles git push
```

## :arrows_counterclockwise: Update

Pull the latest changes and re-apply dotfiles:

```bash
dotfiles --update
```

Public files are updated independently from encrypted files. Run
`dotfiles secrets status` after an update and apply changes explicitly.
Use `dotfiles --update --with-secrets` to update both in one interactive run.

## :shell: Bash environment

Bootstrap manages two blocks in the user's existing `~/.bashrc`. A minimal
environment block runs first, before Ubuntu's non-interactive early return, and
adds `~/.pixi/bin` to `PATH`. Pixi and tools installed through `pixi global` are
therefore available to commands such as:

```bash
ssh host 'pixi --version'
```

The same block sources `~/.config/dotfiles/secrets.sh` when readable. This file
is trusted Bash code intended for exported environment variables. Exported
secrets are inherited by every child process and may be visible to same-user
process inspection.

## :lock: Encrypted dotfiles

Encrypted sources are tracked under `secrets/home/` and map directly below
`$HOME`:

```text
secrets/home/.ssh/config.d/rai.conf.age -> ~/.ssh/config.d/rai.conf
```

One shared age identity is protected by a high-entropy passphrase stored in a
password manager. Its encrypted wrapper is distributed through the repository,
so machines do not need separate private-key provisioning. Bootstrap and update
leave secrets untouched unless explicitly requested.

```bash
dotfiles secrets init
dotfiles secrets encrypt ~/.ssh/config.d/rai.conf
dotfiles secrets status
dotfiles secrets apply
dotfiles --update --with-secrets
dotfiles secrets change-passphrase
```

See [`secrets/README.md`](secrets/README.md) for setup, authoring, deployment,
conflict handling, recovery, and key rotation.

## :wastebasket: Uninstall

Remove all checked-out dotfiles and restore any backed-up originals:

```bash
dotfiles --uninstall
```

Uninstall removes unchanged decrypted files and restores their original backups.
The tracked encrypted identity follows the normal public-dotfile lifecycle. A
legacy plaintext identity is never removed.

## :label: Notes

- Compatible with [GitHub Codespaces](https://docs.github.com/en/codespaces/personalizing-codespaces/personalizing-codespaces-for-your-account) — the devcontainer can run `./bootstrap` as `postCreateCommand`.
- Requires only `pixi` on the host; all Python dependencies are resolved on-the-fly via the shebang.
- `DOTFILES_REPO`, `DOTFILES_DIR`, `BACKUP_DIR` environment variables can override defaults.
