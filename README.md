# :hammer_and_wrench: dotfiles

![][ps1]

- :penguin: Boostrap dotfiles on GNU/Linux systems without cloning first the repository.
- :twisted_rightwards_arrows: Dotfiles handled with a [bare git repository](https://www.atlassian.com/git/tutorials/dotfiles) 
in the user's home (no symlinks).
- :skull_and_crossbones: Never using `sudo`.
- :pencil2: New `config` alias to handle the bare repository (`config status`, `config commit`, ...).
- :fish: `bash` and `fish` support.
- :star: Prompt based on [starship](https://starship.rs/).
- :house: Installs in the user's home a bunch of tools (`bat`, `fzf`, `fd`, ...)

[ps1]: https://user-images.githubusercontent.com/469199/124800077-85817480-df55-11eb-9bc8-b218fdd53d01.png

## :rocket: Bootstrap

Minimal dependencies for both `bash` and `fish`:

```bash
apt update
apt install nano git wget curl unzip bash-completion
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

### :label: Notes

The structure of this repository is compatible with [Github Codespaces](https://docs.github.com/en/free-pro-team@latest/github/developing-online-with-codespaces/personalizing-codespaces-for-your-account).
