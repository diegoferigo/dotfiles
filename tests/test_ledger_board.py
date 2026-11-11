from __future__ import annotations

import asyncio
import functools
import importlib.util
import io
import json
import os
import pathlib
import re
import sys
import typing

import pytest
import rich.color
import rich.console
import textual.widgets
import tomllib
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".agents/skills/orchestrating-agents/scripts/ledger_board.py"


def _tables(*tasks: dict) -> str:
    """One [[task]] table per dict, keys in order."""

    def value(v) -> str:
        return str(v).lower() if isinstance(v, bool) else json.dumps(v)

    return "".join("\n[[task]]\n" + "".join(f"{k} = {value(v)}\n" for k, v in t.items()) for t in tasks)


def _toml(*tasks: dict) -> str:
    return "format = 1\n" + _tables(*tasks)


def T(id: str, title: str, status: str = "pending", **fields) -> dict:  # noqa: N802
    return {"id": id, "title": title, "status": status, **fields}


def _gate(**g1) -> str:
    return _toml(
        {**T("g1", "gate"), **g1, "write_set": ["g"]},
        T("g2", "two", priority="P2", deps=["g1"], write_set=["h"]),
    )


TASKS = _gate()
DONE_TASKS = _gate(status="done")
MILESTONE_TASKS = _gate(milestone="m9-demo")
BLOCKED = _gate(status="blocked", blocker="human: A or B?")
INBOX = _toml(
    T("g1", "gate", "blocked", priority="P2", blocker="human: A or B?", write_set=["g"]),
    T("g2", "two", "blocked", priority="P0", blocker="human: now or later?", write_set=["h"]),
    T("g3", "three", priority="P1", write_set=["i"]),
)
CONTEXT = "## Question\n\nnow or later?\n\n## Recommendation\n\nlater\n"
BRIEF = "## Goal\n\nship the thing\n\n## Decisions\n\n- keep it small\n"
BOARD_TASKS = _toml(
    T("d1", "finished", "done", read_only=True),
    T("r1", "running now", "running", read_only=True, priority="P1"),
    T("lo", "low priority ready task", read_only=True, priority="P3"),
    T("hi", "high priority ready task", read_only=True, priority="P0"),
    T("w1", "waits for the runner", read_only=True, deps=["r1"]),
    T("q1", "needs a choice", "blocked", read_only=True, blocker="human: which API?"),
)
SPOT_TASKS = _toml(
    T("aa", "first", write_set=["a"]),
    T("bb", "second", deps=["aa"], write_set=["b"]),
    T("cc", "third", deps=["bb"], write_set=["c"]),
    T("dd", "alone", write_set=["d"]),
)
EPIC = [
    T("e1", "the epic", type="epic"),
    T("c1", "finished child", "done", parent="e1", write_set=["a"]),
    T("c2", "open child", parent="e1", write_set=["b"]),
]
EXPAND_TASKS = _toml(
    T("epic", "the epic", type="epic"),
    T("base", "base work", "running", parent="epic", read_only=True, worktree="/w/base", branch="base-branch"),
    T("long", "a very long title that would be cut with an ellipsis on a card of this width", parent="epic", read_only=True, deps=["base"]),
)
REVIEW_TASKS = _toml(
    T("rv", "in review", "in_rev", write_set=["a"], worktree="~/wt/rv", branch="feat/rv"),
    T("nw", "review without worktree", "in_rev", write_set=["b"], branch="feat/nw"),
    T("nb", "review without branch", "in_rev", write_set=["c"], worktree="~/wt/nb"),
    T("ip", "still in progress", write_set=["d"], worktree="~/wt/ip", branch="feat/ip"),
)
MARKED = REVIEW_TASKS.replace('id = "rv"\n', 'id = "rv"\nhuman_review = true\n')
REVIEW_COMMAND = "cd ~/wt/rv && hunk diff integration...feat/rv"
TIP_COMMAND = "cd ~/wt/rv && hunk diff stack/tip...feat/rv"
HUNK_NOTE = dict(noteId="user:1794402060751-1", source="user", filePath="m.py", hunkIndex=0, newRange=[9, 9], body="use type hints", author="user", createdAt="2026-11-11T13:01:00.751Z", editable=True)  # noqa: C408
EXPORT = json.dumps({"comments": [HUNK_NOTE]})
REPLAN_TASKS = _toml(
    T("E1", "epic", type="epic"),
    T("a", "inside", parent="E1", write_set=["a"]),
    T("b", "outside", write_set=["b"]),
)


@pytest.fixture(autouse=True)
def _home_with_task_worktrees(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake `~/wt/*` worktrees exist, so review commands keep their `cd`."""

    home = tmp_path / "home"
    for name in ("rv", "nb", "ip"):
        (home / "wt" / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))


@pytest.fixture
def tasks(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(TASKS)
    return path


@pytest.fixture
def inbox(tasks: pathlib.Path) -> pathlib.Path:
    tasks.write_text(INBOX)
    _write(tasks.parent / "inbox" / "g2.md", CONTEXT)
    return tasks


@pytest.fixture
def briefs(tasks: pathlib.Path) -> pathlib.Path:
    tasks.write_text(INBOX)
    _write(tasks.parent / "briefs" / "g2.md", BRIEF)
    return tasks


def test_tab_and_the_awaiting_badge_switch_the_view_and_the_footer_follows_it(tasks):
    """Tab and a click on the awaiting badge switch the view, and the footer follows it."""

    keys = {"switch", "quit", "help", "priority"}

    def chips(app) -> list[str]:
        return [str(c.render()) for c in app.query("FooterLabel.target-chip")]

    async def cycle(app, pilot):
        assert "Todo (" not in _view(app) and "g2" in _view(app)
        await pilot.pause()
        assert _footer_actions(app) == {"brief", "review"} | keys
        await _go(pilot, "tab")
        assert "Todo (" in _view(app) and "In progress (0)" in _view(app)
        assert _footer_actions(app) == {"brief", "review"} | keys
        await _go(pilot, "tab")
        assert (app.view, _view(app), _footer_actions(app)) == ("inbox", "nothing awaiting you", {"brief"} | keys)
        for key, view in [("tab", "warnings"), ("tab", "tree"), ("shift+tab", "warnings"), ("tab", "tree"), ("tab", "board"), ("shift+tab", "tree")]:
            await pilot.press(key)
            assert app.view == view
        await pilot.pause()
        dimmed = {key.action for key in app.query("FooterKey") if key.has_class("-disabled")}
        assert (dimmed, chips(app)) == ({"brief", "review"}, ["click an id"])
        assert _footer_text(app).startswith("click an id | ") and "[o brief]" in _footer_text(app)
        app.spotlit = next(iter(app.graph.tasks))
        app.draw()
        await pilot.pause()
        assert not any(key.has_class("-disabled") for key in app.query("FooterKey"))
        assert chips(app) == [f"target: {app.spotlit}"]
        for view in ("board", "inbox", "warnings"):
            await _go(pilot, "tab")
            assert app.view == view
        assert chips(app) == []

    async def badge(app, pilot):
        await pilot.click("#status", offset=(_status(app).index("2 awaiting") + 1, 0))
        await pilot.pause()
        assert app.view == "inbox"

    _run(tasks, cycle)
    _run(tasks, badge, INBOX, size=(100, 24))


def test_p_writes_the_priority_typed_or_picked_from_the_menu_and_shows_a_refusal(tasks):
    """P writes a typed priority or the one picked in the menu of the spotlit task, and shows a refusal."""

    async def typed(app, pilot):
        await pilot.press("p", "g", "2", "space", "P", "0", "enter")
        assert (app.error, app.query_one("#ask").display, app.graph.tasks["g2"].priority) == ("", False, "P0")
        assert 'priority = "P0"' in tasks.read_text()

    async def refused(app, pilot):
        await pilot.press("p", "g", "2", "space", "P", "9", "enter")
        assert "unknown priority 'P9'" in app.error
        assert tasks.read_text() == TASKS

    async def picked(app, pilot):
        app.action_spotlight("g2")
        await _go(pilot, "p")
        assert _screen(app) == "PickScreen"
        await _go(pilot, "escape")
        assert tasks.read_text() == TASKS
        await _go(pilot, "p")
        await _go(pilot, "home", "enter")
        lines = zip(TASKS.splitlines(), tasks.read_text().splitlines(), strict=True)
        assert (_screen(app), [b for a, b in lines if a != b]) == ("Screen", ['priority = "P0"'])
        assert app.graph.tasks["g2"].priority == "P0"

    for body in (typed, refused, picked):
        _run(tasks, body, TASKS)


def test_the_priority_set_from_the_blockers_list_or_the_overlay_border_is_written_after_a_change(inbox):
    """P in the blockers list and a click on the priority in the overlay border write it, also after a change on disk."""

    async def prefilled(app, pilot):
        await pilot.press("tab", "tab", "down", "p")
        assert app.query_one("#ask").value == "g1 "
        await pilot.press(*"P0", "enter")
        assert (app.error, app.selected_id) == ("", "g1")
        assert 'priority = "P0"' in inbox.read_text()
        assert "> ✖ g1" in _view(app) and _view(app).index("g1") < _view(app).index("g2")
        assert "set g1 to P0" in _status(app)
        await pilot.press("j")
        assert "set g1 to P0" not in _status(app)
        await pilot.press("enter", "e", *"use B", "enter")
        await pilot.pause()
        assert "answered" in _status(app)

    async def border(app, pilot):
        await _open(app, pilot, "g3")
        pane = app.screen.query_one("#brief-pane")
        before = pane.styles.border.top[1]
        await _click_title(app, pilot, "priority")
        assert _screen(app) == "PickScreen"
        await pilot.press("escape")
        assert inbox.read_text() == INBOX
        await _click_title(app, pilot, "priority")
        await _go(pilot, "up", "enter")
        assert (_screen(app), app.graph.tasks["g3"].priority) == ("BriefScreen", "P0")
        assert pane.styles.border.top[1] != before
        await pilot.press("p")
        assert _screen(app) == "PickScreen"
        await _go(pilot, "down", "enter")
        assert (_screen(app), app.graph.tasks["g3"].priority != "P0") == ("BriefScreen", True)
        assert "P0" not in app.screen.heading.plain

    async def retried(app, pilot):
        await pilot.press("tab", "tab", "p")
        ask = app.query_one("#ask")
        assert ask.value == "g2 "
        seen = app.mtime_ns
        _rewrite(inbox, INBOX.replace('title = "two"', 'title = "other"'))
        await pilot.pause(0.3)
        assert app.mtime_ns == seen
        await pilot.press(*"P1", "enter")
        assert (app.error, ask.display) == ("", False)
        assert 'priority = "P1"' in inbox.read_text() and 'title = "other"' in inbox.read_text()
        # The poll resumes once the prompt is closed.
        await pilot.pause(0.3)
        assert "other" in _view(app)

    _run(inbox, prefilled)
    _run(inbox, border, INBOX, size=(100, 30))
    _run(inbox, retried, INBOX, size=(160, 24), poll=True)


@pytest.mark.parametrize(
    ("key", "marker", "text"),
    [("a", "gate", DONE_TASKS), ("m", "m9-demo", MILESTONE_TASKS)],
)
def test_a_and_m_show_and_hide_the_done_tasks_and_the_milestones(tasks, key, marker, text):
    """A and m show and hide the done tasks and the milestones."""

    async def drive(app, pilot):
        assert marker not in _view(app)
        assert not re.search(r"·\s{2,}|·\s*$", _view(app), re.M)
        await pilot.press(key)
        assert marker in _view(app)
        await pilot.press(key)
        assert marker not in _view(app)

    _run(tasks, drive, text)


def test_l_opens_the_legend_and_l_or_escape_closes_it(tasks):
    """L opens the legend over the board, keys behind it do nothing, and l or escape closes it."""

    async def toggle(app, pilot):
        for key in ("l", "escape"):
            await pilot.press("l", "r", "a")
            assert (_screen(app), app.show_done) == ("LegendScreen", False)
            assert "✔  done" in str(app.screen.query_one("#legend").render())
            await pilot.press(key)
            assert len(app.screen_stack) == 1

    _run(tasks, toggle)


def test_a_key_hidden_in_a_view_or_pressed_with_the_prompt_open_does_nothing(tasks):
    """A key hidden in a view, or pressed while the prompt is open, changes nothing and writes nothing."""

    async def hidden(app, pilot):
        await _go(pilot, "c", "tab", "tab", "a", "m", "c", "l", "e", "R", "ctrl+p")
        assert (app.view, len(app.screen_stack), app.show_done, app.show_milestones, app.show_columns) == ("inbox", 1, False, False, True)
        assert not app.query_one("#ask").display

    async def ignored(app, pilot):
        await pilot.pause()
        await pilot.press("p", "x")
        view = app.view
        for key in ("l", "m", "tab"):
            await pilot.press(key)
            assert (app.view, len(app.screen_stack), app.show_milestones) == (view, 1, False)
            assert "m9-demo" not in _view(app)
            assert app.query_one("#ask").display

    _run(tasks, hidden, BLOCKED)
    assert tasks.read_text() == BLOCKED
    assert not (tasks.parent / "replan-requests").exists()
    _run(tasks, ignored, MILESTONE_TASKS)


def test_escape_hides_the_prompt_without_writing_and_then_closes_the_overlay(tasks):
    """Escape hides the prompt without writing, and in the overlay it cancels the prompt before it closes."""

    async def prompt(app, pilot):
        await pilot.press("p", "escape")
        assert (app.query_one("#ask").display, tasks.read_text()) == (False, TASKS)

    async def overlay(app, pilot):
        await _open(app, pilot, "g1")
        await pilot.press("e", "escape")
        assert (_screen(app), app.screen.query_one("#overlay-ask").display, tasks.read_text()) == ("BriefScreen", False, INBOX)
        await pilot.press("escape")
        assert len(app.screen_stack) == 1

    _run(tasks, prompt)
    _run(tasks, overlay, INBOX, size=(100, 30))


def test_the_warnings_tab_lists_the_warnings_of_check_or_says_there_are_none(tasks):
    """The warnings tab lists the warnings of check, an id opens its overlay, and it says when there are none."""

    warning = "g1: ready without briefs/g1.md next to tasks.toml"

    async def listed(app, pilot):
        await pilot.press("shift+tab")
        assert (app.view, _view(app)) == ("warnings", warning)
        await pilot.click("#view", offset=(1, 0))
        await pilot.pause()
        assert (_screen(app), app.screen.task_id) == ("BriefScreen", "g1")

    async def none(app, pilot):
        await pilot.press("shift+tab")
        assert _view(app) == "no warnings"

    _run(tasks, listed, size=(100, 24))
    _write(tasks.parent / "briefs" / "g1.md", "# g1\n\n## Acceptance\n\n- ok\n")
    _run(tasks, none)


def test_the_context_of_an_id_that_walks_out_of_the_inbox_is_refused(tmp_path):
    """The context of an id that walks out of the inbox folder is not read."""

    (tmp_path / "inbox").mkdir()
    (tmp_path.parent / "secret.md").write_text("top secret")
    for task_id in ("../../secret", ".."):
        assert _load()._read_text(task_id, tmp_path / "inbox")[0] == ""


def test_a_click_on_a_tab_switches_the_view_unless_the_legend_or_prompt_is_open(tasks):
    """A click on a tab switches to that view, and is ignored while the legend or the prompt is open."""

    async def drive(app, pilot):
        for target in ("3 Blockers", "2 Board", "1 Tree"):
            await _click_tab(app, pilot, target)
            await pilot.pause()
            assert app.view == _mod().VIEWS[int(target[0]) - 1]
        before = _view(app)
        await _click_tab(app, pilot, "1 Tree")
        await pilot.pause()
        assert (app.view, _view(app)) == ("tree", before)
        await pilot.press("l")
        await pilot.click(offset=(_status(app).index("3 Blockers") + 1, 0))
        await _go(pilot, "escape", "p")
        await _click_tab(app, pilot, "3 Blockers")
        await pilot.pause()
        assert app.view == "tree"

    _run(tasks, drive, BLOCKED, size=(100, 24))


def test_the_blockers_list_sorts_by_urgency_and_keeps_the_selection_across_a_reload_and_an_overlay(inbox):
    """The blockers list puts the most urgent first, and a reload keeps the selection, the view and an open overlay."""

    async def reloaded(app, pilot):
        await pilot.press("tab", "tab")
        assert _view(app).splitlines() == ["> ✖ g2  two  P0", "  ✖ g1  gate  P2"]
        assert "2 awaiting you" in _status(app)
        await pilot.press("down")
        # Now g1 comes before g2 in the list.
        _rewrite(inbox, INBOX.replace('priority = "P2"', 'priority = "P0"', 1))
        await pilot.pause(0.3)
        assert (app.view, app.selected_id, "> ✖ g1" in _view(app)) == ("inbox", "g1", True)

    async def overlay(app, pilot):
        await _open(app, pilot, "g3")
        stat = inbox.stat()
        inbox.write_text(INBOX.replace('title = "three"', 'title = "three!"'))
        os.utime(inbox, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
        app.check_mtime()
        await pilot.pause()
        assert (app.graph.tasks["g3"].title, _screen(app)) == ("three!", "BriefScreen")
        # With the prompt of the overlay open the poll waits, and reads once it closes.
        await pilot.press("escape")
        await _open(app, pilot, "g1")
        await pilot.press("e")
        waiting = app.polled_stamp
        os.utime(inbox, ns=(stat.st_atime_ns, stat.st_mtime_ns + 9_000_000_000))
        await pilot.pause(delay=_mod().POLL_SECONDS + 0.5)
        assert (app.screen.query_one("#overlay-ask").display, app.polled_stamp) == (True, waiting)
        await pilot.press("escape")
        await pilot.pause(delay=_mod().POLL_SECONDS + 0.5)
        assert (app.polled_stamp != waiting, _screen(app)) == (True, "BriefScreen")

    _run(inbox, reloaded, poll=True)
    _run(inbox, overlay, INBOX, size=(100, 30))


def test_the_file_is_read_again_on_r_or_a_change_and_a_broken_one_keeps_the_last_good_view(tasks):
    """R and a change on disk redraw the board, and a broken file is read once and keeps the last good view."""

    async def key(app, pilot):
        tasks.write_text(TASKS.replace('"two"', '"renamed"'))
        await pilot.press("r")
        assert "renamed" in _view(app)
        _rewrite(tasks, TASKS.replace('"two"', '"new title"'))
        await pilot.pause(0.3)
        assert ("new title" in _view(app), "reloaded" in _status(app), app.error) == (True, False, "")
        assert any("reloaded" in n.message for n in app._notifications)

    async def broken(app, pilot):
        module = _mod()
        reads = []
        read = module._read
        module._read = lambda path: reads.append(path) or read(path)
        _rewrite(tasks, "this is [not toml")
        await pilot.pause(0.4)
        assert (len(reads), bool(app.error), "g2" in _view(app), "error:" in _status(app)) == (1, True, True, True)
        await pilot.press("r")
        assert "g2" in _view(app) and "invalid TOML" in app.error
        # The same mtime as the broken file still reads the fixed one.
        stamp = tasks.stat().st_mtime_ns
        tasks.write_text(TASKS.replace('"two"', '"fixed"'))
        os.utime(tasks, ns=(stamp, stamp))
        await pilot.pause(0.3)
        assert (app.error, "fixed" in _view(app)) == ("", True)

    for body in (key, broken):
        _run(tasks, body, TASKS, poll=True)


def test_an_answer_keeps_its_text_when_the_file_is_touched_and_the_question_changed_or_it_is_refused(tasks):
    """A touched file keeps the answer of the same question, and a changed question or a refused answer writes nothing and keeps the typed text."""

    changed = BLOCKED.replace("A or B?", "C or D?")

    async def touched(app, pilot):
        await _open_answer(pilot, "use B")
        _rewrite(tasks, BLOCKED + "\n# touched by another writer\n")
        await pilot.pause(0.3)
        await _go(pilot, "enter")
        assert (app.error, tasks.read_text().count('answer = "use B"')) == ("", 1)
        assert "touched by another writer" in tasks.read_text() and "answered g1" in _status(app)

    async def changed_question(app, pilot):
        await _open_answer(pilot, "use B")
        _rewrite(tasks, changed)
        await pilot.pause(0.3)
        await _go(pilot, "enter")
        ask = app.query_one("#ask")
        assert (tasks.read_text(), ask.display, ask.value) == (changed, True, "g1 use B")
        assert "C or D?" in app.error and "C or D?" in _status(app)

    async def refused(app, pilot):
        box = await _open_answer(pilot)
        box.value = "a\x07b"
        await _go(pilot, "enter")
        ask = app.query_one("#ask")
        assert (tasks.read_text(), ask.display, ask.value) == (BLOCKED, True, "g1 a\x07b")
        assert "control character" in app.error

    for body in (touched, changed_question, refused):
        _run(tasks, body, BLOCKED, size=(160, 40), poll=True)


def test_the_answer_prompt_wraps_and_alt_enter_alt_backspace_and_ctrl_w_edit_it(tasks):
    """A long answer is shown wrapped, alt+enter adds a line, and alt+backspace and ctrl+w delete one word."""

    long = " ".join(["word"] * 40)

    async def wrapped(app, pilot):
        box = await _open_answer(pilot)
        box.value = long
        box.end_cursor()
        await pilot.pause()
        assert box.wrapped_document.height > 1 and "\n" not in box.value
        await _go(pilot, "alt+enter", "x", "enter")
        assert f'answer = "{long}\\nx"' in tasks.read_text()

    async def words(app, pilot):
        box = await _open_answer(pilot, "one two three")
        await pilot.press("alt+backspace")
        assert box.value == "one two "
        await pilot.press("ctrl+w")
        assert box.value == "one "

    _run(tasks, wrapped, BLOCKED, size=(60, 40))
    _run(tasks, words, BLOCKED, size=(80, 40))


def test_the_blockers_selection_stays_out_of_the_tree_prompt_and_help_and_pagedown_work(briefs):
    """The blockers selection does not prefill the tree prompt, the help screen opens and closes, and pagedown scrolls a long list."""

    async def leak(app, pilot):
        await pilot.press("tab", "tab", "down")
        assert app.selected_id == "g1"
        await pilot.press("tab", "tab", "p")
        assert (app.view, app.query_one("#ask").value) == ("tree", "")
        await _go(pilot, "escape", "question_mark")
        assert len(app.screen_stack) == 2
        await _go(pilot, "escape")
        assert len(app.screen_stack) == 1

    async def pages(app, pilot):
        await _go(pilot, "tab", "tab")
        pane = app.query_one("#pane")
        await _go(pilot, "pagedown")
        assert pane.scroll_y > 0
        await _go(pilot, "pageup")
        assert pane.scroll_y == 0

    _run(briefs, leak)
    forty = (T(f"t{n:02d}", f"task {n}", "blocked", blocker=f"human: q{n}", write_set=[f"w{n}"]) for n in range(40))
    _run(briefs, pages, _toml(*forty), size=(100, 12))


def test_c_switches_between_the_columns_and_the_text_overview_and_fits_the_width(tasks):
    """C switches between the columns and the text overview, which order by state and priority and fall back to text when too narrow."""

    async def drive(app, pilot):
        await _go(pilot, "tab")
        drawn = _drawn(app)
        header = drawn.splitlines()[0]
        assert header.index("Todo (3)") < header.index("In progress (1)") < header.index("Blocked (1)")
        assert "columns" in _status(app) and drawn.index("high priority") < drawn.index("low priority")
        await _go(pilot, "c")
        text = _view(app)
        assert "text" in _status(app) and text.index("In progress") < text.index("Todo (") < text.index("Done")
        todo = text[text.index("Todo") :]
        assert todo.index("q1") < todo.index("hi") < todo.index("lo") < todo.index("w1")
        await _go(pilot, "c", "r")
        await pilot.resize_terminal(80, 30)
        await pilot.pause()
        assert app.show_columns and _view(app).startswith("In progress (1)") and "columns need" in _status(app)

    _run(tasks, drive, BOARD_TASKS, size=(110, 30))


def test_a_click_or_enter_on_a_card_or_a_tree_title_opens_the_overlay_and_one_on_the_border_only_selects(tasks):
    """A click or enter on a card or a tree title opens the overlay with every detail, and a click on a card border or priority only selects it."""

    async def card(app, pilot):
        await pilot.press("tab")
        await pilot.pause()
        await pilot.pause()
        collapsed = _drawn(app)
        assert "…" in collapsed and "deps:" not in collapsed
        await pilot.click("#view", offset=(42, 4))
        await pilot.pause()
        assert _screen(app) == "BriefScreen"
        assert "worktree: /w/base" in _rendered_overlay(app)
        await pilot.press("escape")
        await pilot.pause()
        assert len(app.screen_stack) == 1
        assert _drawn(app) == collapsed

    async def enter(app, pilot):
        await pilot.press("tab")
        await pilot.pause()
        await pilot.press("j", "enter")
        await pilot.pause()
        shown = _rendered_overlay(app)
        assert "a very long title that would be cut with an ellipsis" in shown
        assert "deps: base (running)" in shown and "state: pending" in shown
        await pilot.press("escape", "j", "enter")
        await pilot.pause()
        shown = _rendered_overlay(app)
        assert "branch: base-branch" in shown and "state: running" in shown

    async def border(app, pilot):
        await pilot.press("tab")
        await pilot.pause()
        await pilot.pause()
        assert app.card_id == ""
        # The left border of the Todo card, then the priority tag of the other card.
        await pilot.click("#view", offset=(0, 3))
        await pilot.pause()
        assert (len(app.screen_stack), app.card_id) == (1, "long")
        await pilot.click("#view", offset=(_drawn(app).splitlines()[2].rindex("P2") + 1, 2))
        await pilot.pause()
        assert (len(app.screen_stack), app.card_id) == (1, "base")
        await pilot.press("o")
        await pilot.pause()
        assert "worktree: /w/base" in _rendered_overlay(app)

    async def title(app, pilot):
        rows = _view(app).splitlines()
        y = next(n for n, row in enumerate(rows) if " · " in row and "g2" in row)
        await pilot.click("#view", offset=(rows[y].index(" · ") - 1, y))
        await pilot.pause()
        assert "ship the thing" in _rendered_overlay(app)
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        await pilot.press("tab", "tab", "o")
        await pilot.pause()
        assert _screen(app) == "BriefScreen"
        await pilot.press("q")
        assert len(app.screen_stack) == 1

    _run(tasks, card, EXPAND_TASKS, size=(160, 40))
    _run(tasks, enter, EXPAND_TASKS, size=(160, 40))
    _run(tasks, border, EXPAND_TASKS, size=(160, 40))
    _write(tasks.parent / "briefs" / "g2.md", BRIEF)
    _run(tasks, title, INBOX, size=(100, 30))


def test_the_overlay_shows_the_state_the_last_events_and_the_question_only_for_a_blocked_task(briefs):
    """The overlay shows the state and the last events without a brief, and the question and its keys only for a blocked task."""

    folder = briefs.parent
    (folder / "briefs" / "g2.md").unlink()
    lines = [f'{{"ts": "t{n}", "event": "ev{n}", "detail": "d{n}"}}' for n in range(12)]
    _write(folder / "events" / "g2.jsonl", "\n".join([*lines, "{torn"]))

    def footer(app) -> set[str]:
        return {key.action for key in app.screen.query("FooterKey")}

    async def events(app, pilot):
        await pilot.press("tab", "tab", "o")
        await pilot.pause()
        text = _rendered_overlay(app)
        assert "state: " in text and "History" in text
        assert "t1 ev1" not in text
        assert text.rstrip().splitlines()[-3] == "t11 ev11 d11"
        await pilot.press("escape")
        (folder / "events" / "g2.jsonl").write_text("{torn\n")
        await pilot.press("o")
        await pilot.pause()
        assert "History" not in _rendered_overlay(app)

    async def question(app, pilot):
        await _open(app, pilot, "g2")
        shown = _rendered_overlay(app)
        for part in ("Question", "now or later?", "Recommendation", "e to answer"):
            assert part in shown
        await pilot.press("escape")
        await _open(app, pilot, "g1")
        assert "A or B?" in _rendered_overlay(app)
        await pilot.press("escape")
        await _open(app, pilot, "g3")
        assert "Question" not in _rendered_overlay(app)
        assert "e to answer" not in _rendered_overlay(app)
        await pilot.pause()
        assert {"pick('priority')", "pick('state')"} <= footer(app)
        assert "ask('answer')" not in footer(app)
        await pilot.press("e")
        assert not app.screen.query_one("#overlay-ask").display
        await pilot.press("escape")
        assert len(app.screen_stack) == 1
        await _open(app, pilot, "g1")
        await pilot.pause()
        assert "ask('answer')" in footer(app)

    _run(briefs, events, size=(100, 30))
    (folder / "events" / "g2.jsonl").unlink()
    _write(folder / "inbox" / "g2.md", CONTEXT)
    _run(briefs, question, size=(100, 40))


def test_an_answer_typed_in_the_blockers_overlay_writes_what_the_cli_writes(inbox):
    """An answer typed in the overlay of the blockers list writes the same bytes as the CLI."""

    async def through_overlay(app, pilot):
        await pilot.press("tab", "tab", "enter")
        assert app.screen.task_id == "g2"
        await pilot.press("e")
        assert app.screen.query_one("#overlay-ask").display
        await pilot.press(*"use later")
        await pilot.press("enter")
        await pilot.pause()
        assert (len(app.screen_stack), app.error, app.graph.tasks["g2"].status) == (1, "", "pending")
        assert _view(app).splitlines() == ["> ✖ g1  gate  P2"]
        assert "1 awaiting you" in _status(app)
        await pilot.press("shift+tab", "shift+tab")
        await pilot.pause()
        assert "▶" in next(x for x in _view(app).splitlines() if "g2" in x)

    _run(inbox, through_overlay, size=(100, 40))
    cli = inbox.parent / "cli.toml"
    cli.write_text(INBOX)
    _mod().ledger_graph.answer_blocker(cli, "g2", "use later", seen_mtime_ns=cli.stat().st_mtime_ns)
    assert 'answer = "use later"' in inbox.read_text()
    assert inbox.read_text() == cli.read_text()


def test_s_offers_to_activate_a_deferred_task_and_nothing_for_a_running_one(tasks):
    """S or a click on the state offers to activate a deferred task, and only cancel for a running one."""

    text = INBOX + _tables(T("g4", "four", "deferred", write_set=["j"]), T("g5", "five", "in_progress", write_set=["k"]))

    async def drive(app, pilot):
        await _open(app, pilot, "g5")
        await pilot.press("s")
        assert app.screen.query_one("#pick-list").option_count == 1
        await pilot.press("enter")
        assert _screen(app) == "BriefScreen"
        assert tasks.read_text() == text
        await pilot.press("escape")
        await _open(app, pilot, "g4")
        await _click_title(app, pilot, "state")
        assert app.screen.query_one("#pick-list").option_count == 2
        await pilot.press("down", "enter")
        await pilot.pause()
        assert _screen(app) == "BriefScreen"
        assert app.graph.tasks["g4"].status == "pending"
        assert "pending" in app.screen.heading.plain

    _run(tasks, drive, text, size=(100, 30))


def test_a_file_change_under_an_open_overlay_refuses_the_write_typed_in_it(inbox):
    """A write typed in an overlay drawn from an old read is refused, and the reopened overlay writes."""

    changed = INBOX.replace('title = "three"', 'title = "other"')

    async def drive(app, pilot):
        await _open(app, pilot, "g3")
        _rewrite(inbox, changed)
        await pilot.pause(0.3)
        # The poll read the new file under the overlay, which keeps its old content.
        assert app.graph.tasks["g3"].title == "other"
        await pilot.press("p", "up", "enter")
        await pilot.pause()
        assert _screen(app) == "BriefScreen"
        assert inbox.read_text() == changed
        assert app.graph.tasks["g3"].priority == "P1"
        # Opened again, the overlay is drawn from the new read and writes.
        await pilot.press("escape")
        await _open(app, pilot, "g3")
        await pilot.press("p", "up", "enter")
        await pilot.pause()
        assert app.graph.tasks["g3"].priority == "P0"

    _run(inbox, drive, size=(100, 30), poll=True)


def test_a_click_on_an_id_or_on_blocks_n_spotlights_it_and_fades_the_rows_it_does_not_hold_up(tasks):
    """A click on an id or on blocks N spotlights it and fades the other rows, and a second click, x and escape toggle it."""

    spot = {"aa": False, "bb": False, "cc": False, "dd": True}

    async def own(app, pilot):
        await pilot.pause()
        assert (app.spotlit, any(_faded(app).values())) == ("", False)
        await _click_id(app, pilot, "bb")
        await pilot.pause()
        assert (app.spotlit, _faded(app)) == ("bb", spot)
        await _click_id(app, pilot, "bb")
        await pilot.pause()
        assert (app.spotlit, any(_faded(app).values())) == ("", False)
        # The second hit of "bb" is the one cc waits on.
        await _click_id(app, pilot, "bb", nth=1)
        await pilot.pause()
        assert app.spotlit == "bb"
        await _go(pilot, "escape")
        assert app.spotlit == ""
        await _go(pilot, "x")
        assert (app.spotlit, _faded(app)) == ("aa", spot)
        await pilot.press("x")
        assert app.spotlit == ""

    async def blocks(app, pilot):
        await pilot.pause()
        lines = _body(app).plain.split("\n")
        y = next(n for n, line in enumerate(lines) if " aa " in f"{line} " and "blocks" in line)
        await pilot.click("#view", offset=(lines[y].index("blocks") + 2, y))
        await pilot.pause()
        assert (app.spotlit, _faded(app)) == ("aa", spot)

    for body in (own, blocks):
        _run(tasks, body, SPOT_TASKS, size=(120, 30))


def test_an_epic_overlay_lists_the_open_children_and_a_child_opens_its_own(tasks):
    """An epic overlay lists the open children without state or deps, and a click on a child opens its own overlay."""

    async def children(app, pilot):
        await _open(app, pilot, "e1")
        shown = _rendered_overlay(app)
        assert all(part in shown for part in ("Children", "1 done · 1 pending", "c2 open child"))
        assert not any(part in shown for part in ("c1", "state:", "deps:"))
        rows = shown.splitlines()
        y = next(n for n, r in enumerate(rows) if "c2 open child" in r)
        await pilot.click("#brief", offset=(rows[y].index("c2") + 1, y))
        await pilot.pause()
        await pilot.pause()
        assert (_screen(app), app.screen.task_id, len(app.screen_stack)) == ("BriefScreen", "c2", 2)
        shown = _rendered_overlay(app)
        assert "state: pending" in shown and "Children" not in shown

    _run(tasks, children, _toml(*EPIC), size=(100, 30))


@pytest.mark.parametrize("view", ["tree", "columns", "text"])
def test_a_click_on_the_copy_icon_of_a_row_copies_the_command_and_the_id_still_opens_the_overlay(tasks, view):
    """A click on the copy icon of a row copies the review command, and the title still opens the overlay."""

    async def drive(app, pilot):
        if view != "tree":
            await pilot.press("tab")
        await pilot.pause()
        rows = _drawn(app).splitlines()
        ((y, x),) = [(y, row.index("⧉")) for y, row in enumerate(rows) if "⧉" in row]
        await pilot.click("#view", offset=(x + (view == "tree"), y))
        await pilot.pause()
        assert (app.clipboard, len(app.screen_stack)) == (REVIEW_COMMAND, 1)
        if view == "text":
            return
        # The title of a row and the card itself still open the overlay.
        rows = _drawn(app).splitlines()
        y, row = next((y, r) for y, r in enumerate(rows) if "in review" in r)
        await pilot.click("#view", offset=(row.index("in review") + (view == "tree"), y))
        await pilot.pause()
        assert (_screen(app), app.screen.task_id) == ("BriefScreen", "rv")

    _run(tasks, drive, REVIEW_TASKS, size=(60 if view == "text" else 160, 30))


def test_o_and_y_act_on_the_spotlit_task_and_wait_without_one(tasks):
    """O and y act on the spotlit task, and do nothing silently without one."""

    _tip(tasks)
    notes: list[str] = []

    async def drive(app, pilot):
        app.notify = lambda message, **_: notes.append(message)
        await pilot.pause()
        await pilot.press("o", "y")
        assert (len(app.screen_stack), notes) == (1, [])
        assert not app.clipboard
        app.action_spotlight("rv")
        await pilot.press("y")
        assert app.clipboard == TIP_COMMAND
        await pilot.press("o")
        await pilot.pause()
        assert app.screen.task_id == "rv"

    _run(tasks, drive, REVIEW_TASKS, size=(120, 30))


def test_the_review_section_and_its_copy_icon_show_only_for_a_task_with_a_worktree_and_a_branch(tasks):
    """The review section shows the command and its icon, y and the icon copy it, and a task without worktree or branch has neither."""

    async def section(app, pilot):
        await _open(app, pilot, "rv")
        text = _rendered_overlay(app)
        assert "Review" in text and TIP_COMMAND in text
        assert text.count("⧉") == 1
        await pilot.press("y")
        await pilot.pause()
        assert app.clipboard == TIP_COMMAND
        app.copy_to_clipboard("")
        brief = app.screen.query_one("#brief")
        out = io.StringIO()
        rich.console.Console(file=out, width=brief.size.width, color_system=None).print(brief.content)
        rows = out.getvalue().splitlines()
        y, x = next((y, row.index("⧉")) for y, row in enumerate(rows) if "⧉" in row)
        await pilot.click("#brief", offset=(x, y))
        await pilot.pause()
        assert (app.clipboard, _screen(app)) == (TIP_COMMAND, "BriefScreen")

    async def missing(app, pilot, task_id):
        await _open(app, pilot, task_id)
        assert "Review" not in _rendered_overlay(app) and "⧉" not in _rendered_overlay(app)
        await pilot.press("y")
        await pilot.pause()
        assert app.clipboard == ""
        await pilot.press("escape")
        for _ in range(2):
            await pilot.pause()
            rows = [r for r in _drawn(app).splitlines() if task_id in r]
            assert rows and not any("⧉" in row for row in rows)
            await pilot.press("tab")

    _tip(tasks)
    _run(tasks, section, REVIEW_TASKS, size=(120, 30))
    (tasks.parent / "ORCHESTRATION.md").unlink()
    for task_id in ("nw", "nb"):
        _run(tasks, functools.partial(missing, task_id=task_id), REVIEW_TASKS, size=(160, 40))

    module = _mod()
    assert module._review_commands(_graph(REVIEW_TASKS), "integration") == {"rv": REVIEW_COMMAND}
    graph = _graph(REVIEW_TASKS.replace('status = "pending"', 'status = "in_progress"'))
    assert module._review_commands(graph, "integration")["ip"] == "cd ~/wt/ip && hunk diff integration...feat/ip"
    assert "ip" in module._hunk_targets(graph)


def test_h_marks_the_spotlit_task_or_the_overlay_one_and_a_marked_task_awaits_you_in_the_blockers_list(tasks):
    """H marks the spotlit task after a confirmation, cancel writes nothing, h again clears it, and in the overlay h marks it into the blockers list."""

    async def spotlit(app, pilot):
        app.action_spotlight("rv")
        await pilot.press("h")
        await pilot.pause()
        assert _screen(app) == "PickScreen"
        await pilot.press("escape")
        await pilot.pause()
        assert tasks.read_text() == REVIEW_TASKS
        await pilot.press("h")
        await _confirm(pilot)
        assert tasks.read_text() == MARKED
        assert [r for r in _drawn(app).splitlines() if "in review" in r and "◆" in r]
        await pilot.press("h")
        await _confirm(pilot)
        assert tasks.read_text() == REVIEW_TASKS

    async def overlay(app, pilot):
        await _open(app, pilot, "rv")
        await pilot.press("h")
        await _confirm(pilot)
        assert _screen(app) == "BriefScreen"
        assert "marked for your review" in _rendered_overlay(app)
        assert tasks.read_text() == MARKED
        await pilot.press("escape", "tab", "tab")
        await pilot.pause()
        assert app.view == "inbox"
        assert [r for r in _drawn(app).splitlines() if "rv" in r and "◆" in r]
        assert "1 awaiting you" in _status(app)

    _run(tasks, spotlit, REVIEW_TASKS, size=(160, 30))
    _run(tasks, overlay, REVIEW_TASKS, size=(160, 30))


def test_a_confirmation_is_refused_when_the_file_changed_or_disappeared_meanwhile(tasks):
    """A confirmed mark or send is refused when the file changed or disappeared while the confirmation was open."""

    def notified(app, text: str) -> bool:
        return any(text in n.message for n in app._notifications)

    async def disappeared(app, pilot):
        app.action_spotlight("rv")
        await pilot.press("h")
        await pilot.pause()
        tasks.unlink()
        await _confirm(pilot)
        assert app.error

    async def marked_meanwhile(app, pilot):
        app.action_spotlight("rv")
        await pilot.press("h")
        await pilot.pause()
        _rewrite(tasks, MARKED)
        await _confirm(pilot)
        assert tasks.read_text() == MARKED
        assert notified(app, "changed in the file")

    async def left_review(app, pilot):
        app.saved_comments("rv", "~/wt/rv")
        app.action_spotlight("rv")
        await pilot.press("f")
        await pilot.pause()
        _rewrite(tasks, REVIEW_TASKS.replace('title = "in review"\nstatus = "in_rev"', 'title = "in review"\nstatus = "pending"'))
        await _confirm(pilot)
        assert json.loads(_feedback(tasks).read_text())["ready"] is False
        assert notified(app, "not in_progress or in_rev")

    _run(tasks, disappeared, REVIEW_TASKS, size=(160, 30))
    _run(tasks, marked_meanwhile, REVIEW_TASKS, size=(160, 30))
    _run(tasks, left_review, REVIEW_TASKS, size=(160, 30), hunk=FakeHunk())


def test_the_poll_asks_hunk_for_the_tasks_with_a_worktree_saves_a_draft_and_survives_a_failing_hunk(tasks):
    """The poll asks hunk for the running and review tasks with a worktree and saves a draft, and a failing hunk saves nothing."""

    hunk = FakeHunk()
    rv = str(pathlib.Path("~/wt/rv").expanduser())

    async def poll(app, pilot):
        app.poll_hunk()
        await app.workers.wait_for_complete()
        await pilot.pause()
        assert not app.hunk_busy

    async def drive(app, pilot):
        assert _mod()._hunk_targets(app.graph) == {"rv": rv, "nb": str(pathlib.Path("~/wt/nb").expanduser())}
        await poll(app, pilot)
        assert hunk.calls[0][0] == ["session", "comment", "list", "--repo", rv, "--type", "user", "--json"]
        assert json.loads(_feedback(tasks).read_text()) == {"ready": False, "comments": [HUNK_NOTE]}
        assert [r for r in _drawn(app).splitlines() if "in review" in r and "✎1" in r]

    _run(tasks, drive, REVIEW_TASKS, size=(160, 30), hunk=hunk)
    _feedback(tasks).unlink()
    for failing in (FakeHunk("No active Hunk sessions", ok=False), FakeHunk("not json"), RaisingHunk()):
        _run(tasks, poll, REVIEW_TASKS, size=(160, 30), hunk=failing)
        assert not _feedback(tasks).exists()


def test_f_sends_the_saved_comments_after_a_confirmation_and_refuses_when_there_are_none(tasks):
    """F sends the saved comments after a confirmation, and refuses when there are none or they were sent."""

    async def drive(app, pilot):
        app.action_spotlight("rv")
        await pilot.press("f")
        await pilot.pause()
        assert _screen(app) != "PickScreen"
        assert any("no saved comments" in n.message for n in app._notifications)
        app.saved_comments("rv", "~/wt/rv")
        app.reload()
        await pilot.press("f")
        await pilot.pause()
        assert [v for v, *_ in app.screen.choices] == ["cancel", "confirm"]
        await pilot.press("escape")
        await pilot.pause()
        assert json.loads(_feedback(tasks).read_text())["ready"] is False
        await pilot.press("f")
        await pilot.pause()
        await _confirm(pilot)
        assert json.loads(_feedback(tasks).read_text())["ready"] is True
        assert tasks.read_text() == REVIEW_TASKS
        assert [r for r in _drawn(app).splitlines() if "in review" in r and "✉1" in r]
        await pilot.press("f")
        await pilot.pause()
        assert _screen(app) != "PickScreen"
        assert any("sent already" in n.message for n in app._notifications)

    _run(tasks, drive, REVIEW_TASKS, size=(160, 30), hunk=FakeHunk())


def test_f_and_capital_f_work_in_the_overlay_and_restore_reports_what_hunk_said(tasks):
    """F and F work in the overlay, restore applies the comments to hunk, and a refusal shows what hunk said."""

    hunk = FakeHunk()
    worktree = str(pathlib.Path("~/wt/rv").expanduser())

    async def applied(app, pilot):
        app.saved_comments("rv", worktree)
        hunk.calls.clear()
        await _open(app, pilot, "rv")
        assert "✎ 1 comment saved" in _rendered_overlay(app)
        await pilot.press("f")
        await pilot.pause()
        await _confirm(pilot)
        assert _screen(app) == "BriefScreen"
        assert "✉ 1 comment sent" in _rendered_overlay(app)
        hunk.output = ""
        await pilot.press("F")
        await pilot.pause()
        assert _screen(app) == "PickScreen"
        await _confirm(pilot)
        await app.workers.wait_for_complete()
        await pilot.pause()
        args, stdin = hunk.calls[-1]
        assert args == ["session", "comment", "apply", "--repo", worktree, "--stdin"]
        comment = {"filePath": "m.py", "newLine": 9, "summary": "use type hints", "author": "user"}
        assert json.loads(stdin) == {"comments": [comment]}
        assert any("applied 1 comment to hunk" in n.message for n in app._notifications)

    async def refused(app, pilot):
        app.saved_comments("rv", "~/wt/rv")
        app.hunk.output, app.hunk.ok = "No active Hunk sessions", False
        app.action_spotlight("rv")
        await pilot.press("F")
        await pilot.pause()
        await _confirm(pilot)
        await app.workers.wait_for_complete()
        await pilot.pause()
        said = "hunk did not take the comments: No active Hunk sessions"
        assert any(said in n.message for n in app._notifications)

    _run(tasks, applied, REVIEW_TASKS, size=(160, 30), hunk=hunk)
    _feedback(tasks).unlink()
    _run(tasks, refused, REVIEW_TASKS, size=(160, 30), hunk=FakeHunk())


def test_capital_r_offers_the_epic_scope_only_on_a_spotlit_epic_and_writes_one_request(tasks):
    """R offers the epic scope only on a spotlit epic, cancel writes nothing, a pick writes one request, and a multi-line note is refused."""

    folder = tasks.parent / "replan-requests"

    async def choices(app, pilot) -> list[str]:
        await pilot.press("R")
        await pilot.pause()
        return [v for v, *_ in app.screen.choices]

    async def epic(app, pilot):
        assert await choices(app, pilot) == ["cancel", "remaining"]
        await pilot.press("escape")
        await pilot.pause()
        assert not folder.exists()
        app.action_spotlight("a")
        assert await choices(app, pilot) == ["cancel", "remaining"]
        await pilot.press("escape")
        app.action_spotlight("a")
        app.action_spotlight("E1")
        assert await choices(app, pilot) == ["cancel", "remaining", "epic"]
        await pilot.press("j", "j", "enter")
        await pilot.pause()
        ask = app.query_one("#ask")
        assert ask.display
        await pilot.press("s", "p", "l", "i", "t", "enter")
        await pilot.pause()
        assert (app.error, ask.display) == ("", False)

    async def note(app, pilot):
        await pilot.press("R")
        await pilot.pause()
        await _confirm(pilot)
        ask = app.query_one("#ask")
        ask.insert_at_cursor("one\ntwo")
        await pilot.press("enter")
        await pilot.pause()
        assert "one line" in app.error
        assert ask.display
        assert not folder.exists()
        ask.value = ""
        await pilot.press("enter")
        await pilot.pause()
        assert not ask.display

    for body, request in ((epic, {"scope": "epic", "epic": "E1", "note": "split"}), (note, {"scope": "remaining"})):
        _run(tasks, body, REPLAN_TASKS, size=(160, 30))
        (written,) = folder.iterdir()
        assert tomllib.loads(written.read_text()) == request
        assert tasks.read_text() == REPLAN_TASKS
        assert not (tasks.parent / "steering.log").exists()
        written.unlink()
        folder.rmdir()


# Private helpers


def _load() -> typing.Any:
    spec = importlib.util.spec_from_file_location("ledger_board", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _mod() -> typing.Any:
    """The board module the last `_load` or `_run` loaded."""

    return sys.modules["ledger_board"]


def _run(path: pathlib.Path, body, text: str | None = None, *, size=(80, 24), poll: bool = False, **kwargs) -> None:
    """Write `text` to `path` if given, then run `body(app, pilot)` in a board freshly loaded on it."""

    if text is not None:
        path.write_text(text)
    module = _load()
    if poll:
        module.POLL_SECONDS = 0.05
    app = module.BoardApp(path, **kwargs)

    async def drive():
        async with app.run_test(size=size) as pilot:
            await body(app, pilot)

    asyncio.run(drive())


def _write(path: pathlib.Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _graph(text: str):
    return _mod().ledger_graph.Graph(_mod().ledger_graph.parse(text)[0])


def _tip(tasks: pathlib.Path) -> None:
    (tasks.parent / "ORCHESTRATION.md").write_text("- Stack tip: `stack/tip`\n")


def _feedback(path: pathlib.Path) -> pathlib.Path:
    return path.parent / "feedback" / "rv.json"


async def _go(pilot, *keys: str) -> None:
    """Press `keys` and let the board settle."""

    await pilot.press(*keys)
    await pilot.pause()


def _screen(app) -> str:
    return type(app.screen).__name__


def _body(app):
    return app.query_one("#view", textual.widgets.Static).content


def _view(app) -> str:
    return str(_body(app))


def _status(app) -> str:
    return str(app.query_one("#status", textual.widgets.Static).content)


def _rewrite(path: pathlib.Path, text: str) -> None:
    previous = path.stat().st_mtime_ns
    path.write_text(text)
    # A coarse clock could give the same stamp to two quick writes.
    os.utime(path, ns=(previous + 10**9, previous + 10**9))


def _rendered_overlay(app) -> str:
    console = rich.console.Console(width=80, record=True, file=io.StringIO())
    console.print(app.screen.query_one("#brief").content)
    return console.export_text()


def _drawn(app) -> str:
    """The text of the main view as the pane shows it, wrapped to its width."""

    content = _body(app)
    if isinstance(content, str):
        return content
    out = io.StringIO()
    width = app.query_one("#pane").scrollable_content_region.width
    rich.console.Console(file=out, width=width, color_system=None).print(content)
    return out.getvalue()


def _footer_actions(app) -> set[str]:
    return {key.action for key in app.query("FooterKey")}


def _footer_text(app) -> str:
    """The footer as text: the chip, then each key, in brackets when it is dimmed."""

    chip = [str(label.render()) for label in app.query("FooterLabel.target-chip")]
    keys = [f"{key.key_display} {key.description}" for key in app.query("FooterKey")]
    dimmed = [key.has_class("-disabled") for key in app.query("FooterKey")]
    return " | ".join(chip + [f"[{k}]" if d else k for k, d in zip(keys, dimmed, strict=True)])


def _click_tab(app, pilot, text: str):
    return pilot.click("#status", offset=(_status(app).index(text) + 1, 0))


async def _open_answer(pilot, text: str = "") -> typing.Any:
    await pilot.press("tab", "tab", "enter", "e")
    box = pilot.app.screen.query_one("#overlay-ask", textual.widgets.TextArea)
    assert box.display
    if text:
        await pilot.press(*("space" if c == " " else c for c in text))
    return box


async def _open(app, pilot, task_id: str) -> None:
    app.action_brief(task_id)
    await pilot.pause()
    assert _screen(app) == "BriefScreen"


async def _click_title(app, pilot, kind: str) -> None:
    pane = app.screen.query_one("#brief-pane")
    start, _ = app.screen.heading_part(kind)
    await pilot.click(offset=(pane.region.x + _mod().TITLE_INDENT + start, pane.region.y))
    await pilot.pause()


def _faded(app) -> dict[str, bool]:
    """Whether the row of each id is faded, by the color of its first cell."""

    fade = rich.color.Color.parse(_mod().FADE_DARK)
    out = {}
    for line in _body(app).split("\n", allow_blank=True):
        own, _ = _mod()._tree_ids(line.plain, {"aa", "bb", "cc", "dd"})
        if own:
            out[own] = fade in {span.style.color for span in line.spans if hasattr(span.style, "color")}
    return out


def _click_id(app, pilot, task_id: str, nth: int = 0):
    hits = [
        (y, m.start())
        for y, line in enumerate(_body(app).plain.split("\n"))
        for m in re.finditer(rf"\b{task_id}\b", line)
    ]
    y, x = hits[nth]
    # The tree has a left padding of one cell.
    return pilot.click("#view", offset=(x + 1, y))


async def _confirm(pilot) -> None:
    """Pick the second choice of the confirmation, which starts on cancel."""

    await pilot.pause()
    await pilot.press("j", "enter")
    await pilot.pause()


class FakeHunk:
    """A hunk runner that records its calls and answers with a fixed result."""

    def __init__(self, output: str = EXPORT, *, ok: bool = True):
        self.output, self.ok = output, ok
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, args: list[str], stdin: str = "") -> tuple[bool, str]:
        self.calls.append((args, stdin))
        return self.ok, self.output


class RaisingHunk(FakeHunk):
    def __call__(self, args: list[str], stdin: str = "") -> tuple[bool, str]:
        raise RuntimeError("boom")
