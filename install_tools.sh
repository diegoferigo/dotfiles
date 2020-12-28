#!/bin/bash
set -euo pipefail

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

if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which starship)" ]] ; then
    curl -fsSL https://starship.rs/install.sh | bash -s -- -y -b ${DOTFILES_BIN}
fi

if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which bat)" ]] ; then
    BAT_VERSION=0.17.1
    cd /tmp
    wget -q https://github.com/sharkdp/bat/releases/download/v${BAT_VERSION}/bat-v${BAT_VERSION}-x86_64-unknown-linux-gnu.tar.gz
    tar xvf bat-*.tar.gz
    mv bat-*/bat ${DOTFILES_BIN}
    mv bat-*/autocomplete/bat.fish ${FISH_CONFIG_DIR}/completions/
    rm -rf bat-*-unknown*
fi

if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which exa)" ]] ; then
    EXA_VERSION=0.9.0
    cd /tmp
    wget https://github.com/ogham/exa/releases/download/v${EXA_VERSION}/exa-linux-x86_64-${EXA_VERSION}.zip
    unzip exa-linux-x86_64-${EXA_VERSION}.zip
    mv exa-linux-x86_64 ${DOTFILES_BIN}/exa
    rm -rf exa-linux-*
fi

if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which fzf)" ]] ; then
    cd ${DOTFILES_SHARE}
    curl -fsSL https://raw.githubusercontent.com/junegunn/fzf/master/install | bash -s -- --bin
    wget -q https://raw.githubusercontent.com/junegunn/fzf/master/shell/completion.bash -P ${BASH_CONFIG_DIR}/fzf.bash-completion
    wget -q https://raw.githubusercontent.com/junegunn/fzf/master/shell/key-bindings.bash -P ${BASH_CONFIG_DIR}
fi

if [[ ${FORCE_UPDATE} -eq 1 || -f "${DOTFILES_SHARE}/z/f.sh" ]] ; then
    wget https://raw.githubusercontent.com/rupa/z/master/z.sh -P ${DOTFILES_SHARE}/z
fi

if [[ ${FORCE_UPDATE} -eq 1 || -z "$(which fd)" ]] ; then
    FD_VERSION=8.2.1
    cd /tmp
    wget https://github.com/sharkdp/fd/releases/download/v8.2.1/fd-v8.2.1-x86_64-unknown-linux-gnu.tar.gz
    tar xvf fd-*.tar.gz
    mv fd-*/fd ${DOTFILES_BIN}
    mv fd-*/autocomplete/fd.fish ${FISH_CONFIG_DIR}/completions/
    mv fd-*/autocomplete/fd.bash-completion ${BASH_CONFIG_DIR}
    rm -rf fd-*unknown*
fi
