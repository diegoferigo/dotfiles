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
6. Apply encrypted dotfiles when the age identity is available

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

Public files are updated even when `age` or the identity is unavailable. Existing
decrypted files remain in place and the command reports the pending refresh.

## :lock: Encrypted dotfiles

Encrypted sources are tracked under `secrets/home/` and map directly below
`$HOME`:

```text
secrets/home/.ssh/config.d/rai.conf.age -> ~/.ssh/config.d/rai.conf
```

The repository uses one shared age identity across trusted machines. Generate it
once, outside the repository:

```bash
age-keygen -o identity.txt
chmod 600 identity.txt
age-keygen -y identity.txt > secrets/recipients.txt
```

Provision the same private identity on each trusted machine through a secure
channel:

```bash
install -D -m 600 identity.txt \
    ~/.config/dotfiles/age/identity.txt
dotfiles secrets apply
```

Bootstrap and update remain successful without this file. Inspect deployment
state without decrypting content:

```bash
dotfiles secrets status
```

Encrypt a file from a development clone:

```bash
dotfiles secrets encrypt ~/.ssh/config.d/rai.conf
git add secrets/home/.ssh/config.d/rai.conf.age
```

The plaintext is never removed. `apply` writes decrypted files with mode `0600`,
backs up first-time collisions, refuses to overwrite local edits unless
`--force` is passed, and updates all targets transactionally.

The shared identity minimizes per-machine maintenance, but every trusted machine
has the same decryption capability. If one is compromised, generate a new
identity, replace `secrets/recipients.txt`, re-encrypt every source, and
re-provision the remaining machines.

## :wastebasket: Uninstall

Remove all checked-out dotfiles and restore any backed-up originals:

```bash
dotfiles --uninstall
```

Uninstall removes unchanged decrypted files, restores their original backups,
and leaves the age identity untouched.

## :label: Notes

- Compatible with [GitHub Codespaces](https://docs.github.com/en/codespaces/personalizing-codespaces/personalizing-codespaces-for-your-account) — the devcontainer can run `./bootstrap` as `postCreateCommand`.
- Requires only `pixi` on the host; all Python dependencies are resolved on-the-fly via the shebang.
- `DOTFILES_REPO`, `DOTFILES_DIR`, `BACKUP_DIR` environment variables can override defaults.
