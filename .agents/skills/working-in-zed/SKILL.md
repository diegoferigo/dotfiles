---
name: working-in-zed
description: >-
  Conventions for an agent session that runs inside the Zed IDE (the agent
  panel, ACP): detect that Zed hosts the session, write clickable code
  references in chat, and open a file at a line. Use whenever the user says they
  are in Zed, says a link or path is not clickable, asks for clickable code
  links, or when a session shows a `zed-editor` ancestor process, even if they
  do not name this skill. To open Zed itself use opening-zed.
---

# Working in Zed

Zed renders the agent's markdown. A reference is clickable only in the right form, and a wrong form fails silently, so use the form below every time you point at code. The behavior below was read in the Zed 1.23.2 source and changed in June and July 2026, so an older or newer build can differ.

## Detect

Run `~/.agents/skills/working-in-zed/scripts/detect-zed.sh` once per session. It prints `zed` when an ancestor process is `zed-editor`, else `no-zed`. Do not detect from the environment: `TERM_PROGRAM` and `HERDR_*` are inherited from the shell that launched Zed and can name another terminal while Zed still hosts the agent. An explicit statement from the user ("I am in Zed") wins over the script.

## Clickable references

Write every code reference as a markdown link with an absolute path and a line anchor:

```markdown
[motion_switcher_api.hpp:28](/home/user/repo/src/motion_switcher_api.hpp#L28)
```

- Percent-encode spaces in the link target.
- Absolute path: a relative path works for tracked files of the project, but fails for paths in ignored directories such as `.pixi/`. The absolute form works for both, so use it by default.
- Line anchor: `#L<line>`, or `#L<start>-<end>` for a range.
- A plain click opens a link, Ctrl is not needed.
- Fallback only: a relative path in backticks with a line, such as `src/main.rs:42`, is made clickable when the file exists in a project worktree, so do not count on it for ignored paths. In plain text it is not. As a link target, `path:line` and a relative path without `#L` are unreliable: use the form above.
- `file:///abs/path#L10-20` also works.
- Get the absolute path from `realpath`, never guess it. In a worktree, use the worktree path.
- A file outside the project (for example under `/opt` or a conda env) is `unverified`: tell the user if it does not open, and print the `zed <path>:<line>` command instead.
- Over SSH, a link resolves against the remote project. `unverified`: whether `zed ssh://host/path:42` carries the line.

## Open a file at a line

When the user wants the file in the editor, not only a link:

- Local Zed session: `zed --add <path>:<line>`. `--add` adds to the focused window. Do not use `--reuse` (hidden from `--help`): it replaces the window's workspace. Do not use `--wait`: it blocks until the user closes the file. `unverified`: whether `--add` moves the cursor when the file is already open.
- SSH session: do not run `zed` (no display). Use the `opening-zed` skill.

Zed has no ACP method to open a file in the editor: use links or the `zed` CLI. Follow mode needs the agent to report tool-call locations, and whether Copilot CLI does is `unverified`: do not promise it. The user can add the editor selection to the thread with `agent::AddSelectionToThread` (Ctrl+>, from the Linux default keymap of 1.23.2), because the open file is not sent automatically.

## Related skills

- `opening-zed`: opens Zed on a directory, locally or over SSH.
- `guide-me-through`: loads this skill only when the user asks for the IDE tour.
