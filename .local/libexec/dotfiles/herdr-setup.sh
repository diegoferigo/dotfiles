#!/usr/bin/env bash
# Set up herdr for agent CLIs, idempotently. `dotfiles --update` runs it after
# the pixi global tools, so herdr is already installed.
#
# What it does:
#   - installs the herdr hook for Copilot CLI when `herdr integration status`
#     does not report it as current (herdr rewrites the hook on updates, so it
#     is never tracked in git)
#   - writes the herdr agent skill from `herdr --skill` to
#     ~/.agents/skills/herdr/SKILL.md when its content changed (the skill
#     follows the installed herdr version, so it is never tracked either)
#   - sets `default_shell = "bash"` in ~/.config/herdr/config.toml when the file
#     has no [terminal] table, because herdr can start `sh` for new panes and
#     `sh` prints the bash prompt escapes literally
#
# Nothing happens when herdr is not installed.
set -euo pipefail

export PATH="$HOME/.pixi/bin:$PATH"

config_file=$HOME/.config/herdr/config.toml
skill_file=$HOME/.agents/skills/herdr/SKILL.md
tmp=

setup_integration() {
    local status
    status=$(herdr integration status 2>/dev/null || true)
    if grep -q '^copilot: current' <<<"$status"; then
        return 0
    fi
    echo "Installing the herdr hook for Copilot CLI"
    herdr integration install copilot >/dev/null
}

setup_skill() {
    tmp=$(mktemp)
    trap 'rm -f "$tmp"' EXIT

    herdr --skill >"$tmp"
    # Refuse an empty or unexpected output before replacing a working skill.
    head -n 1 "$tmp" | grep -q '^---$' || {
        echo "herdr --skill did not print a skill file" >&2
        return 1
    }

    if cmp -s "$tmp" "$skill_file"; then
        return 0
    fi
    echo "Writing the herdr skill to $skill_file"
    mkdir -p "$(dirname "$skill_file")"
    install -m 0644 "$tmp" "$skill_file"
}

setup_shell() {
    command -v bash >/dev/null || return 0
    # Leave a config that already has a [terminal] table to the user.
    if grep -q '^\[terminal\]' "$config_file" 2>/dev/null; then
        return 0
    fi
    echo "Setting the herdr default shell to bash"
    mkdir -p "$(dirname "$config_file")"
    printf '\n[terminal]\ndefault_shell = "bash"\n' >>"$config_file"
}

main() {
    command -v herdr >/dev/null || return 0

    local failed=0
    setup_integration || failed=1
    setup_skill || failed=1
    setup_shell || failed=1
    return "$failed"
}

main "$@"
