#!/usr/bin/env bash
# Set up compressed swap in RAM (zram) and the matching sysctl values.
#
# Needs sudo, network (apt) and systemd. It reads zram-generator.conf and
# 99-zram.sysctl.conf from its own directory, so it runs from anywhere and does
# not need the dotfiles git clone.
#
# What it does:
#   - installs systemd-zram-generator, which creates /dev/zram0 as swap at boot
#   - installs its config and the sysctl file, and applies both right away
#
# The existing swap file stays as a fallback, because zram has a higher priority.
# Pages already in the swap file are not moved to zram, they leave it as the
# processes touch them or exit.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

sudo apt-get install -y systemd-zram-generator

sudo install -D -p -m0644 "$script_dir/zram-generator.conf" /etc/systemd/zram-generator.conf
sudo install -D -p -m0644 "$script_dir/99-zram.sysctl.conf" /etc/sysctl.d/99-zram.conf

sudo systemctl daemon-reload
sudo systemctl restart systemd-zram-setup@zram0.service
sudo sysctl --system >/dev/null

swapon --show
sysctl vm.swappiness vm.page-cluster
