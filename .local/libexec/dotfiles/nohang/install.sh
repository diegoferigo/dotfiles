#!/usr/bin/env bash
# Install nohang (userspace OOM killer) as a systemd service, from a fresh clone
# of the upstream repository pinned to a commit.
#
# Usage:
#   install.sh           install and start nohang-desktop (needs sudo and network)
#   install.sh render F  print F, an upstream nohang-desktop.conf, with the
#                        overrides of overrides.conf applied
#
# What install does:
#   - clones upstream at `nohang_ref` into a temporary directory and runs
#     `make install` (PREFIX /usr/local)
#   - replaces the installed nohang-desktop.conf with the rendered one
#   - disables systemd-oomd, which would compete for the same victims, and
#     enables and restarts nohang-desktop
#
# It runs on its own, so it also works from a bootstrap without the dotfiles
# clone. Bump `nohang_ref` to update.
set -euo pipefail

nohang_repo=https://github.com/hakavlad/nohang.git
nohang_ref=bf477da78041a1fce8a82d04da422ef60e86c556 # v0.3.0
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
overrides=$script_dir/overrides.conf
installed_conf=/usr/local/etc/nohang/nohang-desktop.conf

render() {
    awk '
        FNR == NR {
            if ($0 ~ /^[[:space:]]*(#|$)/) next
            if ($0 ~ /^@/) { extra[++extras] = $0; next }
            key = $0
            sub(/[[:space:]]*=.*/, "", key)
            override[key] = $0
            next
        }
        /^[a-z_]+[[:space:]]*=/ {
            key = $0
            sub(/[[:space:]]*=.*/, "", key)
            if (key in override) {
                print override[key]
                used[key] = 1
                next
            }
        }
        { print }
        END {
            for (key in override) {
                if (!(key in used)) {
                    printf "unknown nohang key in overrides: %s\n", key > "/dev/stderr"
                    failed = 1
                }
            }
            for (i = 1; i <= extras; i++) print extra[i]
            exit failed
        }
    ' "$overrides" "$1"
}

install_nohang() {
    local tmp
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    git -C "$tmp" init -q src
    git -C "$tmp/src" fetch -q --depth 1 "$nohang_repo" "$nohang_ref"
    git -C "$tmp/src" checkout -q FETCH_HEAD

    sudo make -C "$tmp/src" install

    render "$installed_conf" >"$tmp/nohang-desktop.conf"
    sudo install -p -m0644 "$tmp/nohang-desktop.conf" "$installed_conf"
    /usr/local/sbin/nohang --check --config "$installed_conf"

    sudo systemctl disable --now systemd-oomd.service || true
    sudo systemctl enable nohang-desktop.service
    sudo systemctl restart nohang-desktop.service
    systemctl --no-pager status nohang-desktop.service | head -n 5
}

case ${1:-install} in
    install) install_nohang ;;
    render) render "${2:?usage: install.sh render <nohang-desktop.conf>}" ;;
    *)
        echo "usage: install.sh [install | render <nohang-desktop.conf>]" >&2
        exit 2
        ;;
esac
