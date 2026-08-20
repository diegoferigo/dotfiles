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
5. Install tools via `pixi global` (starship, bat, eza, fzf, fd, zoxide)

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

## :wastebasket: Uninstall

Remove all checked-out dotfiles and restore any backed-up originals:

```bash
dotfiles --uninstall
```

## :label: Notes

- Compatible with [GitHub Codespaces](https://docs.github.com/en/codespaces/personalizing-codespaces/personalizing-codespaces-for-your-account) — the devcontainer can run `./bootstrap` as `postCreateCommand`.
- Requires only `pixi` on the host; all Python dependencies are resolved on-the-fly via the shebang.
- `DOTFILES_REPO`, `DOTFILES_DIR`, `BACKUP_DIR` environment variables can override defaults.

