#!/usr/bin/env bash
# Set up compressed swap in RAM (zram) and the matching sysctl values.
#
# Needs sudo, network (apt) and systemd. It reads zram-generator.conf and
# 99-zram.sysctl.conf from its own directory, so it runs from anywhere and does
# not need the dotfiles git clone.
#
# What it does:
#   - installs systemd-zram-generator, which creates /dev/zram0 as swap at boot
#   - installs its config and the sysctl file, overwriting any edit of the
#     installed files, and applies the sysctl values
#   - recreates the zram device and starts it with the new config
#
# The existing swap file stays as a fallback, because zram has a higher priority.
# Pages already in the swap file are not moved to zram, they leave it as the
# processes touch them or exit. Recreating the device swaps its pages back in,
# which needs free RAM, so the script stops when that fails.
set -euo pipefail

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

sudo apt-get install -y systemd-zram-generator

sudo install -D -p -m0644 "$script_dir/zram-generator.conf" /etc/systemd/zram-generator.conf
sudo install -D -p -m0644 "$script_dir/99-zram.sysctl.conf" /etc/sysctl.d/99-zram.conf

sudo sysctl -q -p /etc/sysctl.d/99-zram.conf

# The package may have started zram0 with its default size, and a configured
# device cannot be reconfigured. A failure below must not leave the machine
# without zram, so it starts the unit again.
trap 'sudo systemctl start systemd-zram-setup@zram0.service || true' ERR
sudo systemctl daemon-reload
sudo systemctl stop systemd-zram-setup@zram0.service
if swapon --show=NAME --noheadings | grep -q '^/dev/zram0$'; then
    sudo swapoff /dev/zram0
fi
if [[ -e /sys/block/zram0 ]]; then
    echo 0 | sudo tee /sys/class/zram-control/hot_remove >/dev/null
fi
sudo modprobe zram
# Reading hot_add creates a device, the attribute cannot be written.
sudo cat /sys/class/zram-control/hot_add >/dev/null
sudo systemctl start systemd-zram-setup@zram0.service
trap - ERR

swapon --show
sysctl vm.swappiness vm.page-cluster
