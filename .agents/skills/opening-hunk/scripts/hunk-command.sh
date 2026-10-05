#!/usr/bin/env bash
# Print the command that opens hunk on a directory of this session.
# hunk is a TUI: the agent must never run it, the user pastes the command in a new terminal.
#
# Usage: hunk-command.sh [path] [hunk args...]   (default args: diff)
#   path  directory, default: current directory (git toplevel if inside a repo)
#
# Environment (optional):
#   HUNK_REMOTE_HOST  host name or ssh alias of THIS machine as the laptop reaches it
set -euo pipefail

target=$(realpath "${1:-.}")
[[ $# -gt 0 ]] && shift
if root=$(git -C "$target" rev-parse --show-toplevel 2>/dev/null); then
    target=$root
fi
args=("$@")
[[ ${#args[@]} -eq 0 ]] && args=(diff)

hunk_bin=$(command -v hunk || true)
if [[ -z $hunk_bin ]]; then
    echo "hunk is not installed on this machine" >&2
    exit 1
fi

# Single-quote a string for a POSIX shell.
sq() {
    if [[ $1 =~ ^[A-Za-z0-9_./:=@+,-]+$ ]]; then
        printf '%s' "$1"
    else
        printf "'%s'" "${1//\'/\'\\\'\'}"
    fi
}

inner="cd $(sq "$target") && $(sq "$hunk_bin")"
for a in "${args[@]}"; do
    inner+=" $(sq "$a")"
done

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
    echo "Run in a new terminal:"
    echo "$inner"
    exit 0
fi

echo "context: ssh"
user=$(id -un)
server_ip="" server_port=22
if [[ -n $conn ]]; then
    read -r _ _ server_ip server_port <<<"$conn"
fi
host=${HUNK_REMOTE_HOST:-${server_ip:-$(hostname -f)}}
port_opt=()
[[ $server_port != 22 ]] && port_opt=(-p "$server_port")

echo "Run in a new terminal on the machine you connected from:"
echo "ssh -t ${port_opt[*]:+${port_opt[*]} }$user@$host $(sq "$inner")"
