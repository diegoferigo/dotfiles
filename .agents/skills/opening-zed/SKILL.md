---
name: opening-zed
description: >-
  Opens the Zed IDE on the current directory or worktree from an agent session,
  whether it runs locally or over SSH (also inside herdr or tmux panes). Locally
  it runs zed; over SSH it asks the laptop that holds the GUI to open the
  ssh:// project through a reverse SSH hop, and otherwise prints the command to
  paste. Use whenever the user asks to open, show or edit something in Zed, to
  open the IDE, the worktree or the project, or to get the command to open Zed
  against this remote machine, even if they do not name this skill. For a
  diff-only review use opening-hunk instead.
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

| Context                                  | Action                                                                                               |
| ---------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| Local                                    | `zed <path>`                                                                                         |
| SSH, laptop identified                   | `ssh <laptop>` runs the laptop's zed on `ssh://user@this-host/path`                                  |
| SSH, laptop not identified or hop failed | Prints `zed ssh://...` to paste on the laptop (exit 0 when it chose to print, 1 when the hop failed) |

SSH is detected from `SSH_CONNECTION`, then from tmux's global environment, then from an `sshd` ancestor. In a herdr or tmux pane the variable can be missing or stale.

## Safety rule: never guess the laptop

The laptop is `ZED_OPEN_LOCAL` (`user@host`) when set. Otherwise it is the client IP from `SSH_CONNECTION`, used only when it is the single client connected to this server's sshd. With two clients on the same account the pane may carry the other person's IP, so the script prints the command instead of opening Zed on the wrong machine. Do not bypass this by setting `ZED_OPEN_LOCAL` yourself: ask the user.

## Requirements and limits

- The reverse hop needs sshd on the laptop and key login from this server to the laptop. When it fails, relay the error and the printed command.
- Zed must already run on the laptop, or the laptop must have a display the script can reach.
- `ZED_OPEN_REMOTE_HOST` sets the host or ssh alias used in the URL, for when the laptop cannot resolve the address in `SSH_CONNECTION`.
- The first project opened on a new server downloads the Zed server binary there, so it can take a while.
- Zed has no native way to open its local UI from a remote shell: the reverse hop is added by this script.
