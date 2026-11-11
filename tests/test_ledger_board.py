from __future__ import annotations

import asyncio
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
import rich.panel
import rich.style
import rich.text
import textual.color
import textual.theme
import textual.widgets
import tomllib
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".agents/skills/orchestrating-agents/scripts/ledger_board.py"
TASKS = """format = 1

[[task]]
id = "g1"
title = "gate"
status = "pending"
write_set = ["g"]

[[task]]
id = "g2"
title = "two"
priority = "P2"
status = "pending"
deps = ["g1"]
write_set = ["h"]
"""


def _load():
    spec = importlib.util.spec_from_file_location("ledger_board", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def _home_with_task_worktrees(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The fake `~/wt/*` worktrees exist, so review commands keep their `cd`."""

    home = tmp_path / "home"
    for name in ("rv", "nb", "ip"):
        (home / "wt" / name).mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))


def _view(app) -> str:
    return str(app.query_one("#view", textual.widgets.Static).content)


@pytest.fixture
def tasks(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(TASKS)
    return path


def test_shift_tab_goes_back_one_view_and_wraps(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("shift+tab")
            assert app.view == "warnings"
            await pilot.press("tab")
            assert app.view == "tree"
            await pilot.press("tab")
            assert app.view == "board"
            await pilot.press("shift+tab")
            assert app.view == "tree"

    asyncio.run(drive())


def test_tab_cycles_the_tree_the_board_and_the_blockers(tasks: pathlib.Path):
    module = _load()
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert module.VIEWS == ("tree", "board", "inbox", "warnings")
            assert "Todo (" not in _view(app)
            assert "g2" in _view(app)
            await pilot.press("tab")
            assert "Todo (" in _view(app)
            assert "In progress (0)" in _view(app)
            await pilot.press("tab")
            assert app.view == "inbox"
            assert "nothing awaiting you" in _view(app)
            await pilot.press("tab", "tab")
            assert app.view == "tree"

    asyncio.run(drive())


def test_r_reloads_the_file(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert "renamed" not in _view(app)
            tasks.write_text(TASKS.replace('title = "two"', 'title = "renamed"'))
            await pilot.press("r")
            assert "renamed" in _view(app)
            assert app.error == ""

    asyncio.run(drive())


def test_a_broken_file_keeps_the_last_good_view(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            good = _view(app)
            tasks.write_text('format = 1\n[[task]]\nid = "g1"\ntitle = "tru')
            await pilot.press("r")
            assert _view(app) == good
            assert "invalid TOML" in app.error

    asyncio.run(drive())


def test_p_writes_the_priority_the_human_types(tasks: pathlib.Path):
    module = _load()
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("p")
            ask = app.query_one("#ask", textual.widgets.TextArea)
            assert ask.display
            await pilot.press("g", "2", "space", "P", "0", "enter")
            assert app.error == ""
            assert not ask.display
            assert 'priority = "P0"' in tasks.read_text()
            assert app.graph is not None
            assert app.graph.tasks["g2"].priority == "P0"

    asyncio.run(drive())


def test_p_on_a_spotlit_task_opens_the_priority_menu_and_writes_only_the_priority(
    tasks: pathlib.Path,
):
    module = _load()
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            app.action_spotlight("g2")
            await pilot.press("p")
            await pilot.pause()
            assert isinstance(app.screen, module.PickScreen)

            await pilot.press("escape")
            await pilot.pause()
            assert tasks.read_text() == TASKS

            await pilot.press("p")
            await pilot.pause()
            await pilot.press("home", "enter")
            await pilot.pause()
            assert not isinstance(app.screen, module.PickScreen)
            before = TASKS.splitlines()
            after = tasks.read_text().splitlines()
            changed = [b for a, b in zip(before, after, strict=True) if a != b]
            assert changed == ['priority = "P0"']
            assert app.graph is not None
            assert app.graph.tasks["g2"].priority == "P0"

    asyncio.run(drive())


def test_p_shows_the_error_of_a_refused_priority(tasks: pathlib.Path):
    module = _load()
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("p")
            await pilot.press("g", "2", "space", "P", "9", "enter")
            assert "unknown priority 'P9'" in app.error
            assert tasks.read_text() == TASKS

    asyncio.run(drive())


def test_a_shows_and_hides_the_done_tasks(tasks: pathlib.Path):
    tasks.write_text(
        TASKS.replace(
            'id = "g1"\ntitle = "gate"\nstatus = "pending"',
            'id = "g1"\ntitle = "gate"\nstatus = "done"',
        )
    )
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert "gate" not in _view(app)
            await pilot.press("a")
            assert "gate" in _view(app)

    asyncio.run(drive())


def test_m_shows_and_hides_the_milestones(tasks: pathlib.Path):
    tasks.write_text(TASKS.replace('status = "pending"\nwrite_set = ["g"]', 'status = "pending"\nmilestone = "m9-demo"\nwrite_set = ["g"]'))
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert "m9-demo" not in _view(app)
            assert not re.search(r"·\s{2,}|·\s*$", _view(app), re.M)
            await pilot.press("m")
            assert "m9-demo" in _view(app)
            await pilot.press("m")
            assert "m9-demo" not in _view(app)

    asyncio.run(drive())


def test_l_opens_the_legend_and_l_or_escape_closes_it(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert "✔ done" not in _view(app)
            for key in ("l", "escape"):
                await pilot.press("l")
                assert type(app.screen).__name__ == "LegendScreen"
                assert "✔  done" in str(app.screen.query_one("#legend").render())
                await pilot.press("r", "a")
                assert not app.show_done
                await pilot.press(key)
                assert len(app.screen_stack) == 1

    asyncio.run(drive())


def test_l_is_ignored_while_the_prompt_is_open(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("p", "l")
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


WIDE = TASKS.replace('title = "two"', f'title = "{"a long title " * 8}"')


def test_the_tree_fits_the_pane_and_follows_a_resize(tmp_path: pathlib.Path):
    """The tree is cut to the width of the pane, and a resize draws it again."""

    path = tmp_path / "tasks.toml"
    path.write_text(WIDE)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(60, 3)) as pilot:
            pane = app.query_one("#pane")
            narrow = _view(app)
            assert pane.show_vertical_scrollbar
            assert not pane.show_horizontal_scrollbar
            assert max(len(line) for line in narrow.splitlines()) <= app.view_width()
            await pilot.resize_terminal(110, 24)
            await pilot.pause()
            wide = _view(app)
            assert wide != narrow
            assert not pane.show_horizontal_scrollbar
            assert max(len(line) for line in wide.splitlines()) <= 110

    asyncio.run(drive())


def test_escape_hides_the_prompt_without_writing(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("p")
            ask = app.query_one("#ask", textual.widgets.TextArea)
            assert ask.display
            await pilot.press("escape")
            assert not ask.display
            assert tasks.read_text() == TASKS
            await pilot.press("q")

    asyncio.run(drive())


BLOCKED = TASKS.replace(
    'id = "g1"\ntitle = "gate"\nstatus = "pending"',
    'id = "g1"\ntitle = "gate"\nstatus = "blocked"\nblocker = "human: A or B?"',
)


def test_an_answer_typed_in_the_blockers_overlay_updates_the_ledger_and_the_list(
    tasks: pathlib.Path,
):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert "1 awaiting you" in str(
                app.query_one("#status", textual.widgets.Static).content
            )
            await pilot.press("tab", "tab")
            assert "g1" in _view(app)
            await pilot.press("enter")
            assert type(app.screen).__name__ == "BriefScreen"
            await pilot.press("e")
            await pilot.press(*"use option B")
            await pilot.press("enter")
            await pilot.pause()
            assert type(app.screen).__name__ != "BriefScreen"
            assert app.error == ""
            assert 'answer = "use option B"' in tasks.read_text()
            assert "nothing awaiting you" in _view(app)
            assert "awaiting you" not in str(
                app.query_one("#status", textual.widgets.Static).content
            )
            await pilot.press("shift+tab", "shift+tab")
            await pilot.pause()
            assert "▶ gate · g1" in _view(app)

    asyncio.run(drive())


def test_e_does_nothing_in_the_blockers_list(tasks: pathlib.Path):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "e")
            assert not app.query_one("#ask", textual.widgets.TextArea).display
            assert type(app.screen).__name__ != "BriefScreen"
            assert tasks.read_text() == BLOCKED

    asyncio.run(drive())


def test_escape_cancels_the_answer_in_the_blockers_overlay(tasks: pathlib.Path):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "enter", "e")
            ask = app.screen.query_one("#overlay-ask", textual.widgets.TextArea)
            assert ask.display
            await pilot.press("escape")
            assert not ask.display
            await pilot.press("escape")
            assert type(app.screen).__name__ != "BriefScreen"
            assert tasks.read_text() == BLOCKED

    asyncio.run(drive())


INBOX = """format = 1

[[task]]
id = "g1"
title = "gate"
priority = "P2"
status = "blocked"
blocker = "human: A or B?"
write_set = ["g"]

[[task]]
id = "g2"
title = "two"
priority = "P0"
status = "blocked"
blocker = "human: now or later?"
write_set = ["h"]

[[task]]
id = "g3"
title = "three"
priority = "P1"
status = "pending"
write_set = ["i"]
"""
CONTEXT = "## Question\n\nnow or later?\n\n## Recommendation\n\nlater\n"


def _overlay_text(app) -> str:
    content = app.screen.query_one("#brief").content
    parts = getattr(content, "renderables", [content])
    return "\n".join(getattr(part, "plain", str(part)) for part in parts)


@pytest.fixture
def inbox(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(INBOX)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "g2.md").write_text(CONTEXT)
    return path


def test_the_blockers_list_has_a_row_per_awaiting_task_most_urgent_first(
    inbox: pathlib.Path,
):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab")
            assert _view(app).splitlines() == ["> ✖ g2  two  P0", "  ✖ g1  gate  P2"]
            assert "2 awaiting you" in str(
                app.query_one("#status", textual.widgets.Static).content
            )

    asyncio.run(drive())


def test_enter_opens_the_overlay_of_the_selected_blocker(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "enter")
            assert type(app.screen).__name__ == "BriefScreen"
            assert "now or later?" in _overlay_text(app)
            await pilot.press("escape")
            await pilot.press("j", "enter")
            assert "A or B?" in _overlay_text(app)

    asyncio.run(drive())


def test_a_click_on_a_blockers_row_opens_its_overlay(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.press("tab", "tab")
            await pilot.click("#view", offset=(8, 1))
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert app.screen.task_id == "g1"

    asyncio.run(drive())


def test_the_warnings_tab_lists_the_warnings_of_check_and_an_id_opens_its_overlay(
    tasks: pathlib.Path,
):
    module = _load()
    app = module.BoardApp(tasks)
    expected = ["g1: ready without briefs/g1.md next to tasks.toml"]

    async def drive():
        async with app.run_test(size=(100, 24)) as pilot:
            assert expected == module.ledger_graph.check_warnings(
                app.graph, tasks.resolve()
            )
            await pilot.press("shift+tab")
            assert app.view == "warnings"
            assert _view(app) == expected[0]
            await pilot.click("#view", offset=(1, 0))
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert app.screen.task_id == "g1"

    asyncio.run(drive())


def test_the_warnings_tab_says_so_when_there_are_none(tasks: pathlib.Path):
    (tasks.parent / "briefs").mkdir()
    (tasks.parent / "briefs" / "g1.md").write_text("# g1\n\n## Acceptance\n\n- ok\n")
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("shift+tab")
            assert _view(app) == "no warnings"

    asyncio.run(drive())


def test_the_awaiting_badge_switches_to_the_blockers_list(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.click("#status", offset=(_status(app).index("2 awaiting") + 1, 0))
            await pilot.pause()
            assert app.view == "inbox"

    asyncio.run(drive())


def test_p_in_the_blockers_list_prefills_the_selected_id(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "down")
            await pilot.press("p")
            ask = app.query_one("#ask", textual.widgets.TextArea)
            assert ask.value == "g1 "
            await pilot.press(*"P0")
            await pilot.press("enter")
            assert app.error == ""
            assert 'priority = "P0"' in inbox.read_text()

    asyncio.run(drive())


def test_the_context_of_an_id_that_walks_out_of_the_inbox_is_refused(
    tmp_path: pathlib.Path,
):
    module = _load()
    (tmp_path / "inbox").mkdir()
    (tmp_path.parent / "secret.md").write_text("top secret")

    assert module._read_text("../../secret", tmp_path / "inbox")[0] == ""
    assert module._read_text("..", tmp_path / "inbox")[0] == ""


def test_the_board_redraws_when_the_file_changes_on_disk(tasks: pathlib.Path):
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert "new title" not in _view(app)
            _rewrite(tasks, TASKS.replace('"two"', '"new title"'))
            await pilot.pause(0.3)
            assert "new title" in _view(app)
            assert "reloaded" not in _status(app)
            assert any("reloaded" in n.message for n in app._notifications)
            assert app.error == ""

    asyncio.run(drive())


def _click_tab(app, pilot, text: str):
    return pilot.click("#status", offset=(_status(app).index(text) + 1, 0))


def test_a_click_on_a_tab_switches_to_that_view(tasks: pathlib.Path):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test(size=(100, 24)) as pilot:
            for target in ("3 Blockers", "2 Board", "1 Tree"):
                await _click_tab(app, pilot, target)
                await pilot.pause()
                assert app.view == _load().VIEWS[int(target[0]) - 1]

    asyncio.run(drive())


def test_a_click_on_a_tab_is_ignored_while_the_legend_or_prompt_is_open(
    tasks: pathlib.Path,
):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test(size=(100, 24)) as pilot:
            x = _status(app).index("3 Blockers") + 1
            await pilot.press("l")
            assert type(app.screen).__name__ == "LegendScreen"
            await pilot.click(offset=(x, 0))
            await pilot.pause()
            assert app.view == "tree"
            await pilot.press("escape")
            await pilot.press("p")
            await _click_tab(app, pilot, "3 Blockers")
            await pilot.pause()
            assert app.view == "tree"

    asyncio.run(drive())


def test_a_reload_keeps_the_selection_and_the_view(inbox: pathlib.Path):
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "down")
            assert "> ✖ g1" in _view(app)

            # Now g1 comes before g2 in the list.
            _rewrite(inbox, INBOX.replace('priority = "P2"', 'priority = "P0"', 1))
            await pilot.pause(0.3)
            assert app.view == "inbox"
            assert app.selected_id == "g1"
            assert "> ✖ g1" in _view(app)

    asyncio.run(drive())


def test_a_broken_file_is_read_once_and_the_last_good_view_stays(
    tasks: pathlib.Path,
):
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(tasks)
    reads = []
    read = module._read
    _patch(module, "_read", lambda path: reads.append(path) or read(path))

    async def drive():
        async with app.run_test() as pilot:
            reads.clear()
            _rewrite(tasks, "this is [not toml")
            await pilot.pause(0.4)
            assert len(reads) == 1
            assert app.error
            assert "g2" in _view(app)
            assert "error:" in _status(app)

            _rewrite(tasks, TASKS.replace('"two"', '"fixed"'))
            await pilot.pause(0.3)
            assert app.error == ""
            assert "fixed" in _view(app)

    asyncio.run(drive())


def test_a_change_during_a_prompt_retries_the_priority_typed_against_the_old_read(
    inbox: pathlib.Path,
):
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(160, 24)) as pilot:
            await pilot.press("tab", "tab", "p")
            assert app.query_one("#ask", textual.widgets.TextArea).value == "g2 "
            seen = app.mtime_ns

            changed = INBOX.replace('title = "two"', 'title = "other"')
            _rewrite(inbox, changed)
            await pilot.pause(0.3)
            assert app.mtime_ns == seen

            await pilot.press(*"P1", "enter")
            assert app.error == ""
            assert 'priority = "P1"' in inbox.read_text()
            assert 'title = "other"' in inbox.read_text()
            assert not app.query_one("#ask", textual.widgets.TextArea).display

            # The poll resumes once the prompt is closed.
            await pilot.pause(0.3)
            assert "other" in _view(app)

    asyncio.run(drive())


async def _open_answer(pilot, text: str = "") -> typing.Any:
    await pilot.press("tab", "tab", "enter", "e")
    box = pilot.app.screen.query_one("#overlay-ask", textual.widgets.TextArea)
    assert box.display
    if text:
        await pilot.press(*("space" if c == " " else c for c in text))
    return box


def test_a_touched_file_does_not_lose_an_answer_whose_question_is_the_same(
    tasks: pathlib.Path,
):
    tasks.write_text(BLOCKED)
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            await _open_answer(pilot, "use B")
            _rewrite(tasks, BLOCKED + "\n# touched by another writer\n")
            await pilot.pause(0.3)
            await pilot.press("enter")
            await pilot.pause()
            assert app.error == ""
            assert tasks.read_text().count('answer = "use B"') == 1
            assert "touched by another writer" in tasks.read_text()
            assert "answered g1" in _status(app)

    asyncio.run(drive())


def test_a_changed_question_keeps_the_typed_answer_and_shows_the_new_question(
    tasks: pathlib.Path,
):
    tasks.write_text(BLOCKED)
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(tasks)
    changed = BLOCKED.replace("A or B?", "C or D?")

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            await _open_answer(pilot, "use B")
            _rewrite(tasks, changed)
            await pilot.pause(0.3)
            await pilot.press("enter")
            await pilot.pause()
            assert tasks.read_text() == changed
            ask = app.query_one("#ask", textual.widgets.TextArea)
            assert ask.display
            assert ask.value == "g1 use B"
            assert "C or D?" in app.error
            assert "C or D?" in _status(app)

    asyncio.run(drive())


def test_a_refused_answer_keeps_the_typed_text(tasks: pathlib.Path):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            box = await _open_answer(pilot)
            box.value = "a\x07b"
            await pilot.press("enter")
            await pilot.pause()
            assert "control character" in app.error
            assert tasks.read_text() == BLOCKED
            ask = app.query_one("#ask", textual.widgets.TextArea)
            assert ask.display
            assert ask.value == "g1 a\x07b"

    asyncio.run(drive())


def test_a_long_answer_is_shown_wrapped_and_alt_enter_adds_a_line(
    tasks: pathlib.Path,
):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)
    long = " ".join(["word"] * 40)

    async def drive():
        async with app.run_test(size=(60, 40)) as pilot:
            box = await _open_answer(pilot)
            box.value = long
            box.end_cursor()
            await pilot.pause()
            assert box.wrapped_document.height > 1
            assert "\n" not in box.value

            await pilot.press("alt+enter", "x", "enter")
            await pilot.pause()
            assert f'answer = "{long}\\nx"' in tasks.read_text()

    asyncio.run(drive())


def test_alt_backspace_and_ctrl_w_delete_one_word(tasks: pathlib.Path):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test(size=(80, 40)) as pilot:
            box = await _open_answer(pilot, "one two three")
            await pilot.press("alt+backspace")
            assert box.value == "one two "
            await pilot.press("ctrl+w")
            assert box.value == "one "

    asyncio.run(drive())


def test_a_fix_with_the_same_mtime_as_the_broken_file_is_read(tasks: pathlib.Path):
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            _rewrite(tasks, "this is [not toml")
            await pilot.pause(0.3)
            assert app.error
            stamp = tasks.stat().st_mtime_ns

            tasks.write_text(TASKS.replace('"two"', '"fixed"'))
            os.utime(tasks, ns=(stamp, stamp))
            await pilot.pause(0.3)
            assert app.error == ""
            assert "fixed" in _view(app)

    asyncio.run(drive())


def test_the_selection_follows_the_task_when_a_priority_reorders_the_inbox(
    inbox: pathlib.Path,
):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "down", "p")
            assert app.query_one("#ask", textual.widgets.TextArea).value == "g1 "
            await pilot.press(*"P0", "enter")
            assert app.error == ""
            assert app.selected_id == "g1"
            assert "> ✖ g1" in _view(app)
            assert _view(app).index("g1") < _view(app).index("g2")

    asyncio.run(drive())


def test_a_write_says_what_it_did_until_the_next_key(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "down", "p")
            await pilot.press(*"P0", "enter")
            assert "set g1 to P0" in _status(app)

            await pilot.press("j")
            assert "set g1 to P0" not in _status(app)

            await pilot.press("enter", "e")
            await pilot.press(*"use B", "enter")
            await pilot.pause()
            assert "answered" in _status(app)

    asyncio.run(drive())


BRIEF = "## Goal\n\nship the thing\n\n## Decisions\n\n- keep it small\n"


@pytest.fixture
def briefs(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(INBOX)
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "g2.md").write_text(BRIEF)
    return path


def test_the_blockers_selection_does_not_leak_into_the_tree_prompt(
    briefs: pathlib.Path,
):
    app = _load().BoardApp(briefs)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "down")
            assert app.selected_id == "g1"
            await pilot.press("tab", "tab")
            assert app.view == "tree"
            await pilot.press("p")
            assert app.query_one("#ask").value == ""

    asyncio.run(drive())


def _forty(tmp_path: pathlib.Path) -> pathlib.Path:
    blocks = "".join(
        f'\n[[task]]\nid = "t{n:02d}"\ntitle = "task {n}"\nstatus = "blocked"\n'
        f'blocker = "human: q{n}"\nwrite_set = ["w{n}"]\n'
        for n in range(40)
    )
    path = tmp_path / "tasks.toml"
    path.write_text("format = 1\n" + blocks)
    return path


def test_pagedown_scrolls_the_blockers_list(tmp_path: pathlib.Path):
    app = _load().BoardApp(_forty(tmp_path))

    async def drive():
        async with app.run_test(size=(100, 12)) as pilot:
            await pilot.press("tab", "tab")
            await pilot.pause()
            pane = app.query_one("#pane")
            await pilot.press("pagedown")
            await pilot.pause()
            assert pane.scroll_y > 0
            await pilot.press("pageup")
            await pilot.pause()
            assert pane.scroll_y == 0

    asyncio.run(drive())


def test_the_help_screen_opens_on_a_question_mark_and_closes_on_escape(
    briefs: pathlib.Path,
):
    app = _load().BoardApp(briefs)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("question_mark")
            await pilot.pause()
            assert len(app.screen_stack) == 2
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


def _patch(module, name: str, value) -> None:
    setattr(module, name, value)


def _status(app) -> str:
    return str(app.query_one("#status", textual.widgets.Static).content)


def _rewrite(path: pathlib.Path, text: str) -> None:
    previous = path.stat().st_mtime_ns
    path.write_text(text)
    # A coarse clock could give the same stamp to two quick writes.
    os.utime(path, ns=(previous + 10**9, previous + 10**9))


def _rgb_colors(app) -> set[str]:
    found: set[str] = set()
    for strip in app.screen._compositor.render_strips():
        for segment in strip:
            style = segment.style
            if style is None:
                continue
            for color in (style.color, style.bgcolor):
                if color is not None and color.type == rich.color.ColorType.TRUECOLOR:
                    found.add(color.name)
    return found


def test_the_app_starts_on_the_ansi_theme(tasks: pathlib.Path):
    module = _load()
    assert module.ANSI_THEME in textual.theme.BUILTIN_THEMES
    app = module.BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            assert app.theme == module.ANSI_THEME
            assert app.native_ansi_color
            await pilot.press("b")
            for _ in module.VIEWS:
                await pilot.pause()
                assert not _rgb_colors(app)
                await pilot.press("tab")

    asyncio.run(drive())


def _backgrounds(app) -> set[str]:
    found: set[str] = set()
    for strip in app.screen._compositor.render_strips():
        for segment in strip:
            color = segment.style.bgcolor if segment.style else None
            found.add("default" if color is None or color.is_default else color.name)
    return found


def test_b_toggles_a_dark_background_and_the_terminal_one(
    tasks: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert "default" not in _backgrounds(app)
            assert "#1e1e1e" in {c.lower() for c in _backgrounds(app)}
            await pilot.press("b")
            await pilot.pause()
            assert _backgrounds(app) == {"default"}
            await pilot.press("b")
            await pilot.pause()
            assert "default" not in _backgrounds(app)

    asyncio.run(drive())


def test_b_does_nothing_and_says_so_with_no_color(
    tasks: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("NO_COLOR", "1")
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert _backgrounds(app) == {"default"}
            await pilot.press("b")
            await pilot.pause()
            assert _backgrounds(app) == {"default"}
            assert "NO_COLOR" in _status(app)

    asyncio.run(drive())


def test_the_ask_prompt_keeps_the_priority_keys_from_acting(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.pause()
            await pilot.press("p")
            await pilot.press("x")
            view = app.view
            await pilot.press("tab")
            assert app.view == view
            assert app.query_one("#ask", textual.widgets.TextArea).value.endswith("x")

    asyncio.run(drive())


def test_m_is_ignored_while_the_prompt_is_open(tasks: pathlib.Path):
    tasks.write_text(TASKS.replace('status = "pending"\nwrite_set = ["g"]', 'status = "pending"\nmilestone = "m9-demo"\nwrite_set = ["g"]'))
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("p", "m")
            assert not app.show_milestones
            assert "m9-demo" not in _view(app)

    asyncio.run(drive())


def test_ctrl_p_does_not_open_a_command_palette(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("tab", "tab", "down")
            await pilot.press("ctrl+p")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.selected_id == "g1"

    asyncio.run(drive())


def test_a_click_on_the_active_tab_does_nothing(tasks: pathlib.Path):
    tasks.write_text(BLOCKED)
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test(size=(100, 24)) as pilot:
            await pilot.press("tab")
            before = _view(app)
            await _click_tab(app, pilot, "2 Board")
            await pilot.pause()
            assert app.view == "board" and _view(app) == before

    asyncio.run(drive())


def test_the_overlay_skips_a_first_heading_that_repeats_the_id_or_the_title(
    briefs: pathlib.Path,
):
    async def drive(text: str) -> str:
        (briefs.parent / "briefs" / "g2.md").write_text(text)
        app = _load().BoardApp(briefs)
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("tab", "tab", "o")
            await pilot.pause()
            return _rendered_overlay(app)

    for heading in ("# g2", "# g2: two", "# two"):
        text = asyncio.run(drive(f"{heading}\n\n{BRIEF}"))
        assert text.splitlines()[0].strip() == "two"
        assert "g2" not in text
        assert text.count("two") == 1
        assert "ship the thing" in text

    text = asyncio.run(drive(f"# Other\n\n{BRIEF}"))
    assert text.splitlines()[0].strip() == "two"
    assert "Other" in text


BOARD_TASKS = """format = 1

[[task]]
id = "d1"
title = "finished"
status = "done"
read_only = true

[[task]]
id = "r1"
title = "running now"
status = "running"
read_only = true
priority = "P1"

[[task]]
id = "lo"
title = "low priority ready task"
status = "pending"
read_only = true
priority = "P3"

[[task]]
id = "hi"
title = "high priority ready task"
status = "pending"
read_only = true
priority = "P0"

[[task]]
id = "w1"
title = "waits for the runner"
status = "pending"
read_only = true
deps = ["r1"]

[[task]]
id = "q1"
title = "needs a choice"
status = "blocked"
read_only = true
blocker = "human: which API?"
"""


def _board(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(BOARD_TASKS)
    return path


def _drawn(app) -> str:
    """The text of the main view as the pane shows it, wrapped to its width."""

    content = app.query_one("#view", textual.widgets.Static).content
    if isinstance(content, str):
        return content

    width = app.query_one("#pane").scrollable_content_region.width
    out = io.StringIO()
    rich.console.Console(file=out, width=width, color_system=None).print(content)
    return out.getvalue()


def test_the_text_overview_puts_in_progress_before_todo_in_pick_up_order(
    tmp_path: pathlib.Path,
):
    app = _load().BoardApp(_board(tmp_path))

    async def drive():
        async with app.run_test(size=(110, 24)) as pilot:
            await pilot.press("tab", "c")
            text = _view(app)
            assert text.index("In progress") < text.index("Todo")
            assert text.index("Todo") < text.index("Done")
            todo = text[text.index("Todo") :]
            assert todo.index("q1") < todo.index("hi") < todo.index("lo")
            assert todo.index("lo") < todo.index("w1")

    asyncio.run(drive())


def test_c_shows_the_columns_with_one_card_per_task(tmp_path: pathlib.Path):
    columns = 110
    app = _load().BoardApp(_board(tmp_path))

    async def drive():
        async with app.run_test(size=(columns, 30)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            drawn = _drawn(app)
            lines = drawn.splitlines()
            assert max(len(line) for line in lines) <= columns
            header = lines[0]
            assert header.index("Todo (3)") < header.index("In progress (1)")
            assert header.index("In progress (1)") < header.index("Blocked (1)")
            assert "columns" in _status(app)
            # The next task is marked in text and the highest priority comes first.
            assert re.search(r"next · P\d", drawn)
            assert drawn.index("high priority") < drawn.index("low priority")
            assert "▌" in drawn
            assert "awaiting you:" not in drawn
            await pilot.press("c")
            await pilot.pause()
            assert "Todo (" in _view(app)
            assert "text" in _status(app)

    asyncio.run(drive())


@pytest.mark.parametrize(("columns", "has_room"), [(100, False), (160, True)])
def test_the_done_column_folds_into_a_count_when_there_is_no_room(
    tmp_path: pathlib.Path, columns: int, has_room: bool
):
    app = _load().BoardApp(_board(tmp_path))

    async def drive():
        async with app.run_test(size=(columns, 30)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            drawn = _drawn(app)
            assert "Done (1)" in drawn
            assert ("finished" in drawn) == has_room
            assert "Blocked (1)" in drawn

    asyncio.run(drive())


def test_the_columns_fall_back_to_the_text_overview_when_they_do_not_fit(
    tmp_path: pathlib.Path,
):
    app = _load().BoardApp(_board(tmp_path))

    async def drive():
        async with app.run_test(size=(80, 30)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            assert app.show_columns
            assert _view(app).startswith("In progress (1)")
            assert "columns need" in _status(app)

    asyncio.run(drive())


def test_the_columns_choice_survives_a_reload_and_a_resize(tmp_path: pathlib.Path):
    app = _load().BoardApp(_board(tmp_path))

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.press("tab", "r")
            await pilot.pause()
            assert app.show_columns
            assert "Blocked (1)" in _drawn(app)
            await pilot.resize_terminal(80, 30)
            await pilot.pause()
            assert _view(app).startswith("In progress (1)")
            await pilot.resize_terminal(160, 30)
            await pilot.pause()
            assert "Blocked (1)" in _drawn(app)

    asyncio.run(drive())


def test_a_deferred_card_is_dim_and_last_in_the_todo_column(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(
        TASKS
        + """
[[task]]
id = "parked"
title = "parked work"
status = "deferred"
priority = "P0"
read_only = true
"""
    )
    module = _load()
    tasks, _ = module.ledger_graph.parse(path.read_text())
    graph = module.ledger_graph.Graph(tasks)
    todo = dict(module.BOARD_COLUMNS)["Todo"]
    cards = module.ledger_graph.board_cards(graph, todo)
    assert cards[-1] == "parked"

    text = rich.text.Text()
    for card in _card_panels(module, graph, ["parked"]):
        assert str(card.border_style) == "dim bright_black"
        assert isinstance(card.renderable, rich.text.Text)
        text.append_text(card.renderable)

    assert "parked work" in text.plain
    assert "parked · deferred" in text.plain
    start = text.plain.index("parked work")
    assert all(
        "dim" in str(span.style)
        for span in text.spans
        if span.start >= start and span.end <= len(text.plain)
    )


def _card_panels(module, graph, task_ids, inner=40, **kwargs):
    group = module._cards(graph, task_ids, "", inner, **kwargs)
    return [r for r in group.renderables if isinstance(r, rich.panel.Panel)]


def _card_graph(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(
        BOARD_TASKS
        + """
[[task]]
id = "longone"
status = "pending"
title = "a very long title that needs more than two lines of the card to be shown in full"
priority = "P0"
read_only = true
"""
    )
    module = _load()
    tasks, _ = module.ledger_graph.parse(path.read_text())
    return module, module.ledger_graph.Graph(tasks)


def test_a_long_title_is_cut_on_two_lines_with_an_ellipsis(tmp_path: pathlib.Path):
    module, graph = _card_graph(tmp_path)
    panel = _card_panels(module, graph, ["longone"], 30)[0]
    out = io.StringIO()
    rich.console.Console(file=out, width=30, color_system=None).print(panel)
    lines = out.getvalue().splitlines()
    assert len(lines) == 2 + 2 + 1
    assert "…" in lines[2]


def test_a_done_card_is_one_line(tmp_path: pathlib.Path):
    module, graph = _card_graph(tmp_path)
    done = module.ledger_graph.board_cards(graph, {"done"})
    panel = _card_panels(module, graph, done, 30, compact=True)[0]
    line = panel.renderable
    assert isinstance(line, rich.text.Text)
    assert line.plain.count("\n") == 0
    assert done[0] in line.plain


def _footer_actions(app) -> set[str]:
    return {key.action for key in app.query("FooterKey")}


def test_the_footer_shows_only_the_keys_of_the_active_view(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)
    global_keys = {"switch", "quit", "help", "priority"}
    expected = {
        "tree": {"brief", "review"},
        "board": {"brief", "review"},
        "inbox": {"brief"},
    }

    async def drive():
        async with app.run_test() as pilot:
            for view, keys in expected.items():
                assert app.view == view
                await pilot.pause()
                assert _footer_actions(app) == keys | global_keys
                await pilot.press("tab")

    asyncio.run(drive())


def _footer_text(app) -> str:
    """The footer as text: the chip, then each key, in brackets when it is dimmed."""

    chip = [str(label.render()) for label in app.query("FooterLabel.target-chip")]
    keys = [
        f"[{key.key_display} {key.description}]" if key.has_class("-disabled") else f"{key.key_display} {key.description}"
        for key in app.query("FooterKey")
    ]
    return " | ".join(chip + keys)


def test_the_footer_dims_the_keys_that_wait_for_a_target(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)
    waiting = {"brief", "review"}

    def dimmed() -> set[str]:
        return {key.action for key in app.query("FooterKey") if key.has_class("-disabled")}

    async def drive():
        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.view == "tree"
            assert dimmed() == waiting
            assert _footer_text(app).startswith("click an id | ")
            assert "[o brief]" in _footer_text(app)
            assert [str(c.render()) for c in app.query("FooterLabel.target-chip")] == ["click an id"]

            app.spotlit = next(iter(app.graph.tasks))
            app.draw()
            await pilot.pause()
            assert not dimmed()
            assert [str(c.render()) for c in app.query("FooterLabel.target-chip")] == [f"target: {app.spotlit}"]

            for view in ("board", "inbox", "warnings"):
                await pilot.press("tab")
                await pilot.pause()
                assert app.view == view
            assert not list(app.query("FooterLabel.target-chip"))

    asyncio.run(drive())


def test_a_key_hidden_in_a_view_does_nothing(tasks: pathlib.Path):
    app = _load().BoardApp(tasks)

    async def drive():
        async with app.run_test() as pilot:
            await pilot.press("c")
            assert app.view == "tree"
            assert app.show_columns
            await pilot.press("tab", "tab")
            await pilot.press("a", "m", "c", "l")
            assert app.view == "inbox"
            assert len(app.screen_stack) == 1
            assert not app.show_done
            assert not app.show_milestones
            assert app.show_columns

    asyncio.run(drive())


def test_the_legend_overlay_has_sections_whole_items_and_a_hint(tasks: pathlib.Path):
    """No item wraps at 80 or 40 columns, and bold, italic, dim are really styled."""

    async def drive(width: int):
        app = _load().BoardApp(tasks)
        async with app.run_test(size=(width, 40)) as pilot:
            await pilot.press("l")
            pane = app.screen.query_one("#legend-pane")
            assert pane.border_title == "Legend"
            card = app.screen.legend
            lines = card.plain.split("\n")
            for heading in ("State", "Title", "Priority"):
                assert any(line.startswith(f" {heading} ─") for line in lines)
            assert " l or esc to close" in lines
            assert all(len(line) <= 40 for line in lines)
            assert app.screen.query_one("#legend").size.height == len(lines)
            for item in ("✔  done", "▶  ready", "✖  blocked", "P0 P1 P2 P3"):
                assert any(item in line for line in lines)
            return card

    wide = asyncio.run(drive(80))
    asyncio.run(drive(40))
    for word in ("bold", "italic", "dim"):
        if f" {word} " not in wide.plain:
            continue
        start = wide.plain.index(f" {word} ") + 1
        assert any(
            word in str(span.style) and span.start == start for span in wide.spans
        )


def _expand_tasks(tmp_path: pathlib.Path) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(
        """format = 1

[[task]]
id = "epic"
type = "epic"
title = "the epic"
status = "pending"

[[task]]
id = "base"
parent = "epic"
title = "base work"
status = "running"
read_only = true
worktree = "/w/base"
branch = "base-branch"

[[task]]
id = "long"
parent = "epic"
title = "a very long title that would be cut with an ellipsis on a card of this width"
status = "pending"
read_only = true
deps = ["base"]
"""
    )
    return path


def test_a_click_on_a_card_opens_the_overlay_and_the_card_does_not_expand(
    tmp_path: pathlib.Path,
):
    app = _load().BoardApp(_expand_tasks(tmp_path))

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            collapsed = _drawn(app)
            assert "…" in collapsed
            assert "deps:" not in collapsed
            await pilot.click("#view", offset=(42, 4))
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert "worktree: /w/base" in _rendered_overlay(app)
            await pilot.press("escape")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert _drawn(app) == collapsed

    asyncio.run(drive())


def test_a_click_on_a_card_border_or_priority_selects_it_without_the_overlay(
    tmp_path: pathlib.Path,
):
    app = _load().BoardApp(_expand_tasks(tmp_path))

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            await pilot.pause()
            assert app.card_id == ""
            # The left border of the Todo card, then the priority tag of the other card.
            await pilot.click("#view", offset=(0, 3))
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.card_id == "long"
            await pilot.click("#view", offset=(_drawn(app).splitlines()[2].rindex("P2") + 1, 2))
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.card_id == "base"
            await pilot.press("o")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert "worktree: /w/base" in _rendered_overlay(app)

    asyncio.run(drive())


def test_enter_opens_the_overlay_of_the_selected_card_with_every_detail(
    tmp_path: pathlib.Path,
):
    app = _load().BoardApp(_expand_tasks(tmp_path))

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            await pilot.press("tab")
            await pilot.pause()
            await pilot.press("j", "enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            shown = _rendered_overlay(app)
            assert "a very long title that would be cut with an ellipsis" in shown
            assert "deps: base (running)" in shown
            assert "state: pending" in shown
            await pilot.press("escape")
            await pilot.press("j", "enter")
            await pilot.pause()
            shown = _rendered_overlay(app)
            assert "branch: base-branch" in shown
            assert "state: running" in shown

    asyncio.run(drive())


def test_a_click_on_a_tree_title_opens_the_brief_overlay_and_escape_closes_it(
    briefs: pathlib.Path,
):
    app = _load().BoardApp(briefs)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            rows = _view(app).splitlines()
            y = next(n for n, row in enumerate(rows) if " · " in row and "g2" in row)
            await pilot.click("#view", offset=(rows[y].index(" · ") - 1, y))
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert "ship the thing" in _rendered_overlay(app)
            await pilot.press("escape")
            assert len(app.screen_stack) == 1
            await pilot.press("tab", "tab", "o")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            await pilot.press("q")
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


def _rendered_overlay(app) -> str:
    console = rich.console.Console(width=80, record=True, file=io.StringIO())
    console.print(app.screen.query_one("#brief").content)
    return console.export_text()


def test_the_overlay_shows_the_state_and_the_last_events_without_a_brief(
    briefs: pathlib.Path,
):
    (briefs.parent / "briefs" / "g2.md").unlink()
    (briefs.parent / "events").mkdir()
    lines = [
        f'{{"ts": "t{n}", "event": "ev{n}", "detail": "d{n}"}}' for n in range(12)
    ]
    (briefs.parent / "events" / "g2.jsonl").write_text("\n".join([*lines, "{torn"]))
    app = _load().BoardApp(briefs)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await pilot.press("tab", "tab", "o")
            await pilot.pause()
            text = _rendered_overlay(app)
            assert "state: " in text
            assert "History" in text
            assert "t1 ev1" not in text
            assert text.rstrip().splitlines()[-3] == "t11 ev11 d11"
            await pilot.press("escape")
            (briefs.parent / "events" / "g2.jsonl").write_text("{torn\n")
            await pilot.press("o")
            await pilot.pause()
            assert "History" not in _rendered_overlay(app)

    asyncio.run(drive())


def _open_overlay(app, task_id: str):
    async def run(pilot) -> None:
        app.action_brief(task_id)
        await pilot.pause()
        assert type(app.screen).__name__ == "BriefScreen"

    return run


def test_the_overlay_shows_the_question_only_for_a_blocked_task(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 40)) as pilot:
            await _open_overlay(app, "g2")(pilot)
            shown = _rendered_overlay(app)
            assert "Question" in shown
            assert "now or later?" in shown
            assert "Recommendation" in shown
            assert "e to answer" in shown
            await pilot.press("escape")
            await _open_overlay(app, "g1")(pilot)
            shown = _rendered_overlay(app)
            assert "A or B?" in shown
            await pilot.press("escape")
            await _open_overlay(app, "g3")(pilot)
            shown = _rendered_overlay(app)
            assert "Question" not in shown
            assert "e to answer" not in shown

    asyncio.run(drive())


def test_a_task_without_a_question_has_no_answer_key_in_its_overlay(
    inbox: pathlib.Path,
):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g3")(pilot)
            await pilot.pause()
            actions = {key.action for key in app.screen.query("FooterKey")}
            assert "pick('priority')" in actions
            assert "pick('state')" in actions
            assert "ask('answer')" not in actions
            await pilot.press("e")
            assert not app.screen.query_one("#overlay-ask").display
            await pilot.press("escape")
            assert len(app.screen_stack) == 1
            await _open_overlay(app, "g1")(pilot)
            await pilot.pause()
            assert "ask('answer')" in {
                key.action for key in app.screen.query("FooterKey")
            }

    asyncio.run(drive())


def test_an_answer_typed_in_the_blockers_overlay_writes_what_the_cli_writes(
    tmp_path: pathlib.Path,
):
    module = _load()
    for name in ("overlay", "cli"):
        folder = tmp_path / name
        (folder / "inbox").mkdir(parents=True)
        (folder / "tasks.toml").write_text(INBOX)
        (folder / "inbox" / "g2.md").write_text(CONTEXT)

    async def through_overlay():
        app = module.BoardApp(tmp_path / "overlay" / "tasks.toml")
        async with app.run_test(size=(100, 40)) as pilot:
            await pilot.press("tab", "tab", "enter")
            assert app.screen.task_id == "g2"
            await pilot.press("e")
            assert app.screen.query_one("#overlay-ask").display
            await pilot.press(*"use later")
            await pilot.press("enter")
            await pilot.pause()
            assert len(app.screen_stack) == 1
            assert app.error == ""
            assert app.graph.tasks["g2"].status == "pending"
            assert _view(app).splitlines() == ["> ✖ g1  gate  P2"]
            assert "1 awaiting you" in _status(app)

    asyncio.run(through_overlay())
    cli = tmp_path / "cli" / "tasks.toml"
    module.ledger_graph.answer_blocker(
        cli, "g2", "use later", seen_mtime_ns=cli.stat().st_mtime_ns
    )
    written = (tmp_path / "overlay" / "tasks.toml").read_text()
    assert 'answer = "use later"' in written
    assert written == cli.read_text()
    assert written != INBOX


def test_escape_in_the_overlay_cancels_the_prompt_before_it_closes(
    inbox: pathlib.Path,
):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g1")(pilot)
            await pilot.press("e")
            ask = app.screen.query_one("#overlay-ask")
            assert ask.display
            await pilot.press("escape")
            assert type(app.screen).__name__ == "BriefScreen"
            assert not ask.display
            assert inbox.read_text() == INBOX
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


def test_p_in_the_overlay_sets_the_priority_of_the_task(inbox: pathlib.Path):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g3")(pilot)
            await pilot.press("p")
            assert type(app.screen).__name__ == "PickScreen"
            await pilot.press("up", "enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert app.graph.tasks["g3"].priority == "P0"
            assert "P0" in app.screen.heading.plain

    asyncio.run(drive())


async def _click_title(app, pilot, kind: str) -> None:
    pane = app.screen.query_one("#brief-pane")
    start, _ = app.screen.heading_part(kind)
    await pilot.click(
        offset=(pane.region.x + module_title_indent(app) + start, pane.region.y)
    )
    await pilot.pause()


def module_title_indent(app) -> int:
    return sys.modules[type(app).__module__].TITLE_INDENT


def test_a_click_on_the_priority_in_the_border_picks_and_writes_it(
    inbox: pathlib.Path,
):
    app = _load().BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g3")(pilot)
            pane = app.screen.query_one("#brief-pane")
            before = pane.styles.border.top[1]
            await _click_title(app, pilot, "priority")
            assert type(app.screen).__name__ == "PickScreen"
            await pilot.press("escape")
            assert inbox.read_text() == INBOX
            await _click_title(app, pilot, "priority")
            await pilot.press("up", "enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert app.graph.tasks["g3"].priority == "P0"
            assert pane.styles.border.top[1] != before

    asyncio.run(drive())


def test_s_offers_to_activate_a_deferred_task_and_nothing_for_a_running_one(
    tmp_path: pathlib.Path,
):
    text = INBOX.replace(
        'write_set = ["i"]\n',
        'write_set = ["i"]\n\n[[task]]\nid = "g4"\ntitle = "four"\n'
        'status = "deferred"\nwrite_set = ["j"]\n\n[[task]]\nid = "g5"\n'
        'title = "five"\nstatus = "in_progress"\nwrite_set = ["k"]\n',
    )
    path = tmp_path / "tasks.toml"
    path.write_text(text)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g5")(pilot)
            await pilot.press("s")
            picker = app.screen.query_one("#pick-list")
            assert picker.option_count == 1
            await pilot.press("enter")
            assert type(app.screen).__name__ == "BriefScreen"
            assert path.read_text() == text
            await pilot.press("escape")

            await _open_overlay(app, "g4")(pilot)
            await _click_title(app, pilot, "state")
            assert app.screen.query_one("#pick-list").option_count == 2
            await pilot.press("down", "enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert app.graph.tasks["g4"].status == "pending"
            assert "pending" in app.screen.heading.plain

    asyncio.run(drive())


def test_a_file_change_under_an_open_overlay_refuses_the_write_typed_in_it(
    inbox: pathlib.Path,
):
    module = _load()
    _patch(module, "POLL_SECONDS", 0.05)
    app = module.BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g3")(pilot)
            changed = INBOX.replace('title = "three"', 'title = "other"')
            _rewrite(inbox, changed)
            await pilot.pause(0.3)
            # The poll read the new file under the overlay, which keeps its old content.
            assert app.graph.tasks["g3"].title == "other"
            await pilot.press("p", "up", "enter")
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert inbox.read_text() == changed
            assert app.graph.tasks["g3"].priority == "P1"

            # Opened again, the overlay is drawn from the new read and writes.
            await pilot.press("escape")
            await _open_overlay(app, "g3")(pilot)
            await pilot.press("p", "up", "enter")
            await pilot.pause()
            assert app.graph.tasks["g3"].priority == "P0"

    asyncio.run(drive())


def _screen_lines(app) -> list[str]:
    return [
        "".join(segment.text for segment in strip).rstrip()
        for strip in app.screen._compositor.render_strips()
    ]


def _blocked_overlay_files(tmp_path: pathlib.Path, context: str) -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(INBOX)
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "g1.md").write_text(context)
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "g1.md").write_text(
        "## Change\n\nedit it\n\n## Acceptance\n\n- it works\n"
    )
    return path


@pytest.mark.parametrize(
    "context",
    ["# Domanda\n\nA or B?\n\n## Contesto\n\nwhy\n", "## Question:\n\nA or B?\n\n## Contesto\n\nwhy\n"],
)
def test_the_question_heading_of_the_context_file_is_not_repeated(
    tmp_path: pathlib.Path, context: str
):
    path = _blocked_overlay_files(tmp_path, context)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g1")(pilot)
            shown = _rendered_overlay(app)
            assert shown.count("Question") == 1
            assert "Domanda" not in shown
            assert "A or B?" in shown
            assert "Contesto" in shown

    asyncio.run(drive())


def test_a_context_file_keeps_a_first_heading_that_is_not_a_question_title(
    tmp_path: pathlib.Path,
):
    path = _blocked_overlay_files(tmp_path, "## Options\n\n- A\n- B\n")
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g1")(pilot)
            assert "Options" in _rendered_overlay(app)

    asyncio.run(drive())


def test_the_poll_survives_a_change_of_the_file_while_the_overlay_is_open(
    inbox: pathlib.Path,
):
    module = _load()
    app = module.BoardApp(inbox)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            await _open_overlay(app, "g3")(pilot)
            before = app.polled_stamp
            stat = inbox.stat()
            inbox.write_text(INBOX.replace('title = "three"', 'title = "three!"'))
            os.utime(inbox, ns=(stat.st_atime_ns, stat.st_mtime_ns + 5_000_000_000))
            app.check_mtime()
            await pilot.pause()
            assert app.polled_stamp != before
            assert app.graph.tasks["g3"].title == "three!"
            assert type(app.screen).__name__ == "BriefScreen"

            # With the prompt of the overlay open the poll waits, and reads once it closes.
            await pilot.press("escape")
            await _open_overlay(app, "g1")(pilot)
            await pilot.press("e")
            waiting = app.polled_stamp
            os.utime(inbox, ns=(stat.st_atime_ns, stat.st_mtime_ns + 9_000_000_000))
            await pilot.pause(delay=module.POLL_SECONDS + 0.5)
            assert app.screen.query_one("#overlay-ask").display
            assert app.polled_stamp == waiting
            await pilot.press("escape")
            await pilot.pause(delay=module.POLL_SECONDS + 0.5)
            assert app.polled_stamp != waiting
            assert type(app.screen).__name__ == "BriefScreen"
            await pilot.press("escape")
            assert len(app.screen_stack) == 1

    asyncio.run(drive())


SPOT_TASKS = """format = 1

[[task]]
id = "aa"
title = "first"
status = "pending"
write_set = ["a"]

[[task]]
id = "bb"
title = "second"
status = "pending"
deps = ["aa"]
write_set = ["b"]

[[task]]
id = "cc"
title = "third"
status = "pending"
deps = ["bb"]
write_set = ["c"]

[[task]]
id = "dd"
title = "alone"
status = "pending"
write_set = ["d"]
"""


def _faded(app) -> dict[str, bool]:
    """Whether the row of each id is faded, by the color of its first cell."""

    module = sys.modules["ledger_board"]
    fade = rich.color.Color.parse(module.FADE_DARK)
    body = app.query_one("#view", textual.widgets.Static).content
    out = {}
    for line in body.split("\n", allow_blank=True):
        own, _ = module._tree_ids(line.plain, {"aa", "bb", "cc", "dd"})
        if own:
            colors = {span.style.color for span in line.spans if hasattr(span.style, "color")}
            out[own] = fade in colors
    return out


def _click_id(app, pilot, task_id: str, nth: int = 0):
    body = app.query_one("#view", textual.widgets.Static).content
    hits = [
        (y, m.start())
        for y, line in enumerate(body.plain.split("\n"))
        for m in re.finditer(rf"\b{task_id}\b", line)
    ]
    y, x = hits[nth]
    # The tree has a left padding of one cell.
    return pilot.click("#view", offset=(x + 1, y))


def test_a_click_on_an_id_fades_the_rows_it_does_not_hold_up(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(SPOT_TASKS)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            assert app.spotlit == ""
            assert not any(_faded(app).values())
            await _click_id(app, pilot, "bb")
            await pilot.pause()
            assert app.spotlit == "bb"
            assert _faded(app) == {"aa": False, "bb": False, "cc": False, "dd": True}
            await _click_id(app, pilot, "bb")
            await pilot.pause()
            assert app.spotlit == ""
            assert not any(_faded(app).values())

    asyncio.run(drive())


def test_a_click_on_an_id_after_waits_picks_that_task_and_x_and_escape_toggle(
    tmp_path: pathlib.Path,
):
    path = tmp_path / "tasks.toml"
    path.write_text(SPOT_TASKS)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            # The second hit of "bb" is the one cc waits on.
            await _click_id(app, pilot, "bb", nth=1)
            await pilot.pause()
            assert app.spotlit == "bb"
            await pilot.press("escape")
            await pilot.pause()
            assert app.spotlit == ""
            await pilot.press("x")
            await pilot.pause()
            assert app.spotlit == "aa"
            assert _faded(app) == {"aa": False, "bb": False, "cc": False, "dd": True}
            await pilot.press("x")
            assert app.spotlit == ""

    asyncio.run(drive())


def test_the_blockers_of_the_spotlit_task_take_a_second_color(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(SPOT_TASKS)
    module = _load()
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await _click_id(app, pilot, "cc")
            await pilot.pause()
            body = app.query_one("#view", textual.widgets.Static).content
            for line in body.split("\n", allow_blank=True):
                own, _ = module._tree_ids(line.plain, {"aa", "bb", "cc", "dd"})
                colors = {
                    s.style.color.name
                    for s in line.spans
                    if hasattr(s.style, "color") and s.style.color
                }
                if own == "bb":
                    assert "cyan" in colors
                    assert module.FADE_DARK not in colors
            assert _faded(app)["aa"] is True

    asyncio.run(drive())


def test_the_file_holder_of_the_spotlit_task_takes_the_blocker_color(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(SPOT_TASKS.replace('deps = ["aa"]\nwrite_set = ["b"]', 'write_set = ["a"]'))
    module = _load()
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await _click_id(app, pilot, "aa")
            await pilot.pause()
            body = app.query_one("#view", textual.widgets.Static).content
            lines = body.split("\n", allow_blank=True)
            assert any("waits bb (files)" in line.plain for line in lines)
            for line in lines:
                own, _ = module._tree_ids(line.plain, {"aa", "bb", "cc", "dd"})
                if own == "bb":
                    colors = {
                        s.style.color.name
                        for s in line.spans
                        if hasattr(s.style, "color") and s.style.color
                    }
                    assert "cyan" in colors

    asyncio.run(drive())


def test_the_ids_carry_no_link_style(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(SPOT_TASKS)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            body = app.query_one("#view", textual.widgets.Static).content
            for span in body.spans:
                if hasattr(span.style, "meta"):
                    assert "@click" not in span.style.meta
                    assert not span.style.link

    asyncio.run(drive())


EPIC_TASKS = """format = 1

[[task]]
id = "e1"
title = "the epic"
type = "epic"
status = "pending"

[[task]]
id = "c1"
title = "finished child"
parent = "e1"
status = "done"
write_set = ["a"]

[[task]]
id = "c2"
title = "open child"
parent = "e1"
status = "pending"
write_set = ["b"]
"""


def test_an_epic_overlay_has_the_children_and_no_state_or_deps(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(EPIC_TASKS)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            app.action_brief("e1")
            await pilot.pause()
            shown = _rendered_overlay(app)
            assert "Children" in shown
            assert "1 done · 1 pending" in shown
            assert "c2 open child" in shown
            assert "c1" not in shown
            assert "state:" not in shown
            assert "deps:" not in shown
            rows = shown.splitlines()
            y = next(n for n, r in enumerate(rows) if "c2 open child" in r)
            await pilot.click("#brief", offset=(rows[y].index("c2") + 1, y))
            await pilot.pause()
            await pilot.pause()
            assert type(app.screen).__name__ == "BriefScreen"
            assert app.screen.task_id == "c2"
            assert len(app.screen_stack) == 2

    asyncio.run(drive())


EPIC_SECTIONS_TASKS = EPIC_TASKS + """
[[task]]
id = "long-child-id"
title = "second open child"
parent = "e1"
status = "blocked"
write_set = ["c"]
"""


@pytest.mark.parametrize("width", [80, 130])
def test_the_epic_overlay_sections_start_at_the_same_column(
    tmp_path: pathlib.Path, width: int
):
    path = tmp_path / "tasks.toml"
    path.write_text(EPIC_SECTIONS_TASKS)
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "e1.jsonl").write_text(
        '{"ts": "t1", "event": "ev1", "detail": "d1"}\n'
    )
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(width, 30)) as pilot:
            await _open_overlay(app, "e1")(pilot)
            lines = _screen_lines(app)

            def column(prefix: str) -> int:
                return next(line.index(prefix) for line in lines if prefix in line)

            def row(text: str) -> int:
                return next(n for n, line in enumerate(lines) if text in line)

            heading = column("Children")
            assert column("History") == heading
            assert column("esc or q to close") == heading
            assert column("1 done") == heading
            for child in ("c2", "long-child-id"):
                assert lines[row(child)].index(child) == heading + 2
            assert lines[row("c2")].index("open child") == lines[
                row("long-child-id")
            ].index("second open child")
            assert lines[row("Children") + 1].strip("│ ") == ""
            assert lines[row("History") + 1].strip("│ ") == ""

    asyncio.run(drive())


def test_a_task_overlay_keeps_its_state_and_has_no_children_section(
    tmp_path: pathlib.Path,
):
    path = tmp_path / "tasks.toml"
    path.write_text(EPIC_TASKS)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(100, 30)) as pilot:
            app.action_brief("c2")
            await pilot.pause()
            shown = _rendered_overlay(app)
            assert "state: pending" in shown
            assert "Children" not in shown

    asyncio.run(drive())


def test_a_click_on_blocks_n_highlights_the_dependents(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(SPOT_TASKS)
    app = _load().BoardApp(path)

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            lines = app.query_one("#view", textual.widgets.Static).content.plain.split("\n")
            y = next(n for n, line in enumerate(lines) if " aa " in f"{line} " and "blocks" in line)
            await pilot.click("#view", offset=(lines[y].index("blocks") + 1 + 1, y))
            await pilot.pause()
            assert app.spotlit == "aa"
            assert _faded(app) == {"aa": False, "bb": False, "cc": False, "dd": True}

    asyncio.run(drive())


def test_a_card_has_the_priority_in_the_border_and_a_constant_height(
    tmp_path: pathlib.Path,
):
    path = tmp_path / "tasks.toml"
    path.write_text(
        """format = 1

[[task]]
id = "ab"
title = "same title"
status = "pending"
priority = "P2"
write_set = ["a"]

[[task]]
id = "cleanup-patch-equivalence"
title = "same title"
status = "pending"
priority = "P2"
write_set = ["b"]
"""
    )
    module = _load()
    tasks, _ = module.ledger_graph.parse(path.read_text())
    graph = module.ledger_graph.Graph(tasks)
    short, long = _card_panels(
        module, graph, ["ab", "cleanup-patch-equivalence"], inner=24
    )

    def render(panel) -> list[str]:
        out = io.StringIO()
        console = rich.console.Console(file=out, width=26, color_system=None)
        console.print(panel)
        return out.getvalue().splitlines()

    assert len(render(long)) == len(render(short))
    for rows in (render(short), render(long)):
        assert "P2" in rows[0]
        assert all("P2" not in row for row in rows[1:])
    assert "…" in "\n".join(render(long))


def test_next_is_in_the_title_border_before_the_priority(tmp_path: pathlib.Path):
    module = _load()
    tasks, _ = module.ledger_graph.parse(_board(tmp_path).read_text())
    graph = module.ledger_graph.Graph(tasks)
    group = module._cards(graph, ["hi"], "hi", 40)
    card = next(r for r in group.renderables if isinstance(r, rich.panel.Panel))
    assert isinstance(card.renderable, rich.text.Text)
    assert card.renderable.plain.splitlines()[-1].strip() == "hi"
    assert isinstance(card.title, rich.text.Text)
    assert re.fullmatch(r" next · P\d ", card.title.plain)


def test_every_ready_card_is_marked_next(tmp_path: pathlib.Path):
    module = _load()
    tasks, _ = module.ledger_graph.parse(_board(tmp_path).read_text())
    graph = module.ledger_graph.Graph(tasks)
    ready = set(graph.ready())
    assert ready
    group = module._cards(graph, sorted(ready), "", 40, ready)
    cards = [r for r in group.renderables if isinstance(r, rich.panel.Panel)]
    assert len(cards) == len(ready)
    for card in cards:
        assert isinstance(card.renderable, rich.text.Text)
        assert isinstance(card.title, rich.text.Text)
        assert card.title.plain.startswith(" next · ")


REVIEW_TASKS = """format = 1

[[task]]
id = "rv"
title = "in review"
status = "in_rev"
write_set = ["a"]
worktree = "~/wt/rv"
branch = "feat/rv"

[[task]]
id = "nw"
title = "review without worktree"
status = "in_rev"
write_set = ["b"]
branch = "feat/nw"

[[task]]
id = "nb"
title = "review without branch"
status = "in_rev"
write_set = ["c"]
worktree = "~/wt/nb"

[[task]]
id = "ip"
title = "still in progress"
status = "pending"
write_set = ["d"]
worktree = "~/wt/ip"
branch = "feat/ip"
"""
REVIEW_COMMAND = "cd ~/wt/rv && hunk diff integration...feat/rv"


def _review(tmp_path: pathlib.Path, tip: str = "") -> pathlib.Path:
    path = tmp_path / "tasks.toml"
    path.write_text(REVIEW_TASKS)
    if tip:
        (tmp_path / "ORCHESTRATION.md").write_text(f"- Stack tip: `{tip}`\n")
    return path


def _icon_click(app, pilot, *, pad: int = 0):
    """Click the one copy icon of the main view, found in the text as the pane draws it."""

    rows = _drawn(app).splitlines()
    hits = [(y, row.index("⧉")) for y, row in enumerate(rows) if "⧉" in row]
    assert len(hits) == 1
    y, x = hits[0]
    return pilot.click("#view", offset=(x + pad, y))


@pytest.mark.parametrize("view", ["tree", "columns", "text"])
def test_a_click_on_the_copy_icon_of_a_row_copies_the_command_and_the_id_still_opens_the_overlay(
    tmp_path: pathlib.Path, view: str
):
    app = _load().BoardApp(_review(tmp_path))

    async def drive():
        async with app.run_test(size=(60 if view == "text" else 160, 30)) as pilot:
            if view != "tree":
                await pilot.press("tab")
            await pilot.pause()
            await _icon_click(app, pilot, pad=1 if view == "tree" else 0)
            await pilot.pause()
            assert app.clipboard == REVIEW_COMMAND
            assert len(app.screen_stack) == 1
            if view == "text":
                return

            # The title of a row and the card itself still open the overlay.
            rows = _drawn(app).splitlines()
            y, row = next((y, r) for y, r in enumerate(rows) if "in review" in r)
            await pilot.click("#view", offset=(row.index("in review") + (view == "tree"), y))
            await pilot.pause()
            assert isinstance(app.screen, sys.modules["ledger_board"].BriefScreen)
            assert app.screen.task_id == "rv"

    asyncio.run(drive())


def test_o_and_y_act_on_the_spotlit_task_and_wait_without_one(tmp_path: pathlib.Path):
    app = _load().BoardApp(_review(tmp_path, tip="stack/tip"))
    command = "cd ~/wt/rv && hunk diff stack/tip...feat/rv"
    notes: list[str] = []
    app.notify = lambda message, **_: notes.append(message)  # type: ignore[method-assign]

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            await pilot.pause()
            await pilot.press("o", "y")
            assert len(app.screen_stack) == 1
            assert notes == []
            assert not app.clipboard
            app.action_spotlight("rv")
            await pilot.press("y")
            assert app.clipboard == command
            await pilot.press("o")
            await pilot.pause()
            assert app.screen.task_id == "rv"

    asyncio.run(drive())


def test_the_review_section_shows_the_command_and_the_icon_and_y_copy_it(
    tmp_path: pathlib.Path,
):
    app = _load().BoardApp(_review(tmp_path, tip="stack/tip"))
    command = "cd ~/wt/rv && hunk diff stack/tip...feat/rv"

    async def drive():
        async with app.run_test(size=(120, 30)) as pilot:
            app.action_brief("rv")
            await pilot.pause()
            text = _overlay_text(app)
            assert "Review" in text
            assert command in text
            assert text.count("⧉") == 1
            await pilot.press("y")
            await pilot.pause()
            assert app.clipboard == command

            app.copy_to_clipboard("")
            width = app.screen.query_one("#brief").size.width
            out = io.StringIO()
            rich.console.Console(file=out, width=width, color_system=None).print(
                app.screen.query_one("#brief").content
            )
            rows = out.getvalue().splitlines()
            y, x = next((y, row.index("⧉")) for y, row in enumerate(rows) if "⧉" in row)
            await pilot.click("#brief", offset=(x, y))
            await pilot.pause()
            assert app.clipboard == command
            assert isinstance(app.screen, sys.modules["ledger_board"].BriefScreen)

    asyncio.run(drive())


@pytest.mark.parametrize("task_id", ["nw", "nb"])
def test_a_task_without_worktree_or_branch_has_no_icon_and_no_section(
    tmp_path: pathlib.Path, task_id: str
):
    module = _load()
    app = module.BoardApp(_review(tmp_path))

    async def drive():
        async with app.run_test(size=(160, 40)) as pilot:
            app.action_brief(task_id)
            await pilot.pause()
            assert "Review" not in _overlay_text(app)
            assert "⧉" not in _overlay_text(app)
            await pilot.press("y")
            await pilot.pause()
            assert app.clipboard == ""
            await pilot.press("escape")
            for _ in range(2):
                await pilot.pause()
                rows = [r for r in _drawn(app).splitlines() if task_id in r]
                assert rows
                assert not any("⧉" in row for row in rows)
                await pilot.press("tab")

    asyncio.run(drive())
    graph = module.ledger_graph.Graph(module.ledger_graph.parse(REVIEW_TASKS)[0])
    assert module._review_commands(graph, "integration") == {"rv": REVIEW_COMMAND}


def test_a_running_task_has_the_hunk_command_to_steer_it():
    module = _load()
    text = REVIEW_TASKS.replace('status = "pending"', 'status = "in_progress"')
    graph = module.ledger_graph.Graph(module.ledger_graph.parse(text)[0])
    assert module._review_commands(graph, "integration")["ip"] == "cd ~/wt/ip && hunk diff integration...feat/ip"
    assert "ip" in module._hunk_targets(graph)


def _confirm(pilot):
    """Pick the second choice of the confirmation, which starts on cancel."""

    return pilot.press("j", "enter")


def test_h_marks_the_spotlit_task_after_a_confirmation_and_clears_the_mark_the_same_way(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.action_spotlight("rv")
            await pilot.press("h")
            await pilot.pause()
            assert isinstance(app.screen, module.PickScreen)

            # Cancelling writes nothing.
            await pilot.press("escape")
            await pilot.pause()
            assert path.read_text() == REVIEW_TASKS

            await pilot.press("h")
            await pilot.pause()
            await _confirm(pilot)
            await pilot.pause()
            assert path.read_text() == REVIEW_TASKS.replace(
                'id = "rv"\n', 'id = "rv"\nhuman_review = true\n'
            )
            assert [r for r in _drawn(app).splitlines() if "in review" in r and "◆" in r]

            await pilot.press("h")
            await pilot.pause()
            await _confirm(pilot)
            await pilot.pause()
            assert path.read_text() == REVIEW_TASKS

    asyncio.run(drive())


def test_a_file_that_disappears_before_the_write_shows_the_error_instead_of_crashing(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.action_spotlight("rv")
            await pilot.press("h")
            await pilot.pause()
            path.unlink()
            await _confirm(pilot)
            await pilot.pause()
            assert app.error

    asyncio.run(drive())


def test_a_marked_task_in_review_awaits_you_in_the_blockers_list_and_h_works_in_the_overlay(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.action_brief("rv")
            await pilot.pause()
            await pilot.press("h")
            await pilot.pause()
            await _confirm(pilot)
            await pilot.pause()
            assert isinstance(app.screen, module.BriefScreen)
            assert "marked for your review" in _overlay_text(app)
            assert "human_review = true" in path.read_text()

            await pilot.press("escape")
            await pilot.press("tab", "tab")
            await pilot.pause()
            assert app.view == "inbox"
            assert [r for r in _drawn(app).splitlines() if "rv" in r and "◆" in r]
            assert "1 awaiting you" in app.query_one("#status").content.plain

    asyncio.run(drive())


def test_a_confirmation_is_refused_when_the_file_changed_the_mark_meanwhile(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.action_spotlight("rv")
            await pilot.press("h")
            await pilot.pause()

            # Somebody else marked it while the confirmation was open.
            marked = REVIEW_TASKS.replace('id = "rv"\n', 'id = "rv"\nhuman_review = true\n')
            path.write_text(marked)
            os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))
            await _confirm(pilot)
            await pilot.pause()
            assert path.read_text() == marked
            assert any("changed in the file" in n.message for n in app._notifications)

    asyncio.run(drive())


def test_the_legend_lists_every_icon_whether_or_not_the_tree_draws_it(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    # No task is in review, so the tree draws no icon.
    path.write_text(REVIEW_TASKS.replace('status = "in_rev"', 'status = "pending"'))
    app = module.BoardApp(path)

    async def legend() -> str:
        async with app.run_test(size=(120, 40)) as pilot:
            await pilot.press("l")
            await pilot.pause()
            return app.screen.legend.plain

    def line(text: str, label: str) -> str:
        return next(x for x in text.splitlines() if label in x)

    shown = asyncio.run(legend())
    graph = module.ledger_graph
    assert line(shown, "copy command").startswith(f" {graph.COPY_ICON}  ")
    assert line(shown, "your review").startswith(f" {graph.HUMAN_REVIEW_ICON}  ")
    assert line(shown, "N drafts").startswith(f" {graph.DRAFT_ICON}  ")
    assert line(shown, "N sent").startswith(f" {graph.SENT_ICON}  ")
    assert "not shown now" not in shown
    assert all(len(x) <= 40 for x in shown.splitlines())



HUNK_NOTE = {
    "noteId": "user:1794402060751-1",
    "source": "user",
    "filePath": "m.py",
    "hunkIndex": 0,
    "newRange": [9, 9],
    "body": "use type hints",
    "author": "user",
    "createdAt": "2026-11-11T13:01:00.751Z",
    "editable": True,
}
EXPORT = json.dumps({"comments": [HUNK_NOTE]})


class FakeHunk:
    """A hunk runner that records its calls and answers with a fixed result."""

    def __init__(self, output: str = EXPORT, *, ok: bool = True):
        self.output = output
        self.ok = ok
        self.calls: list[tuple[list[str], str]] = []

    def __call__(self, args: list[str], stdin: str = "") -> tuple[bool, str]:
        self.calls.append((args, stdin))
        return self.ok, self.output


def _feedback_file(path: pathlib.Path) -> pathlib.Path:
    return path.parent / "feedback" / "rv.json"


def test_the_poll_asks_hunk_for_the_running_and_review_tasks_with_a_worktree_and_saves_a_draft(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    hunk = FakeHunk()
    app = module.BoardApp(path, hunk=hunk)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            assert module._hunk_targets(app.graph) == {
                "rv": str(pathlib.Path("~/wt/rv").expanduser()),
                "nb": str(pathlib.Path("~/wt/nb").expanduser()),
            }
            app.poll_hunk()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.hunk_busy
            assert hunk.calls[0][0] == [
                "session",
                "comment",
                "list",
                "--repo",
                str(pathlib.Path("~/wt/rv").expanduser()),
                "--type",
                "user",
                "--json",
            ]
            assert json.loads(_feedback_file(path).read_text()) == {"ready": False, "comments": [HUNK_NOTE]}
            assert [r for r in _drawn(app).splitlines() if "in review" in r and "✎1" in r]

    asyncio.run(drive())


class RaisingHunk(FakeHunk):
    def __call__(self, args: list[str], stdin: str = "") -> tuple[bool, str]:
        raise RuntimeError("boom")


@pytest.mark.parametrize(
    "hunk",
    [
        FakeHunk("No active Hunk sessions", ok=False),
        FakeHunk("not json"),
        RaisingHunk(),
    ],
    ids=["no session", "bad json", "raises"],
)
def test_a_hunk_that_fails_or_answers_badly_never_raises_and_saves_nothing(
    tmp_path: pathlib.Path, hunk: FakeHunk
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path, hunk=hunk)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.poll_hunk()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert not app.hunk_busy
            assert not _feedback_file(path).exists()

    asyncio.run(drive())


def test_f_sends_the_saved_comments_after_a_confirmation_and_refuses_when_there_are_none(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path, hunk=FakeHunk())

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.action_spotlight("rv")
            await pilot.press("f")
            await pilot.pause()
            assert not isinstance(app.screen, module.PickScreen)
            assert any("no saved comments" in n.message for n in app._notifications)

            app.saved_comments("rv", "~/wt/rv")
            app.reload()
            await pilot.press("f")
            await pilot.pause()
            assert isinstance(app.screen, module.PickScreen)
            assert [v for v, *_ in app.screen.choices] == ["cancel", "confirm"]

            # Cancelling writes nothing.
            await pilot.press("escape")
            await pilot.pause()
            assert json.loads(_feedback_file(path).read_text())["ready"] is False

            await pilot.press("f")
            await pilot.pause()
            await _confirm(pilot)
            await pilot.pause()
            assert json.loads(_feedback_file(path).read_text())["ready"] is True
            assert path.read_text() == REVIEW_TASKS
            assert [r for r in _drawn(app).splitlines() if "in review" in r and "✉1" in r]

            await pilot.press("f")
            await pilot.pause()
            assert not isinstance(app.screen, module.PickScreen)
            assert any("sent already" in n.message for n in app._notifications)

    asyncio.run(drive())


def test_a_send_is_refused_when_the_file_changed_while_the_confirmation_was_open(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    app = module.BoardApp(path, hunk=FakeHunk())

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.saved_comments("rv", "~/wt/rv")
            app.action_spotlight("rv")
            await pilot.press("f")
            await pilot.pause()

            # The task left review while the confirmation was open.
            moved = REVIEW_TASKS.replace('id = "rv"\ntitle = "in review"\nstatus = "in_rev"', 'id = "rv"\ntitle = "in review"\nstatus = "pending"')
            path.write_text(moved)
            os.utime(path, ns=(1, path.stat().st_mtime_ns + 10**9))
            await _confirm(pilot)
            await pilot.pause()
            assert json.loads(_feedback_file(path).read_text())["ready"] is False
            assert any("not in_progress or in_rev" in n.message for n in app._notifications)

    asyncio.run(drive())


def test_f_and_capital_f_work_in_the_overlay_and_restore_applies_the_comments_to_hunk(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = _review(tmp_path)
    hunk = FakeHunk()
    app = module.BoardApp(path, hunk=hunk)
    worktree = str(pathlib.Path("~/wt/rv").expanduser())

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.saved_comments("rv", worktree)
            hunk.calls.clear()
            app.action_brief("rv")
            await pilot.pause()
            assert "✎ 1 comment saved" in _overlay_text(app)

            await pilot.press("f")
            await pilot.pause()
            await _confirm(pilot)
            await pilot.pause()
            assert isinstance(app.screen, module.BriefScreen)
            assert "✉ 1 comment sent" in _overlay_text(app)

            hunk.output = ""
            await pilot.press("F")
            await pilot.pause()
            assert isinstance(app.screen, module.PickScreen)
            await _confirm(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            args, stdin = hunk.calls[-1]
            assert args == ["session", "comment", "apply", "--repo", worktree, "--stdin"]
            assert json.loads(stdin) == {
                "comments": [{"filePath": "m.py", "newLine": 9, "summary": "use type hints", "author": "user"}]
            }
            assert any("applied 1 comment to hunk" in n.message for n in app._notifications)

    asyncio.run(drive())


def test_a_restore_that_hunk_refuses_is_a_message_with_what_hunk_said(tmp_path: pathlib.Path):
    module = _load()
    path = _review(tmp_path)
    hunk = FakeHunk()
    app = module.BoardApp(path, hunk=hunk)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            app.saved_comments("rv", "~/wt/rv")
            hunk.output, hunk.ok = "No active Hunk sessions", False
            app.action_spotlight("rv")
            await pilot.press("F")
            await pilot.pause()
            await _confirm(pilot)
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert any(
                "hunk did not take the comments: No active Hunk sessions" in n.message
                for n in app._notifications
            )

    asyncio.run(drive())


REPLAN_TASKS = """format = 1

[[task]]
id = "E1"
type = "epic"
title = "epic"
status = "pending"

[[task]]
id = "a"
parent = "E1"
title = "inside"
status = "pending"
write_set = ["a"]

[[task]]
id = "b"
title = "outside"
status = "pending"
write_set = ["b"]
"""


def test_capital_r_offers_the_epic_scope_only_on_a_spotlit_epic_and_writes_one_request(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REPLAN_TASKS)
    app = module.BoardApp(path)
    folder = tmp_path / "replan-requests"

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.press("R")
            await pilot.pause()
            assert isinstance(app.screen, module.PickScreen)
            assert [v for v, *_ in app.screen.choices] == ["cancel", "remaining"]

            # Cancelling writes nothing.
            await pilot.press("escape")
            await pilot.pause()
            assert not folder.exists()

            app.action_spotlight("a")
            await pilot.press("R")
            await pilot.pause()
            assert [v for v, *_ in app.screen.choices] == ["cancel", "remaining"]
            await pilot.press("escape")

            app.action_spotlight("a")
            app.action_spotlight("E1")
            await pilot.press("R")
            await pilot.pause()
            assert [v for v, *_ in app.screen.choices] == ["cancel", "remaining", "epic"]
            await pilot.press("j", "j", "enter")
            await pilot.pause()
            ask = app.query_one("#ask", textual.widgets.TextArea)
            assert ask.display
            await pilot.press("s", "p", "l", "i", "t", "enter")
            await pilot.pause()
            assert app.error == ""
            assert not ask.display

    asyncio.run(drive())

    (request,) = folder.iterdir()
    assert tomllib.loads(request.read_text()) == {"scope": "epic", "epic": "E1", "note": "split"}
    assert path.read_text() == REPLAN_TASKS
    assert not (tmp_path / "steering.log").exists()


def test_capital_r_writes_a_request_without_a_note_and_a_multi_line_note_is_refused(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REPLAN_TASKS)
    app = module.BoardApp(path)
    folder = tmp_path / "replan-requests"

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.press("R")
            await pilot.pause()
            await _confirm(pilot)
            await pilot.pause()
            ask = app.query_one("#ask", textual.widgets.TextArea)
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

    asyncio.run(drive())

    (request,) = folder.iterdir()
    assert tomllib.loads(request.read_text()) == {"scope": "remaining"}


def test_capital_r_does_nothing_outside_the_tree(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REPLAN_TASKS)
    app = module.BoardApp(path)

    async def drive():
        async with app.run_test(size=(160, 30)) as pilot:
            await pilot.press("tab", "R")
            await pilot.pause()
            assert not isinstance(app.screen, module.PickScreen)

    asyncio.run(drive())

    assert not (tmp_path / "replan-requests").exists()


def test_a_menu_shows_a_long_id_shortened_and_the_action_whole_at_80_columns():
    module = _load()
    task_id = "pick-priority-on-target-" + "x" * 16
    screen = module.PickScreen(
        f"{task_id}: human review",
        [("cancel", "cancel", "do nothing"), ("confirm", "mark for your review")],
        "cancel",
    )

    class Host(module.textual.app.App):
        def on_mount(self):
            self.push_screen(screen)

    async def drive():
        async with Host().run_test(size=(80, 24)) as pilot:
            await pilot.pause()
            pane = screen.query_one("#pick-pane")
            assert pane.region.width <= 80
            assert str(screen.query_one("#pick-action").render()) == "human review"
            assert task_id in screen.chip() or "…" in screen.chip()
            assert screen.prompt("cancel", "cancel").plain.startswith("  ✕ ")
            assert screen.prompt("confirm", "x", selected=True).plain.startswith("▌ ◆ ")

    asyncio.run(drive())


@pytest.mark.parametrize("theme", ["ansi-dark", "textual-dark", "textual-light"])
def test_the_menu_title_and_key_chips_have_no_filled_background(theme):
    """The id on the border and the keys of the bottom line are plain colored text."""
    module = _load()
    screen = module.PickScreen(
        "build-local-stack: human review", [("cancel", "cancel", "do nothing")], "cancel"
    )

    class Host(module.textual.app.App):
        def __init__(self):
            super().__init__(ansi_color=theme == "ansi-dark")

        def on_mount(self):
            self.theme = theme
            self.push_screen(screen)

    async def drive():
        async with Host().run_test(size=(100, 24)) as pilot:
            await pilot.pause()
            pane = screen.query_one("#pick-pane")
            assert pane.border_title == "build-local-stack"
            assert pane.styles.border_title_background.a == 0
            assert pane.styles.border_title_color.a > 0
            keys = screen.query_one("#pick-keys").render()
            spans = [s for s in keys.spans if keys.plain[s.start : s.end] in ("enter", "esc")]
            assert len(spans) == 2
            for span in spans:
                style = module.rich.style.Style.parse(str(span.style).replace("$accent", "red"))
                assert style.bgcolor is None and not style.reverse

    asyncio.run(drive())


def _luminance(color: rich.color.Color) -> float:
    def channel(value: int) -> float:
        unit = value / 255
        return unit / 12.92 if unit <= 0.03928 else ((unit + 0.055) / 1.055) ** 2.4

    red, green, blue = color.get_truecolor()
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast(foreground: rich.color.Color, background: rich.color.Color) -> float:
    light, dark = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


@pytest.mark.parametrize("theme", ["ansi-dark", "textual-dark", "textual-light"])
@pytest.mark.parametrize("width", [100, 40])
def test_the_selected_menu_option_is_readable_without_a_background_block(theme, width):
    module = _load()
    screen = module.PickScreen(
        "g2: human review",
        [("cancel", "cancel", "do nothing"), ("confirm", "mark for your review", "it stays marked")],
        "cancel",
    )
    words = [("cancel", "do"), ("mark", "it")]

    class Host(module.textual.app.App):
        def __init__(self):
            super().__init__(ansi_color=theme == "ansi-dark")

        def on_mount(self):
            self.theme = theme
            self.push_screen(screen)

    def segment(rows, option, line):
        # An option spans its label, its description and a blank line.
        return next(s for s in rows[option * 3 + line] if words[option][line] in s.text).style

    async def drive():
        async with Host().run_test(size=(width, 24)) as pilot:
            await pilot.pause()
            picker = screen.query_one("#pick-list")
            for selected in (0, 1):
                picker.highlighted = selected
                await pilot.pause()
                rows = [picker.render_line(y) for y in range(picker.virtual_size.height)]
                other = 1 - selected
                for line in (0, 1):
                    style, plain = segment(rows, selected, line), segment(rows, other, line)
                    # The selected text looks like the text of an option that is not selected: no block.
                    assert style.color == plain.color, (theme, selected, line, style)
                    assert style.bgcolor == plain.bgcolor, (theme, selected, line, style)
                    if theme != "ansi-dark":
                        assert _contrast(style.color, style.bgcolor) >= 4.5, (theme, selected, line, style)

    asyncio.run(drive())
