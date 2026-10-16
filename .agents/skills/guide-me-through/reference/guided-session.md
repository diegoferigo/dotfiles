# Guided session (chat or IDE)

Use when the user chose `guide in chat` or `guide in IDE`. The explanation is a sequence of stops the user steers, not one long answer.

## Plan the tour

Explore first, then plan the agenda from verified locations only (never write `path:line` stops before reading the code). List 4 to 8 stops, one line each: `path:line`, what the reader learns. Fewer for a quick scope.

## One stop

1. Name the stop (`3/6: where requests are routed`) and show the `path:line`.
2. Show only the relevant lines, then explain them in 3 to 6 sentences. Define a term the first time.
3. Say what connects it to the next stop.
4. Wait. Any reply continues to the next stop. At the first stop only, list the options once: `deeper`, `back`, `skip`, or a question.

Update the agenda when the user redirects. If a question needs a new trace, say so and add it as a stop instead of answering from memory.

## IDE delivery (Zed)

Goal: the user sees the code at each stop in Zed.

- Load the `working-in-zed` skill now: it owns the link format and how to open a file at a line. Use it only for this delivery, never for `direct` or `guide in chat`.
- Local session: at each stop open the file as `working-in-zed` says and show the stop as a clickable link.
- Zed detection: do not run it. The user's choice of `guide in IDE` is enough.
- SSH session: do not run `zed` (no display). Follow the `opening-zed` skill to print the `zed ssh://...` command once, then print the `path:line` of each stop for the user to jump to.
- Branch in a worktree: when the session is in the main tree and the code to tour is on a branch checked out in a worktree (git refuses a second checkout of it), do not switch the user's branch silently. Ask which of these they want, with the cost of each:
  - open the worktree in Zed, replacing the current window (`zed --reuse <worktree>`, hidden in `--help`, replaces the window's workspace), then tour there;
  - remove the worktree and check out the branch in the main tree, only when `git -C <worktree> status --porcelain` is empty and the branch has no commits missing from its remote (otherwise offer the first option only), with `wt remove` as in the `using-worktrunk` skill.
- The chat keeps the explanation; the IDE shows the code. Keep stops identical in both.
- `unverified`: whether Zed's agent "follow" mode works with an external CLI agent. Do not promise it (see `working-in-zed`). Opening the file at the line is the guaranteed behavior.

## Ending

Offer a recap of the stops as a list of `path:line` with one line each, and offer to save it as notes only if the user wants a file.
