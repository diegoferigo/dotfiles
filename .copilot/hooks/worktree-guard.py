#!/usr/bin/env -S pixi exec --spec python=3.13 -- python
"""Deny `git worktree add` in the Copilot bash tool and point the agent to worktrunk.

A worktree made with `git worktree add` runs none of the worktrunk hooks and can
land outside `<repo>/.worktrees`. The hook only acts when `wt` is installed and
never fails: a command preToolUse hook that crashes would deny every tool call.
Set DOTFILES_ALLOW_GIT_WORKTREE=1 to let the command through.
"""

import json
import os
import pathlib
import re
import shutil
import sys

# Matches `git [-C dir] [-c key=value] worktree add` at the start of a command,
# including after `;`, `&&`, `||`, `|`, `(` or a newline, so text that only appears
# in an argument, a commit message or an echo does not trigger the hook.
GIT_WORKTREE_ADD = re.compile(
    r"(?:^|[;&|(\n])[ \t]*(?:[A-Za-z_][A-Za-z0-9_]*=\S*[ \t]+)*"
    r"git[ \t]+(?:-[Cc][ \t]+\S+[ \t]+)*worktree[ \t]+add\b"
)

REASON = (
    "Create worktrees with worktrunk so its hooks run and the checkout lands in "
    "<repo>/.worktrees: `wt switch --create <branch> --no-cd` (it prints the path), "
    "or reuse one listed by `wt list`. Ask the user before using `git worktree add`."
)


def main() -> None:
    if os.environ.get("DOTFILES_ALLOW_GIT_WORKTREE") == "1":
        return

    data = json.load(sys.stdin)
    tool_name = data.get("toolName") or data.get("tool_name")

    if tool_name != "bash":
        return

    arguments = data.get("toolArgs", data.get("tool_input"))

    if isinstance(arguments, str):
        arguments = json.loads(arguments)

    command = arguments.get("command") if isinstance(arguments, dict) else None

    if not isinstance(command, str) or GIT_WORKTREE_ADD.search(command) is None:
        return

    if not worktrunk_installed():
        return

    print(json.dumps({"permissionDecision": "deny", "permissionDecisionReason": REASON}))


def worktrunk_installed() -> bool:
    """Return whether the wt binary is on PATH or in the pixi global bin."""

    return shutil.which("wt") is not None or (pathlib.Path.home() / ".pixi/bin/wt").is_file()


if __name__ == "__main__":
    try:
        main()
    except Exception:
        # No output makes Copilot fall back to its normal permission flow.
        pass
