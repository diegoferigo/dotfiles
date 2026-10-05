#!/usr/bin/env bash
# Open Zed on a directory of this session.
#
# Usage: open-zed.sh [--print] [path]
#   path     directory or file, default: current directory (git toplevel if inside a repo)
#   --print  never open anything, only print the command
#
# Local session: runs `zed <path>`.
# SSH session: prints `zed ssh://user@this-host/path` to paste on the laptop.
# TODO: open Zed on the laptop without copy and paste, either with a reverse SSH
# hop to the laptop (needs a safe way to identify it) or with a socket forwarded
# through the SSH connection (RemoteForward).
#
# Environment (optional):
#   ZED_OPEN_REMOTE_HOST  host name or ssh alias of THIS machine as the laptop reaches it;
#                         the port from SSH_CONNECTION is then not added
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

conn=${SSH_CONNECTION:-}

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
server_ip="" server_port=22
if [[ -n $conn ]]; then
    read -r _ _ server_ip server_port <<<"$conn"
fi
host=${ZED_OPEN_REMOTE_HOST:-${server_ip:-$(hostname -f)}}
url_host=$host
[[ $url_host == *:* ]] && url_host="[$url_host]"
port_part=""
# An alias carries its own port in the laptop ssh config.
[[ -z ${ZED_OPEN_REMOTE_HOST:-} && $server_port != 22 ]] && port_part=":$server_port"
url="ssh://$user@$url_host$port_part$target"

echo "Run this on the machine you connected from:"
echo "zed $(printf '%q' "$url")"
