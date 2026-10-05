#!/usr/bin/env bash
# Open Zed on a directory of this session.
#
# Usage: open-zed.sh [--print] [path]
#   path     directory or file, default: current directory (git toplevel if inside a repo)
#   --print  never open anything, only print the command to paste on the laptop
#
# Local session: runs `zed <path>`.
# SSH session: asks the laptop that holds the GUI to open `ssh://user@this-host/path`
# through a reverse SSH hop (transport_reverse_ssh). When the laptop cannot be
# identified safely, or the hop fails, it prints the command to paste instead.
#
# Environment (all optional):
#   ZED_OPEN_LOCAL        user@host of the laptop, skips the safety checks
#   ZED_OPEN_REMOTE_HOST  host name or ssh alias of THIS machine as the laptop reaches it
set -euo pipefail

print_only=0
if [[ ${1:-} == --print ]]; then
    print_only=1
    shift
fi

target=$(realpath "${1:-.}")
if [[ -d $target ]] && root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null); then
    target=$root
fi

# Multiplexers can outlive the SSH login, so $SSH_CONNECTION may be missing in a pane.
conn=${SSH_CONNECTION:-}
if [[ -z $conn && -n $(type -t tmux) ]]; then
    conn=$(tmux show-environment SSH_CONNECTION 2>/dev/null | sed -n 's/^SSH_CONNECTION=//p' || true)
fi

in_sshd_tree() {
    local pid=$$
    while [[ -n $pid && $pid -gt 1 ]]; do
        [[ $(ps -o comm= -p "$pid" 2>/dev/null) == sshd* ]] && return 0
        pid=$(ps -o ppid= -p "$pid" 2>/dev/null | tr -d ' ')
    done
    return 1
}

if [[ -z $conn ]] && ! in_sshd_tree; then
    echo "context: local"
    if ((print_only)); then
        echo "zed $(printf '%q' "$target")"
        exit 0
    fi
    exec zed "$target"
fi

echo "context: ssh"
user=$(id -un)
client_ip="" server_ip="" server_port=22
if [[ -n $conn ]]; then
    read -r client_ip _ server_ip server_port <<<"$conn"
fi
host=${ZED_OPEN_REMOTE_HOST:-${server_ip:-$(hostname -f)}}
url_host=$host
[[ $url_host == *:* ]] && url_host="[$url_host]"
port_part=""
[[ $server_port != 22 ]] && port_part=":$server_port"
url="ssh://$user@$url_host$port_part$target"

paste_command() {
    echo "Run this on the machine you connected from:"
    echo "zed $(printf '%q' "$url")"
}

# Distinct client IPs with an established connection to this server's sshd.
sshd_clients() {
    ss -Htn state established "( sport = :$server_port )" 2>/dev/null |
        awk '{ sub(/:[0-9]+$/, "", $4); print $4 }' | sort -u
}

# Print the laptop as user@host, or nothing when it cannot be told safely.
pick_laptop() {
    if [[ -n ${ZED_OPEN_LOCAL:-} ]]; then
        echo "$ZED_OPEN_LOCAL"
        return
    fi
    [[ -n $client_ip ]] || return 0
    local clients
    clients=$(sshd_clients)
    # Several clients on this account: the env of a long-lived pane may belong to another one.
    if [[ $clients != "$client_ip" ]]; then
        echo "reverse ssh skipped: SSH clients connected: ${clients//$'\n'/ }, expected only $client_ip" >&2
        return 0
    fi
    echo "$user@$client_ip"
}

# The script run on the laptop. Non-interactive ssh has a minimal PATH, so look in the usual places.
# shellcheck disable=SC2016  # expanded on the laptop, not here
laptop_script='
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
if [ -z "${WAYLAND_DISPLAY:-}${DISPLAY:-}" ]; then
    if [ -S "$XDG_RUNTIME_DIR/wayland-0" ]; then export WAYLAND_DISPLAY=wayland-0; else export DISPLAY=:0; fi
fi
for c in "$HOME/.pixi/bin/zed" "$HOME/.local/bin/zed" "$HOME/.local/zed.app/bin/zed" \
         "/Applications/Zed.app/Contents/MacOS/cli" /usr/local/bin/zed /usr/bin/zed; do
    [ -x "$c" ] && exec "$c" "$1"
done
z=$(command -v zed) || { echo "zed not found on the laptop" >&2; exit 127; }
exec "$z" "$1"
'

transport_reverse_ssh() {
    local dest=$1
    echo "opening $url on $dest" >&2
    ssh -o BatchMode=yes -o ConnectTimeout=8 "$dest" "bash -s -- $(printf '%q' "$url")" <<<"$laptop_script"
}

if ((print_only)); then
    paste_command
    exit 0
fi

dest=$(pick_laptop)
if [[ -z $dest ]]; then
    paste_command
    exit 0
fi

if ! transport_reverse_ssh "$dest"; then
    echo "reverse ssh to $dest failed" >&2
    paste_command
    exit 1
fi
