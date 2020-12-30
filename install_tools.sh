#!/bin/bash
set -eEuo pipefail

# Environment variables
#
# FORCE_UPDATE
# DOTFILES_SHARE
# FISH_CONFIG_DIR
#

# Set to true to force updating all the tools
FORCE_UPDATE=${FORCE_UPDATE:-0}

# Directory with all dotfiles tools
DOTFILES_SHARE=${DOTFILES_SHARE:-~/.dotfiles/share}
DOTFILES_BIN=${DOTFILES_SHARE}/bin
mkdir -p ${DOTFILES_SHARE}
mkdir -p ${DOTFILES_BIN}

# Directory with Fish configuration
FISH_CONFIG_DIR=${FISH_CONFIG_DIR:-~/.config/fish}
mkdir -p ${FISH_CONFIG_DIR}
mkdir -p ${FISH_CONFIG_DIR}/completions

# Directory with Bash configuration
BASH_CONFIG_DIR=${BASH_CONFIG_DIR:-~/.local/share/bash-completion}
mkdir -p ${BASH_CONFIG_DIR}

# Report errors
trap 'echo $(basename $BASH_SOURCE): Error $? at line \#${LINENO}' ERR

# Operate on a tmp directory
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "$TEMP_DIR"' EXIT

echo " -> starship"
if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which starship)" ]] ; then
    curl -fsSL https://starship.rs/install.sh | bash -s -- -y -b ${DOTFILES_BIN}
fi

echo " -> bat"
if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which bat)" ]] ; then
    BAT_VERSION=0.17.1
    cd ${TEMP_DIR}
    wget -q https://github.com/sharkdp/bat/releases/download/v${BAT_VERSION}/bat-v${BAT_VERSION}-x86_64-unknown-linux-gnu.tar.gz
    tar xvf bat-*.tar.gz
    mv bat-*/bat ${DOTFILES_BIN}
    mv bat-*/autocomplete/bat.fish ${FISH_CONFIG_DIR}/completions/
fi

echo " -> exa"
if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which exa)" ]] ; then
    EXA_VERSION=0.9.0
    cd ${TEMP_DIR}
    wget -q https://github.com/ogham/exa/releases/download/v${EXA_VERSION}/exa-linux-x86_64-${EXA_VERSION}.zip
    unzip exa-linux-x86_64-${EXA_VERSION}.zip >/dev/null
    mv exa-linux-x86_64 ${DOTFILES_BIN}/exa
fi

echo " -> fzf"
if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which fzf)" ]] ; then
    cd ${DOTFILES_SHARE}
    curl -fsSL https://raw.githubusercontent.com/junegunn/fzf/master/install | bash -s -- --bin
    wget -q https://raw.githubusercontent.com/junegunn/fzf/master/shell/completion.bash -P ${BASH_CONFIG_DIR}/fzf.bash-completion
    wget -q https://raw.githubusercontent.com/junegunn/fzf/master/shell/key-bindings.bash -P ${BASH_CONFIG_DIR}
fi

echo " -> z"
if [[ ${FORCE_UPDATE} -eq 1 || -f "${DOTFILES_SHARE}/z/f.sh" ]] ; then
    wget -q https://raw.githubusercontent.com/rupa/z/master/z.sh -P ${DOTFILES_SHARE}/z
fi

echo " -> fd"
if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which fd)" ]] ; then
    FD_VERSION=8.2.1
    cd ${TEMP_DIR}
    wget -q https://github.com/sharkdp/fd/releases/download/v8.2.1/fd-v8.2.1-x86_64-unknown-linux-gnu.tar.gz
    tar xvf fd-*.tar.gz >/dev/null
    mv fd-*/fd ${DOTFILES_BIN}
    mv fd-*/autocomplete/fd.fish ${FISH_CONFIG_DIR}/completions/
    mv fd-*/autocomplete/fd.bash-completion ${BASH_CONFIG_DIR}
fi

echo " -> fish"
if [[ -n "$(which fish)" ]] ; then
    has_fisher=1 && fish -c "functions -q fisher" || has_fisher=0
    if [[ ${FORCE_UPDATE} -eq 1 || $has_fisher -eq 0 ]] ; then
        fish -c "curl -fsSL git.io/fisher | source && fisher install < ~/.config/fish/fish_plugins" >/dev/null
    fi
fi
