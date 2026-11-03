#!/usr/bin/env bash
# Install nohang (userspace OOM killer) as a systemd service, from a fresh clone
# of the upstream repository pinned to a commit.
#
# Usage: install.sh   (needs sudo, network and python3)
#
# What install does:
#   - clones upstream at `nohang_ref` into a temporary directory and runs
#     `make install` (PREFIX /usr/local)
#   - replaces the installed nohang-desktop.conf with the one rendered by
#     render.py from the upstream file and overrides.conf
#   - disables systemd-oomd, which would compete for the same victims, and
#     enables and restarts nohang-desktop
#
# It runs on its own, so it also works from a bootstrap without the dotfiles
# clone. Bump `nohang_ref` to update.
set -euo pipefail

nohang_repo=https://github.com/hakavlad/nohang.git
nohang_ref=bf477da78041a1fce8a82d04da422ef60e86c556 # v0.3.0
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
installed_conf=/usr/local/etc/nohang/nohang-desktop.conf

install_nohang() {
    local tmp
    tmp=$(mktemp -d)
    trap 'rm -rf "$tmp"' EXIT

    git -C "$tmp" init -q src
    git -C "$tmp/src" fetch -q --depth 1 "$nohang_repo" "$nohang_ref"
    git -C "$tmp/src" checkout -q FETCH_HEAD

    sudo make -C "$tmp/src" install

    python3 "$script_dir/render.py" "$installed_conf" "$script_dir/overrides.conf" >"$tmp/nohang-desktop.conf"
    sudo install -p -m0644 "$tmp/nohang-desktop.conf" "$installed_conf"
    /usr/local/sbin/nohang --check --config "$installed_conf"

    sudo systemctl disable --now systemd-oomd.service || true
    sudo systemctl enable nohang-desktop.service
    sudo systemctl restart nohang-desktop.service
    systemctl --no-pager status nohang-desktop.service | head -n 5
}

install_nohang
