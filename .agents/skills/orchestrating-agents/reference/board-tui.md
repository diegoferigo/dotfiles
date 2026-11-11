# The tree and the board TUI: visual spec

What `ledger_graph.py <tasks.toml> tree` and `board` print and what `scripts/ledger_board.py <tasks.toml>` draws. The behavior an agent needs (who writes what, the stale-file guard, the inbox) is in [task-graph.md](task-graph.md#the-board-tui). The flags and output keys of the commands are in `scripts/ledger_graph.py <tasks.toml> <command> --help`.

## Contents

- [The tree](#the-tree)
- [Sort order](#sort-order)
- [The board TUI](#the-board-tui)
- [Keys](#keys)
- [The board view](#the-board-view)
- [The task overlay](#the-task-overlay)
- [Review command](#review-command)
- [Human review mark](#human-review-mark)
- [Replan request](#replan-request)
- [Review feedback](#review-feedback)
- [The inbox view](#the-inbox-view)

## The tree

The epics and their tasks as a text tree that fits the terminal. One row per task on one line, never wrapped: `art symbol title id detail`, with the id after the title and a dim `·`.

- **Symbol per state:** `✔` done, `●` running, a yellow `○` for a running task that waits (`set <id> waiting <reason>`), `▶` ready, `○` waiting, `✖` blocked, `◌` deferred (the whole row dim, sorted after the waiting siblings).
- **Id color by priority:** P0 red, P1 yellow, P2 bright blue, P3 bright black. A task without a priority is dim. Without colors every row has a `PX` prefix before the id (`--` for none), so color is never the only mark.
- **Title:** the default foreground, with `**x**` spans in bold when the asterisks flank a word. A newline or a run of spaces shows as one space. A running task (`running`, `in_progress`, `in_rev`, `returned`) has a bold title. A task that waits on a pending task, a blocked task or a human has a dim title.
- **Italic title:** a task that starts as soon as it can: `next` would start it, or skips it for a write set conflict, a cap or a shared resource, or every dependency it waits on is running. Plain output (piped or `--color never`) has no italic mark, and the branch glyph is `@`.
- Nothing is aligned to the right: the detail parts come right after the id, each after `·`.

### Epic rows

An epic is a section header and only a task has a status. Its row has no symbol, no `blocks N`, no `held by`.

- The id keeps the color of its priority, the title is dim, and the milestone shows in its detail (a leaf shows it only when it differs from its parent epic).
- A progress bar and `done/total` sit at the start of the row: `━` green for the done leaves, yellow for `in_progress` and `in_rev`, `─` for the rest, after a dim `┬ ` connector in the column of its children's `├─` and `└─`. A narrow pane shortens the title first and drops the connector before it cuts the bar.
- The title is drawn in capitals (`EPIC`); `LEDGER_PLAIN_EPIC=1` turns that off.
- Its position comes from its leaves. A task with no epic stays at the root with no header.

### Detail parts

In order after the id, each after `·`:

- the milestone of an epic, or `not broken down` when it has no task;
- a yellow `blocks N` on a task that is not done (N is the number of not started tasks behind it through the deps, nothing when 0);
- a dim comment: `waits <ids> (dep)` with at most two own deps that are not done and `+N` for the others (only on a `pending`, `deferred` or `blocked` task); `inherited` with the deps that come from an epic above; the status of a task in `in_rev` or `returned`; `waits <id> (files)` (the same family as `waits`, the reason in parentheses; spotlighting or selecting the task highlights the holder for both reasons) on a ready task that `next` holds back because its `write_set` overlaps a running or higher-priority ready task; and `awaiting you` on a task blocked on a human, whose question `board` shows;
- the branch after the branch glyph (U+E0A0, `@` without colors), shown when the task has a worktree or a branch, and for a done task only while it has a worktree.

### Width, hidden and collapsed

- The width is the terminal when stdout is one and 80 columns otherwise. `--width N` forces it. A row wider than the width drops, in this order, the branch, what the task waits for and the epic summary until the title keeps a few columns, then ends its title with `…`. The id and `awaiting you` always stay.
- Done tasks are hidden by default, and so is an epic whose leaves are all done. A deferred leaf is still a todo, so it and its epic show. `--all` shows them and opens every epic. `--epic <id>` shows one subtree (a finished epic too) and opens it.
- An epic with no task running, ready or awaiting a human collapses to its own row and says `N tasks hidden`, the open leaves of its subtree. Epics nest at any depth, and a collapsed epic hides its whole subtree.
- The legend below the tree is complete and static: it lists every state, title style, priority color, the epic bar, `blocks N`, `waits X (dep|files)`, the icons `⧉`, `◆`, `✎`, `✉` and the branch mark, whether or not a row shows them. The branch is keyed with the Powerline glyph `\ue0a0` when colored and `@` in plain output. The tree prints no warnings: `check` is the only place that does.
- Colors are on for a terminal, off when piped or when `NO_COLOR` is set, and `--color always|never` forces them.

## Sort order

The `tree` command and the board sort the epics, and the floating tasks with them, at every level by:

1. done first, in file order, when done tasks are shown (`--all`, `a` in the board);
2. a running task (statuses `running`, `in_progress`, `in_rev`, `returned`), a floating task before a running epic, whatever the priority;
3. the priority;
4. the state, ready or awaiting a human before waiting;
5. the milestone as `next` sorts it;
6. the file order.

A deferred leaf sorts after all of these, last among its siblings. So a P1 waiting task stays above a P2 ready one, and among equal priorities the ready one comes first.

An epic takes its state from its leaves: running when a leaf has a running status, else ready when a leaf is ready or awaiting a human, else waiting. The priority of an epic is its own `priority` key, else the best of its leaves that are not done, else the default `P2`, except for an epic that is `done`, which has none (dim id, `--` as the prefix). An epic that is not `done` never sorts as done, even with every leaf done.

## The board TUI

```bash
scripts/ledger_board.py <ledger-dir>/tasks.toml
```

The shebang runs it through `pixi exec` with Python 3.12, `textual` and `rich`. It shows the current state of the file only, no history, and opens on the tree, drawn to the width of its pane and again on resize.

- **Reload:** it polls the file every second and reads it again when it changed, keeping the view and the selected question. It does not read while a `p` or `e` prompt is open (it reads once the prompt closes). `r` reads at once. A reload shows as a toast of about 3 seconds.

- **Header:** the file name, the views as numbered tabs with the active one highlighted, and a yellow `N awaiting you` badge (hidden when N is 0, a click switches to Blockers). The done filter shows as `all` only when it is not the default, and the last error or write follows the tabs. A click on a tab switches view, and is ignored while a prompt or an overlay is open. On a narrow terminal the file name, the badge and the other tabs are dropped before anything wraps.

- **Footer:** it lists only the main keys of the active view: `o` brief, `h` review (both dimmed, still visible, until a task is spotlit in the tree, a card is selected on the board or a row is selected in Blockers), `p` priority, `tab` next view, `?` help and `q` quit. A key that does nothing in the view is hidden. A chip at the left names the target (`target: <id>`) or says how to get one (`click an id`, `select a card`, `press c for columns`); Warnings has none. The other keys (`f F y R x a m l c r b`, the arrows and `pgup/pgdn`) work but live in the help only. `p` and `R` stay normal with no target: `p` opens its prompt, `R` replans the remaining tasks, `x` takes the selected row.

  | Key           | Tree                              | Board (cards)  | Board (overview) | Blockers       | Warnings       |
  | ------------- | --------------------------------- | -------------- | ---------------- | -------------- | -------------- |
  | `o`           | needs a target                    | needs a target | needs a target   | needs a target | absent         |
  | `h f F`       | needs a target                    | needs a target | needs a target   | absent         | absent         |
  | `y`           | needs a target (a task in review) | same           | same             | absent         | absent         |
  | `p`           | works (menu, or prompt)           | works          | works (prompt)   | works (prompt) | works (prompt) |
  | `R x a m l`   | works                             | absent         | absent           | absent         | absent         |
  | `c`           | absent                            | works          | works            | absent         | absent         |
  | `b r ? q tab` | works                             | works          | works            | works          | works          |

- **Writes:** the board writes no other field than the ones in [task-graph.md](task-graph.md#the-board-tui). It never rewrites the file from memory: it edits the `priority` line, the `human_review` line, or the `status`, `blocker` and `answer` lines of the task it unblocks, and it adds a file to `replan-requests/` on `R`, and it adds and sets `ready` in a file of `feedback/` (the poll saves the draft, `f` sends it). `escape` cancels a prompt without writing. A write typed in an overlay is checked against the read the overlay was drawn from, so a file that changed while the overlay was open refuses it with the usual `changed since it was read` error. The mtime is checked once more just before the rename, which narrows but does not close the window between the read and the write.

### Keys

- `tab` next view (Tree, Board, Blockers, Warnings); `shift+tab` previous view, wrapping around.
- `c` the board as the text overview, or back as the columns (the default). Kept on a reload, not written to disk.
- `r` read the file again. A broken file keeps the last good view and shows the error in the status line.
- `a` show or hide the done tasks in the tree (the columns always show them). `m` show or hide the milestones in the tree, hidden by default.
- `l` open the legend of the tree in an overlay; `l` or `Esc` closes it. It also lists the icons the board draws, always: the copy icon `⧉` (copies the hunk command, `y` in the overlay), the mark `◆` (your review, `h`), and the feedback marks `✎` (drafts, `f` sends) and `✉` (sent, `F` restores). The ones no row of the tree shows now are listed the same way; the icon and label columns are aligned and a line is at most 40 columns.
- Click a task id in the tree (or a card of the board), then press a key: `h f F R p o y` act on the clicked task, `x` fades around it. `Esc` clears the spotlight, cancels a prompt or closes an overlay. The footer lists only the main keys; `?` opens the help with all of them, a card of sections (`Act on a task`, `Views`, `Anything else`) whose headings are bold in the normal text color, with a dim rule after them, the keys are bold cyan (an ANSI color) and the labels are dim.
- `x` in the tree, fade every row but the spotlit task and the tasks that depend on it. Again, or `Esc`, clears it. A click on an id or on a `blocks N` does the same, and the blockers of the clicked task take a second color (cyan). With no task it says `no task selected`.
- `o` open the overlay of the spotlit task in the tree, the selected card on the board, the selected row in Blockers ([The task overlay](#the-task-overlay)).
- `y` copies the hunk command of the spotlit task in the tree or the selected card on the board, like the copy icon `⧉` ([Review command](#review-command)). A task that is neither running nor in review has none and says so.
- The hint of a key with no target depends on the view: `click an id first` in the tree, `press c for columns` in the text overview, `select a card` in the columns.
- A click on the copy icon `⧉` of a running task or one in review copies its hunk command ([Review command](#review-command)). A click on the title or the card still opens the overlay.
- `h` marks the task for your review, or clears the mark, after a confirmation menu (cancel is the default). The target is the task spotlit by a click on its id or `x` in the tree, the selected card in the columns, or the task of the open overlay. It does nothing in the text overview and in Blockers, and says so when no task is selected. A task that is done cannot be marked. It writes only the `human_review` line ([Human review gate](task-graph.md#human-review-gate)).
- `R` in the tree asks the orchestrator to replan ([Replan request](#replan-request)).
- `f` sends the saved hunk comments of the task, `F` applies them to hunk again, after a confirmation menu each (cancel is the default). The target is the same as for `h`. Both do nothing in the text overview and in Blockers ([Review feedback](#review-feedback)).
- `p` opens the priority menu (P0 to P3) of the spotlit task in the tree or the selected card on the board, like `h`, and writes the choice (only the `priority` line) and reloads. With no such target it asks for `<id> <P0..P3>` instead, and in Blockers that prompt pre-fills the id of the selected row.
- `e` does nothing in the views: the answer to a blocked task is typed with `e` in the overlay of the task, which a click or `enter` on a row of Blockers opens.
- `up`, `down`, `j`, `k` select the previous or the next row in Blockers and the card in the columns. In the other views they scroll. `enter` in the columns opens the overlay of the selected card.
- `q` quit.

### The board view

One column per status with a bordered card per task, or, with `c`, the text overview (In progress, Todo, Done, each in the order `next` picks up).

- **Card:** the border is rounded and in the color of its priority (bold when selected). The right of the title border shows the priority, or `next · P1` on the task `next` would start first. The body has the state symbol, the title on two lines at most, the id dim and `deferred` after it. Deferred cards come last in Todo. A task that waits on a human has a violet bar at the left, and so does a marked task in review. A marked task shows the icon `◆` after its id. A done card is one line.
- **Select:** a click on the border of a card or on its priority tag only selects it (the same selection as `j`/`k`, bold border), with no overlay, so `h p o y f F R` then act on it.
- **Open:** a click on the body of a card (title, id line) opens its overlay, as `enter` does. A card never expands in place.
- **Responsive:** Done folds into a count under 115 columns, and under 86 the text overview shows again, which the status line says. `NO_COLOR` keeps the borders and drops the colors.

### The warnings view

One row per warning `check` prints, in the same order. A click on a task id in a row opens its overlay. With no warning it says `no warnings`. The tree does not show warnings.

### The task overlay

`o`, `enter` on a card or a click on a task opens it, centered and scrollable (up/down, `j`/`k`, `pgup`/`pgdn`, `home`/`end`), with the brief (`briefs/<id>.md` next to the file) rendered as Markdown. `Esc`, `q` or a click outside closes it. A task without a brief shows its state, priority, epic, deps (with their status), blocker, worktree and branch (the last four only when set).

- **Border title:** `<id> · <state symbol> <status> · <priority>`, in the colors of the tree.
- **Content:** the title first, in bold. A first `# <id>`, `# <title>` or `# <id>: <title>` heading of the brief is skipped as a repeat.
- **Sections:** `Question` first for a task that waits on a human: its `inbox/<id>.md` (else the question), then `e to answer`. `Review` next for a task in review ([Review command](#review-command)). `Children` for an epic: a count per status, then a row per child not done; a click on an id opens that child. `History` last: the last 10 events of `events/<id>.jsonl` (`ts event detail`, newest last), nothing when the log is missing.
- **`e`** asks for the answer only, in a box that wraps a long text: `enter` submits, `alt+enter` inserts a newline (a terminal that sends the same bytes for both cannot tell them apart), `ctrl+w` and `alt+backspace` delete the word to the left. A submit closes the overlay and writes like the `p` prompt; a write that fails keeps the typed text in the prompt of the board, and so does a file that changed while the question is still the one shown, which is retried once. A task without a question has no `e`.
- **Review mark:** a task marked `human_review` shows `◆ marked for your review, h clears the mark` under the title, and `h` opens the confirmation as in the views.
- **Feedback:** a task with a feedback file shows a `Feedback` section after `Review`: `✎ N comments saved from hunk, not sent yet` with `f to send, F to apply them to hunk again`, or `✉ N comments sent, the orchestrator reads them when you tell it to` with `F to apply them to hunk again`. `f` and `F` open their confirmation as in the views, and a sent draft leaves the overlay open and refreshed.
- **Pick menus:** `p`, or a click on the priority in the border title, opens a menu (P0 to P3, the current one marked `(current)`). `s`, or a click on the state, opens the menu of the transitions a human owns: `deferred` to `pending` (activate) and `pending` to `deferred` (park); any other state shows a line saying the orchestrator owns it. Every menu, including the confirmation ones, shows the task id on the border as bold text in the border color, with no filled background (shortened with `…` in the middle when long) and the action (`human review`, `priority`) as a bold line inside the box, so the action is never cut. Each option leads with its glyph in a fixed column (`●` in the color of the priority or the state, `◆` for a review, `✉` for send, `✎` for restore, `✕` for cancel), then the label in bold in the default color. Only the glyph is colored. The description is dim, always on its own line under the label and indented to it, and a blank line separates the options (`cancel` is a normal option: `✕ cancel` with `do nothing` below). The selected option has no background and keeps the colors of the others, the default text color for the bold label and the dim one for the description (also on a 40-column terminal, where the description wraps inside the box), and a `▌` accent bar on each of its lines. The key line closes the box, with `enter` and `esc` in bold accent text like the keys of the footer and the help card, with no block behind them. Arrows or `j`/`k` and `enter` pick, a click on a row picks, `esc` cancels.
- **Writes:** a pick writes through `set` and the stale-file guard of the overlay, then the overlay stays open and refreshed. A refused write leaves the file as it is and shows the error.

### Review command

For a task with a worktree and a branch (its own, else its closest epic's) whose status is `in_progress` or `in_rev` (so you can steer a running task with feedback too), the board offers the command that opens its diff in hunk:

```bash
cd <worktree> && hunk diff <base>...<branch>
```

`<base>` is the ref `resume` uses for `ahead N`: the `Stack tip:` of `ORCHESTRATION.md`, else `integration`. A task without a worktree or a branch, or in any other status, has no icon and no section. The command is local: a remote task is out of scope.

- **Overlay:** a `Review` section after `Question` (and before the brief) with the command as text, a copy icon `⧉` after it and `y to copy`. A click on the icon or `y` copies the full command and shows a `copied` toast. The command stays on screen as text, so it can be selected with the terminal when the clipboard does not work.
- **Tree and Board:** the icon sits right after the id on the row of such a task (the tree) or on the id line of its card (the columns), and after the id in the text overview. A click on it copies the command and does not open anything. A click on the title or the card opens the overlay as before, and a click on the id in the tree fades the rows as before. The icon is a glyph, never a color alone, and the tree reserves two columns for it when any task shows one.
- **Clipboard:** it uses Textual's `copy_to_clipboard`, an OSC 52 escape sequence that not every terminal honors. The comments are not exported: the orchestrator reads them with `hunk session comment list --repo <worktree> --type user --json` when asked.

## Human review mark

`h` toggles `human_review = true` on a task that is not done, after a confirmation menu (see [Keys](#keys) for the target). A marked task keeps a `◆` after its id (before the copy icon `⧉` when it has one) in the tree, on its card and in the text overview, and the tree reserves two more columns for it. A marked task in `in_rev` counts as awaiting you: its card gets the violet bar and sorts first in its column, the header badge counts it, and Blockers lists it with a violet `◆` instead of the red `✖`. The overlay says `◆ marked for your review, h clears the mark` under the title. `set <id> status done` refuses the task until the mark is cleared. The legend of the tree always lists `◆` and `⧉`.

## Replan request

`R` in the tree opens a confirmation menu titled `replan request` (cancel is the default) with `replan the remaining tasks` and, only when the spotlit task (a click on its id, or `x`) is an epic, `replan the open tasks of <epic>`. A choice opens the prompt below the tree for an optional one-line note: `enter` sends the request (an empty prompt sends none), `alt+enter` is refused as a multi-line note, `esc` cancels without writing. Nothing is drawn on the tree for a request, so the legend has no new mark. It does nothing outside the tree and while a prompt or an overlay is open. The file, its keys and what the orchestrator does with it are in [Replan requests](task-graph.md#replan-requests).

## Review feedback

While the board runs, a poll every 2 seconds saves the comments the human wrote in hunk for each running task and task in review as a draft ([Review feedback](task-graph.md#review-feedback) has the file and the flow). A task with a draft shows `✎N` after its id (and after its `◆` and `⧉`) in the tree, on its card and in the text overview, and `✉N` once the draft is sent. The tree reserves the width of the label. The poll runs in a worker thread, never raises and draws again only when no overlay or menu is open. `f` opens a menu titled `<id>: send feedback` with `cancel` and `send N comments`. `F` opens `<id>: restore feedback` with `cancel` and `apply N comments to hunk`, and says how it went in a message: `applied N comments to hunk`, or `hunk did not take the comments: <what hunk said>`. The restore against a real hunk is unverified. The legend of the tree always explains the marks: `✎N draft, ✉N sent, f send, F restore`.

## The inbox view

The third view, Blockers, is a plain list of the tasks that wait on you, most urgent first (priority, then file order). A row is a red `✖` (a violet `◆` for a task marked for your review), the id, the title and the priority. `up`, `down`, `j` and `k` select a row, and `enter`, `o` or a click opens the overlay of the task, where the question, its `inbox/<id>.md` context and the keys `e` (answer) and `p` (priority) are. The context file is a convention, not a key of `tasks.toml`. The sections are the ones a human needs to decide (written in the human's conversation language; ids, commands, file paths and code stay as they are):

```markdown
## Question

Retry three times or fail fast?

## Context

`src/retry.py` retries forever today, and the integration test times out after 30 s.

## Options

### Retry three times

What happens: the client hides a flaky backend. Cost: one more config key. Risk: a slow failure looks like a hang. Reversible.

### Fail fast

What happens: the caller sees the error at once. Cost: every caller needs its own retry. Risk: a flaky backend breaks the run. Reversible.

## Recommendation

Retry three times, because the backend is flaky and the caller cannot tell.
```
