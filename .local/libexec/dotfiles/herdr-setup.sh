#!/usr/bin/env bash
# Set up herdr for agent CLIs, idempotently. `dotfiles --update` runs it after
# the pixi global tools, so herdr is already installed.
#
# What it does:
#   - installs the herdr hook for Copilot CLI when `herdr integration status`
#     does not report it as current (herdr rewrites the hook on updates, so it
#     is never tracked in git). The tracked ~/.copilot/settings.json already
#     holds the hook entry, which herdr does not recognize and would repeat, so
#     the file is put back as it was when it already referenced the hook
#   - writes the herdr agent skill from `herdr --skill` to
#     ~/.agents/skills/herdr/SKILL.md when its content changed (the skill
#     follows the installed herdr version, so it is never tracked either)
#   - installs the worktrunk plugin for herdr at a pinned commit unless
#     `herdr plugin list` already shows that commit (a different one is
#     replaced), so bumping the pin updates every machine
#
# Nothing happens when herdr is not installed.
set -euo pipefail

export PATH="$HOME/.pixi/bin:$PATH"

skill_file=$HOME/.agents/skills/herdr/SKILL.md
worktrunk_plugin=devashish2203/herdr-worktrunk
worktrunk_plugin_ref=f9df9ba700b8a4f4d97ddfe3ea3eb345e80b880b
tmp=

setup_integration() {
    local status
    status=$(herdr integration status 2>/dev/null || true)
    if grep -q '^copilot: current' <<<"$status"; then
        return 0
    fi
    echo "Installing the herdr hook for Copilot CLI"

    local settings=$HOME/.copilot/settings.json backup= result=0
    if [[ -f $settings ]] && grep -q herdr-agent-state.sh "$settings"; then
        backup=$(mktemp)
        cp -p "$settings" "$backup"
    fi

    herdr integration install copilot >/dev/null || result=$?

    if [[ -n $backup ]]; then
        cp -p "$backup" "$settings"
        rm -f "$backup"
    fi
    return "$result"
}

setup_plugin() {
    # Without worktrunk the plugin actions only print an error.
    command -v wt >/dev/null || return 0

    if herdr plugin list 2>/dev/null | grep -q "@$worktrunk_plugin_ref"; then
        return 0
    fi
    echo "Installing the herdr worktrunk plugin"
    herdr plugin install --yes --ref "$worktrunk_plugin_ref" "$worktrunk_plugin" >/dev/null
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

main() {
    command -v herdr >/dev/null || return 0

    local failed=0
    setup_integration || failed=1
    setup_skill || failed=1
    setup_plugin || failed=1
    return "$failed"
}

main "$@"
