---
name: opening-zed
description: >-
  Opens the Zed IDE on the current directory or worktree from an agent session,
  whether it runs locally or over SSH (also inside herdr panes). Locally
  it runs zed; over SSH it prints the zed ssh:// command to paste on the laptop.
  Use whenever the user asks to open, show or edit something in Zed, to open the
  IDE, the worktree or the project, or to get the command to open Zed against
  this remote machine, even if they do not name this skill. For a diff-only
  review use opening-hunk instead.
---

# Opening Zed

Zed is a GUI: it needs the user's desktop. In an SSH session the agent has no display, so it must never run `zed` itself there. The script decides.

## Run

```bash
~/.agents/skills/opening-zed/scripts/open-zed.sh [--print] [path]
```

- `path` defaults to the current directory and is resolved to the git toplevel, so a worktree opens as its own project.
- `--print` only prints the command to paste on the laptop. Use it when the user asks for the command, not for the window.

The first output line is `context: local` or `context: ssh`. Report it, plus whatever the script printed, to the user.

## What the script does

| Context | Action                                                        |
| ------- | ------------------------------------------------------------- |
| Local   | `zed <path>`                                                  |
| SSH     | Prints `zed ssh://user@this-host/path` to paste on the laptop |

SSH is detected from `SSH_CONNECTION`, then from an `sshd` ancestor. In a herdr pane the variable can be missing or stale, so the host in the printed command can be wrong: say so if the user reports a failure.

## Requirements and limits

- `--print` and the SSH output are identical: the user runs the command on the laptop.
- `ZED_OPEN_REMOTE_HOST` sets the host or ssh alias used in the URL, for when the laptop cannot resolve the address in `SSH_CONNECTION`. The port from `SSH_CONNECTION` is then not added: the alias carries its own.
- The first project opened on a new server downloads the Zed server binary there, so it can take a while.
- Zed has no native way to open its local UI from a remote shell. Opening it without copy and paste is a TODO in the script.
