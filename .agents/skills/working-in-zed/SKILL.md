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

Zed renders the agent's markdown. A reference is clickable only in the right form, and a wrong form fails silently, so use the form below every time you point at code.

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
- A plain `path:line` in text or in backticks is not clickable.
- Get the absolute path from `realpath`, never guess it. In a worktree, use the worktree path.
- A file outside the project (for example under `/opt` or a conda env) is `unverified`: tell the user if it does not open, and print the `zed <path>:<line>` command instead.

## Open a file at a line

When the user wants the file in the editor, not only a link:

- Local Zed session: `zed --add -- <path>:<line>`. `--add` adds to the focused window. Do not use `--reuse`: `unverified`, it is not listed by `zed --help`. Also `unverified`: whether `--add` moves the cursor when the file is already open.
- SSH session: do not run `zed` (no display). Use the `opening-zed` skill.

Zed's agent "follow" mode with an external CLI agent is `unverified`: do not promise it.

## Related skills

- `opening-zed`: opens Zed on a directory, locally or over SSH.
- `guide-me-through`: loads this skill only when the user asks for the IDE tour.
