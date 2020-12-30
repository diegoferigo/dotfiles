# dotfiles

- Boostrap dotfiles on GNU/Linux systems without cloning first the repository.
- Dotfiles handled with a [bare git repository](https://www.atlassian.com/git/tutorials/dotfiles) 
in the user's home (no symlinks).
- New `config` alias to handle the bare repository (`config status`, `config commit ...`).
- `bash` and `fish` support.
- Prompt based on [starship](https://starship.rs/).
- Installs in the user's home a bunch of tools (`bat`, `fzf`, `fd`, ...)

## Bootstrap

Minimal dependencies for both `bash` and `fish`:

```bash
apt update
apt install nano git wget curl unzip
```

Optionally install a recent `fish` version: 

```
apt install software-properties-common
apt-add-repository ppa:fish-shell/release-3
apt install fish
```

Bootstrap the dotfiles:

```
curl -fsSL https://raw.githubusercontent.com/diegoferigo/dotfiles/main/bootstrap | bash
```

### Notes

The structure of this repository is compatible with [Github Codespaces](https://docs.github.com/en/free-pro-team@latest/github/developing-online-with-codespaces/personalizing-codespaces-for-your-account).
