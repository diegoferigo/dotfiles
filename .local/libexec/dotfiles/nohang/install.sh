#!/usr/bin/env bash
# Install nohang (userspace OOM killer) as a systemd service, from a fresh clone
# of the upstream repository pinned to a commit.
#
# Needs sudo, network, git, make, python3 and systemd. It reads render.py and
# overrides.conf from its own directory, so it runs from anywhere and does not
# need the dotfiles git clone.
#
# What it does:
#   - clones upstream at `nohang_ref` into a temporary directory and runs
#     `make install` (PREFIX /usr/local)
#   - renders nohang-desktop.conf, checks it and only then installs it
#   - enables and restarts nohang-desktop, and only once it is active disables
#     systemd-oomd, which would compete for the same victims
#
# A manual edit of the installed config is lost on every run. Bump `nohang_ref`
# to update.
set -euo pipefail

nohang_repo=https://github.com/hakavlad/nohang.git
nohang_ref=bf477da78041a1fce8a82d04da422ef60e86c556 # v0.3.0
script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
prefix=/usr/local
installed_conf=$prefix/etc/nohang/nohang-desktop.conf

tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT

git -C "$tmp" init -q src
git -C "$tmp/src" fetch -q --depth 1 "$nohang_repo" "$nohang_ref"
git -C "$tmp/src" checkout -q FETCH_HEAD

sudo make -C "$tmp/src" install PREFIX="$prefix"

python3 "$script_dir/render.py" "$installed_conf" "$script_dir/overrides.conf" >"$tmp/nohang-desktop.conf"
"$prefix/sbin/nohang" --check --config "$tmp/nohang-desktop.conf"
sudo install -p -m0644 "$tmp/nohang-desktop.conf" "$installed_conf"

sudo systemctl enable nohang-desktop.service
sudo systemctl restart nohang-desktop.service
sleep 2
systemctl is-active --quiet nohang-desktop.service
echo "nohang-desktop is active"

if systemctl cat systemd-oomd.service &>/dev/null; then
    sudo systemctl disable --now systemd-oomd.service ||
        echo "warning: could not disable systemd-oomd, it may compete with nohang" >&2
fi
