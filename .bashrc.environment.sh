# shellcheck shell=bash

case ":$PATH:" in
    *":$HOME/.pixi/bin:"*) ;;
    *) export PATH="$HOME/.pixi/bin${PATH:+:$PATH}" ;;
esac

secrets_file=~/.config/dotfiles/secrets.sh

if [[ -r $secrets_file ]]; then
    # shellcheck disable=SC1090
    source "$secrets_file"
fi

unset secrets_file
