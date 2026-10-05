---
name: opening-hunk
description: >-
  Prints the command that opens hunk, the terminal diff viewer, on the current
  directory or worktree, for a local session or an SSH one (also inside herdr panes). Use whenever the user wants to see or review a diff, a worktree
  or a staged change in the terminal, asks for hunk, or wants a review
  alternative to an IDE on a remote machine, even if they do not name this
  skill. The agent never runs hunk: it is a TUI the user opens in a new
  terminal. For the full IDE use opening-zed instead.
---

# Opening hunk

hunk needs a real terminal. The agent only prints the command, and the user pastes it in a new terminal or herdr tab.

## Run

```bash
~/.agents/skills/opening-hunk/scripts/hunk-command.sh [path] [hunk args...]
```

- `path` defaults to the current directory and is resolved to the git toplevel, so a worktree shows its own diff.
- The arguments default to `diff`. Examples: `diff --staged`, `show`, `diff main`.

The first output line is `context: local` or `context: ssh`. Show the user the printed command as is.

## What it prints

| Context | Command                                                                                                |
| ------- | ------------------------------------------------------------------------------------------------------ |
| Local   | `cd <path> && <hunk> diff`                                                                             |
| SSH     | `ssh -t [-p port] user@host 'cd <path> && <hunk> diff'`, to run on the machine the user connected from |

The host is the address from `SSH_CONNECTION` (SSH is otherwise detected from an `sshd` ancestor). Set `HUNK_REMOTE_HOST` to a host name or ssh alias when the laptop cannot resolve that address. In a herdr pane the variable can be missing or stale: if the user has several clients, confirm the host before relying on it.

The script exits 1 when hunk is not installed on this machine.
