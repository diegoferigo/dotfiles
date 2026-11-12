#!/usr/bin/env -S pixi exec --spec python=3.12 --spec textual --spec rich python
"""Show the task graph of an orchestration ledger, and steer it by hand.

Usage: `ledger_board.py <tasks.toml>`. The board shows the current state of the
file as a tree (`m` shows the milestones, hidden by default), as the Todo, In progress, Blocked and Done columns or, with `c`, as
the In progress, Todo and Done overview (the overview shows when the
main pane is under 86 columns wide, and Done folds into a count under 115), or
as the Blockers list of the tasks that wait on a human. A task opens in an
overlay with its brief (`briefs/<id>.md` next to the file). `?` lists the keys.
It reloads on `r` and whenever the file changes on disk, and it writes nothing
but the priority a human types on `p`, the answer to a blocked task a human
types on `e` in the overlay of the task, the human review mark that `h`
sets or clears after a confirmation, and the replan request that `R` writes in
`replan-requests/` after a confirmation, where the orchestrator reads it. While
a task is `in_progress` or `in_rev` it also polls the hunk session of its worktree and saves
the comments of the human in `feedback/<id>.json`, which `f` marks ready after
a confirmation and `F` applies again to a new hunk session.
"""


import contextlib
import dataclasses
import json
import pathlib
import re
import shutil
import subprocess
import sys
import textwrap
import typing

import rich.box
import rich.cells
import rich.console
import rich.markdown
import rich.panel
import rich.rule
import rich.style
import rich.table
import rich.text
import textual.app
import textual.binding
import textual.color
import textual.containers
import textual.events
import textual.geometry
import textual.message
import textual.screen
import textual.widget
import textual.widgets
import textual.widgets.option_list

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import ledger_graph

VIEWS = ("tree", "board", "inbox", "warnings")
# The status changes a human owns, as (value, label) by current status.
OWNED_TRANSITIONS = {
    "deferred": ("pending", "pending (activate)"),
    "pending": ("deferred", "deferred (park)"),
}
# Columns between the corner of the border and the first character of its title.
TITLE_INDENT = 3
ASKS = {
    "priority": "<id> <P0..P3>",
    "answer": "<id> <answer>",
    "replan": "an optional note for the replan, enter sends the request",
}
CONTEXT_DIR = "inbox"
BRIEF_DIR = "briefs"
HISTORY_LIMIT = 10
BRIEF_HINT = "esc or q to close, up/down to scroll"
# The first heading of a context file that the Question heading of the overlay replaces.
QUESTION_TITLES = frozenset(
    {"question", "questions", "the question", "domanda", "domande", "la domanda", "quesito"}
)
# Left aligned, no underline, so the heading of a brief is not a centered title.
# H2 and H3 keep the accent color of the Markdown renderer, and none is dim.
HEADING_STYLES = {"h1": "bold", "h2": "bold magenta", "h3": "bold magenta"}
POLL_SECONDS = 1.0
# How often the hunk sessions of the `in_progress` and `in_rev` tasks are asked for their comments, and how long one call may take.
HUNK_POLL_SECONDS = 2.0
HUNK_TIMEOUT = 5.0
# What a call to hunk gives back: whether it succeeded, and its output (the first line of the error when it did not).
HunkRunner = typing.Callable[[list[str], str], tuple[bool, str]]
# The ANSI theme of Textual 8.2.8 (there is no "textual-ansi"): the background
# and the 16 colors are the ones of the terminal, which decides dark or light.
ANSI_THEME = "ansi-dark"
# The fade of the rows a clicked task does not hold up: almost the background
# (#1e1e1e, or a light terminal), still readable on a close look.
FADE_DARK = "#3a3a3a"
FADE_LIGHT = "#d4d4d4"
BLOCKER_STYLE = rich.style.Style(color="cyan")
# The columns of the board view, one card per task. The text overview folds
# Blocked into Todo, the columns do not.
BOARD_COLUMNS = (
    ("Todo", {"pending", "deferred"}),
    ("In progress", ledger_graph.RUNNING),
    ("Blocked", {"blocked"}),
    ("Done", {"done"}),
)
# The least a column keeps, and the gap between two. Below the width of the four
# columns Done folds into a count, and below the three the text overview shows.
COLUMN_MIN_WIDTH = 28
COLUMN_GAP = 1
# The rule under the header of a column, by state; Todo stays neutral.
COLUMN_RULES = {"In progress": "yellow", "Blocked": "red", "Done": "green"}
# The border of the state menu: Todo has no color of its own, so pending takes the ready symbol's.
STATE_STYLES = {
    "pending": "cyan",
    "deferred": "bright_black",
    "in_progress": COLUMN_RULES["In progress"],
    "blocked": COLUMN_RULES["Blocked"],
    "done": COLUMN_RULES["Done"],
}
CARD_TITLE_LINES = 2
# The priority bindings of the app, which a modal screen must keep for itself.
PRIORITY_ACTIONS = ("switch", "goto", "move", "page")
# The views where a key works. An action that is not here works in every view.
VIEW_ACTIONS = {
    "toggle_done": ("tree",),
    "toggle_milestones": ("tree",),
    "legend": ("tree",),
    "spotlight": ("tree",),
    "columns": ("board",),
    "brief": ("tree", "board", "inbox"),
    "copy": ("tree", "board"),
    "review": ("tree", "board"),
    "replan": ("tree",),
    "send": ("tree", "board"),
    "restore": ("tree", "board"),
}
# The keys that act on a task and wait, dimmed in the footer, until there is one.
# p, R and x are not here: they work with no target (a prompt, the remaining tasks, the selected row).
TARGET_ACTIONS = frozenset({"brief", "copy", "review", "send", "restore"})
CLICK_HINT = "click an id"


@dataclasses.dataclass
class _Layout:
    """The cards of the board columns in order, and the first line and height of each."""

    order: list[str] = dataclasses.field(default_factory=list)
    spans: dict[str, tuple[int, int]] = dataclasses.field(default_factory=dict)


class Pane(textual.containers.VerticalScroll):
    """The scroll of the main view, which draws again when its width changes.

    A `Resize` event does not bubble, and the app sees its own resize before
    the layout gives the pane the new width, so the tree has to follow the
    size the pane itself reports.
    """

    def on_resize(self, event: textual.events.Resize) -> None:

        app = typing.cast("BoardApp", self.app)
        app.reveal = True
        app.draw()


# The sections of the help card: the keys, then one short label that fits a 48 column card.
HELP_TOP = "Click an id or a card, then press a key"
HELP_SECTIONS = (
    (
        "Act on a task",
        (
            ("o", "open the brief"),
            ("h", "mark for review"),
            ("p", "set the priority"),
            ("f", "send the hunk comments"),
            ("F", "reapply the comments"),
            ("y", "copy the hunk command"),
            ("R", "replan (tree)"),
            ("x", "show what it blocks"),
        ),
    ),
    (
        "Views",
        (
            ("tab", "next view"),
            ("shift+tab", "previous view"),
            ("a", "done tasks (tree)"),
            ("m", "milestones (tree)"),
            ("l", "legend (tree)"),
            ("c", "text/columns (board)"),
        ),
    ),
    (
        "Anything else",
        (
            ("up down j k", "move"),
            ("pgup pgdn", "scroll"),
            ("r", "reload the file"),
            ("b", "dark background"),
            ("esc", "clear, cancel, close"),
            ("?", "this help"),
            ("q", "quit"),
        ),
    ),
)
HELP_WIDTH = 42
HELP_KEYS = 12
# An ANSI color name, so the terminal palette picks it, like the other accents here.
HELP_KEYS_STYLE = "bold cyan"


def help_card() -> rich.text.Text:
    """The help of an overlay, as a card like the legend: sections of a key column and a short label."""

    # No base style: a base "dim" would dim the bold headings and keys too.
    card = rich.text.Text()
    card.append(HELP_TOP, "dim")
    for heading, rows in HELP_SECTIONS:
        card.append("\n\n")
        card.append(f"{heading} ", "bold")
        card.append("─" * (HELP_WIDTH - len(heading) - 1), "dim")
        for keys, label in rows:
            card.append("\n")
            card.append(keys.ljust(HELP_KEYS), HELP_KEYS_STYLE)
            card.append(label, "dim")
    return card



class TargetFooter(textual.widgets.Footer):
    """The footer, with a chip at its left that names the task the keys act on."""

    def compose(self) -> textual.app.ComposeResult:

        chip = getattr(self.app, "target_chip", None)
        if chip is not None and self._bindings_ready and (text := chip()):
            yield textual.widgets._footer.FooterLabel(text, classes="target-chip")
        yield from super().compose()


class HelpScreen(textual.screen.ModalScreen[None]):
    """The keys of the board, closed with `?`, `Esc` or `q`."""

    BINDINGS: typing.ClassVar[list[textual.binding.BindingType]] = [
        ("escape", "dismiss", "close"),
        ("question_mark", "dismiss", "close"),
        ("q", "dismiss", "close"),
    ]
    CSS = """
    HelpScreen { align: center middle; }
    #help-pane { width: 48; max-width: 100%; height: auto; max-height: 100%;
            border: round $primary; background: $surface; padding: 0 1; }
    #help { color: $text; }
    """

    def compose(self) -> textual.app.ComposeResult:

        pane = textual.containers.VerticalScroll(id="help-pane")
        pane.border_title = "Help"
        with pane:
            yield textual.widgets.Static(help_card(), id="help")
        yield textual.widgets.Footer()


class LegendScreen(textual.screen.ModalScreen[None]):
    """The legend of the tree, an overlay closed with `l` or `Esc`."""

    BINDINGS: typing.ClassVar[list[textual.binding.BindingType]] = [
        ("escape", "dismiss", "close"),
        ("l", "dismiss", "close"),
    ]
    CSS = """
    LegendScreen { align: center middle; }
    #legend-pane { width: 44; max-width: 100%; height: auto; max-height: 100%;
            border: round $primary; background: $surface; padding: 1 1; }
    """

    def __init__(self, legend: rich.text.Text) -> None:

        super().__init__()
        self.legend = legend

    def compose(self) -> textual.app.ComposeResult:

        pane = textual.containers.VerticalScroll(id="legend-pane")
        pane.border_title = "Legend"
        with pane:
            yield textual.widgets.Static(self.legend, id="legend")
        yield textual.widgets.Footer()


class BriefHeading(rich.markdown.Heading):
    """A Markdown heading that is left aligned, bold, and never underlined."""

    def __init__(self, tag: str) -> None:

        super().__init__(tag)
        self.style_name = HEADING_STYLES.get(tag, "bold")

    def __rich_console__(
        self, console: rich.console.Console, options: rich.console.ConsoleOptions
    ) -> rich.console.RenderResult:

        text = self.text.copy()
        text.justify = "left"
        yield text


class BriefMarkdown(rich.markdown.Markdown):
    """Markdown with the headings of `BriefHeading`."""

    elements: typing.ClassVar[dict[str, type[rich.markdown.MarkdownElement]]] = {
        **rich.markdown.Markdown.elements,
        "heading_open": BriefHeading,
    }


class PickScreen(textual.screen.ModalScreen[str | None]):
    """A short list of choices, closed with the one picked or with None on `Esc`."""

    BINDINGS: typing.ClassVar[list[textual.binding.BindingType]] = [
        ("escape", "cancel", "cancel"),
        textual.binding.Binding("j", "move('down')", show=False),
        textual.binding.Binding("k", "move('up')", show=False),
    ]
    CSS = """
    PickScreen { align: center middle; }
    #pick-pane { width: 64; max-width: 100%; height: auto; max-height: 100%;
            border: round $primary; border-title-color: $primary;
            border-title-style: bold; background: $surface; padding: 0 1; }
    #pick-action { text-style: bold; margin-bottom: 1; }
    #pick-list { height: auto; border: none; padding: 0; background: $surface; }
    #pick-list:focus { background-tint: transparent; }
    #pick-list > .option-list--option-highlighted {
            color: $foreground; background: $surface; text-style: none; }
    #pick-keys { margin-top: 1; color: $text-muted; }
    """
    # Columns a row can use: the pane minus its border, its padding and the glyph column.
    ROW_WIDTH = 64 - 4 - 3
    PRIORITY_GLYPH = "●"
    CANCEL_GLYPH = "✕"
    # The glyph of a "confirm" choice, by the action named in the title.
    CONFIRM_GLYPHS: typing.ClassVar[dict[str, str]] = {
        "human review": ledger_graph.HUMAN_REVIEW_ICON,
        "send feedback": ledger_graph.DRAFT_ICON,
        "restore feedback": ledger_graph.SENT_ICON,
    }

    def __init__(
        self,
        title: str,
        choices: list[tuple[str, str] | tuple[str, str, str]],
        current: str,
        styles: dict[str, str] | None = None,
    ) -> None:
        """A choice is (value, label) or (value, label, description).

        A title of the form `<id>: <action>` shows the id as a chip on the border and the action inside.
        """

        super().__init__()
        self.styles_by_value = styles or {}
        self.task_id, _, self.action = title.rpartition(": ")
        if not self.task_id:
            self.task_id, self.action = "", title
        self.choices = [(value, label, *rest[:1]) for value, label, *rest in choices]
        self.current = current

    def chip(self) -> str:
        """The id, shortened in the middle when it does not fit on the border."""

        room = self.ROW_WIDTH - 2
        task_id = self.task_id
        if len(task_id) > room:
            keep = room - 1
            task_id = f"{task_id[: keep - keep // 2]}…{task_id[len(task_id) - keep // 2 :]}"
        return task_id

    def glyph(self, value: str) -> tuple[str, str]:
        """The icon of a choice and its own style."""

        if value == "cancel":
            return self.CANCEL_GLYPH, "dim"
        style = self.styles_by_value.get(value, "")
        if style:
            return self.PRIORITY_GLYPH, style
        icon = self.CONFIRM_GLYPHS.get(self.action, "↻")
        return icon, ledger_graph.AWAITING_STYLE if icon == "◆" else "bold"

    def row_width(self) -> int:
        """Columns a row can use, less on a terminal narrower than the pane."""

        return min(self.ROW_WIDTH, self.app.size.width - 4 - 3)

    def prompt(
        self, value: str, label: str, description: str = "", *, selected: bool = False
    ) -> rich.text.Text:
        """An option: the accent bar, the glyph column, the bold label, the dim description below and a blank line.

        Only the glyph is colored. The accent bar `▌` marks the selected option on every line of it.
        """

        icon, icon_style = self.glyph(value)
        indent = "    "
        accent = "▌" if selected else " "
        text = rich.text.Text()
        text.append(accent, "bold cyan")
        text.append(f" {icon} ", icon_style)
        text.append(label, "bold")
        if value == self.current and value != "cancel":
            text.append(" (current)", "dim")
        lines = textwrap.wrap(description, self.row_width() - 6) if description else []
        for line in [*lines, ""] if lines else []:
            text.append("\n")
            text.append(accent, "bold cyan")
            text.append(indent + line, "dim")
        return text

    def compose(self) -> textual.app.ComposeResult:

        options = [
            textual.widgets.option_list.Option(self.prompt(value, label, *rest), id=value)
            for value, label, *rest in self.choices
        ]
        pane = textual.containers.Vertical(id="pick-pane")
        if self.task_id:
            pane.border_title = self.chip()
        with pane:
            yield textual.widgets.Static(self.action, id="pick-action")
            yield textual.widgets.OptionList(*options, id="pick-list")
            yield textual.widgets.Static(
                "[bold $accent]enter[/] confirm   [bold $accent]esc[/] cancel", id="pick-keys"
            )
        yield textual.widgets.Footer()

    def on_mount(self) -> None:

        picker = self.query_one("#pick-list", textual.widgets.OptionList)
        picker.highlighted = next(
            (n for n, (value, *_) in enumerate(self.choices) if value == self.current), 0
        )
        picker.focus()

    def border_style(self, value: str | None) -> str:
        """The style of an option, else the one of the current choice."""

        return self.styles_by_value.get(value or "", self.styles_by_value.get(self.current, ""))

    def on_option_list_option_highlighted(
        self, event: textual.widgets.OptionList.OptionHighlighted
    ) -> None:
        """Move the accent bar to the highlighted option and color the border like it."""

        picker = self.query_one("#pick-list", textual.widgets.OptionList)
        for value, label, *rest in self.choices:
            picker.replace_option_prompt(
                value, self.prompt(value, label, *rest, selected=value == event.option_id)
            )

        style = self.border_style(event.option_id)
        if style:
            pane = self.query_one("#pick-pane")
            pane.styles.border = ("round", textual.color.Color.parse(f"ansi_{style}"))

    def action_move(self, where: str) -> None:

        picker = self.query_one("#pick-list", textual.widgets.OptionList)
        getattr(picker, f"action_cursor_{where}")()

    def action_cancel(self) -> None:

        self.dismiss(None)

    def on_option_list_option_selected(
        self, event: textual.widgets.OptionList.OptionSelected
    ) -> None:

        event.stop()
        self.dismiss(None if event.option_id == self.current else event.option_id)

    def on_click(self, event: textual.events.Click) -> None:
        """Cancel on a click outside the pane."""

        if event.widget is self:
            self.dismiss(None)


class AnswerBox(textual.widgets.TextArea):
    """A prompt that wraps a long text on several lines.

    `Enter` submits and `Alt+Enter` inserts a newline. A pasted or set text
    keeps its newlines, with CRLF and CR turned into LF. The word deletion keys
    are those of `TextArea` (`Ctrl+W`, `Alt+Backspace`).
    """

    DEFAULT_CSS = """
    AnswerBox { height: auto; max-height: 6; }
    """

    class Submitted(textual.message.Message):
        """Posted when `Enter` is pressed."""

        def __init__(self, box: "AnswerBox", value: str) -> None:
            super().__init__()
            self.box = box
            self.value = value

    def __init__(self, *, id: str) -> None:
        super().__init__(
            soft_wrap=True,
            show_line_numbers=False,
            tab_behavior="focus",
            placeholder=ASKS["priority"],
            id=id,
        )

    @property
    def value(self) -> str:

        return self.text

    @value.setter
    def value(self, value: str) -> None:

        self.text = _lf(value)

    def end_cursor(self) -> None:
        """Put the cursor at the end of the text."""

        self.move_cursor(self.document.end)

    def insert_at_cursor(self, text: str) -> None:
        """Replace the selection with the text, and put the cursor after it."""

        if result := self._replace_via_keyboard(_lf(text), *self.selection):
            self.move_cursor(result.end_location)

    async def _on_key(self, event: textual.events.Key) -> None:

        if event.key == "enter":
            event.stop()
            event.prevent_default()
            self.post_message(self.Submitted(self, self.text))
            return

        if event.key == "alt+enter":
            event.stop()
            event.prevent_default()
            self.insert_at_cursor("\n")
            return

        await super()._on_key(event)

    async def _on_paste(self, event: textual.events.Paste) -> None:

        event.stop()
        self.insert_at_cursor(event.text)


def _lf(text: str) -> str:
    """The text with CRLF and CR line breaks turned into LF."""

    return text.replace("\r\n", "\n").replace("\r", "\n")


class BriefScreen(textual.screen.ModalScreen[tuple[str, str] | None]):
    """The brief of a task, or its state without one, an overlay closed with `Esc`, `q` or a click outside.

    `e` answers its question when it has one: it opens a prompt and closes the
    overlay with the typed write, which the app runs like the prompt of the
    inbox does. `p` and `s` (or a click on the priority or the state in the
    border) open a `PickScreen` and write in place, keeping the overlay open.
    `h` asks the same way to mark the task for review or to clear the mark, and
    `f` and `F` to send the saved hunk comments or apply them to hunk again.
    """

    BINDINGS: typing.ClassVar[list[textual.binding.BindingType]] = [
        ("escape", "close", "close"),
        ("q", "close", "close"),
        ("e", "ask('answer')", "answer"),
        ("p", "pick('priority')", "priority"),
        ("s", "pick('state')", "state"),
        ("h", "review", "review"),
        ("f", "send", "send"),
        ("F", "restore", "restore"),
        ("y", "copy", "copy"),
        textual.binding.Binding("down", "scroll('down')", show=False),
        textual.binding.Binding("j", "scroll('down')", show=False),
        textual.binding.Binding("up", "scroll('up')", show=False),
        textual.binding.Binding("k", "scroll('up')", show=False),
        textual.binding.Binding("pagedown", "scroll('page_down')", show=False),
        textual.binding.Binding("pageup", "scroll('page_up')", show=False),
        textual.binding.Binding("home", "scroll('home')", show=False),
        textual.binding.Binding("end", "scroll('end')", show=False),
    ]
    CSS = """
    BriefScreen { align: center middle; }
    #brief-pane { width: 90; max-width: 100%; height: auto; max-height: 100%;
            border: round $primary; background: $surface; padding: 0 1; }
    BriefScreen.asking #brief-pane { max-height: 70%; }
    #overlay-ask { width: 90; max-width: 100%; }
    BriefScreen Footer { overflow-x: hidden; }
    """

    def __init__(
        self,
        title: rich.text.Text,
        content: rich.console.RenderableType,
        task_id: str = "",
        *,
        answerable: bool = False,
        priority: str = "",
        command: str = "",
    ) -> None:

        super().__init__()
        self.heading = title
        self.content = content
        self.task_id = task_id
        self.answerable = answerable
        self.priority = priority
        self.command = command
        self.asked = ""

    def compose(self) -> textual.app.ComposeResult:

        pane = textual.containers.VerticalScroll(id="brief-pane")
        self.paint(pane)
        with pane:
            yield textual.widgets.Static(self.content, id="brief")
        yield AnswerBox(id="overlay-ask")
        yield textual.widgets.Footer()

    def on_mount(self) -> None:

        self.query_one("#overlay-ask").display = False

    def paint(self, pane: textual.widget.Widget) -> None:
        """Set the border title and the border color of the pane."""

        pane.border_title = self.heading
        # The border takes the color of the priority, as the ids and the cards do.
        style = ledger_graph.PRIORITY_STYLES.get(self.priority)
        if style:
            pane.styles.border = ("round", textual.color.Color.parse(f"ansi_{style}"))

    def update_from(self, fresh: "BriefScreen") -> None:
        """Show the title, the border and the content of a screen drawn from a newer read."""

        self.heading, self.content = fresh.heading, fresh.content
        self.priority, self.answerable = fresh.priority, fresh.answerable
        self.command = fresh.command
        self.paint(self.query_one("#brief-pane"))
        self.query_one("#brief", textual.widgets.Static).update(self.content)
        self.refresh_bindings()

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Offer the answer only for a task with a question, the copy only with a command, the menus for any task."""

        if action == "ask":
            return bool(self.task_id) and self.answerable
        if action == "copy":
            return bool(self.command)
        if action == "pick":
            return bool(self.task_id)
        if action == "review":
            return bool(self.task_id)
        if action in ("send", "restore"):
            return bool(self.task_id)

        return True

    def action_pick(self, kind: str) -> None:
        """Open the menu of the priority or of the state, which writes and keeps the overlay."""

        if not self.check_action("pick", (kind,)):
            return

        app = typing.cast("BoardApp", self.app)
        task = app.graph.tasks[self.task_id] if app.graph else None
        if task is None:
            return

        if kind == "priority":
            choices = [(p, p) for p in sorted(ledger_graph.PRIORITIES)]
            screen = PickScreen(
                f"{self.task_id}: priority",
                choices,
                task.priority,
                styles=dict(ledger_graph.PRIORITY_STYLES),
            )
        else:
            offered = OWNED_TRANSITIONS.get(task.status)
            choices: list[tuple[str, str] | tuple[str, str, str]] = [(task.status, task.status)]
            if offered and app.graph.question(self.task_id) is None:
                choices.append(offered)
            else:
                choices = [(task.status, task.status, "the orchestrator owns this state")]
            screen = PickScreen(
                f"{self.task_id}: state",
                choices,
                task.status,
                styles=STATE_STYLES,
            )

        self.app.push_screen(screen, lambda value: self.picked(kind, value))

    def picked(self, kind: str, value: str | None) -> None:
        """Write the choice of a menu, unless it was cancelled or is the current one."""

        if value:
            typing.cast("BoardApp", self.app).apply_pick(self, kind, value)

    def heading_part(self, kind: str) -> tuple[int, int]:
        """The first column and the width of the priority or the state in the border title."""

        parts = self.heading.plain.split(" · ")
        if len(parts) != 3:
            return 0, 0

        if kind == "priority":
            return len(parts[0]) + len(parts[1]) + 6, len(parts[2])

        return len(parts[0]) + 3, len(parts[1])

    def action_copy(self) -> None:
        """Copy the hunk command of the Review section to the clipboard."""

        if self.check_action("copy", ()):
            typing.cast("BoardApp", self.app).copy_command(self.command)

    def action_review(self) -> None:
        """Ask to mark the task for review or to clear the mark, against the read of the overlay."""

        if self.check_action("review", ()):
            typing.cast("BoardApp", self.app).confirm_review(self.task_id, self)

    def action_send(self) -> None:
        """Ask to send the saved hunk comments of the task, against the read of the overlay."""

        if self.check_action("send", ()):
            typing.cast("BoardApp", self.app).confirm_send(self.task_id, self)

    def action_restore(self) -> None:
        """Ask to apply the saved hunk comments of the task to hunk again."""

        if self.check_action("restore", ()):
            typing.cast("BoardApp", self.app).confirm_restore(self.task_id, self)

    def action_ask(self, kind: str) -> None:
        """Open the prompt of one kind of write, which asks for the value only."""

        if not self.check_action("ask", (kind,)):
            return

        self.asked = kind
        self.add_class("asking")
        ask = self.query_one("#overlay-ask", AnswerBox)
        ask.value = ""
        ask.placeholder = f"{self.task_id}: {ASKS[kind].split(maxsplit=1)[1]}"
        ask.display = True
        ask.focus()

    def action_close(self) -> None:
        """Cancel an open prompt first, else close the overlay."""

        ask = self.query_one("#overlay-ask", AnswerBox)
        if not ask.display:
            self.dismiss(None)
            return

        self.asked = ""
        ask.display = False
        self.remove_class("asking")
        self.query_one("#brief-pane").focus()

    def on_answer_box_submitted(self, event: AnswerBox.Submitted) -> None:
        """Close with the write typed, in the form the prompt of the inbox takes."""

        event.stop()
        self.dismiss((self.asked, f"{self.task_id} {event.value.strip()}"))

    def action_scroll(self, where: str) -> None:

        getattr(self.query_one("#brief-pane"), f"scroll_{where}")(animate=False)

    def on_click(self, event: textual.events.Click) -> None:
        """Close on a click outside the pane, which lands on the screen itself."""

        pane = self.query_one("#brief-pane")
        # A border title is not clickable in Textual, so the top row is mapped by hand.
        if event.screen_y == pane.region.y and self.task_id:
            column = event.screen_x - pane.region.x - TITLE_INDENT
            for kind in ("priority", "state"):
                start, width = self.heading_part(kind)
                if start <= column < start + width:
                    self.action_pick(kind)
                    return

        command = event.style.meta.get("copy")
        if command:
            typing.cast("BoardApp", self.app).copy_command(command)
            return

        if event.widget is self and not pane.region.contains(event.screen_x, event.screen_y):
            self.dismiss(None)
            return

        child = event.style.meta.get("brief")
        if child:
            self.dismiss(("open", child))


class BoardApp(textual.app.App):
    """The task graph as a tree, a board or an inbox, with what a human types."""

    CSS = """
    BoardApp.dark Screen, BoardApp.dark #pane {
        background: #1e1e1e;
    }
    BoardApp.dark #status, BoardApp.dark Footer, BoardApp.dark Footer * {
        background: #2a2a2a;
    }
    BoardApp.dark VerticalScroll { scrollbar-background: #2a2a2a; }
    #view { width: auto; }
    #view.wrapped { width: 100%; }
    #view.padded { padding-left: 1; }
    #status { background: $panel; color: $text; height: 1; margin-bottom: 1; }
    VerticalScroll {
        overflow-x: auto;
        scrollbar-gutter: stable;
        scrollbar-size-vertical: 1;
        scrollbar-size-horizontal: 1;
        scrollbar-background: $panel;
        scrollbar-color: $primary;
        scrollbar-color-hover: $primary-lighten-1;
        scrollbar-color-active: $accent;
    }
    """
    ENABLE_COMMAND_PALETTE = False
    BINDINGS: typing.ClassVar[list[textual.binding.BindingType]] = [
        textual.binding.Binding("tab", "switch", "next view", priority=True),
        textual.binding.Binding(
            "shift+tab", "switch(-1)", "previous view", show=False, priority=True
        ),
        textual.binding.Binding("a", "toggle_done", "done", show=False),
        textual.binding.Binding("m", "toggle_milestones", "milestones", show=False),
        textual.binding.Binding("c", "columns", "columns", show=False),
        textual.binding.Binding("b", "background", "background", show=False),
        ("p", "priority", "priority"),
        textual.binding.Binding("r", "reload", "reload", show=False),
        textual.binding.Binding("l", "legend", "legend", show=False),
        ("o", "brief", "brief"),
        textual.binding.Binding("x", "spotlight", "blocked", show=False),
        ("h", "review", "review"),
        textual.binding.Binding("R", "replan", "replan", show=False),
        textual.binding.Binding("f", "send", "send", show=False),
        textual.binding.Binding("F", "restore", "restore", show=False),
        textual.binding.Binding("y", "copy", "copy", show=False),
        ("q", "quit", "quit"),
        ("question_mark", "help", "help"),
        textual.binding.Binding(
            "down", "move(1)", "next", key_display="down/j", priority=True, show=False
        ),
        textual.binding.Binding(
            "up", "move(-1)", "prev", key_display="up/k", priority=True, show=False
        ),
        textual.binding.Binding("j", "move(1)", "down", show=False),
        textual.binding.Binding("k", "move(-1)", "up", show=False),
        textual.binding.Binding("escape", "cancel", "cancel", show=False),
        textual.binding.Binding("enter", "brief", "open", show=False),
        textual.binding.Binding(
            "pagedown", "page(1)", "scroll", key_display="pgup/pgdn", priority=True, show=False
        ),
        textual.binding.Binding(
            "pageup", "page(-1)", "scroll up", show=False, priority=True
        ),
        textual.binding.Binding("ctrl+d", "page(1)", "scroll", show=False),
        textual.binding.Binding("ctrl+u", "page(-1)", "scroll up", show=False),
    ]

    def __init__(self, path: pathlib.Path, hunk: HunkRunner | None = None) -> None:

        super().__init__(ansi_color=True)
        self.hunk: HunkRunner = hunk or _run_hunk
        # A poll of the hunk sessions is running, and the drawn view lags behind a draft it saved.
        self.hunk_busy = False
        self.hunk_dirty = False
        self.theme = ANSI_THEME
        self.dark_background = not self.no_color
        self.set_class(self.dark_background, "dark")
        self.path = pathlib.Path(path)
        self.graph: ledger_graph.Graph | None = None
        self.mtime_ns = 0
        # The mtime of the read the open overlay was drawn from, which its write is checked against.
        self.overlay_mtime_ns = 0
        self.overlay_graph: ledger_graph.Graph | None = None
        self.polled_stamp: tuple[int, int, int] | None = None
        self.view = VIEWS[0]
        self.show_done = False
        self.show_milestones = False
        self.show_columns = True
        self.card_id = ""
        self.layout = _Layout()
        self.board_note = ""
        self.error = ""
        self.notice = ""
        self.asked = "priority"
        self.selected_id = ""
        self.selected_index = 0
        self.reveal = False
        self.spotlit = ""
        # The (scope, epic) of the replan request whose note the prompt asks for.
        self.replan_target = ("remaining", "")
        self.drawn_text: tuple[str, str] = ("", "")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        """Leave the keys of the priority bindings to the help screen and the prompt."""

        if isinstance(self.screen, (LegendScreen, BriefScreen)):
            return False
        if action in PRIORITY_ACTIONS:
            asking = any(ask.display for ask in self.query("#ask"))
            return len(self.screen_stack) == 1 and not asking

        if self.view not in VIEW_ACTIONS.get(action, VIEWS):
            return False

        # None greys the key in the footer, False hides it.
        if action in TARGET_ACTIONS and not self.current_target():
            return None

        if action == "copy" and not self.copy_command_of(self.current_target()):
            return None

        return True

    def current_target(self) -> str:
        """The task the keys act on in the active view, or "" when there is none, without a hint."""

        if len(self.screen_stack) != 1 or self.graph is None:
            return ""

        if self.view == "inbox":
            task_id = self.chosen(self.listed_ids())
        elif self.view == "warnings":
            task_id = ""
        else:
            task_id = self.target_task(hint=False)

        return task_id if task_id in self.graph.tasks else ""

    def copy_command_of(self, task_id: str) -> str:
        """The hunk command of a task, which only an `in_progress` or `in_rev` task has."""

        if not task_id or self.graph is None:
            return ""

        return ledger_graph.review_command(self.graph, task_id, ledger_graph.ledger_base(self.path))

    def target_chip(self) -> str:
        """The text at the left of the footer: the target, or how to get one; nothing where no key needs one."""

        if self.view == "warnings" or len(self.screen_stack) != 1:
            return ""

        if target := self.current_target():
            return f"target: {target}"

        if self.view == "board" and not self.show_columns:
            return "press c for columns"

        return CLICK_HINT if self.view == "tree" else "select a card" if self.view == "board" else ""

    def compose(self) -> textual.app.ComposeResult:

        yield textual.widgets.Static(id="status")
        with Pane(id="pane"):
            yield textual.widgets.Static(id="view")
        yield AnswerBox(id="ask")
        yield TargetFooter()

    def on_mount(self) -> None:

        self.hide_ask()
        self.reload()
        self.set_interval(POLL_SECONDS, self.check_mtime)
        self.set_interval(HUNK_POLL_SECONDS, self.poll_hunk)

    def check_mtime(self) -> None:
        """Reload when the file changed on disk since the last read.

        The poll waits while a prompt is open, in the main screen or in the
        overlay of a task, so a write typed against an older read is still
        refused by the mtime guard of the ledger.
        """

        stamp = _stamp(self.path)
        if stamp is None or stamp == self.polled_stamp:
            return

        if self.query_one("#ask", AnswerBox).display:
            return

        if any(isinstance(s, BriefScreen) and s.asked for s in self.screen_stack):
            return

        self.notify("reloaded, the file changed", timeout=3)
        self.reload()

    def reload(self) -> None:
        """Read the file again, and keep the last good graph when it is broken."""

        # A broken file is polled once, not parsed again on every tick.
        self.polled_stamp = _stamp(self.path)
        try:
            graph, mtime_ns = _read(self.path.resolve())
        except (OSError, ValueError) as exc:
            self.error = str(exc)
            self.draw()
            return

        self.graph, self.mtime_ns, self.error = graph, mtime_ns, ""
        self.draw()

    def poll_hunk(self) -> None:
        """Ask the hunk session of every `in_progress` or `in_rev` task for its comments, in a worker thread.

        Nothing starts while a poll runs or when no `in_progress` or `in_rev` task has a
        worktree, so a slow hunk never stacks calls and the board never waits.
        """

        targets = _hunk_targets(self.graph)
        if self.hunk_busy or not targets:
            return

        self.hunk_busy = True
        self.run_worker(lambda: self.collect_comments(targets), thread=True, exit_on_error=False)

    def collect_comments(self, targets: dict[str, str]) -> None:
        """Save the comments of each session as a draft, then tell the app, from the worker thread."""

        # Every session is asked, so no short circuit.
        results = [self.saved_comments(task_id, worktree) for task_id, worktree in targets.items()]
        changed = any(results)
        # The app may be closing: there is nobody to tell.
        with contextlib.suppress(RuntimeError):
            self.call_from_thread(self.polled_hunk, changed)

    def saved_comments(self, task_id: str, worktree: str) -> bool:
        """Save the user comments of the hunk session of a worktree, and say whether the draft changed.

        It never raises. No session, a hunk that fails or an export that is not
        one leaves the draft as it is.
        """

        try:
            ok, output = self.hunk(
                ["session", "comment", "list", "--repo", worktree, "--type", "user", "--json"], ""
            )
            comments = ledger_graph.feedback_comments(output) if ok else None
            return comments is not None and ledger_graph.save_feedback_draft(self.path, task_id, comments)
        except Exception:  # noqa: BLE001
            return False

    def polled_hunk(self, changed: bool) -> None:
        """Draw again after a poll saved a draft, once no overlay or menu is open."""

        self.hunk_busy = False
        self.hunk_dirty = self.hunk_dirty or changed
        if self.hunk_dirty and len(self.screen_stack) == 1:
            self.hunk_dirty = False
            self.draw()

    def feedback_labels(self) -> dict[str, str]:
        """The `✎N` or `✉N` label of every task of the graph that has a feedback file, by id."""

        state = ledger_graph.feedback_state(self.path.resolve())
        tasks = self.graph.tasks if self.graph else {}
        return {i: _feedback_label(*marks) for i, marks in state.items() if i in tasks}
    def draw(self) -> None:
        """Render the current view and the status line."""

        self.refresh_bindings()
        status = self.query_one("#status", textual.widgets.Static)
        view = self.query_one("#view", textual.widgets.Static)

        if self.graph is None:
            status.update(self.status_line(0))
            return

        awaiting = _awaiting(self.graph)
        shown = self.listed_ids()
        chosen = self.chosen(shown)
        commands = _review_commands(self.graph, ledger_graph.ledger_base(self.path))
        feedback = self.feedback_labels()
        # The tree wraps to the pane, the other views may be wider than it.
        board_columns = None
        if self.view == "board" and self.show_columns:
            self.layout = _Layout()
            board_columns = _columns(
                self.graph,
                self.view_width(),
                selected=self.card_id,
                layout=self.layout,
                commands=commands,
                feedback=feedback,
            )
            self.board_note = (
                "columns" if board_columns else f"text, columns need a pane {_columns_least()} wide"
            )
        else:
            self.board_note = "text" if self.view == "board" else ""

        view.set_class(self.view != "board" or board_columns is not None, "wrapped")
        view.set_class(self.view == "tree", "padded")
        body: str | rich.text.Text
        if self.view == "tree":
            body = _brief_links(
                rich.text.Text.from_ansi(
                    ledger_graph.tree(
                        self.graph,
                        show_done=self.show_done,
                        milestones=self.show_milestones,
                        color=True,
                        width=max(self.view_width() - 1 - _icon_columns(self.graph, commands, feedback), 1),
                        legend_into=[],
                    )
                ),
                self.graph,
            )
            if self.spotlit not in self.graph.tasks:
                self.spotlit = ""
            body = _spotlight(
                self.graph,
                body,
                self.spotlit,
                dark=self.dark_background,
                commands=commands,
                feedback=feedback,
            )
        elif self.view == "board":
            body = board_columns or _board_overview(self.graph, commands, feedback)
        elif self.view == "warnings":
            body = _warnings(
                self.graph,
                ledger_graph.check_warnings(self.graph, self.path.resolve()),
            )
        else:
            body = _listing(self.graph, shown, chosen, self.view_width())

        view.update(body)
        status.update(self.status_line(len(awaiting)))
        # Another task or view shows its row.
        if (self.view, chosen) != self.drawn_text:
            self.drawn_text = (self.view, chosen)
            self.reveal = True

        if self.reveal:
            self.reveal = False
            index = shown.index(chosen) if chosen in shown else 0
            self.call_after_refresh(self.show_row, index)

    def show_row(self, index: int) -> None:
        """Scroll the list so the selected row is in view."""

        self.query_one("#pane", Pane).scroll_to_region(
            textual.geometry.Region(0, index, 1, 1), animate=False
        )

    def folder_of(self, name: str) -> pathlib.Path:
        """A folder of texts next to the file."""

        return self.path.resolve().parent / name

    def listed_ids(self) -> list[str]:
        """The ids of the Blockers list, empty in the other views."""

        if self.view == "inbox":
            return _awaiting(self.graph)

        return []

    def chosen(self, awaiting: list[str]) -> str:
        """The selected task id, which stays on its task when the list reorders.

        A task that left the list hands the selection to the one now at its
        place, or to the last one.
        """

        if not awaiting:
            return ""

        if self.selected_id in awaiting:
            index = awaiting.index(self.selected_id)
        else:
            index = min(self.selected_index, len(awaiting) - 1)

        self.selected_id, self.selected_index = awaiting[index], index
        return self.selected_id

    def view_width(self) -> int:
        """The columns the main pane has, without the room of its scrollbar.

        The gutter of the vertical scrollbar is stable, so the width does not
        depend on how tall the view happens to be. The pane has no region
        before the first layout, and the first `Resize` of the pane draws the
        views again with the real width.
        """

        pane = self.query_one("#pane", Pane)
        return max(pane.scrollable_content_region.width, 1)

    def status_line(self, awaiting: int) -> rich.text.Text:
        """The header bar: the file, the views as tabs, the badge and the last error.

        What does not fit is dropped or shortened before anything wraps: the
        file name first, then the long badge and the other tabs.
        """

        width = self.size.width or 80
        names = {"inbox": "Blockers"}
        tabs = [
            (
                f"[{number} {names.get(view, view.title())}]"
                if view == self.view
                else f" {number} {names.get(view, view.title())} ",
                view == self.view,
                view,
            )
            for number, view in enumerate(VIEWS, 1)
        ]
        notes = []
        if self.show_done:
            notes.append("all")
        if self.board_note:
            notes.append(self.board_note)
        if self.error:
            notes.append(f"error: {self.error}")
        elif self.notice:
            notes.append(self.notice)

        name = f" {self.path.name}  "
        badges = [f" {awaiting} awaiting you ", f" {awaiting} "] if awaiting else [""]
        for keep_name, badge, keep_notes, all_tabs in (
            (True, badges[0], True, True),
            (False, badges[0], True, True),
            (False, badges[-1], True, True),
            (False, badges[-1], True, False),
        ):
            left = rich.text.Text(name if keep_name else " ", style="dim", no_wrap=True)
            for label, active, target in tabs:
                if all_tabs or active:
                    style = rich.style.Style(
                        bold=active,
                        reverse=active,
                        meta={"goto": target},
                    )
                    left.append(label, style=style)
                    left.append(" ")
            if keep_notes and notes:
                left.append(" " + "  ".join(notes))
            if left.cell_len + len(badge) + 1 <= width:
                break

        left.truncate(max(width - len(badge) - 1, 1), overflow="ellipsis")
        gap = max(width - left.cell_len - len(badge), 1)
        left.append(" " * gap)
        if badge:
            left.append(
                badge,
                style=rich.style.Style.parse("bold black on yellow")
                + rich.style.Style(meta={"goto": "inbox"}),
            )

        left.overflow = "ellipsis"
        return left

    def action_switch(self, step: int = 1) -> None:

        self.notice = ""
        self.view = VIEWS[(VIEWS.index(self.view) + step) % len(VIEWS)]
        self.reveal = True
        self.draw()

    def action_goto(self, view: str) -> None:
        """Switch to a view, the target of a click on its tab."""

        if view != self.view:
            self.notice = ""
            self.view = view
            self.reveal = True
            self.draw()

    def action_move(self, step: int) -> None:
        """Select the next or the previous task, or scroll the other views."""

        if self.view == "board" and self.show_columns and self.layout.order:
            self.move_card(step)
            return

        if self.view != "inbox":
            pane = self.query_one("#pane", textual.containers.VerticalScroll)
            pane.scroll_relative(y=step, animate=False)
            return

        shown = self.listed_ids()
        if shown:
            index = shown.index(self.chosen(shown)) + step
            index = min(max(index, 0), len(shown) - 1)
            self.selected_id, self.selected_index = shown[index], index

        self.notice = ""
        self.reveal = True
        self.draw()

    def move_card(self, step: int) -> None:
        """Select the next or the previous card, column by column, and show it."""

        order = self.layout.order
        if self.card_id in order:
            index = min(max(order.index(self.card_id) + step, 0), len(order) - 1)
        else:
            index = 0 if step > 0 else len(order) - 1

        self.card_id = order[index]
        self.notice = ""
        self.draw()
        top, height = self.layout.spans[self.card_id]
        self.call_after_refresh(
            self.query_one("#pane", Pane).scroll_to_region,
            textual.geometry.Region(0, top, 1, height),
            animate=False,
        )

    def action_page(self, step: int) -> None:
        """Scroll the main pane by a page."""

        pane = self.query_one("#pane", textual.containers.VerticalScroll)
        if step > 0:
            pane.scroll_page_down(animate=False)
        else:
            pane.scroll_page_up(animate=False)

    def action_reload(self) -> None:

        self.notice = ""
        self.reveal = True
        self.reload()

    def action_columns(self) -> None:
        """Toggle the columns and the text overview, and show the board."""

        self.notice = ""
        self.show_columns = not self.show_columns
        self.view = "board"
        self.draw()

    def action_toggle_done(self) -> None:

        self.notice = ""
        self.show_done = not self.show_done
        self.reveal = True
        self.draw()

    def action_toggle_milestones(self) -> None:

        self.notice = ""
        self.show_milestones = not self.show_milestones
        self.reveal = True
        self.draw()

    def action_background(self) -> None:

        if self.no_color:
            self.notice = "no background with NO_COLOR"
        else:
            self.notice = ""
            self.dark_background = not self.dark_background
            self.set_class(self.dark_background, "dark")
        self.draw()

    def action_help(self) -> None:

        if len(self.screen_stack) == 1:
            self.push_screen(HelpScreen())

    def action_legend(self) -> None:

        if len(self.screen_stack) != 1:
            return
        card = ledger_graph.legend_card()
        self.push_screen(LegendScreen(rich.text.Text("\n").join(card)))

    def action_brief(self, task_id: str = "") -> None:
        """Open the brief of a task, a click names it, else the selected one."""

        if len(self.screen_stack) != 1 or self.graph is None:
            return

        if not task_id:
            if self.view == "tree":
                task_id = self.target_task()
                if not task_id:
                    return
            elif self.view == "board":
                task_id = self.card_id if self.show_columns else ""
            else:
                task_id = self.chosen(self.listed_ids())

        if task_id not in self.graph.tasks:
            return

        self.overlay_mtime_ns = self.mtime_ns
        self.overlay_graph = self.graph
        self.push_screen(self.brief_screen(task_id), self.after_overlay)

    def brief_screen(self, task_id: str) -> BriefScreen:
        """The overlay of a task, drawn from the last read."""

        graph = typing.cast("ledger_graph.Graph", self.graph)
        command = ledger_graph.review_command(graph, task_id, ledger_graph.ledger_base(self.path))
        return BriefScreen(
            _overlay_state(graph, task_id),
            _overlay(
                graph,
                task_id,
                self.path.resolve().parent / BRIEF_DIR,
                self.path,
                context_folder=self.folder_of(CONTEXT_DIR),
                command=command,
                feedback=ledger_graph.feedback_state(self.path.resolve()).get(task_id),
            ),
            task_id,
            answerable=graph.question(task_id) is not None,
            priority=(
                graph.tasks[task_id].priority
                if graph.tasks[task_id].has_priority
                else ""
            ),
            command=command,
        )

    def apply_pick(self, screen: BriefScreen, kind: str, value: str) -> None:
        """Write the choice of a menu against the read the overlay was drawn from.

        The overlay stays open and shows the new read. A refused write leaves the
        file and the overlay as they are, and says why.
        """

        writer = (
            ledger_graph.set_priority if kind == "priority" else ledger_graph.set_status
        )
        try:
            writer(self.path, screen.task_id, value, seen_mtime_ns=self.overlay_mtime_ns)
        except (OSError, ValueError) as exc:
            self.notify(str(exc), severity="error", timeout=6)
            return

        self.reload()
        self.overlay_mtime_ns = self.mtime_ns
        self.overlay_graph = self.graph
        if self.graph and screen.task_id in self.graph.tasks:
            screen.update_from(self.brief_screen(screen.task_id))
        self.notify(f"set {screen.task_id} {kind} to {value}", timeout=3)

    def target_task(self, hint: bool = True) -> str:
        """The task a key of the tree or the board acts on, or "" with a hint: the spotlit one, or the selected card."""

        if len(self.screen_stack) != 1 or self.graph is None:
            return ""

        if self.view == "tree":
            task_id = self.spotlit
        else:
            task_id = self.card_id if self.show_columns else ""

        if task_id not in self.graph.tasks:
            if hint:
                self.hint_no_target()
            return ""

        return task_id

    def hint_no_target(self) -> None:
        """Say how to pick the task a key acts on, which depends on the view."""

        if self.view == "tree":
            how = "click an id first"
        elif self.view == "warnings":
            how = "click an id"
        elif self.show_columns:
            how = "select a card"
        else:
            how = "press c for columns"
        self.notify(f"no task selected: {how}", severity="warning", timeout=4)

    def action_copy(self) -> None:
        """Copy the hunk command of the spotlit task of the tree, or of the selected card."""

        if not (task_id := self.target_task()):
            return

        graph = typing.cast("ledger_graph.Graph", self.graph)
        command = ledger_graph.review_command(graph, task_id, ledger_graph.ledger_base(self.path))
        if not command:
            self.notify(f"'{task_id}' has no hunk command: only an in_progress or in_rev task has one", severity="warning", timeout=4)
            return

        self.copy_command(command)

    def action_review(self) -> None:
        """Ask to mark the spotlit task of the tree, or the selected card, or to clear its mark."""

        if task_id := self.target_task():
            self.confirm_review(task_id)

    def confirm_review(self, task_id: str, screen: BriefScreen | None = None) -> None:
        """Ask before the human review mark of a task flips, and write it on a yes.

        The write is checked against the read the human saw: the one the overlay
        was drawn from, else the last one.
        """

        graph = self.overlay_graph if screen else self.graph
        seen_mtime_ns = self.overlay_mtime_ns if screen else self.mtime_ns
        task = graph.tasks.get(task_id) if graph else None
        if task is None:
            return

        marked = task.human_review
        if task.status == "done" and not marked:
            self.notify(f"'{task_id}' is done: a done task needs no review", severity="error", timeout=6)
            return

        if marked:
            action, note = "clear the mark", "this does not mark the task done"
        else:
            action, note = "mark for your review", "the orchestrator cannot set it done until you clear the mark"

        self.push_screen(
            PickScreen(
                f"{task_id}: human review",
                [
                    ("cancel", "cancel", "do nothing"),
                    ("confirm", action, note),
                ],
                "cancel",
            ),
            lambda value: self.reviewed(task_id, marked, seen_mtime_ns, screen, value),
        )

    def reviewed(
        self,
        task_id: str,
        marked: bool,
        seen_mtime_ns: int,
        screen: BriefScreen | None,
        value: str | None,
    ) -> None:
        """Flip the mark after a yes, and show the new read, in the overlay when it is open.

        A refused write leaves the file and the views as they are, and says why.
        """

        if value != "confirm":
            return

        failure = self.write_review(task_id, marked, seen_mtime_ns)
        if failure:
            self.notify(failure, severity="error", timeout=6)
            return

        self.reload()
        if screen:
            self.overlay_mtime_ns = self.mtime_ns
            self.overlay_graph = self.graph
            if self.graph and task_id in self.graph.tasks:
                screen.update_from(self.brief_screen(task_id))
        self.notify(f"{task_id}: {'cleared the human review mark' if marked else 'marked for human review'}", timeout=3)

    def write_review(self, task_id: str, marked: bool, seen_mtime_ns: int) -> str:
        """Flip the mark of a task, and return the error when the write fails.

        A file that changed since the read is read again, and the write is tried
        once more when the mark is still the one the human saw there.
        """

        try:
            ledger_graph.set_human_review(self.path, task_id, not marked, seen_mtime_ns=seen_mtime_ns)
        except (OSError, ValueError) as exc:
            if _unchanged(self.path, seen_mtime_ns):
                return str(exc)

            self.reload()
            current = self.graph.tasks.get(task_id) if self.graph else None
            if self.error or current is None or current.human_review != marked:
                return self.error or f"the human review of '{task_id}' changed in the file"

            try:
                ledger_graph.set_human_review(self.path, task_id, not marked, seen_mtime_ns=self.mtime_ns)
            except (OSError, ValueError) as retry_exc:
                return str(retry_exc)

        return ""

    def action_send(self) -> None:
        """Ask to send the saved comments of the spotlit task, or of the selected card."""

        if task_id := self.target_task():
            self.confirm_send(task_id)

    def confirm_send(self, task_id: str, screen: BriefScreen | None = None) -> None:
        """Ask before the saved comments of a task are marked ready, and write it on a yes.

        The write is checked against the read the human saw, as the one of `h` is.
        """

        graph = self.overlay_graph if screen else self.graph
        seen_mtime_ns = self.overlay_mtime_ns if screen else self.mtime_ns
        if graph is None or task_id not in graph.tasks:
            return

        state = ledger_graph.feedback_state(self.path.resolve()).get(task_id)
        if state is None or not state[0]:
            self.notify(f"'{task_id}' has no saved comments: write them in hunk while the board runs", severity="warning", timeout=6)
            return

        if state[1]:
            self.notify(f"the comments of '{task_id}' are sent already", severity="warning", timeout=4)
            return

        count = state[0]
        self.push_screen(
            PickScreen(
                f"{task_id}: send feedback",
                [
                    ("cancel", "cancel", "do nothing"),
                    (
                        "confirm",
                        f"send {_comments(count)}",
                        "the orchestrator reads them when you tell it to, and sends them to the writer",
                    ),
                ],
                "cancel",
            ),
            lambda value: self.sent(task_id, seen_mtime_ns, screen, value),
        )

    def sent(
        self, task_id: str, seen_mtime_ns: int, screen: BriefScreen | None, value: str | None
    ) -> None:
        """Mark the comments ready after a yes, and show the new read, in the overlay when it is open."""

        if value != "confirm":
            return

        count, failure = self.write_send(task_id, seen_mtime_ns)
        if failure:
            self.notify(failure, severity="error", timeout=6)
            return

        self.reload()
        if screen:
            self.overlay_mtime_ns = self.mtime_ns
            self.overlay_graph = self.graph
            if self.graph and task_id in self.graph.tasks:
                screen.update_from(self.brief_screen(task_id))
        self.notify(f"{task_id}: sent {_comments(count)}", timeout=3)

    def write_send(self, task_id: str, seen_mtime_ns: int) -> tuple[int, str]:
        """Mark the comments of a task ready, and return their count and the error when the write fails.

        A file that changed since the read is read again, and the write is tried
        once more when the task is still `in_progress` or `in_rev`.
        """

        try:
            return ledger_graph.send_feedback(self.path, task_id, seen_mtime_ns=seen_mtime_ns), ""
        except (OSError, ValueError) as exc:
            if _unchanged(self.path, seen_mtime_ns):
                return 0, str(exc)

            self.reload()
            current = self.graph.tasks.get(task_id) if self.graph else None
            if self.error or current is None or current.status not in ledger_graph.HUNK_STATUSES:
                return 0, self.error or f"'{task_id}' is not in_progress or in_rev any more"

            try:
                return ledger_graph.send_feedback(self.path, task_id, seen_mtime_ns=self.mtime_ns), ""
            except (OSError, ValueError) as retry_exc:
                return 0, str(retry_exc)

    def action_restore(self) -> None:
        """Ask to apply the saved comments of the spotlit task, or of the selected card, to hunk again."""

        if task_id := self.target_task():
            self.confirm_restore(task_id)

    def confirm_restore(self, task_id: str, screen: BriefScreen | None = None) -> None:
        """Ask before the saved comments of a task are applied to the hunk session of its worktree.

        Nothing of the ledger is written, so no read is checked.
        """

        graph = self.overlay_graph if screen else self.graph
        if graph is None or task_id not in graph.tasks:
            return

        state = ledger_graph.feedback_state(self.path.resolve()).get(task_id)
        if state is None or not state[0]:
            self.notify(f"'{task_id}' has no saved comments", severity="warning", timeout=4)
            return

        worktree = graph.workspace(task_id)[0]
        if not worktree:
            self.notify(f"'{task_id}' has no worktree: hunk has no session to apply them to", severity="error", timeout=6)
            return

        count = state[0]
        self.push_screen(
            PickScreen(
                f"{task_id}: restore feedback",
                [
                    ("cancel", "cancel", "do nothing"),
                    (
                        "confirm",
                        f"apply {_comments(count)} to hunk",
                        "hunk must be open on the worktree; they come back as notes of an agent, not editable ones",
                    ),
                ],
                "cancel",
            ),
            lambda value: self.restored(task_id, str(pathlib.Path(worktree).expanduser()), value),
        )

    def restored(self, task_id: str, worktree: str, value: str | None) -> None:
        """Apply the comments after a yes, in a worker thread so the board does not wait for hunk."""

        if value == "confirm":
            self.run_worker(lambda: self.apply_comments(task_id, worktree), thread=True, exit_on_error=False)

    def apply_comments(self, task_id: str, worktree: str) -> None:
        """Apply the saved comments to the hunk session of a worktree and say how it went, from the worker thread.

        It never raises: a failure of hunk is a message, with what hunk said.
        """

        severity: typing.Literal["information", "warning", "error"] = "information"
        try:
            batch, skipped = ledger_graph.feedback_batch(self.path.resolve(), task_id)
            applied = len(batch["comments"])
            if applied:
                ok, output = self.hunk(
                    ["session", "comment", "apply", "--repo", worktree, "--stdin"], json.dumps(batch)
                )
            else:
                ok, output = False, "no comment has a file, a line and a body"
        except (OSError, ValueError) as exc:
            ok, output, applied, skipped = False, str(exc), 0, 0

        if ok:
            message = f"{task_id}: applied {_comments(applied)} to hunk"
            if skipped:
                message += f", skipped {skipped} without a file, a line or a body"
                severity = "warning"
        else:
            message, severity = f"{task_id}: hunk did not take the comments: {output}", "error"

        # The app may be closing: there is nobody to tell.
        with contextlib.suppress(RuntimeError):
            self.call_from_thread(self.notify, message, severity=severity, timeout=6)

    def action_replan(self) -> None:
        """Ask for a replan of the remaining tasks, or of the spotlit epic, then for a note."""

        asking = any(ask.display for ask in self.query("#ask"))
        if len(self.screen_stack) != 1 or self.graph is None or asking:
            return

        chosen = self.graph.tasks.get(self.spotlit)
        epic = self.spotlit if chosen and chosen.type == "epic" else ""
        stays = "done and started tasks stay as they are; the orchestrator runs the replan"
        choices = [
            ("cancel", "cancel", "do nothing"),
            ("remaining", "replan the remaining tasks", stays),
        ]
        if epic:
            choices.append(("epic", f"replan the open tasks of {epic}", stays))

        self.push_screen(
            PickScreen("replan request", choices, "cancel"),
            lambda value: self.chose_replan(value, epic),
        )

    def chose_replan(self, value: str | None, epic: str) -> None:
        """Ask for the optional note of the request after a scope."""

        if value not in ledger_graph.REPLAN_SCOPES:
            return

        self.replan_target = (value, epic if value == "epic" else "")
        self.ask_for("replan")

    def write_replan(self, note: str, seen_mtime_ns: int) -> str:
        """Write the request, and return the error when the write fails.

        A file that changed since the read is read again and the write is tried
        once more: the request holds no task state, and the writer checks the epic.
        """

        scope, epic = self.replan_target
        try:
            ledger_graph.write_replan_request(self.path, scope, epic, note, seen_mtime_ns=seen_mtime_ns)
        except (OSError, ValueError) as exc:
            if _unchanged(self.path, seen_mtime_ns):
                return str(exc)

            self.reload()
            if self.error:
                return self.error

            try:
                ledger_graph.write_replan_request(self.path, scope, epic, note, seen_mtime_ns=self.mtime_ns)
            except (OSError, ValueError) as retry_exc:
                return str(retry_exc)

        self.notice = f"asked the orchestrator to replan {'the open tasks of ' + epic if epic else 'the remaining tasks'}"
        return ""

    def after_overlay(self, written: tuple[str, str] | None) -> None:
        """Run the write the overlay closed with, against the read it was drawn from.

        A file that changed while the overlay was open refuses the write, and
        the error shows once the overlay is closed.
        """

        if written and written[0] == "open":
            self.action_brief(written[1])
        elif written:
            self.submit(
                *written,
                seen_mtime_ns=self.overlay_mtime_ns,
                seen_graph=self.overlay_graph,
            )

    def action_priority(self) -> None:
        """Open the priority menu of the spotlit task or the selected card, else the text prompt."""

        task_id = self.target_task(hint=False) if self.view != "inbox" else ""
        task = self.graph.tasks.get(task_id) if self.graph and task_id else None
        if task is None:
            self.ask_for("priority")
            return

        seen_mtime_ns = self.mtime_ns
        self.push_screen(
            PickScreen(
                f"{task_id}: priority",
                [(p, p) for p in sorted(ledger_graph.PRIORITIES)],
                task.priority,
                styles=dict(ledger_graph.PRIORITY_STYLES),
            ),
            lambda value: self.priority_picked(task_id, task.priority, seen_mtime_ns, value),
        )

    def priority_picked(self, task_id: str, was: str, seen_mtime_ns: int, value: str | None) -> None:
        """Write the priority of a menu, once more against a newer read when the file changed under it."""

        if not value or value == was:
            return

        try:
            ledger_graph.set_priority(self.path, task_id, value, seen_mtime_ns=seen_mtime_ns)
        except (OSError, ValueError) as exc:
            if _unchanged(self.path, seen_mtime_ns):
                self.notify(str(exc), severity="error", timeout=6)
                return

            self.reload()
            current = self.graph.tasks.get(task_id) if self.graph else None
            if self.error or current is None or current.priority != was:
                self.notify(self.error or f"the priority of '{task_id}' changed in the file", severity="error", timeout=6)
                return

            try:
                ledger_graph.set_priority(self.path, task_id, value, seen_mtime_ns=self.mtime_ns)
            except (OSError, ValueError) as retry_exc:
                self.notify(str(retry_exc), severity="error", timeout=6)
                return

        self.reload()
        self.notify(f"set {task_id} priority to {value}", timeout=3)

    def ask_for(self, kind: str) -> None:
        """Show the prompt of one kind of write and focus it."""

        self.asked = kind
        self.notice = ""
        ask = self.query_one("#ask", AnswerBox)
        ask.value = "" if kind == "replan" else self.prefill()
        ask.placeholder = ASKS[kind]
        ask.end_cursor()
        ask.display = True
        ask.focus()

    def prefill(self) -> str:
        """The id the list has selected, so a human only types the answer."""

        shown = self.listed_ids()
        if not shown:
            return ""

        return f"{self.chosen(shown)} "

    def action_cancel(self) -> None:

        self.spotlit = ""
        self.hide_ask()
        self.reload()

    def action_spotlight(self, task_id: str = "") -> None:
        """Fade the tree but the tasks a task holds up, or clear it on the same task.

        The key acts on the selected task of the lists, a click names its own.
        """

        task_id = task_id or self.spotlit or self.chosen(_open_tasks(self.graph, show_done=self.show_done))
        if not task_id:
            self.hint_no_target()
            return

        self.spotlit = "" if task_id == self.spotlit else task_id
        self.draw()

    def hide_ask(self) -> None:
        """Hide the prompt and give the focus back to the view."""

        self.query_one("#ask", AnswerBox).display = False
        self.query_one("#pane", textual.containers.VerticalScroll).focus()

    def on_click(self, event: textual.events.Click) -> None:
        """Switch view on a click on a tab, select a card on its border or priority, or open it on its body.

        Neither is a `@click` link: Textual would draw it with the link style.
        A click on a copy icon copies and does not open anything, even on a card.
        """

        command = event.style.meta.get("copy")
        if command and len(self.screen_stack) == 1:
            self.copy_command(command)
            return

        brief = event.style.meta.get("brief")
        if brief and len(self.screen_stack) == 1:
            self.action_brief(brief)
            return

        pick = event.style.meta.get("pick")
        if pick and self.check_action("move", (1,)):
            self.card_id = pick
            self.draw()
            return

        card = event.style.meta.get("card")
        if card and self.check_action("move", (1,)):
            self.card_id = card
            self.draw()
            self.action_brief(card)
            return

        task = event.style.meta.get("task")
        if task and self.check_action("move", (1,)):
            self.action_spotlight(task)
            return

        target = event.style.meta.get("goto")
        if target and self.check_action("goto", (target,)):
            self.action_goto(target)

    def copy_command(self, command: str) -> None:
        """Put a command on the clipboard (OSC 52, which not every terminal honors) and say so."""

        self.copy_to_clipboard(command)
        self.notify("copied", timeout=3)

    def on_answer_box_submitted(self, event: AnswerBox.Submitted) -> None:

        if self.submit(self.asked, event.value):
            self.hide_ask()

    def submit(
        self,
        kind: str,
        typed: str,
        *,
        seen_mtime_ns: int | None = None,
        seen_graph: ledger_graph.Graph | None = None,
    ) -> bool:
        """Write what the human typed for one kind of write, and read the file again.

        The write is checked against `seen_mtime_ns`, the last read by default.
        A failed write keeps the typed text in the prompt and returns False.
        """

        self.asked = kind
        failure = self.write(typed, seen_mtime_ns=seen_mtime_ns, seen_graph=seen_graph)

        self.reload()
        if failure:
            self.error = failure
            self.keep_typed(typed)
            self.draw()

        return not failure

    def keep_typed(self, typed: str) -> None:
        """Show the prompt of the current kind of write with the text the human typed."""

        ask = self.query_one("#ask", AnswerBox)
        ask.value = typed
        ask.placeholder = ASKS[self.asked]
        ask.end_cursor()
        ask.display = True
        ask.focus()

    def write(
        self,
        typed: str,
        *,
        seen_mtime_ns: int | None = None,
        seen_graph: ledger_graph.Graph | None = None,
    ) -> str:
        """Run the write the human typed, and return its error when it fails.

        A file that changed since the read is read again, and the write is tried
        once more when what the human saw is still true there.
        """

        if seen_mtime_ns is None:
            seen_mtime_ns = self.mtime_ns
        if seen_graph is None:
            seen_graph = self.graph

        if self.asked == "replan":
            return self.write_replan(typed, seen_mtime_ns)

        # An answer is free text, so only the id is split off.
        parts = typed.split(maxsplit=1) if self.asked == "answer" else typed.split()

        if len(parts) != 2:
            return f"type '{ASKS[self.asked]}', not {typed!r}"

        writer = (
            ledger_graph.answer_blocker
            if self.asked == "answer"
            else ledger_graph.set_priority
        )
        try:
            writer(self.path, parts[0], parts[1], seen_mtime_ns=seen_mtime_ns)
        except (OSError, ValueError) as exc:
            if _unchanged(self.path, seen_mtime_ns):
                return str(exc)

            changed = self.still_true(parts[0], seen_graph)
            if changed:
                return changed

            try:
                writer(self.path, parts[0], parts[1], seen_mtime_ns=self.mtime_ns)
            except (OSError, ValueError) as retry_exc:
                return str(retry_exc)

        self.notice = (
            f"answered {parts[0]}"
            if self.asked == "answer"
            else f"set {parts[0]} to {parts[1]}"
        )
        return ""

    def still_true(self, task_id: str, seen: ledger_graph.Graph | None) -> str:
        """Read the file again, and say why the human's view no longer holds, or ''."""

        self.reload()
        if self.error:
            return self.error

        graph = self.graph
        if graph is None or task_id not in graph.tasks:
            return f"unknown id '{task_id}' after the file changed"

        if self.asked != "answer":
            return ""

        question = graph.question(task_id)
        if question is None:
            return f"'{task_id}' is no longer blocked on a human"

        if seen is None or task_id not in seen.tasks or question != seen.question(task_id):
            return f"the question of '{task_id}' changed to: {question}"

        return ""


def main(argv: list[str]) -> int:

    if len(argv) != 1:
        sys.stderr.write("usage: ledger_board.py <tasks.toml>\n")
        return 2

    BoardApp(pathlib.Path(argv[0])).run()
    return 0


# Private helpers


def _stamp(path: pathlib.Path) -> tuple[int, int, int] | None:
    """The mtime, size and inode of the file, or None when it cannot be read.

    The mtime alone can repeat when a writer truncates and fills the file in
    place within one clock tick, so the size and the inode join it.
    """

    try:
        stat = path.resolve().stat()
    except OSError:
        return None

    return stat.st_mtime_ns, stat.st_size, stat.st_ino


def _unchanged(path: pathlib.Path, seen_mtime_ns: int) -> bool:
    """Whether the file still has the mtime that was read, False when it cannot be read."""

    stamp = _stamp(path)
    return stamp is not None and stamp[0] == seen_mtime_ns


def _columns_least() -> int:
    """The width below which the board falls back to the text overview."""

    return _columns_width(len(BOARD_COLUMNS) - 1)


def _columns_width(count: int) -> int:

    return count * COLUMN_MIN_WIDTH + (count - 1) * COLUMN_GAP


def _columns(
    graph: ledger_graph.Graph,
    width: int,
    *,
    selected: str = "",
    layout: _Layout | None = None,
    commands: dict[str, str] | None = None,
    feedback: dict[str, str] | None = None,
) -> rich.console.RenderableType | None:
    """The board as columns of cards for a pane `width` columns wide, or None.

    With room for all columns Done is one of them. With room for the others
    only, Done is a count under them. With less, the caller shows the text.
    `layout` gets the cards in the order of the columns and the first line and
    the height of each one. `commands` are the hunk commands of the tasks in
    review, which a copy icon on their card puts on the clipboard, and
    `feedback` the labels of their saved hunk comments.
    """

    if width < _columns_least():
        return None

    shown = BOARD_COLUMNS if width >= _columns_width(len(BOARD_COLUMNS)) else BOARD_COLUMNS[:-1]
    first = ledger_graph.next_start(graph)
    table = rich.table.Table(
        box=None, expand=True, padding=(0, COLUMN_GAP, 0, 0), pad_edge=False, show_header=False
    )
    cells = []
    inner = (width - COLUMN_GAP * (len(shown) - 1)) // len(shown) - COLUMN_GAP
    ready = set(graph.ready())
    layout = _Layout() if layout is None else layout
    for name, statuses in shown:
        cards = ledger_graph.board_cards(graph, statuses)
        table.add_column(min_width=COLUMN_MIN_WIDTH, ratio=1)
        head = rich.text.Text(name, style="bold")
        head.append(f" ({len(cards)})", "dim")
        rule = rich.rule.Rule(style=COLUMN_RULES.get(name, "dim"))
        compact = name == "Done"
        body = _cards(
            graph,
            cards,
            first,
            inner,
            ready,
            compact=compact,
            selected=selected,
            layout=layout,
            commands=commands,
            feedback=feedback,
        )
        cells.append(rich.console.Group(head, rule, body))

    table.add_row(*cells)
    if shown == BOARD_COLUMNS:
        return table

    done = len(ledger_graph.board_cards(graph, BOARD_COLUMNS[-1][1]))
    return rich.console.Group(table, rich.text.Text(f"\nDone ({done})", style="bold"))


def _cards(
    graph: ledger_graph.Graph,
    cards: list[str],
    first: str,
    inner: int = COLUMN_MIN_WIDTH,
    ready: set[str] | None = None,
    *,
    compact: bool = False,
    selected: str = "",
    layout: _Layout | None = None,
    commands: dict[str, str] | None = None,
    feedback: dict[str, str] | None = None,
) -> rich.console.RenderableType:
    """The cards of one column, bordered and a blank line apart.

    `inner` is the room of the column. The priority, the next task and the
    question that waits on a human are text or a bar, so no state rests on a
    color, and the borders stay without colors.
    """

    if not cards:
        return rich.text.Text("-", style="dim")

    parts: list[rich.console.RenderableType] = []
    # The header and its rule take two lines.
    top = 2
    for n, task_id in enumerate(cards):
        if n:
            parts.append(rich.text.Text(""))
            top += 1
        card, height = _card(
            graph,
            task_id,
            first,
            inner,
            ready,
            compact=compact,
            selected=task_id == selected,
            command=(commands or {}).get(task_id, ""),
            feedback=(feedback or {}).get(task_id, ""),
        )
        parts.append(card)
        if layout is not None:
            layout.order.append(task_id)
            layout.spans[task_id] = (top, height)
        top += height

    return rich.console.Group(*parts)


def _card(
    graph: ledger_graph.Graph,
    task_id: str,
    first: str,
    inner: int,
    ready: set[str] | None,
    *,
    compact: bool,
    selected: bool = False,
    command: str = "",
    feedback: str = "",
) -> tuple[rich.panel.Panel, int]:
    """One card and its height: the state symbol, the title on two lines at most, the id, and the priority in the border.

    A click on a card carries its id, and the selected card has a bold border.
    A card with a `command` has a copy icon after its id, which copies it, a
    card marked for human review has a `◆` before the copy icon, and a card
    with saved hunk comments has their `feedback` label after it.

    A task that waits on a human has a violet bar at the left, a done card is one
    line and the border takes the color of the priority.
    """

    task = graph.tasks[task_id]
    parked = task.status == "deferred"
    waiting = graph.awaits_you(task_id)
    # The border and the padding take 4 cells, the symbol 2 and the bar 2.
    room = max(inner - 6 - (2 if waiting else 0), 1)
    symbol = ledger_graph.state_symbol(
        task.status,
        busy=task.status in ledger_graph.RUNNING,
        ready=task_id in (graph.ready() if ready is None else ready),
        waiting=bool(task.waiting),
    )
    title_style = "dim" if parked else ""

    if compact:
        line = rich.text.Text.assemble(symbol, " ", (_shorten(task.title, room - len(task_id) - 2), title_style))
        line.append(f"  {task_id}", "dim")
        lines = [line]
    else:
        lines = []
        for n, chunk in enumerate(_wrap(task.title, room)):
            lines.append(
                rich.text.Text.assemble(
                    symbol if n == 0 else "  ", " " if n == 0 else "", (chunk, title_style)
                )
            )

        # The priority is in the border, so the line has the id and the marks:
        # the marks go first and the id is cut before the line wraps.
        left = room + 2
        marks = ["deferred"] if parked else []
        sep = ledger_graph.DETAIL_SEP
        icon = 2 * (bool(command) + task.human_review) + (len(feedback) + 1 if feedback else 0)
        while marks and 2 + len(task_id) + icon + sum(len(sep) + len(m) for m in marks) > left:
            marks.pop()
        tail = rich.text.Text("  ")
        tail.append(_shorten(task_id, max(left - 2 - icon, 1)), "dim")
        if task.human_review:
            tail.append(" ")
            tail.append(ledger_graph.HUMAN_REVIEW_ICON, ledger_graph.AWAITING_STYLE)
        if command:
            tail.append(" ")
            tail.append(ledger_graph.COPY_ICON, rich.style.Style(meta={"copy": command}))
        if feedback:
            tail.append(" ")
            tail.append(feedback, "bold")
        for mark in marks:
            tail.append(sep, "dim")
            tail.append(mark, "dim")
        lines.append(tail)

    body = rich.text.Text("\n").join(lines)
    if waiting:
        bar = rich.text.Text("\n").join(
            rich.text.Text("▌ ", ledger_graph.AWAITING_STYLE) for _ in lines
        )
        grid = rich.table.Table.grid()
        grid.add_column()
        grid.add_column()
        grid.add_row(bar, body)
        body = grid

    color = "bright_black" if parked else ledger_graph.PRIORITY_STYLES[task.priority]
    # The border and the priority tag only select the card, the body opens it.
    pick = rich.style.Style(meta={"pick": task_id})
    title = rich.text.Text(" ", style=pick)
    if task_id == first or task_id in (ready or ()):
        title.append("next", "bold")
        title.append(ledger_graph.DETAIL_SEP, "dim")
    title.append(task.priority, "dim" if parked else ledger_graph.PRIORITY_STYLES[task.priority])
    title.append(" ")
    panel = rich.panel.Panel(
        body,
        box=rich.box.ROUNDED,
        border_style=rich.style.Style.parse(f"bold {color}" if selected else f"dim {color}") + pick,
        style=rich.style.Style(meta={"card": task_id}),
        padding=(0, 1),
        expand=True,
        title=title,
        title_align="right",
    )
    # The border takes a line above and one below the text.
    return panel, len(lines) + 2


def _details(graph: ledger_graph.Graph, task_id: str, room: int) -> list[rich.text.Text]:
    """The state lines of a task in its overlay: state, parent, deps, blocker, worktree, branch."""

    task = graph.tasks[task_id]
    epic = task.type == "epic"
    fields = [] if epic else [("state", f"{task.status}  {task.priority}")]
    if task.parent:
        fields.append(("epic", task.parent))
    deps = [f"{d} ({graph.tasks[d].status})" for d in sorted(graph.deps[task_id])]
    if deps and not epic:
        fields.append(("deps", ", ".join(deps)))
    if task.blocker:
        fields.append(("blocker", task.blocker))
    if task.status == "done" and task.done_at:
        fields.append(("done", task.done_at))
    if task.worktree:
        fields.append(("worktree", task.worktree))
    if task.branch:
        fields.append(("branch", task.branch))

    lines = [rich.text.Text("")]
    for label, value in fields:
        head = f"{label}: "
        chunks = _wrap_all(value, max(room - len(head), 1))
        for n, chunk in enumerate(chunks):
            line = rich.text.Text(head if n == 0 else " " * len(head), "dim")
            line.append(chunk)
            lines.append(line)

    return lines


def _wrap_all(text: str, room: int) -> list[str]:
    """The text on as many lines of `room` columns as it needs, cutting long words."""

    lines: list[str] = []
    current = ""
    for word in text.split():
        while rich.cells.cell_len(word) > room:
            if current:
                lines.append(current)
                current = ""
            lines.append(word[:room])
            word = word[room:]

        trial = f"{current} {word}".strip()
        if rich.cells.cell_len(trial) <= room:
            current = trial
        else:
            lines.append(current)
            current = word

    return [*lines, current] if current or not lines else lines


def _wrap(text: str, room: int) -> list[str]:
    """The text on `CARD_TITLE_LINES` lines of `room` columns, cut with an ellipsis."""

    words = text.split()
    lines: list[str] = []
    current = ""
    for n, word in enumerate(words):
        trial = f"{current} {word}".strip()
        if rich.cells.cell_len(trial) <= room:
            current = trial
            continue

        lines.append(current)
        current = word
        if len(lines) == CARD_TITLE_LINES - 1:
            current = " ".join(words[n:])
            break

    lines.append(current)
    lines = [line for line in lines if line] or [""]
    lines[-1] = _shorten(lines[-1], room)
    return lines


def _tree_ids(line: str, known: typing.Container[str]) -> tuple[str, list[tuple[int, int, str]]]:
    """The own id of a tree row and the (start, end, id) of every id it shows.

    The ids are the one after the title and the ones after `waits` or
    `inherited`, so a title that happens to be an id is not one.
    """

    found: list[tuple[int, int, str]] = []
    start = 0
    for number, piece in enumerate(line.split(ledger_graph.DETAIL_SEP)):
        if number:
            words = list(re.finditer(r"[^\s,]+", piece))
            if piece.split(" ", 1)[0] in ("waits", "inherited"):
                words = words[1:]
            elif not found:
                words = words[-1:]
            else:
                words = []
            for word in words:
                if word.group() in known:
                    begin = start + word.start()
                    found.append((begin, begin + len(word.group()), word.group()))
        start += len(piece) + len(ledger_graph.DETAIL_SEP)
    return (found[0][2] if found else ""), found


def _icon_columns(
    graph: ledger_graph.Graph,
    commands: dict[str, str],
    feedback: dict[str, str] | None = None,
) -> int:
    """The columns a row of the tree keeps for the icons after its id: the copy icon, the mark and the feedback label."""

    labels = max((len(label) + 1 for label in (feedback or {}).values()), default=0)
    return 2 * (bool(commands) + any(t.human_review for t in graph.tasks.values())) + labels


def _spotlight(
    graph: ledger_graph.Graph,
    body: rich.text.Text,
    task_id: str,
    *,
    dark: bool,
    commands: dict[str, str] | None = None,
    feedback: dict[str, str] | None = None,
) -> rich.text.Text:
    """Make the ids of the tree clickable and, for a chosen task, fade the rest.

    The rows of the tasks that depend on it, directly or not, and its own row
    keep their style, its blockers take a second color, and every other row
    fades to a color close to the background. A row of a task with a command
    gets a copy icon after its id, and a task marked for human review a `◆`
    before the copy icon. A task with saved hunk comments has their `feedback`
    label after the icons. All fade with the row.
    """

    kept: set[str] = set()
    todo = [task_id]
    while todo and task_id:
        for dependent in graph.dependents.get(todo.pop(), ()):
            if dependent not in kept:
                kept.add(dependent)
                todo.append(dependent)
    kept.add(task_id)
    blockers = set(graph.blocked_by(task_id)) if task_id else set()
    if task_id and (holder := ledger_graph.file_holds(graph).get(task_id)):
        blockers.add(holder)
    fade = rich.style.Style(color=FADE_DARK if dark else FADE_LIGHT)

    lines = body.split("\n", allow_blank=True)
    for number, line in enumerate(lines):
        own, ids = _tree_ids(line.plain, graph.tasks)
        for begin, end, found in ids:
            line.stylize(rich.style.Style(meta={"task": found}), begin, end)
        marked = bool(own) and graph.tasks[own].human_review
        command = (commands or {}).get(own, "")
        label = (feedback or {}).get(own, "")
        if marked or command or label:
            before, after = line.divide([ids[0][1]])
            if marked:
                before.append(" ")
                before.append(ledger_graph.HUMAN_REVIEW_ICON, ledger_graph.AWAITING_STYLE)
            if command:
                before.append(" ")
                before.append(ledger_graph.COPY_ICON, rich.style.Style(meta={"copy": command}))
            if label:
                before.append(" ")
                before.append(label, "bold")
            before.append_text(after)
            lines[number] = line = before
        if own:
            for held in re.finditer(r"blocks \d+", line.plain):
                line.stylize(
                    rich.style.Style(meta={"task": own}), held.start(), held.end()
                )
        if not task_id or not own:
            continue

        if own in blockers:
            line.stylize(BLOCKER_STYLE)
        elif own not in kept:
            line.stylize(fade)
    return rich.text.Text("\n").join(lines)


def _awaiting(graph: ledger_graph.Graph | None) -> list[str]:
    """The leaves that wait on a human, a question or a review, most urgent first and then in file order."""

    if graph is None:
        return []

    asking = [i for i in graph.leaves if graph.awaits_you(i)]
    return sorted(
        asking, key=lambda i: ledger_graph.PRIORITIES[graph.tasks[i].priority]
    )


def _open_tasks(graph: ledger_graph.Graph | None, *, show_done: bool) -> list[str]:
    """The leaves in file order, without the done ones unless asked."""

    if graph is None:
        return []

    return [i for i in graph.leaves if show_done or graph.tasks[i].status != "done"]


def _shorten(text: str, room: int) -> str:
    """The text cut to `room` columns, with an ellipsis in the last one."""

    if room <= 0:
        return ""

    kept = rich.text.Text(text)
    kept.truncate(room, overflow="ellipsis")
    return kept.plain


def _listing(
    graph: ledger_graph.Graph, awaiting: list[str], chosen: str, width: int
) -> rich.text.Text:
    """The Blockers list, one row per task: a red cross (a `◆` for a review), the id, the title and the priority.

    The selected row has a cursor and is bold. The priority is text and the id
    takes its color. A long title ends in an ellipsis so the priority stays in
    view. Every row is tagged with its id, so a click opens its brief.
    """

    if not awaiting:
        return rich.text.Text("nothing awaiting you")

    listing = rich.text.Text(no_wrap=True, overflow="ellipsis")
    for index, task_id in enumerate(awaiting):
        task = graph.tasks[task_id]
        style = ledger_graph.PRIORITY_STYLES[task.priority]
        asks = graph.question(task_id) is not None
        head = f"{'> ' if task_id == chosen else '  '}{'✖' if asks else ledger_graph.HUMAN_REVIEW_ICON} {task_id}  "
        room = width - len(head) - len(task.priority) - 2
        row = rich.text.Text(no_wrap=True)
        row.append(head)
        row.append(_shorten(task.title, room))
        row.append(f"  {task.priority}", style)
        row.stylize("red" if asks else ledger_graph.AWAITING_STYLE, 2, 3)
        row.stylize(style, 4, 4 + len(task_id))
        if task_id == chosen:
            row.stylize("bold")

        row.stylize(rich.style.Style(meta={"brief": task_id}))
        listing.append_text(row)
        if index < len(awaiting) - 1:
            listing.append("\n")

    return listing


def _warnings(graph: ledger_graph.Graph, found: list[str]) -> rich.text.Text:
    """The warnings of `check`, one row each, with the task ids they name tagged for a click."""

    if not found:
        return rich.text.Text("no warnings")

    lines = []
    for warning in found:
        row = rich.text.Text(warning)
        for word in re.finditer(r"[\w.-]+", warning):
            task_id = word.group().rstrip(".-")
            if task_id in graph.tasks:
                row.stylize(
                    rich.style.Style(meta={"brief": task_id}),
                    word.start(),
                    word.start() + len(task_id),
                )
        lines.append(row)

    return rich.text.Text("\n").join(lines)


def _brief_links(text: rich.text.Text, graph: ledger_graph.Graph) -> rich.text.Text:
    """Tag the title of every row of the tree with its id, for a click to open its brief.

    A row is `art symbol title · [PX ]id detail`; the id after the first ` · `
    that names a task says which row it is. It is meta, not a link: Textual
    would draw a link with its own style.
    """

    lines = text.split("\n", allow_blank=True)
    offset = 0
    for line in lines:
        plain = line.plain
        start = len(plain) - len(plain.lstrip("│├└─━┬┴┼ "))
        for found in re.finditer(r" · (?:P\d |-- )?([\w.-]+)", plain):
            task_id = found.group(1)
            if task_id not in graph.tasks:
                continue

            if graph.tasks[task_id].type != "epic":
                start += 2
            if start < found.start():
                text.stylize(
                    rich.style.Style(meta={"brief": task_id}),
                    offset + start,
                    offset + found.start(),
                )
            break

        offset += len(plain) + 1

    return text


def _overlay_state(graph: ledger_graph.Graph, chosen: str) -> rich.text.Text:
    """The border title of an overlay: id, state symbol, status and priority, split by a dim dot."""

    task = graph.tasks[chosen]
    dot = rich.text.Text(" · ", "dim")
    line = rich.text.Text(chosen)
    line.append_text(dot)
    line.append_text(
        ledger_graph.state_symbol(
            task.status,
            busy=task.status in ledger_graph.RUNNING,
            ready=chosen in graph.ready(),
            waiting=bool(task.waiting),
        )
    )
    line.append(f" {task.status}")
    line.append_text(dot)
    line.append(task.priority, ledger_graph.PRIORITY_STYLES.get(task.priority, "dim"))
    return line


def _review_commands(graph: ledger_graph.Graph, base: str) -> dict[str, str]:
    """The hunk command of every leaf that has one, by id."""

    commands = {i: ledger_graph.review_command(graph, i, base) for i in graph.leaves}
    return {i: command for i, command in commands.items() if command}


def _hunk_targets(graph: ledger_graph.Graph | None) -> dict[str, str]:
    """The worktree of every `in_progress` or `in_rev` leaf that has one, by id: the hunk sessions to ask for comments."""

    if graph is None:
        return {}

    targets = {}
    for task_id in graph.leaves:
        worktree = graph.workspace(task_id)[0]
        if worktree and graph.tasks[task_id].status in ledger_graph.HUNK_STATUSES:
            targets[task_id] = str(pathlib.Path(worktree).expanduser())

    return targets


def _feedback_label(count: int, sent: bool) -> str:
    """`✎N` for N saved comments, `✉N` for N sent."""

    return f"{ledger_graph.SENT_ICON if sent else ledger_graph.DRAFT_ICON}{count}"


def _comments(count: int) -> str:
    """`1 comment`, `2 comments`."""

    return f"{count} comment{'' if count == 1 else 's'}"


def _run_hunk(args: list[str], stdin: str = "") -> tuple[bool, str]:
    """Run hunk with the arguments, and give back whether it succeeded and its output.

    It never raises: a missing binary, a timeout or an error exit is a failure,
    with the first line of what hunk said. No hunk session for the worktree is
    one of them.
    """

    binary = shutil.which("hunk") or str(pathlib.Path.home() / ".hunk" / "bin" / "hunk")
    try:
        done = subprocess.run(  # noqa: S603
            [binary, *args],
            input=stdin,
            capture_output=True,
            text=True,
            timeout=HUNK_TIMEOUT,
            check=False,
        )
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return False, str(exc)

    if done.returncode:
        lines = (done.stderr or done.stdout).strip().splitlines()
        return False, lines[0] if lines else f"hunk exited with {done.returncode}"

    return True, done.stdout


def _board_overview(
    graph: ledger_graph.Graph,
    commands: dict[str, str],
    feedback: dict[str, str] | None = None,
) -> str | rich.text.Text:
    """The text overview of the board, with the icons after the id of an `in_progress` or `in_rev` task, of a task marked for human review or with saved hunk comments.

    It is the plain `board` text when no row has an icon.
    """

    feedback = feedback or {}
    lines = ledger_graph.board(graph).split("\n")
    rows = [re.match(r"  ([^\s]+)", line) for line in lines]
    ids = [row.group(1) if row and row.group(1) in graph.tasks else "" for row in rows]
    iconed = [i for i in ids if i and (i in commands or i in feedback or graph.tasks[i].human_review)]
    if not iconed:
        return "\n".join(lines)

    text = rich.text.Text()
    for number, (line, row, own) in enumerate(zip(lines, rows, ids, strict=True)):
        if number:
            text.append("\n")
        if row and own in iconed:
            text.append(line[: row.end()])
            if graph.tasks[own].human_review:
                text.append(" ")
                text.append(ledger_graph.HUMAN_REVIEW_ICON, ledger_graph.AWAITING_STYLE)
            if own in commands:
                text.append(" ")
                text.append(ledger_graph.COPY_ICON, rich.style.Style(meta={"copy": commands[own]}))
            if own in feedback:
                text.append(" ")
                text.append(feedback[own], "bold")
            text.append(line[row.end() :])
        else:
            text.append(line)

    return text


def _overlay(
    graph: ledger_graph.Graph,
    chosen: str,
    folder: pathlib.Path,
    tasks_path: pathlib.Path,
    *,
    context_folder: pathlib.Path | None = None,
    command: str = "",
    feedback: tuple[int, bool] | None = None,
) -> rich.console.Group:
    """The head of a task and its brief, or its state details when it has none.

    A task with a question for the human shows it first, with its options (the
    context file of the inbox, else the question alone) and the key that
    answers it. A task with a `command` shows a Review section next: the
    command as text, a copy icon and the key that copies it. A task with saved
    hunk `feedback` (the count and whether it is sent) shows a Feedback section
    with the keys that send it and apply it again. The last events of
    the task follow under a History heading, when it has any. A task marked for
    human review says so under the title.
    """

    task = graph.tasks[chosen]
    head = rich.text.Text(task.title, style="bold", justify="center")
    text = _read_text(chosen, folder)[0]
    body: list[rich.console.RenderableType] = [head]
    if task.human_review:
        body += [
            rich.text.Text(""),
            rich.text.Text.assemble(
                (ledger_graph.HUMAN_REVIEW_ICON, ledger_graph.AWAITING_STYLE),
                " marked for your review, h clears the mark",
            ),
        ]
    question = graph.question(chosen)
    if question is not None:
        context = _read_text(chosen, context_folder)[0] if context_folder else ""
        body += [
            rich.text.Text(""),
            rich.text.Text("Question", style=ledger_graph.AWAITING_STYLE),
            rich.text.Text(""),
            BriefMarkdown(_without_question_heading(context))
            if context.strip()
            else rich.text.Text(question),
            rich.text.Text(""),
            rich.text.Text("e to answer", style="dim"),
        ]

    if command:
        body += [
            rich.text.Text(""),
            rich.text.Text("Review", style=HEADING_STYLES["h2"]),
            rich.text.Text(""),
            rich.text.Text.assemble(
                command, " ", (ledger_graph.COPY_ICON, rich.style.Style(meta={"copy": command}))
            ),
            rich.text.Text(""),
            rich.text.Text("y to copy", style="dim"),
        ]

    if feedback:
        count, sent = feedback
        body += [
            rich.text.Text(""),
            rich.text.Text("Feedback", style=HEADING_STYLES["h2"]),
            rich.text.Text(""),
            rich.text.Text(
                f"{ledger_graph.SENT_ICON} {_comments(count)} sent, the orchestrator reads them when you tell it to"
                if sent
                else f"{ledger_graph.DRAFT_ICON} {_comments(count)} saved from hunk, not sent yet"
            ),
            rich.text.Text(
                "F to apply them to hunk again" if sent else "f to send, F to apply them to hunk again",
                style="dim",
            ),
        ]
    if text.strip():
        body.append(rich.text.Text(""))
        body.append(BriefMarkdown(_without_repeated_heading(text, task)))
    else:
        body += _details(graph, chosen, 80)

    body += _children_section(graph, chosen)
    events = ledger_graph.read_events(tasks_path, chosen)[-HISTORY_LIMIT:]
    if events:
        body.append(rich.text.Text(""))
        body.append(rich.text.Text("History", style=HEADING_STYLES["h2"]))
        body.append(rich.text.Text(""))
        body += [
            rich.text.Text(ledger_graph.format_event(event), style="dim")
            for event in events
        ]

    body += [rich.text.Text(""), rich.text.Text(BRIEF_HINT, style="dim")]
    return rich.console.Group(*body)


def _children_section(
    graph: ledger_graph.Graph, epic: str
) -> list[rich.console.RenderableType]:
    """The Children section of an epic: a count per status and a row per child not done.

    Only the direct children are listed. The id of a row carries the `brief`
    meta, so a click on it opens the overlay of that child.
    """

    if graph.tasks[epic].type != "epic" or not graph.children[epic]:
        return []

    counts: dict[str, int] = {}
    for child in graph.children[epic]:
        status = graph.tasks[child].status
        counts[status] = counts.get(status, 0) + 1

    summary = " · ".join(f"{n} {status}" for status, n in counts.items())
    rows: list[rich.console.RenderableType] = [
        rich.text.Text(""),
        rich.text.Text("Children", style=HEADING_STYLES["h2"]),
        rich.text.Text(""),
        rich.text.Text(summary),
    ]
    ready = set(graph.ready())
    shown = [c for c in graph.children[epic] if graph.tasks[c].status != "done"]
    id_width = max((len(child) for child in shown), default=0)
    for child in shown:
        status = graph.tasks[child].status
        row = ledger_graph.state_symbol(
            status,
            busy=status in ledger_graph.RUNNING,
            ready=child in ready,
            waiting=bool(graph.tasks[child].waiting),
        )
        row.append(" ")
        row.append(child, rich.style.Style(meta={"brief": child}))
        row.append(" " * (id_width - len(child)))
        row.append(f" {graph.tasks[child].title}", "dim")
        rows.append(row)

    return rows


def _without_repeated_heading(text: str, task: ledger_graph.Task) -> str:
    """The brief without its first line when that is a `# ` heading of only the id and/or the title."""

    lines = text.lstrip("\n").split("\n")
    first = lines[0].strip()
    if not first.startswith("# "):
        return text

    repeats = {task.id, task.title, f"{task.id}: {task.title}"}
    if first[2:].strip() not in repeats:
        return text

    return "\n".join(lines[1:]).lstrip("\n")


def _without_question_heading(text: str) -> str:
    """The context of a question without its first line when that is a heading that only says `Question`.

    The overlay has its own Question heading, so the file's one would repeat it.
    Any other heading of the file stays.
    """

    lines = text.lstrip("\n").split("\n")
    match = re.fullmatch(r"#{1,6}\s+(.*?)\s*#*\s*", lines[0])
    if match is None:
        return text

    words = match.group(1).strip().rstrip(":?").strip().casefold()
    if words not in QUESTION_TITLES:
        return text

    return "\n".join(lines[1:]).lstrip("\n")


def _read_text(chosen: str, folder: pathlib.Path) -> tuple[str, str]:
    """The text of `<folder>/<id>.md` and why there is none: `missing` or the error.

    The id builds a path, so an id that could walk out of the folder is refused
    and the file is read only when it resolves inside it.
    """

    if "/" in chosen or "\\" in chosen or ".." in chosen:
        return "", "the id is not a file name"

    path = folder / f"{chosen}.md"
    if path.resolve().parent != folder.resolve():
        return "", "the file is outside the folder"

    if not path.is_file():
        return "", "missing"

    try:
        return path.read_text(), ""
    except (OSError, UnicodeDecodeError) as exc:
        return "", str(exc)


def _read(path: pathlib.Path) -> tuple[ledger_graph.Graph, int]:
    """The graph of a file and its mtime, or a ValueError with every error in it."""

    mtime_ns = path.stat().st_mtime_ns
    tasks, errors = ledger_graph.parse(path.read_text())
    if errors:
        msg = "; ".join(errors)
        raise ValueError(msg)

    graph = ledger_graph.Graph(tasks)
    if graph.errors:
        msg = "; ".join(graph.errors)
        raise ValueError(msg)

    return graph, mtime_ns


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
