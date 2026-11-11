from __future__ import annotations

import dataclasses
import datetime
import importlib.util
import io
import json
import os
import pathlib
import re
import subprocess
import sys
from typing import Any

import pytest
import tomllib
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".agents/skills/orchestrating-agents/scripts/ledger_graph.py"


def _load():
    spec = importlib.util.spec_from_file_location("ledger_graph", SCRIPT)
    assert spec
    assert spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def caps(
    *,
    rw: int | None = 3,
    ro: int | None = 3,
    budget: int | None = 6,
    note: str | None = "test",
) -> tuple[str, ...]:
    """The flags next requires, with a flag left out when its value is None."""

    given = {"--rw": rw, "--ro": ro, "--budget": budget, "--caps-note": note}
    return tuple(x for k, v in given.items() if v is not None for x in (k, str(v)))


@pytest.fixture
def run_bare(tmp_path: pathlib.Path):
    """Like `run`, but writes no briefs, so `check` sees only the ones a test adds."""

    module = _load()

    def _run(text: str, *args: str) -> tuple[int, dict]:
        tasks = tmp_path / "tasks.toml"
        tasks.write_text(text)
        out = io.StringIO()
        code = module.run([str(tasks), *args], out)
        return code, json.loads(out.getvalue())

    return _run


@pytest.fixture
def run(tmp_path: pathlib.Path, run_bare):
    """Runs with a complete brief for every task, so `check` does not warn about them."""

    def _run(text: str, *args: str) -> tuple[int, dict]:
        briefs = tmp_path / "briefs"
        briefs.mkdir(exist_ok=True)
        for task_id in re.findall(r'^id = "([^"]+)"', text, re.MULTILINE):
            (briefs / f"{task_id}.md").write_text("## Change\n\n## Acceptance\n")
        return run_bare(text, *args)

    return _run


@pytest.fixture
def run_text(tmp_path: pathlib.Path):
    module = _load()

    def _run(text: str, *args: str) -> list[str]:
        tasks = tmp_path / "tasks.toml"
        tasks.write_text(text)
        out = io.StringIO()
        assert module.run([str(tasks), *args], out) == 0
        return out.getvalue().splitlines()

    return _run


@pytest.fixture
def compare(tmp_path: pathlib.Path):
    module = _load()

    def _run(old: str, new: str) -> tuple[int, dict]:
        (tmp_path / "old.toml").write_text(old)
        (tmp_path / "new.toml").write_text(new)
        out = io.StringIO()
        code = module.run(
            [str(tmp_path / "old.toml"), "compare", str(tmp_path / "new.toml")], out
        )
        return code, json.loads(out.getvalue())

    return _run


def ids(items: list[dict]) -> list[str]:
    return [item["id"] for item in items]


def reasons(result: dict) -> set[tuple[str, str]]:
    return {(s["id"], s["reason"]) for s in result["skipped"]}


def _toml(value) -> str:
    """A TOML value: JSON writes strings, lists, integers and booleans the same way."""

    return json.dumps(value, ensure_ascii=False)


def _block(task_id: str, status: str = "pending", **keys) -> str:
    """A task block titled by its id; a leaf writes [id] unless keys say otherwise.

    A key set to None is left out.
    """

    keys = {"id": task_id, "title": task_id, "status": status, **keys}
    leaf = keys.get("type") != "epic"
    if leaf and keys.get("write_set") is None and keys.get("read_only") is None:
        keys["write_set"] = [task_id]
    lines = (f"{k} = {_toml(v)}\n" for k, v in keys.items() if v is not None)
    return "\n[[task]]\n" + "".join(lines)


def _ledger(*blocks: str, header: str = "") -> str:
    return "format = 1\n" + header + "".join(blocks)


def _epic(task_id: str, status: str = "pending", **keys) -> str:
    return _block(task_id, status, type="epic", **keys)


def _with(text: str, task_id: str, **keys) -> str:
    """The ledger with keys of a task replaced in place or added after its id.

    A key set to None is removed.
    """

    head, *blocks = text.split("[[task]]")
    for n, block in enumerate(blocks):
        if f'\nid = "{task_id}"\n' not in block:
            continue
        for key, value in keys.items():
            line = "" if value is None else f"{key} = {_toml(value)}\n"
            pattern = re.compile(rf"^{key} = .*\n", re.M)
            block, found = pattern.subn(lambda _, line=line: line, block, count=1)
            if not found:
                block = block.replace(
                    f'id = "{task_id}"\n', f'id = "{task_id}"\n{line}'
                )
        blocks[n] = block
    return "[[task]]".join([head, *blocks])


def _leaf(i: str, title: str, status: str = "pending", **keys) -> str:
    return _block(i, status, **{"title": title, **keys})


TASKS = _ledger(
    _epic("E1", title="epic", priority="P1", milestone="m1"),
    _leaf("t1", "a", "done", parent="E1", write_set=["src/a.py"]),
    _leaf("t2", "b", parent="E1", priority="P1", deps=["t1"], write_set=["src/b/**"]),
    _leaf("t3", "c", parent="E1", priority="P0", deps=["t1"], write_set=["src/b/x.py"]),
    _leaf("t4", "d", priority="P3", write_set=["docs/**"]),
    _leaf("t5", "e", deps=["E1"], read_only=True),
    _leaf("t6", "f", "running", priority="P1", write_set=["lib/**"]),
)
PARALLEL = _ledger(
    _block(
        "w1", priority="P0", write_set=["src/one/**"], footprint=3, exclusive=["gpu"]
    ),
    _block("w2", priority="P1", write_set=["src/two/**"], exclusive=["gpu"]),
    _block("r1", priority="P1", read_only=True),
    _block("r2", priority="P2", read_only=True),
)


def test_next_exits_2_when_a_cap_or_the_note_is_missing_or_not_positive(run, tmp_path):
    """A missing cap names nproc, a missing note does not, and a flag must be positive."""

    for caps_kwargs, flag, mentions_nproc in [
        ({"rw": None}, "--rw", True),
        ({"ro": None}, "--ro", True),
        ({"budget": None}, "--budget", True),
        ({"note": None}, "--caps-note", False),
        ({"note": " "}, "--caps-note", False),
    ]:
        code, result = run(TASKS, "next", *caps(**caps_kwargs))
        assert code == 2
        assert flag in result["errors"][0]
        assert ("nproc" in result["errors"][0]) is mentions_nproc
        assert not (tmp_path / "caps.log").exists()

    code, result = run(PARALLEL, "next", *caps(rw=0))
    assert code == 2
    assert result["errors"] == ["--rw must be a positive integer"]


def test_next_starts_by_priority_and_logs_the_caps_and_the_note(run, tmp_path):
    """next starts by priority and milestone, skips write set conflicts and logs its caps."""

    code, result = run(BLOCKED, "next", "--awaiting")
    assert code == 0
    assert set(result) == {
        "awaiting_human",
        "awaiting_event",
        "replan_requests",
        "feedback",
    }
    assert result["awaiting_human"]
    assert not (tmp_path / "caps.log").exists()

    code, result = run(PARALLEL, "next", "--pick", "w1", *caps(note=None))
    assert code == 0
    assert ids(result["start"]) == ["w1"]
    assert not (tmp_path / "caps.log").exists()
    code, _ = run(PARALLEL, "next", "--pick", "w1", *caps(note="why"))
    assert code == 0
    assert (tmp_path / "caps.log").read_text().endswith(" why\n")

    code, _ = run(TASKS, "next", *caps(rw=2, ro=1, budget=4, note="load 1/14"))
    assert code == 0
    line = (tmp_path / "caps.log").read_text().splitlines()[-1]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\S+ rw=2 ro=1 budget=4 load 1/14", line)

    code, result = run(TASKS, "next", *caps())
    assert code == 0
    assert ids(result["start"]) == ["t3", "t4"]
    assert result["skipped"][0]["id"] == "t2"
    assert result["skipped"][0]["with"] == "t3"
    assert result["running"] == [{"id": "t6", "worktree": None, "branch": None}]

    _, result = run(
        TASKS.replace('priority = "P3"', 'priority = "P0"'), "next", *caps()
    )
    assert ids(result["start"]) == ["t3", "t4"]
    assert result["start"][0]["milestone"] == "m1"

    text = TASKS.replace('write_set = ["docs/**"]', 'write_set = ["./lib/y.py"]')
    _, result = run(text, "next", *caps())
    assert "t4" not in ids(result["start"])
    assert any(s["id"] == "t4" and s["with"] == "t6" for s in result["skipped"])

    # The running t6 takes one of the two writer slots, so only t3 starts.
    _, result = run(TASKS, "next", "--slots", "2", *caps(rw=None))
    assert ids(result["start"]) == ["t3"]
    assert {"id": "t4", "reason": "no free slot"} in result["skipped"]
    _, same = run(TASKS, "next", *caps(rw=2))
    assert same["start"] == result["start"]


def test_next_fills_the_two_caps_the_budget_and_the_exclusive_names(run):
    """Each cap counts its own tasks, the budget the footprints, and a heavy task waits alone."""

    gpu = {"reason": "exclusive resource", "with": "w1", "detail": "gpu"}
    # A flag of next replaces the header cap of the same name.
    header = PARALLEL.replace("format = 1", "format = 1\nrw = 1\nro = 1")
    for text, flags, start, skip in [
        (
            PARALLEL,
            {"ro": 1},
            ["w1", "r1"],
            {"id": "r2", "reason": "no free read-only slot"},
        ),
        (
            PARALLEL,
            {"rw": 1},
            ["w1", "r1", "r2"],
            {"id": "w2", "reason": "no free slot"},
        ),
        # w1 weighs 3, so it starts alone under a budget of 2 and fills a budget of 3.
        (PARALLEL, {"budget": 2}, ["w1"], {"id": "r1", "reason": "no budget left"}),
        (PARALLEL, {"budget": 4}, ["w1", "r1"], None),
        # w1 cannot fit while r1 runs, so nothing lighter starts ahead of it.
        (
            _with(PARALLEL, "r1", status="running"),
            {"budget": 2},
            [],
            {
                "id": "w2",
                "reason": "budget reserved",
                "detail": "w1 needs the whole budget",
            },
        ),
        # A running w1 takes the gpu and 3 of the 4 units of the budget.
        (_with(PARALLEL, "w1", status="running"), {"budget": 4}, ["r1"], gpu),
        # w1 and w2 both want the gpu, so the second one waits.
        (PARALLEL, {}, ["w1", "r1", "r2"], gpu),
        (header, {"rw": 1, "ro": 1, "budget": 9}, ["w1", "r1"], None),
        (header, {"rw": 1, "ro": 2, "budget": 9}, ["w1", "r1", "r2"], None),
    ]:
        _, result = run(text, "next", *caps(**flags))
        assert ids(result["start"]) == start, flags
        assert not skip or any(skip.items() <= s.items() for s in result["skipped"])
    assert result["limits"] == {"rw": 1, "ro": 2, "budget": 9}


def test_pick_keeps_a_subset_and_refuses_anything_else(run, tmp_path):
    """An arbiter may only reduce the list the script computed."""

    code, result = run(PARALLEL, "next", "--pick", "w1,r2", *caps(note=None))
    assert (code, ids(result["start"])) == (0, ["w1", "r2"])

    for pick, error in [("w1,w2", "cannot start w2 now"), (",", "at least one id")]:
        code, result = run(PARALLEL, "next", "--pick", pick, *caps(note=None))
        assert code == 2
        assert any(error in e for e in result["errors"])

    code, _ = run(PARALLEL, "next", "--pick", "w1,nope", *caps(note="why"))
    assert code == 2
    assert not (tmp_path / "caps.log").exists()


def test_epics_gate_their_leaves_and_are_never_scheduled(run):
    """An epic waits for its leaves, passes its deps and blocker down and never starts."""

    text = _with(_with(TASKS, "t2", status="done"), "t3", status="done")
    _, result = run(text, "next", *caps())
    assert {"id": "t5", "blocked_by": ["E1"]} in result["waiting"]
    _, result = run(_with(text, "E1", status="done"), "next", *caps())
    assert "t5" in ids(result["start"])

    _, result = run(_with(TASKS, "E1", status="blocked"), "next", *caps())
    assert {"id": "t2", "blocked_by": ["E1"]} in result["waiting"]

    _, result = run(_with(TASKS, "E1", deps=["t4"]), "next", *caps())
    assert {"id": "t2", "blocked_by": ["t4"]} in result["waiting"]

    text = TASKS + _epic("E2")
    _, result = run(text, "ready")
    assert "E2" not in result["ready"]
    _, result = run(text, "epics")
    assert result["epics"][-1]["children_done"] is False

    _, epics = run(TASKS, "epics")
    assert epics["epics"][0]["leaves_done"] == 1
    assert epics["epics"][0]["children_done"] is False
    _, why = run(TASKS, "explain", "t1")
    assert why["blocking"] == ["t2", "t3"]
    _, why = run(TASKS, "explain", "t2")
    assert why["ready"] is True
    assert why["wave"] == 2
    _, why = run(TASKS, "explain", "t5")
    assert why["wave"] == 4


CHECK_ERRORS = [
    ("format = 1", "format = 2", "unsupported format 2"),
    (
        'deps = ["t1"]\nwrite_set = ["src/b/**"]',
        'deps = ["t9"]\nwrite_set = ["src/b/**"]',
        "dep 't9' does not exist",
    ),
    (
        'id = "t1"\ntitle = "a"\nstatus = "done"\nparent = "E1"',
        'id = "t1"\ntitle = "a"\nstatus = "done"\nparent = "E1"\ndeps = ["t3"]',
        "dependency cycle",
    ),
    ('write_set = ["lib/**"]', "", "set write_set"),
    ('write_set = ["lib/**"]', "write_set = []", "set write_set"),
    (
        "read_only = true",
        'read_only = true\nwrite_set = ["x"]',
        "exclude each other",
    ),
    (
        'title = "f"',
        'title = "f"\nblocker = "human: now?"',
        "'blocker' needs status",
    ),
    ('id = "t6"', 'id = "t4"', "duplicate id"),
    ('title = "f"', 'title = "f"\nmodel = 5', "'model' must be a string"),
    (
        "format = 1",
        'format = 1\n[effort]\nimplementer = "max"',
        "'effort' must be one of",
    ),
    ("format = 1", "format = 1\nmodel = 3", "header: 'model' must be a table"),
    ("format = 1", "format = 1\nro = 0", "header: 'ro' must be a positive integer"),
    (
        'parent = "E1"\nwrite_set = ["src/a.py"]',
        'parent = "t4"\nwrite_set = ["src/a.py"]',
        "is not an epic",
    ),
]


def test_check_reports_errors_and_accepts_a_model_and_an_effort(run):
    """Every row of CHECK_ERRORS is refused, while a model and an effort are accepted."""

    for old, new, message in CHECK_ERRORS:
        code, result = run(TASKS.replace(old, new, 1), "check")
        assert code == 2, message
        assert any(message in e for e in result["errors"]), message

    text = TASKS.replace(
        "format = 1\n",
        'format = 1\n[model]\nimplementer = "claude-sonnet-5.5"\n[effort]\nreviewer = "high"\n',
        1,
    ).replace('id = "t3"', 'id = "t3"\nmodel = "claude-opus-9"\neffort = "xhigh"', 1)
    code, _ = run(text, "check")
    assert code == 0
    _, result = run(text, "next", *caps())
    first = next(t for t in result["start"] if t["id"] == "t3")
    assert (first["model"], first["effort"]) == ("claude-opus-9", "xhigh")
    other = next(t for t in result["start"] if t["id"] != "t3")
    assert "model" not in other


SYMBOLS = ("✔", "●", "▶", "○", "✖", "◌")


def _words(line: str) -> list[str]:
    """The words of a tree row as symbol, priority, id, title, detail.

    The art and the bar of an epic are dropped, and the id, which follows the
    title in the row, is moved before it.
    """

    words = line.lstrip("┃ │├└─┬").split()
    if words and re.fullmatch(r"[━─]*", words[0]):
        words = words[1:]
    if words and re.fullmatch(r"\d+/\d+", words[0]):
        words = words[1:]
    head = [words.pop(0)] if words and words[0] in SYMBOLS else []
    if "·" not in words:
        return head + words
    at = words.index("·")
    title, rest = words[:at], words[at + 1 :]
    prio = [rest.pop(0)] if rest and re.fullmatch(r"P\d|--", rest[0]) else []
    return head + prio + rest[:1] + title + rest[1:]


def _label(line: str) -> tuple[str, str]:
    """The priority shown and the id of a tree row, empty for any other line.

    A plain row prefixes the priority to the id, `--` when it has none.
    """

    words = _words(line)
    if words and words[0] not in SYMBOLS and re.fullmatch(r"P\d|--", words[0]):
        # An epic row has no symbol.
        return words[0], words[1]
    if not words or words[0] not in SYMBOLS:
        return "", ""
    return words[1], words[2]


def test_tree_and_board_show_the_marks_the_blockers_and_the_pick_up_order(run_text):
    """The tree marks each task and hides done ones without --all, the board folds and orders."""

    lines = [
        " ".join(line.split()) for line in run_text(TASKS, "tree", "--all") if line
    ]
    assert lines[:5] == [
        "● f · P1 t6",
        "┬ ━━────── 1/3 epic · P1 E1 · m1",
        "├─ ✔ a · P2 t1",
        "├─ ▶ c · P0 t3 · blocks 1",
        "└─ ▶ b · P1 t2 · blocks 1 · waits t3 (files)",
    ]
    assert "○ e · P2 t5 · waits E1 (dep)" in lines
    assert any(_label(line)[1] == "t1" for line in lines)
    assert not any(_label(line)[1] == "t1" for line in run_text(TASKS, "tree"))

    lines = run_text(TASKS, "tree", "--epic", "E1")
    assert _words(lines[0])[:2] == ["P1", "E1"]
    assert not any(" t4 " in line for line in lines)

    lines = run_text(TASKS, "board")
    assert lines[0] == "In progress (1)"
    assert lines.index("Todo (4)") < lines.index("Done (1)")
    assert "  t5  e  P2  waits E1 (dep)" in lines
    todo = lines[lines.index("Todo (4)") + 1 :][:4]
    assert [line.split()[0] for line in todo] == ["t3", "t2", "t4", "t5"]


LEDGER = """# Orchestration ledger: demo

Status: active. Task graph: tasks.toml, format 1. Constraints: constraints.md. Sessions: x.

## Decisions & rationale
- 2026-01-01 t1: approved: one
- 2026-01-02 t2: approved: two
- 2026-01-03 t3: approved: three
- 2026-01-04 t4: approved: four

## Active agent registry
| task | attempt | unique name | agent id | status |
|------|---------|-------------|----------|--------|
| t6 | 1 | impl | agent-7 | running |

## Next actions
1. Reconcile t6, then run next.
2. Later.
"""


def _resume_text(tmp_path, text, ledger=None, constraints=False):
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(text)
    if ledger is not None:
        (tmp_path / "ORCHESTRATION.md").write_text(ledger)
    if constraints:
        (tmp_path / "constraints.md").write_text("rules")
    out = io.StringIO()
    assert _load().run([str(tasks), "resume"], out) == 0
    return out.getvalue().splitlines()


def test_resume_prints_every_part_of_the_digest(tmp_path):
    text = _with(TASKS, "t6", worktree="/w/t6", branch="t6")
    lines = _resume_text(tmp_path, text, LEDGER, constraints=True)
    assert lines[0].startswith(f"RESUME {tmp_path.name}  tasks=")
    assert lines[1] == (
        f"CONSTRAINTS {tmp_path / 'constraints.md'}  (read it before acting)"
    )
    heads = " ".join(line.split()[0] for line in lines if not line.startswith(" "))
    assert heads == "RESUME CONSTRAINTS READY RUNNING BLOCKED ANSWERED DECISIONS NEXT"
    assert "  t6  agent=agent-7  /w/t6  t6  missing" in lines
    assert any(line.startswith("  t3  P0  unblocks") for line in lines)
    assert "  t5  waits E1 (dep)" in lines
    assert "DECISIONS (last 3)" in lines
    assert "  2026-01-04 t4: approved: four" in lines
    assert not any("one" in line for line in lines)
    assert lines[-1] == "NEXT Reconcile t6, then run next."
    assert len(lines) < 30

    lines = _resume_text(_fresh(tmp_path, "no-constraints"), TASKS, LEDGER)
    assert lines[1].endswith("constraints.md  (MISSING, ask the human)")

    lines = _resume_text(_fresh(tmp_path, "no-ledger"), TASKS)
    assert lines[0].endswith("ORCHESTRATION.md  (missing)")
    assert lines[1].startswith("CONSTRAINTS none named")
    assert lines[-1] == "NEXT (none in the ledger)"

    for row in [
        "| t6 | 1 | impl | pending | pending |\n",
        "| t6 | 1 | impl | dead-1 | stale |\n",
        "",
    ]:
        ledger = LEDGER.replace("| t6 | 1 | impl | agent-7 | running |\n", row)
        lines = _resume_text(tmp_path, text, ledger, constraints=True)
        assert any(line.startswith("  t6  agent=?  /w/t6") for line in lines), row


def test_next_and_resume_show_the_session_of_a_running_task(run, tmp_path):
    text = _with(TASKS, "t6", session="abc", session_dir="/s")
    code, result = run(text, "next", *caps())
    assert code == 0
    assert result["running"][0]["session"] == "abc"
    assert result["running"][0]["session_dir"] == "/s"
    assert "session" not in result["start"][0]
    assert any(
        line.endswith("  session=abc  session_dir=/s")
        for line in _resume_text(_fresh(tmp_path, "session"), text)
    )


def test_resume_survives_an_undecodable_ledger(tmp_path):
    (tmp_path / "ORCHESTRATION.md").write_bytes(b"\xff\xfe")
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(TASKS)
    out = io.StringIO()
    assert _load().run([str(tasks), "resume"], out) == 0
    assert out.getvalue().splitlines()[0].endswith("(unreadable)")


def test_resume_from_stdin_does_not_pretend_to_read_a_ledger(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(TASKS))
    out = io.StringIO()
    assert _load().run(["-", "resume"], out) == 0
    lines = out.getvalue().splitlines()
    assert lines[0].endswith("ledger=-  (needs a tasks.toml path, not stdin)")
    assert lines[1] == "CONSTRAINTS unknown  (no ledger was read)"
    assert lines[-1] == "NEXT (none in the ledger)"


def test_malformed_input_exits_2_with_errors(run, tmp_path: pathlib.Path):
    code, result = run('format = 1\n[task]\nid = "t1"\n', "check")
    assert code == 2
    assert "[[task]]" in result["errors"][0]
    out = io.StringIO()
    assert _load().run([str(tmp_path / "missing.toml"), "check"], out) == 2
    assert "cannot read" in json.loads(out.getvalue())["errors"][0]


EVERY_ERROR = """format = 1
[[task]]
id = "x"
bogus = 1
title = 1
parent = []
deps = "a"
write_set = [1]
status = "nope"
priority = "P9"
type = "nope"
read_only = "yes"
overlap = "lines"
footprint = 0
model = "a b"
effort = "ultra"
worktree = "rel/x"
session = " a b"
session_dir = "d\\n"
stack = "s"
exclusive = [" "]
blocker = ""
answer = ""

[[task]]
title = ""
session_dir = "/s"
type = "epic"
stack = 0
overlap = 3

[[task]]
id = "e"
title = "e"
status = "blocked"
type = "epic"
stack = " "
footprint = true
session = ""
similar = ["a", 2]
"""


def test_parse_lists_every_block_error_in_order():
    statuses = (
        "status must be one of ['blocked', 'deferred', 'done', 'in_progress',"
        " 'in_rev', 'pending', 'returned', 'running']"
    )
    assert _load().parse(EVERY_ERROR) == (
        [],
        [
            "x: unknown key 'bogus'",
            "x: 'parent' must be a string",
            "x: 'title' must be a string",
            "x: 'deps' must be a list of strings",
            "x: 'write_set' must be a list of strings",
            f"x: {statuses}",
            "x: priority must be one of ['P0', 'P1', 'P2', 'P3']",
            "x: type must be one of ['epic', 'task']",
            "x: 'read_only' must be a boolean",
            "x: 'overlap' must be one of ['hunks']",
            "x: 'footprint' must be a positive integer",
            "x: 'model' must be one id without spaces",
            "x: 'effort' must be one of ['low', 'medium', 'high', 'xhigh']",
            "x: 'worktree' must be an absolute path",
            "x: 'session' must be a non-empty string without edges",
            "x: 'session_dir' must be a non-empty string without edges",
            "x: 'session' must be one token, with no whitespace",
            "x: 'session_dir' must not contain a newline",
            "x: 'stack' is only valid on an epic",
            "x: an entry of 'exclusive' is empty",
            "x: 'blocker' needs status = \"blocked\"",
            "x: empty blocker",
            "x: empty answer",
            "block 2: 'overlap' must be a string",
            "block 2: empty id",
            "block 2: empty title",
            f"block 2: {statuses}",
            "block 2: 'session_dir' needs a 'session'",
            "block 2: 'stack' must be a non-empty name or a number",
            "e: 'similar' must be a list of strings",
            "e: 'footprint' must be a positive integer",
            "e: 'session' must be a non-empty string without edges",
            "e: 'stack' must be a non-empty name or a number",
        ],
    )


def test_task_build_takes_every_given_key_as_is_and_keeps_its_defaults_apart():
    """Task.build keeps each given key, never shares a default list, and reads a numeric stack."""

    module = _load()
    block = {
        "id": "a",
        "type": "epic",
        "title": "t",
        "status": "blocked",
        "priority": "P0",
        "milestone": "m",
        "parent": "p",
        "deps": ["d"],
        "integrate_after": ["i"],
        "write_set": ["w"],
        "read_only": True,
        "footprint": 3,
        "exclusive": ["gpu"],
        "needs": ["cores"],
        "blocker": "human: q",
        "answer": "yes",
        "worktree": "/w",
        "branch": "b",
        "base": "main",
        "stack": "s",
        "overlap": "hunks",
        "model": "m1",
        "effort": "high",
        "session": "abc",
        "session_dir": "/s",
        "similar": ["x"],
        "done_at": "2026-01-02T10:00:00+01:00",
        "human_review": True,
        "waiting": "integration",
    }
    task = module.Task.build(7, block)
    expected = block | {"row": 7, "has_priority": True}
    assert dataclasses.asdict(task) == expected
    assert task.deps is block["deps"]
    assert module.KEYS == {"id", *module.COMPARED} == set(block)

    first, second = (module.Task.build(n, {"id": "a"}) for n in (1, 2))
    for key in ("deps", "exclusive", "similar"):
        getattr(first, key).append("x")
        assert getattr(second, key) == []
    assert second == module.Task.build(2, {"id": "a", "priority": "P2"})

    assert module.Task.build(1, {"id": "a", "write_set": []}).write_set == []
    assert module.Task.build(1, {"id": "a"}).write_set is None
    assert module.Task.build(1, {"id": "a", "stack": 2}).stack == "2"
    assert module.Task.build(1, {"id": "a", "overlap": "hunks"}).overlap == "hunks"


def _cli(cwd: pathlib.Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
        env={**os.environ, "COLUMNS": "80", **env},
    )


def test_cli_errors_exit_2_with_usage_on_stderr_or_json_on_stdout(tmp_path):
    """A usage error goes to stderr only, an invalid input to stdout as JSON only."""

    (tmp_path / "tasks.toml").write_text(TASKS)
    for args, error in [
        (
            ["frobnicate"],
            "ledger_graph.py: error: argument command: invalid choice: 'frobnicate'"
            " (choose from check, ready, epics, tree, board, resume, cleanup, next,"
            " explain, set, answer, event, register, attach, machine, compare)",
        ),
        ([], "ledger_graph.py: error: the following arguments are required: command"),
    ]:
        done = _cli(tmp_path, "tasks.toml", *args)
        assert done.returncode == 2
        assert done.stdout == ""
        assert done.stderr.startswith("usage: ledger_graph.py")
        assert done.stderr.splitlines()[-1] == error

    for text, args, errors in [
        (
            None,
            ["explain", "t1"],
            [
                "cannot read missing.toml: [Errno 2] No such file"
                " or directory: '{tmp}/missing.toml'"
            ],
        ),
        (TASKS, ["explain", "nope"], ["unknown id 'nope'"]),
        (
            TASKS,
            ["tree", "--max-blocked", "-1"],
            ["--max-blocked must not be negative"],
        ),
        (TASKS, ["tree", "--width", "0"], ["--width must be a positive integer"]),
    ]:
        tasks = "missing.toml" if text is None else "tasks.toml"
        if text is not None:
            (tmp_path / tasks).write_text(text)
        done = _cli(tmp_path, tasks, *args)
        assert done.returncode == 2
        assert done.stderr == ""
        expected = [e.replace("{tmp}", str(tmp_path.resolve())) for e in errors]
        assert json.loads(done.stdout) == {"ok": False, "errors": expected}
    assert done.stdout.endswith("}\n")


def test_cli_cleanup_exits_1_when_git_fails_on_stdout_only(fleet):
    repo, ledger = fleet
    done = _cli(
        repo, str(ledger), "cleanup", "--repo", str(repo), PATH=str(repo / "nowhere")
    )
    assert done.returncode == 1
    assert done.stderr == ""
    assert done.stdout.startswith("failed: cannot list the worktrees")
    assert done.stdout.count("\n") == 1


def test_cli_cleanup_apply_exits_1_when_wt_fails_on_stdout_only(fleet, tmp_path):
    repo, ledger = fleet
    ledger.write_text(
        "format = 1\n" + _task("merged", "done", tmp_path / "merged", "merged")
    )
    (tmp_path / "ORCHESTRATION.md").write_text("# Ledger\n")
    (tmp_path / "bin").mkdir()
    _fake_wt(tmp_path / "bin" / "wt", "echo refused >&2; exit 1")
    args = ["cleanup", "--repo", str(repo), "--tip", "main", "--apply"]
    path = f"{tmp_path / 'bin'}:{os.environ['PATH']}"
    done = _cli(repo, str(ledger), *args, PATH=path)
    assert done.returncode == 1
    assert done.stderr == ""
    assert "failed merged: refused" in done.stdout
    assert (
        "cleanup: failed merged: refused" in (tmp_path / "ORCHESTRATION.md").read_text()
    )


BLOCKED = _with(TASKS, "t2", status="blocked", blocker="human: which API?")
WORKTREE = _with(BLOCKED, "E1", worktree="/w/E1", branch="e1")


def test_tree_shows_the_branch_and_only_that_a_human_is_awaited(run, run_text):
    """next lists what waits and the nearest worktree, the tree only the branch and the marks."""

    text = _with(TASKS, "t2", status="blocked", blocker="event: review on 1a2b3c4")
    _, result = run(text, "next", *caps())
    assert result["awaiting_event"] == [{"id": "t2", "event": "review on 1a2b3c4"}]
    assert result["awaiting_human"] == []
    assert result["blocked"] == ["t2"]

    _, result = run(BLOCKED, "next", *caps())
    assert result["awaiting_human"] == [{"id": "t2", "question": "which API?"}]
    assert ids(result["start"]) == ["t3", "t4"]
    assert result["blocked"] == ["t2"]

    _, result = run(WORKTREE, "next", *caps())
    start = {t["id"]: (t["worktree"], t["branch"]) for t in result["start"]}
    assert start == {"t3": ("/w/E1", "e1"), "t4": (None, None)}

    _, result = run(_with(WORKTREE, "t3", worktree="/w/t3"), "next", *caps())
    assert (result["start"][0]["worktree"], result["start"][0]["branch"]) == (
        "/w/t3",
        "e1",
    )


STEERED = """format = 1

# The gate.
[[task]]
id = "g1"
title = "gate"      # the first one
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


@pytest.fixture
def steered(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(STEERED)
    return path


def _apply(path: pathlib.Path, writer: str, *args):
    """Call a writer of the script on a file read just now."""

    return getattr(_load(), writer)(path, *args, seen_mtime_ns=path.stat().st_mtime_ns)


def test_concurrent_writers_keep_the_file_valid(steered: pathlib.Path):
    import threading

    module = _load()
    failures: list[BaseException] = []

    def attempt(task_id: str, priority: str) -> bool:
        try:
            _apply(steered, "set_priority", task_id, priority)
        except ValueError as error:
            if "changed" not in str(error):
                failures.append(error)
            return "changed" not in str(error)
        return True

    def write(task_id: str, priority: str) -> None:
        if not any(attempt(task_id, priority) for _ in range(200)):
            failures.append(RuntimeError("never wrote"))

    threads = [
        threading.Thread(target=write, args=(task, priority))
        for task, priority in [("g1", "P0"), ("g2", "P1")] * 4
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not failures
    tasks, errors = module.parse(steered.read_text())
    assert not errors
    assert [t.priority for t in tasks] == ["P0", "P1"]
    assert not list(steered.parent.glob("*.tmp"))


def test_set_priority_writes_its_line_after_the_id_and_refuses_bad_input(
    steered: pathlib.Path, tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """set_priority keeps comments, logs, follows a symlink and writes nothing it refuses."""

    module = _load()
    # The CLI refuses an unknown id before the writer, so call the writer.
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        _apply(steered, "set_priority", "nope", "P0")
    for setter, args in [
        ("set_status", ("g1", "pending")),
        ("set_priority", ("g2", "P2")),
    ]:
        before = steered.stat().st_mtime_ns
        _apply(steered, setter, *args)
        assert steered.stat().st_mtime_ns == before
        assert steered.read_text() == STEERED
        assert not (steered.parent / "steering.log").exists()

    def boom(*_args, **_kwargs):
        msg = "disk full"
        raise OSError(msg)

    with monkeypatch.context() as patch:
        patch.setattr(module.os, "fsync", boom)
        with pytest.raises(OSError, match="disk full"):
            _apply(steered, "set_priority", "g2", "P0")
    assert steered.read_text() == STEERED
    assert not list(steered.parent.glob("*.tmp"))

    _apply(steered, "set_priority", "g1", "P1")
    _apply(steered, "set_priority", "g2", "P0")
    text = steered.read_text()
    assert "# The gate." in text and "# the first one" in text
    assert text.index('priority = "P0"') < text.index('deps = ["g1"]')
    assert not list(steered.parent.glob("*.tmp"))
    lines = text.splitlines()
    assert lines[lines.index('id = "g1"') + 1] == 'priority = "P1"'
    log = (steered.parent / "steering.log").read_text().splitlines()
    assert len(log) == 2
    assert log[1].endswith("g2 P2 -> P0")

    real = _fresh(tmp_path, "real")
    (real / "tasks.toml").write_text(STEERED)
    link = _fresh(tmp_path, "link") / "tasks.toml"
    link.symlink_to(real / "tasks.toml")
    _apply(link, "set_priority", "g2", "P1")
    assert link.is_symlink()
    assert 'priority = "P1"' in (real / "tasks.toml").read_text()
    assert (real / "steering.log").exists()
    assert not (link.parent / "steering.log").exists()

    steered.write_text(STEERED.replace("format = 1", "format = 2"))
    with pytest.raises(ValueError, match="unsupported format"):
        _apply(steered, "set_priority", "g2", "P0")


def test_a_setter_refuses_a_file_that_changed_meanwhile(
    steered: pathlib.Path, tmp_path: pathlib.Path
):
    """Every writer refuses a file whose mtime moved since it was read."""

    asked = _fresh(tmp_path, "asked") / "tasks.toml"
    asked.write_text(ASKED)
    for path, setter, args in [
        (steered, "set_priority", ("g2", "P0")),
        (steered, "set_status", ("g1", "done")),
        (steered, "set_human_review", ("g2", True)),
        (asked, "answer_blocker", ("g1", "B")),
    ]:
        before = path.read_text()
        with pytest.raises(ValueError, match="changed since it was read"):
            getattr(_load(), setter)(path, *args, seen_mtime_ns=1)
        assert path.read_text() == before


RUNNING_TASK = """format = 1

[[task]]
id = "w1"
title = "running"
status = "in_progress"
write_set = ["w"]
"""


def test_set_waiting_round_trips_and_a_status_change_clears_it(run, tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    code, result = run(RUNNING_TASK, "set", "w1", "waiting", "integration")
    assert (code, result) == (0, {"ok": True, "id": "w1", "waiting": "integration"})
    assert 'waiting = "integration"' in path.read_text()

    code, _ = run(path.read_text(), "set", "w1", "waiting", "")
    assert code == 0
    assert path.read_text() == RUNNING_TASK

    run(RUNNING_TASK, "set", "w1", "waiting", "restack")
    code, _ = run(path.read_text(), "set", "w1", "status", "done", "--no-next")
    assert code == 0
    assert "waiting" not in path.read_text()


def test_check_rejects_waiting_on_a_task_that_is_not_in_progress(run, tmp_path: pathlib.Path):
    text = RUNNING_TASK.replace("in_progress", "pending").replace(
        'write_set', 'waiting = "integration"\nwrite_set'
    )
    code, result = run(text, "check")
    assert code == 2
    assert "'waiting' needs status" in result["errors"][0]

    code, result = run(RUNNING_TASK.replace("in_progress", "pending"), "set", "w1", "waiting", "x")
    assert code == 2
    assert "only an in_progress task can wait" in result["errors"][0]


def test_tree_draws_a_hollow_dot_and_the_reason_for_a_waiting_task(run_text):
    waiting = RUNNING_TASK.replace("write_set", 'waiting = "integration"\nwrite_set')
    row = next(
        line for line in run_text(waiting, "tree", "--color", "never") if "w1" in line
    )
    assert "○" in row and "integration" in row and "●" not in row
    row = next(
        line for line in run_text(RUNNING_TASK, "tree", "--color", "never") if "w1" in line
    )
    assert "●" in row and "○" not in row
    legend = "\n".join(run_text(waiting, "tree", "--color", "never"))
    assert "○ (yellow) stalled" in legend


def test_set_done_writes_the_status_and_answers_with_next(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g1", "status", "done")
    assert code == 0
    assert list(result) == ["ok", "id", "status", "limits", "start", "skipped"]
    assert (result["ok"], result["id"], result["status"]) == (True, "g1", "done")
    assert ids(result["start"]) == ["g2"]
    text = (tmp_path / "tasks.toml").read_text()
    stamp = re.search(
        r'^done_at = "(\d{4}-\d\d-\d\dT[\d:]+[+-]\d\d:\d\d)"$', text, re.M
    )
    assert stamp
    expected = STEERED.replace('one\nstatus = "pending"', 'one\nstatus = "done"')
    assert text == _with(expected, "g1", done_at=stamp.group(1))
    log = (tmp_path / "steering.log").read_text().splitlines()
    assert log[0].endswith("g1 status pending -> done")
    assert run_after(run, tmp_path, "g1", "pending")[0] == 0
    assert "done_at" not in (tmp_path / "tasks.toml").read_text()


def test_integrate_after_lets_a_task_start_but_not_finish_and_check_refuses_a_bad_one(
    run, tmp_path: pathlib.Path
):
    text = _ledger(
        _block("a", "in_progress", read_only=True),
        _block("b", read_only=True, integrate_after=["a"]),
    )
    assert run(text, "ready")[1]["ready"] == ["b"]
    code, result = run(
        _with(text, "b", status="in_progress"), "set", "b", "status", "done"
    )
    assert code == 2
    assert "integrates after a" in result["errors"][0]
    run_after(run, tmp_path, "a", "done")
    assert run_after(run, tmp_path, "b", "done")[0] == 0

    for other, error in [
        ("x", "integrate_after 'x' is not another task"),
        ("b", "integrate_after cycle"),
    ]:
        code, result = run(
            _with(text, "a", status="pending", integrate_after=[other]), "check"
        )
        assert code == 2
        assert error in result["errors"][0]


def run_after(run, tmp_path: pathlib.Path, task_id: str, status: str):
    """Set a status on the file a test already wrote, without rewriting it."""

    out = io.StringIO()
    args = [str(tmp_path / "tasks.toml"), "set", task_id, "status", status]
    return _load().run(args, out), json.loads(out.getvalue())


def _done_blocks(*stamps: str | None) -> str:
    return _ledger(
        *(
            _block(f"t{n}", "done", read_only=True, done_at=s)
            for n, s in enumerate(stamps)
        )
    )


def test_done_tasks_sort_newest_first_by_done_at_then_unstamped_in_reverse_file_order():
    module = _load()
    text = _done_blocks(
        "2026-01-02T10:00:00+01:00",
        None,
        "2026-01-03T10:00:00+01:00",
        None,
        "2026-01-02T08:00:00+00:00",
    )
    tasks, errors = module.parse(text)
    assert not errors
    graph = module.Graph(tasks)
    # t4 is 09:00 at +01:00 on the 2nd, so it is older than t0 (10:00 +01:00).
    assert module.board_cards(graph, {"done"}) == ["t2", "t0", "t4", "t3", "t1"]
    ranked = module._ranked(graph, graph.order, ready=set())
    assert ranked == ["t2", "t0", "t4", "t3", "t1"]


def test_check_warns_about_a_done_task_without_done_at_and_rejects_a_bad_one(run_bare):
    code, result = run_bare(_done_blocks(None, "2026-01-02T10:00:00+01:00"), "check")
    assert code == 0
    assert [w for w in result["warnings"] if "done_at" in w] == [
        "t0: done without done_at, set it to when the task finished"
    ]
    code, result = run_bare(_done_blocks("yesterday"), "check")
    assert code == 2
    assert (
        "t0: 'done_at' must be an ISO 8601 time with an offset" in result["errors"][0]
    )
    assert run_bare(_done_blocks("2026-01-02T10:00:00"), "check")[0] == 2


def test_set_status_prints_the_change_or_next_and_refuses_a_bad_one(
    run, tmp_path: pathlib.Path
):
    """set prints the change, or next under the header caps, and refuses bad input.

    priority and status share one writer, so its refusals are tested once here.
    """

    for field, value, flags in [
        ("status", "in_progress", []),
        ("status", "done", ["--no-next"]),
        ("priority", "P0", []),
    ]:
        code, result = run(STEERED, "set", "g1", field, value, *flags)
        assert (code, result) == (0, {"ok": True, "id": "g1", field: value})
        assert f'{field} = "{value}"' in (tmp_path / "tasks.toml").read_text()

    # g1 is running and g2 waits on it: a pending g1 starts again, a blocked one not.
    text = _with(STEERED, "g1", status="in_progress")
    for status, start in [("pending", ["g1"]), ("blocked", [])]:
        code, result = run(text, "set", "g1", "status", status)
        assert (code, result["status"], result["skipped"]) == (0, status, [])
        assert ids(result["start"]) == start

    # w1 is done and its gpu is free, so w2 and the reviewers compete for the caps.
    _, free = run(PARALLEL, "set", "w1", "status", "done")
    assert ids(free["start"]) == ["w2", "r1", "r2"]
    assert free["limits"] == {"rw": None, "ro": None, "budget": None}
    assert free["skipped"] == []
    for cap, start, skipped in [
        ("rw = 1\n", ["w2", "r1", "r2"], None),
        ("ro = 1\n", ["w2", "r1"], ("r2", "no free read-only slot")),
        ("budget = 1\n", ["w2"], ("r1", "no budget left")),
    ]:
        capped_text = PARALLEL.replace("format = 1\n", f"format = 1\n{cap}", 1)
        _, capped = run(capped_text, "set", "w1", "status", "done")
        assert {k: v for k, v in capped["limits"].items() if v} == {cap.split()[0]: 1}
        assert ids(capped["start"]) == start
        assert not skipped or skipped in reasons(capped)

    for args, error in [
        (("g1", "status", "finished"), "unknown status 'finished'"),
        (("g2", "priority", "P9"), "unknown priority 'P9'"),
        (("nope", "status", "done"), "unknown id 'nope'"),
        (("g2", "title", "x"), "only 'priority', 'status' and 'waiting' can be set, not 'title'"),
        (("g2", "priority", "P0", "--no-next"), "--no-next only applies to status"),
    ]:
        code, result = run(STEERED, "set", *args)
        assert code == 2
        assert error in result["errors"][0]
        assert (tmp_path / "tasks.toml").read_text() == STEERED

    code, result = run(ASKED, "set", "g1", "status", "pending")
    assert code == 2
    assert "blocked on a human" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == ASKED


REVIEWING = _ledger(
    _block(
        "r1",
        "in_rev",
        title="review me",
        write_set=["r"],
        worktree="/w/r1",
        branch="r1",
        human_review=True,
    )
)


def test_set_human_review_writes_its_line_and_refuses_an_unknown_or_done_task(
    steered: pathlib.Path,
):
    _apply(steered, "set_human_review", "g2", True)
    assert steered.read_text() == STEERED.replace(
        'id = "g2"\n', 'id = "g2"\nhuman_review = true\n'
    )
    _apply(steered, "set_human_review", "g2", False)
    assert steered.read_text() == STEERED
    log = (steered.parent / "steering.log").read_text().splitlines()
    assert [line.split(maxsplit=1)[1] for line in log[-2:]] == [
        "g2 human_review marked",
        "g2 human_review cleared",
    ]
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        _apply(steered, "set_human_review", "nope", True)
    steered.write_text(before := _with(STEERED, "g1", status="done"))
    with pytest.raises(ValueError, match="'g1' is done"):
        _apply(steered, "set_human_review", "g1", True)
    assert steered.read_text() == before


def test_set_done_is_refused_on_a_task_marked_for_human_review(run, tmp_path):
    code, result = run(REVIEWING, "set", "r1", "status", "done")
    assert code == 2
    assert "marked for human review" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == REVIEWING
    assert run_after(run, tmp_path, "r1", "in_progress")[0] == 0


def test_next_awaiting_lists_a_task_in_review_with_its_command(run, tmp_path):
    """The worktree, else the repository root, else nowhere; base of task, then epic."""

    def reviews(text: str) -> dict:
        awaiting = run(text, "next", "--awaiting")[1]["awaiting_human"]
        return {a["id"]: a["review"] for a in awaiting}

    worktree = tmp_path / "w" / "r1"
    worktree.mkdir(parents=True)
    hunk = "hunk diff integration...r1"
    for where, review in [
        (worktree, f"cd {worktree} && {hunk}"),
        (tmp_path / "repo" / ".worktrees" / "r1", f"cd {tmp_path / 'repo'} && {hunk}"),
        ("/w/r1", hunk),
    ]:
        assert reviews(REVIEWING.replace("/w/r1", str(where))) == {"r1": review}
    assert reviews(_with(REVIEWING, "r1", status="pending")) == {}

    leaf = {"status": "in_rev", "parent": "e", "human_review": True}
    epic = _ledger(
        _epic("e", "in_rev", base="layer"),
        _block("own", **leaf, worktree="/w/own", branch="own", base="mine"),
        _block("inherit", **leaf, worktree="/w/inherit", branch="inherit"),
    )
    found = reviews(epic)
    assert found["own"].endswith("hunk diff mine...own")
    assert found["inherit"].endswith("hunk diff layer...inherit")


def test_check_refuses_a_bad_base_or_mark_and_warns_on_a_done_marked_task(run):
    for value, message in (("", "non-empty string"), ("a b", "no whitespace")):
        code, result = run(_with(REVIEWING, "r1", base=value), "check")
        assert code == 2
        assert any(message in e for e in result["errors"])
    code, result = run(_with(REVIEWING, "r1", status="done"), "check")
    assert code == 0
    assert [w for w in result["warnings"] if "human_review" in w] == [
        "r1: done but still marked human_review, the human clears the mark in the board"
    ]
    code, result = run(_with(REVIEWING, "r1", human_review="yes"), "check")
    assert code == 2
    assert "'human_review' must be a boolean" in result["errors"][0]


def test_set_status_drops_the_blocker_when_it_leaves_blocked(
    run, tmp_path: pathlib.Path
):
    text = ASKED.replace("human: option A or B?", "waits for the CI")
    code, _ = run(text, "set", "g1", "status", "pending", "--no-next")
    assert code == 0
    assert (tmp_path / "tasks.toml").read_text() == text.replace(
        'status = "blocked"\nblocker = "waits for the CI"\n', 'status = "pending"\n'
    )


def test_set_status_keeps_the_other_keys_comments_and_line_endings(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    text = STEERED.replace(
        'status = "pending"\nwrite_set = ["g"]',
        'status = "pending" # now\nbranch = "feat/g"\nwrite_set = ["g"]',
    ).replace("\n", "\r\n")
    path.write_bytes(text.encode())
    module.set_status(path, "g1", "done", seen_mtime_ns=path.stat().st_mtime_ns)
    written = path.read_bytes().decode()
    match = re.search(r'done_at = "([^"]+)"\r\n', written)
    assert match
    stamp = match.group(1)
    assert written == text.replace(
        'status = "pending" # now', 'status = "done" # now'
    ).replace('id = "g1"\r\n', f'id = "g1"\r\ndone_at = "{stamp}"\r\n', 1)


def test_set_status_warns_when_the_file_cannot_be_read_again(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    """The status is already written, so the failure must not hide it."""

    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(STEERED)

    def broken(_text: str):
        msg = "boom"
        raise ValueError(msg)

    monkeypatch.setattr(module, "read_limits", broken)
    out = io.StringIO()
    code = module.run([str(path), "set", "g1", "status", "done"], out)
    result = json.loads(out.getvalue())
    assert code == 0
    assert result == {
        "ok": True,
        "id": "g1",
        "status": "done",
        "warning": "status written, but next could not run: boom",
    }
    assert 'status = "done"' in path.read_text()


def test_set_priority_refuses_an_edit_inside_a_multiline_string(
    tmp_path: pathlib.Path,
):
    """A writer edits only its lines: comments, prefixes, CRLF and mode survive."""

    module = _load()
    path = _fresh(tmp_path, "header") / "tasks.toml"
    path.write_text(
        STEERED.replace('[[task]]\nid = "g2"', '[[task]] # note\nid = "g2"')
    )
    module.set_priority(path, "g2", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
    text = path.read_text()
    assert "[[task]] # note" in text
    assert 'priority = "P0"' in text

    path = _fresh(tmp_path, "prefix") / "tasks.toml"
    path.write_text(
        'format = 1\n[[task]]\nid = "g1"\ntitle = "one"\nstatus = "pending"\n'
        'priority = "P1"\nwrite_set = ["a"]\n\n[[task]]\nid = "g10"\ntitle = "ten"\n'
        'status = "pending"\npriority = "P3"\nwrite_set = ["b"]\n'
    )
    module.set_priority(path, "g10", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
    text = path.read_text()
    assert 'priority = "P1"' in text
    assert 'priority = "P0"' in text
    assert 'priority = "P3"' not in text

    path = _fresh(tmp_path, "comment") / "tasks.toml"
    path.write_text(STEERED.replace('priority = "P2"', 'priority = "P2"  # raised'))
    module.set_priority(path, "g2", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
    assert 'priority = "P0"  # raised' in path.read_text()

    for writer in ["set_priority", "answer_blocker"]:
        path = _fresh(tmp_path, f"crlf-{writer}") / "tasks.toml"
        if writer == "set_priority":
            source, expected = (
                STEERED,
                STEERED.replace('priority = "P2"', 'priority = "P0"'),
            )
            args = ("g2", "P0")
        else:
            source, expected = ASKED, ANSWERED
            args = ("g1", "use option B")
        path.write_bytes(source.replace("\n", "\r\n").encode())
        getattr(module, writer)(path, *args, seen_mtime_ns=path.stat().st_mtime_ns)
        assert path.read_bytes() == expected.replace("\n", "\r\n").encode()

    for writer, args in [
        ("set_priority", ("g2", "P0")),
        ("answer_blocker", ("g1", "B")),
    ]:
        path = _fresh(tmp_path, f"mode-{writer}") / "tasks.toml"
        path.write_text(STEERED if writer == "set_priority" else ASKED)
        path.chmod(0o600)
        getattr(module, writer)(path, *args, seen_mtime_ns=path.stat().st_mtime_ns)
        assert path.stat().st_mode & 0o777 == 0o600

    header = (
        'format = 1\n[[task]]\nid = "a"\ntitle = """\n[[task]]\nid = "b"\n"""\n'
        'priority = "P2"\nstatus = "pending"\nwrite_set = ["a"]\n'
    )
    key = (
        'format = 1\n[[task]]\nid = "a"\ntitle = """\npriority = "P9"\n"""\n'
        'status = "pending"\nwrite_set = ["a"]\n'
    )
    path = tmp_path / "tasks.toml"

    for text in (header, key):
        path.write_text(text)
        with pytest.raises(ValueError, match="more than the priority"):
            module.set_priority(path, "a", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
        assert path.read_text() == text
        assert not list(path.parent.glob("*.tmp"))


def test_set_priority_refuses_a_file_touched_while_it_is_rewritten(
    steered: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    module = _load()
    rewrite = module._rewrite_block

    def touch_then_rewrite(text: str, task_id: str, values: dict) -> str:
        os.utime(steered, ns=(0, 0))
        return rewrite(text, task_id, values)

    monkeypatch.setattr(module, "_rewrite_block", touch_then_rewrite)

    with pytest.raises(ValueError, match="changed while it was rewritten"):
        module.set_priority(
            steered, "g2", "P0", seen_mtime_ns=steered.stat().st_mtime_ns
        )

    assert steered.read_text() == STEERED
    assert not list(steered.parent.glob("*.tmp"))


ASKED = STEERED.replace(
    'status = "pending"\nwrite_set = ["g"]',
    'status = "blocked"\nblocker = "human: option A or B?"\nwrite_set = ["g"]',
)
ANSWERED = ASKED.replace('id = "g1"\n', 'id = "g1"\nanswer = "use option B"\n').replace(
    'status = "blocked"\nblocker = "human: option A or B?"\n', 'status = "pending"\n'
)


@pytest.fixture
def asked(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(ASKED)
    return path


def test_answer_blocker_writes_the_answer_or_refuses_a_bad_one(asked: pathlib.Path):
    """answer_blocker unblocks, round-trips, logs, keeps comments and refuses bad input."""

    module = _load()

    def answer(path: pathlib.Path, task_id: str, text: str) -> None:
        module.answer_blocker(
            path, task_id, text, seen_mtime_ns=path.stat().st_mtime_ns
        )

    ci = ASKED.replace("human: option A", "ci: option A")
    for text, task_id, error in [
        (ASKED, "g1", "contains a control character"),
        (ASKED, "nope", "unknown id 'nope'"),
        (ci, "g1", "not blocked on a human"),
    ]:
        asked.write_text(text)
        with pytest.raises(ValueError, match=error):
            answer(asked, task_id, "a\x07b" if "control" in error else "B")
        assert asked.read_text() == text

    asked.write_text(ASKED)
    answer(asked, "g1", "use option B")
    assert asked.read_text() == ANSWERED
    assert not list(asked.parent.glob("*.tmp"))
    log = (asked.parent / "steering.log").read_text().splitlines()
    assert len(log) == 1
    assert log[0].endswith("g1 answer")

    for text in ['take "B", not C:\\x', "perché B 🙂\na\n\tc"]:
        asked.write_text(ASKED)
        answer(asked, "g1", text)
        tasks, errors = module.parse(asked.read_text())
        assert (errors, tasks[0].answer, tasks[0].status, tasks[0].blocker) == (
            [],
            text,
            "pending",
            "",
        )

    asked.write_text(
        ASKED.replace('"blocked"', '"blocked"  # for now').replace(
            'B?"', 'B?"  # asked #2'
        )
    )
    answer(asked, "g1", "B")
    assert 'status = "pending"  # for now' in asked.read_text()
    assert "#2" not in asked.read_text()


def test_answer_command_writes_the_answer_or_refuses_a_task_not_blocked(run, tmp_path):
    code, result = run(ASKED, "answer", "g2", "B")
    assert code == 2
    assert "not blocked on a human" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == ASKED

    code, result = run(ASKED, "answer", "g1", "use option B")
    assert code == 0
    assert result == {"ok": True, "id": "g1", "answer": "use option B"}
    assert (tmp_path / "tasks.toml").read_text() == ANSWERED


def test_compare_reports_the_changes_and_simulates_the_schedule(compare):
    """compare lists the changed keys, then the waves, gates, rounds and stuck."""

    old = _plan(("a", "pending", []), ("b", "pending", ["a"]), ("c", "pending", []))
    new = _plan(("a", "done", []), ("b", "pending", []), ("d", "pending", ["b"]))
    code, result = compare(old, new)
    assert code == 0
    assert (result["added"], result["removed"]) == (["d"], ["c"])
    assert result["changed"] == [
        {"id": "a", "fields": {"status": ["pending", "done"]}},
        {"id": "b", "fields": {"deps": [["a"], []]}},
    ]

    p, r = "pending", "read_only"
    chain = _plan(("a", "done", []), ("b", p, ["a"]), ("c", p, ["b"]))
    gated = _plan(("a", p, []), ("g", p, ["a"], r), ("b", p, ["g"]), ("c", p, ["b"]))
    clash = _plan(("a", p, []), ("b", p, []), ("c", p, ["a"]))
    for old, new, key, before, after in [
        (chain, _with(chain, "c", deps=[]), "waves", [["b"], ["c"]], [["b", "c"]]),
        (chain, _with(chain, "c", deps=[]), "ready", ["b"], ["b", "c"]),
        (gated, _with(gated, "b", deps=["a"]), "gates", {"g": ["b", "c"]}, {}),
        (
            gated,
            _with(gated, "b", deps=["a"]),
            "rounds",
            [["a"], ["g"], ["b"], ["c"]],
            [["a"], ["b", "g"], ["c"]],
        ),
        (
            clash,
            _with(clash, "b", write_set=["a"]),
            "rounds",
            [["a", "b"], ["c"]],
            [["a"], ["b", "c"]],
        ),
    ]:
        result = compare(old, new)[1]
        assert (result["before"][key], result["after"][key]) == (before, after), key

    for rows, rounds, stuck in [
        ([("h", "blocked", []), ("c", "pending", ["h"])], [["b"]], ["h", "c"]),
        ([("x", "pending", [])], [["x"], ["b"]], []),
    ]:
        plan = _plan(("a", "in_progress", []), ("b", "pending", ["a"]), *rows)
        _, result = compare(plan, plan)
        assert (result["before"]["rounds"], result["before"]["stuck"]) == (
            rounds,
            stuck,
        )
        assert result["added"] == result["removed"] == result["changed"] == []
    assert result["before"]["ready"] == ["x"]

    plan = _plan(("a", "pending", []), ("d", "deferred", []), ("c", "pending", ["d"]))
    schedule = compare(plan, plan)[1]["before"]
    assert (schedule["waves"], schedule["stuck"]) == ([["a", "c"]], ["c"])
    assert "d" not in [i for wave in schedule["rounds"] for i in wave]

    # Epics never enter the waves or the rounds: open, nested and all done.
    after_epic = _block("b", deps=["E"])
    for tasks, waves in [
        (
            [_block("a", parent="E"), _block("c", deps=["a"]), after_epic],
            [["a"], ["c", "b"]],
        ),
        ([_epic("F", parent="E"), _block("a", parent="F"), after_epic], [["a"], ["b"]]),
        ([_block("a", "done", parent="E"), after_epic, _block("x")], [["b", "x"]]),
    ]:
        plan = _ledger(_epic("E"), *tasks)
        schedule = compare(plan, plan)[1]["before"]
        assert schedule["waves"] == schedule["rounds"] == waves
        assert schedule["stuck"] == []

    old = _ledger(_block("a"), _block("e"))
    new = _ledger(_block("a", parent="e", write_set=None, read_only=True), _epic("e"))
    a = {"parent": ["", "e"], "write_set": [["a"], None], "read_only": [False, True]}
    assert compare(old, new)[1]["changed"] == [
        {"id": "a", "fields": a},
        {"id": "e", "fields": {"type": ["task", "epic"], "write_set": [["e"], None]}},
    ]

    def unordered(*order: str) -> str:
        keys: dict[str, Any] = {
            k: list(order) for k in ("deps", "write_set", "exclusive")
        }
        return _ledger(_block("x"), _block("y"), _block("a", **keys))

    assert compare(unordered("x", "y"), unordered("y", "x"))[1]["changed"] == []


def test_compare_refuses_a_new_file_and_reports_the_header_and_a_new_priority(compare):
    code, result = compare(_ledger(_block("a")), 'format = 1\n[task]\nid = "x"\n')
    assert code == 2
    assert "new.toml: 'task' must be an array" in result["errors"][0]

    header = 'rw = {}\n[model]\nworker = "{}"\n[effort]\nworker = "low"\n'
    old = _ledger(_block("a"), header=header.format(2, "m1"))
    new = _ledger(_block("a"), header=header.format(3, "m2"))
    _, result = compare(old, new)
    assert result["header"] == {"rw": [2, 3], "model.worker": ["m1", "m2"]}
    assert result["changed"] == []
    assert compare(old, old)[1]["header"] == {}

    _, result = compare(_ledger(_block("a")), _ledger(_block("a", priority="P2")))
    assert result["changed"] == [{"id": "a", "fields": {"priority": [None, "P2"]}}]


def _plan(*rows: tuple) -> str:
    """A tasks.toml of leaves given as (id, status, deps, and "read_only" or nothing)."""

    return _ledger(
        *(_block(i, s, deps=d, read_only=True if k else None) for i, s, d, *k in rows)
    )


def test_check_warns_about_a_task_that_holds_up_too_many(run, run_text):
    """check, explain, tree and board count what each task holds up."""

    hub = _bottleneck_ledger()
    code, result = run(hub, "check")
    assert (code, result["warnings"]) == (
        0,
        [
            "g9 holds up 4 open tasks (limit 3): d1 (deps), d2 (deps), s1 (write_set), "
            "s2 (write_set)"
        ],
    )
    assert "warnings" not in run(hub, "check", "--max-blocked", "4")[1]
    code, result = run(hub, "check", "--max-blocked", "-1")
    assert code == 2 and "--max-blocked" in result["errors"][0]
    _, result = run(hub, "explain", "g9")
    assert "g9 holds up 4 open tasks" in result["warning"]
    assert run(hub, "explain", "d1")[1]["warning"] is None
    assert "blocks 4" not in "".join(run_text(hub, "board", "--max-blocked", "4"))

    x, ws = "x", "write_set"
    held = _ledger(
        *(_block(i, "running", write_set=[x]) for i in "ab"),
        _block("c", write_set=[x]),
    )
    behind = _ledger(
        _block("a", "running", write_set=[x]),
        *(_block(i, write_set=[x]) for i in "bf"),
        *(_block(i, deps=[d]) for i, d in ["cb", "db", "ec"]),
    )
    ties = _ledger(
        _block("low", write_set=[x]), _block("high", write_set=[x], priority="P0")
    )
    epic = _ledger(
        _epic("E"),
        _block("done", "done", parent="E", write_set=[x]),
        _block("later", parent="E", write_set=[x]),
    )
    for text, task, holds_up in [
        (hub, "g9", {"d1": "deps", "d2": "deps", "s1": ws, "s2": ws}),
        (hub, "d1", {"d2": "deps"}),
        (hub, "s1", {"s2": ws}),
        (behind, "a", {"b": ws, "f": ws, "c": "deps", "d": "deps", "e": "deps"}),
        (ties, "low", {}),
        (ties, "high", {"low": ws}),
        (_ledger(*(_block(i, "running", write_set=[x]) for i in "ab")), "a", {}),
        (held, "a", {"c": ws}),
        (epic, "E", {}),
    ]:
        assert run(text, "explain", task)[1]["holds_up"] == holds_up, (task, text)


def _chain(*rows: tuple[str, str, str]) -> str:
    """A ledger of leaves given as (id, status, extra keys)."""

    return "format = 1\n" + "".join(
        f'\n[[task]]\nid = "{i}"\ntitle = "{i}"\nstatus = "{status}"\n{extra}\n'
        for i, status, extra in rows
    )


def _bottleneck_ledger() -> str:
    """One running task that holds two tasks by deps and two by a shared file."""

    shared = ["lib/shared.py"]
    return _ledger(
        _block("g9", "running", write_set=shared),
        _block("d1", deps=["g9"]),
        _block("d2", deps=["d1"]),
        _block("s1", write_set=shared),
        _block("s2", write_set=shared),
    )


STACKED = (
    "format = 1\n"
    + _epic("S1", stack="auth")
    + _block("a", "done", parent="S1", branch="auth-a", worktree="/w/a")
    + _block("b", parent="S1", branch="auth-b")
    + _block("c", parent="S1")
    + _epic("S2", stack="12")
    + _block("d", parent="S2", branch="d")
    + _block("e")
)


def test_resume_groups_the_branches_by_stack(tmp_path):
    """resume lists the stacks, a numeric one equal to its name, none without one."""

    assert "STACKS" not in "".join(_resume_text(_fresh(tmp_path, "no"), TASKS))
    lines = _resume_text(tmp_path, STACKED.replace('stack = "12"', "stack = 12"))
    start = lines.index("STACKS (2)")
    assert lines[start : start + 7] == [
        "STACKS (2)",
        "  auth",
        "    a  done  /w/a  auth-a",
        "    b  pending  -  auth-b",
        "  12",
        "    d  pending  -  d",
        "NEXT (none in the ledger)",
    ]


def test_check_refuses_a_stack_whose_leaves_share_a_branch(run, tmp_path):
    """check refuses a bad stack key, a stack on a task, a relative worktree."""

    def nested(levels: int) -> str:
        return _ledger(
            *(_epic(f"E{n}", parent=f"E{n - 1}" if n else None) for n in range(levels))
        )

    noshare = STACKED.replace('"auth-a"\n', '"x"\n').replace('"auth-b"', '"x"')
    for text, code, error in [
        (
            STACKED.replace('stack = "auth"', "stack = 0"),
            2,
            "non-empty name or a number",
        ),
        (
            STACKED.replace('id = "e"', 'id = "e"\nstack = "x"'),
            2,
            "e: 'stack' is only valid on an epic",
        ),
        (
            _ledger(_task("a", "pending", "rel/a", "a")),
            2,
            "'worktree' must be an absolute path",
        ),
        (noshare, 2, "b: shares branch 'x' with a in stack 'auth'"),
        (STACKED.replace('branch = "d"', 'branch = "auth-a"'), 0, None),
        (nested(9), 0, None),
        (nested(10), 0, "E9: nests 9 levels deep, GitHub sub-issues stop at 8"),
    ]:
        got, result = run(text, "check")
        found = result.get("errors", []) + result.get("warnings", [])
        assert got == code
        assert any(error in e for e in found) if error else "errors" not in result


def _git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


def _task(name, status, worktree, branch):
    return _block(name, status, worktree=str(worktree), branch=branch)


@pytest.fixture
def fleet(tmp_path, monkeypatch):
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{key}_NAME", "t")
        monkeypatch.setenv(f"{key}_EMAIL", "t@example.com")

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "root")
    for name in ("merged", "ahead", "dirty", "open", "stranger", "swapped", "gone"):
        _git(repo, "worktree", "add", "-q", "-b", name, str(tmp_path / name))

    (tmp_path / "ahead" / "f").write_text("x")
    _git(tmp_path / "ahead", "add", "f")
    _git(tmp_path / "ahead", "commit", "-q", "-m", "ahead")
    (tmp_path / "dirty" / "f").write_text("x")
    _git(tmp_path / "swapped", "switch", "-q", "-c", "other")
    _git(repo, "worktree", "remove", str(tmp_path / "gone"))

    states = {
        "merged": "done",
        "ahead": "done",
        "dirty": "done",
        "open": "pending",
        "swapped": "done",
        "gone": "done",
    }
    ledger = tmp_path / "tasks.toml"
    ledger.write_text(
        "format = 1\n"
        + "".join(_task(n, s, tmp_path / n, n) for n, s in states.items())
    )
    return repo, ledger


def _cleanup(repo, ledger, *args):
    out = io.StringIO()
    code = _load().run([str(ledger), "cleanup", "--repo", str(repo), *args], out)
    return code, out.getvalue()


def _fake_wt(path, script):
    path.write_text(f"#!/bin/sh\n{script}\n")
    path.chmod(0o755)
    return str(path)


def test_cleanup_classifies_each_named_worktree(fleet, monkeypatch, tmp_path):
    """cleanup sorts the named worktrees and keeps what must stay, applying nothing."""

    repo, ledger = fleet
    code, text = _cleanup(repo, ledger, "--tip", "main")
    lines = text.splitlines()
    assert code == 0
    assert lines[:2] == ["remove (1)", "  wt remove merged"]
    for name, reason in [
        ("ahead", "commits that 'main'"),
        ("dirty", "uncommitted changes"),
        ("open", "open is not done"),
        ("swapped", "'other'"),
        ("gone", "worktree is gone"),
    ]:
        assert any(f" {name} at " in x and reason in x for x in lines), name
    assert {"keep (3)", "orphan (2)"} <= set(lines)
    assert "stranger" not in text
    assert (repo.parent / "merged").is_dir()

    main, merged = repo, repo.parent / "merged"
    for tasks, reason in [
        ([("m", "done", main, "main")], "main checkout"),
        ([("r", "running", merged, "merged")], "r is not done"),
        (
            [("s1", "done", merged, "merged"), ("s2", "pending", merged, "merged")],
            "s2 is not done",
        ),
    ]:
        other = tmp_path / "other.toml"
        other.write_text(_ledger(*(_task(*t) for t in tasks)))
        text = _cleanup(repo, other, "--tip", "main")[1]
        assert "wt remove" not in text and reason in text, text

    with monkeypatch.context() as patch:
        patch.chdir(merged)
        _, text = _cleanup(repo, ledger, "--tip", "main")
        assert "wt remove" not in text and "current directory" in text

    # merged is measured against the tip, not the current directory.
    with monkeypatch.context() as patch:
        patch.chdir(repo.parent / "dirty")
        assert "wt remove ahead" not in _cleanup(repo, ledger)[1]
        assert "wt remove ahead" in _cleanup(repo, ledger, "--tip", "ahead")[1]
        (repo.parent / "ORCHESTRATION.md").write_text("Stack tip: ahead\n")
        assert "wt remove ahead" in _cleanup(repo, ledger)[1]
        (repo.parent / "ORCHESTRATION.md").unlink()

    # A cherry-picked branch is merged, also from outside the repo.
    _git(repo, "cherry-pick", "ahead")
    monkeypatch.chdir(repo.parent.parent)
    text = _cleanup(repo.parent / "nowhere", ledger, "--tip", "main")[1]
    assert "wt remove ahead" in text
    assert "wt remove merged" in text
    assert "uncommitted" in text


def test_cleanup_apply_removes_only_the_remove_class_and_goes_on_after_a_failure(
    fleet, monkeypatch
):
    """A missing wt, then a refusal of merged, then a plain removal, on one fleet."""

    repo, ledger = fleet
    base = repo.parent
    _git(repo, "worktree", "add", "-q", "-b", "late", str(base / "late"))
    ledger.write_text(ledger.read_text() + _task("late", "done", base / "late", "late"))
    (base / "ORCHESTRATION.md").write_text("# Ledger\n")
    module = _load()
    remove = 'git worktree remove "../$4"'
    refuse = '[ "$4" = merged ] && { echo refused >&2; exit 1; }; ' + remove
    args = [str(ledger), "cleanup", "--repo", str(repo), "--tip", "main", "--apply"]
    for wt, code, said in [
        (str(base / "no-such-wt"), 1, ["failed merged: cannot run"]),
        (_fake_wt(base / "wt1", refuse), 1, ["failed merged: refused", "removed late"]),
        (_fake_wt(base / "wt2", remove), 0, [f"removed merged at {base / 'merged'}"]),
    ]:
        monkeypatch.setattr(module, "WT", wt)
        out = io.StringIO()
        assert module.run(args, out) == code
        assert all(s in out.getvalue() for s in said), out.getvalue()
    assert not (base / "merged").exists() and not (base / "late").exists()
    for kept in ("ahead", "dirty", "open", "swapped"):
        assert (base / kept).is_dir()
    log = (base / "ORCHESTRATION.md").read_text()
    assert "cleanup: failed merged: refused" in log
    assert "cleanup: removed merged" in log

    monkeypatch.setenv("PATH", str(base / "nowhere"))
    code, text = _cleanup(repo, ledger)
    assert code == 1 and text.startswith("failed: cannot list the worktrees")


def test_cleanup_apply_classifies_again_before_each_removal(fleet, monkeypatch):
    repo, ledger = fleet
    module = _load()
    tasks, _ = module.parse(ledger.read_text())
    graph = module.Graph(tasks)
    found = module.cleanup(graph, repo=repo, tip="main")
    (repo.parent / "merged" / "late").write_text("x")
    monkeypatch.setattr(module, "WT", _fake_wt(repo.parent / "wt", "exit 0"))

    out = io.StringIO()
    code = module.cleanup_apply(
        graph, found, repo=repo, tip="main", ledger=None, out=out
    )
    assert code == 0
    assert "skipped merged: now keep, uncommitted changes" in out.getvalue()


def test_next_and_resume_count_the_remove_candidates(fleet, monkeypatch):
    repo, ledger = fleet
    monkeypatch.chdir(repo)
    module = _load()

    out = io.StringIO()
    module.run([str(ledger), "next", *caps()], out)
    assert json.loads(out.getvalue())["cleanup"] == {"remove": 1, "warning": None}

    out = io.StringIO()
    module.run([str(ledger), "resume"], out)
    assert "CLEANUP 1 worktrees can be removed" in out.getvalue()
    assert "WARNING" not in out.getvalue()
    note = module.cleanup_note(9, 8)
    assert note and "WARNING: more than 8" in note


def _hunks(*specs: tuple[str, str, str]) -> str:
    """Leaves with their own worktree, the extra lines as the third item."""

    return _chain(
        *(
            (i, s, f'worktree = "/wt/{i}"\nbranch = "{i}"\n{extra}')
            for i, s, extra in specs
        )
    )


HUNKS = 'overlap = "hunks"\nwrite_set = ["x.py"]'


def test_overlap_needs_the_key_on_both_tasks_and_two_worktrees(run):
    """overlap = "hunks" lets two worktrees share a file, and nothing else."""

    pair = _hunks(("a", "pending", HUNKS), ("b", "pending", HUNKS))
    _, result = run(pair, "next", *caps())
    assert ids(result["start"]) == ["a", "b"]
    assert result["start"][1]["overlaps"] == ["a"]
    assert result["skipped"] == []
    assert ids(run(pair, "next", *caps(rw=1))[1]["start"]) == ["a"]

    code, result = run(pair, "check")
    assert code == 0 and len(result["notes"]) == 1
    assert result["notes"][0].startswith("a and b:")
    plain = 'write_set = ["x.py"]'
    assert "notes" not in run(_hunks(("a", "pending", plain)), "check")[1]

    # Pending tasks without a worktree yet still start together.
    ledger = _chain(("a", "pending", HUNKS), ("b", "pending", HUNKS))
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["a", "b"]

    ledger = _hunks(("a", "running", HUNKS), ("b", "pending", HUNKS))
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["b"]
    assert result["start"][0]["overlaps"] == ["a"]

    ledger = _hunks(
        ("a", "pending", HUNKS + '\nexclusive = ["db"]'),
        ("b", "pending", HUNKS + '\nexclusive = ["db"]'),
    )
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["a"]
    assert result["skipped"][0]["reason"] == "exclusive resource"

    for value, message in [
        ('"all"', "'overlap' must be one of ['hunks']"),
        ("3", "'overlap' must be a string"),
    ]:
        ledger = _hunks(("a", "pending", f'overlap = {value}\nwrite_set = ["x.py"]'))
        code, result = run(ledger, "check")
        assert code == 2
        assert message in result["errors"][0]


def test_overlap_hunks_is_not_a_write_set_hold(run):
    """explain holds a sharer only when the overlap is not on purpose."""

    ledger = _hunks(("a", "running", HUNKS), *((i, "pending", HUNKS) for i in "bcde"))
    _, result = run(ledger, "explain", "a")
    assert result["holds_up"] == {}
    assert result["warning"] is None
    assert set(result["overlaps_on_purpose"]) == {"b", "c", "d", "e"}

    # One task only, the same worktree, or a running task without one: a conflict.
    for ledger in [
        _hunks(("a", "running", HUNKS), ("b", "pending", 'write_set = ["x.py"]')),
        _chain(
            ("a", "running", 'worktree = "/wt/a"\n' + HUNKS),
            ("b", "pending", 'worktree = "/wt/a"\n' + HUNKS),
        ),
        _chain(("a", "running", HUNKS), ("b", "pending", HUNKS)),
    ]:
        _, result = run(ledger, "explain", "a")
        assert result["holds_up"] == {"b": "write_set"}
        assert result["overlaps_on_purpose"] == {}
        _, result = run(ledger, "next", *caps())
        assert [s["reason"] for s in result["skipped"]] == ["write set conflict"]

    ledger = _hunks(
        ("a", "running", HUNKS), ("r", "pending", 'overlap = "hunks"\nread_only = true')
    )
    _, result = run(ledger, "explain", "a")
    assert result["overlaps_on_purpose"] == {}
    assert [t["id"] for t in run(ledger, "next", *caps())[1]["start"]] == ["r"]

    # A parallel sharer still holds the tasks behind it.
    ledger = _hunks(
        ("a", "running", HUNKS),
        ("b", "pending", HUNKS),
        ("c", "pending", 'deps = ["b"]\nwrite_set = ["c.py"]'),
    )
    _, result = run(ledger, "explain", "a")
    assert result["holds_up"] == {}
    _, b = run(ledger, "explain", "b")
    assert b["holds_up"] == {"c": "deps"}


def test_check_warns_about_a_missing_brief_acceptance_or_recommendation(
    run_bare, tmp_path
):
    """Open leaves need briefs/<id>.md with Acceptance, an inbox file a Recommendation."""

    pair = _plan(("a", "in_progress", []), ("b", "pending", ["a"]))
    ready = _plan(("a", "pending", []), ("b", "pending", ["a"]))
    epic = _ledger(_epic("E", "in_progress"), _block("a", parent="E"))
    done = _ledger(_epic("E"), _block("a", "done", parent="E", done_at="2026-01-02"))
    asked = _ledger(_block("a", "blocked", blocker="human: which one?"))
    ok, bare = "## Acceptance\n", "## Change\nAcceptance is fine.\n"
    no_brief = "without briefs/a.md next to tasks.toml"
    for plan, files, warnings in [
        (pair, {}, [f"a: in progress {no_brief}"]),
        (ready, {}, [f"a: ready {no_brief}"]),
        (ready, {"briefs/a.md": ok}, []),
        (epic, {"briefs/a.md": ok}, []),
        (
            pair,
            {"briefs/a.md": bare, "briefs/b.md": "## Change\n"},
            [f"{i}: briefs/{i}.md has no Acceptance heading" for i in "ab"],
        ),
        (done, {"briefs/a.md": bare, "briefs/E.md": bare}, []),
        (done, {}, []),
        (
            asked,
            {"briefs/a.md": ok, "inbox/a.md": "## Question\n\n## Options\n"},
            ["a: inbox/a.md has no Recommendation heading"],
        ),
        (asked, {"briefs/a.md": ok, "inbox/a.md": "## Raccomandazione\n"}, []),
    ]:
        root = _fresh(tmp_path, str(len(list(tmp_path.iterdir()))))
        for name, body in files.items():
            (root / name).parent.mkdir(exist_ok=True)
            (root / name).write_text(body)
        (root / "tasks.toml").write_text(plan)
        out = io.StringIO()
        _load().run([str(root / "tasks.toml"), "check"], out)
        assert json.loads(out.getvalue()).get("warnings", []) == warnings, files


def _alike(first: str, second: str, ws=("a", "b"), **keys) -> str:
    """Two open tasks with these titles; ws gives each its write set."""

    a, b = ([w] if isinstance(w, str) else w for w in ws)
    return _ledger(
        _block("k", title=first, write_set=a, **keys),
        _block("l", title=second, write_set=b),
    )


def test_check_warns_about_a_long_title_or_two_open_tasks_that_look_alike(run):
    """A look-alike pair warns unless related, docs only or both hunks."""

    for size, count in ((60, 0), (61, 1)):
        _, result = run(_ledger(_block("a", title="x" * size)), "check")
        assert len(result.get("warnings", [])) == count
    assert "a: title has 61 characters" in result["warnings"][0]

    _, result = run(
        _alike("Kanban columns on the board", "Board kanban columns"), "check"
    )
    assert result["warnings"][0].startswith("k and l look alike")

    kanban = "Kanban columns"
    same, doc, docs = ("s/x.py", "s/x.py"), ("g.md", "g.md"), (["g.md", "A.md"],) * 2
    for text, count in [
        (_alike(kanban, kanban, deps=["l"]), 0),
        (_alike(kanban, kanban, similar=["l"]), 0),
        (_alike(kanban, kanban, status="deferred"), 0),
        (_alike(kanban, kanban, deps=["m"]) + _block("m", deps=["l"]), 0),
        (_alike("Add a cache", "Fix the parser"), 0),
        (_alike("Add a cache", "Fix the parser", same), 0),
        (_alike("Epic empty bar shows done", "Show deferred rows", same), 0),
        (_alike("Add token cache", "Fix token cache", same), 1),
        (_alike("Add token cache", "Fix token cache", same, overlap="hunks"), 1),
        (_alike("Add token cache", "Fix token cache", doc), 0),
        (_alike("Add token cache", "Fix token cache", docs), 1),
    ]:
        assert len(run(text, "check")[1].get("warnings", [])) == count, text
    both = _alike("Add token cache", "Fix token cache", same, overlap="hunks")
    both = _with(both, "l", overlap="hunks")
    assert "warnings" not in run(both, "check")[1]

    for keys, message in [
        ({"similar": ["nope"]}, "similar"),
        ({"similar": ["k"]}, "similar"),
        ({"session": "a b"}, "'session' must be one token"),
        ({"session": "a", "session_dir": "/x\ny"}, "must not contain a newline"),
    ]:
        code, result = run(_alike("Alpha", "Beta", **keys), "check")
        assert code == 2 and any(message in e for e in result["errors"]), keys
    assert (
        run(_alike("Alpha", "Beta", session="a", session_dir="/my dir"), "check")[0]
        == 0
    )


PARKED = _ledger(
    _epic("E1", title="work"),
    _block("a", title="active", parent="E1", read_only=True),
    _block("later", "deferred", title="parked", parent="E1", priority="P0"),
    _block("after", title="behind parked", parent="E1", deps=["later"], read_only=True),
    _block("w", title="waiting", parent="E1", deps=["a"], read_only=True),
    _epic("E2", title="only parked"),
    _block("p2", "deferred", title="parked too", parent="E2", read_only=True),
)


def test_a_deferred_task_is_parked_never_started_and_holds_nothing(run, tmp_path):
    _, result = run(PARKED, "next", *caps())
    assert ids(result["start"]) == ["a"]
    assert result["skipped"] == result["running"] == []
    assert {"id": "after", "blocked_by": ["later"]} in result["waiting"]
    assert "later" not in ids(result["waiting"])

    _, result = run(PARKED, "explain", "after")
    assert (result["ready"], result["blocked_by"]) == (False, ["later"])
    _, parked = run(PARKED, "explain", "later")
    assert parked["status"] == "deferred"
    assert (parked["ready"], parked["holds_up"], parked["warning"]) == (False, {}, None)

    assert run(PARKED, "check")[0] == 0
    code, result = run(PARKED, "set", "a", "status", "deferred")
    assert (code, result["ok"]) == (0, True)
    assert 'status = "deferred"' in (tmp_path / "tasks.toml").read_text()


def test_event_appends_lines_and_prints_them(run, tmp_path: pathlib.Path):
    code, result = run(TASKS, "event", "t1", "commit", "abc1234 first")
    assert code == 0 and result["event"]["task"] == "t1"
    assert datetime.datetime.fromisoformat(result["event"]["ts"]).utcoffset() == (
        datetime.timedelta(0)
    )
    run(TASKS, "event", "t1", "verdict", "ok")
    log = tmp_path / "events" / "t1.jsonl"
    rows = [json.loads(line) for line in log.read_text().splitlines()]
    assert [r["event"] for r in rows] == ["commit", "verdict"]
    assert all({"ts", "task", "event", "detail"} <= set(r) for r in rows)
    code, shown = run(TASKS, "event", "t1")
    assert code == 0 and shown["events"] == rows


def test_event_refuses_an_unknown_task_and_a_status_ignores_the_log(
    run, tmp_path: pathlib.Path
):
    code, result = run(TASKS, "event", "nope", "agent", "x")
    assert code == 2 and not result["ok"]
    assert not (tmp_path / "events").exists()
    (tmp_path / "events").mkdir()
    (tmp_path / "events" / "t1.jsonl").write_text("garbage\n")
    code, _ = run(TASKS, "set", "t1", "status", "done")
    assert code == 0


def test_resume_shows_the_last_event_of_a_running_task(tmp_path: pathlib.Path):
    row = '{{"ts": "2026-01-0{}T00:00:00+00:00", "task": "r1", "event": "{}", "detail": "{}"}}\n'
    raw = row.format(1, "agent", "one") + "torn line\n" + row.format(2, "commit", "abc")
    line, _ = _attempt_resume(tmp_path, [], raw)
    assert "last=2026-01-02T00:00:00+00:00 commit abc" in line


def test_resume_shows_worktree_state(tmp_path: pathlib.Path, monkeypatch):
    for key in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{key}_NAME", "t")
        monkeypatch.setenv(f"{key}_EMAIL", "t@t")
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "integration")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    for name in ("clean", "dirty", "ahead"):
        _git(repo, "worktree", "add", "-q", "-b", name, str(tmp_path / name))
    (tmp_path / "dirty" / "f").write_text("x")
    _git(tmp_path / "ahead", "commit", "-q", "--allow-empty", "-m", "more")

    names = ("clean", "dirty", "ahead", "missing")
    text = _ledger(*(_task(n, "running", tmp_path / n, n) for n in names))
    line = {x.split()[0]: x for x in _resume_text(tmp_path, text) if x[:2] == "  "}
    assert "clean ahead 0" in line["clean"]
    assert "dirty 1 ahead 0" in line["dirty"]
    assert "clean ahead 1" in line["ahead"]
    assert line["missing"].split(" branch")[0].endswith("missing")


def _subcommands(module):
    parser = module.build_parser()
    action = next(a for a in parser._actions if a.choices and a.dest == "command")
    return action.choices


def _keys(value):
    if isinstance(value, dict):
        for key, inner in value.items():
            yield key
            yield from _keys(inner)
    elif isinstance(value, list):
        for inner in value:
            yield from _keys(inner)


def test_the_contract_of_every_command_names_the_keys_of_its_answer(
    run, tmp_path: pathlib.Path
):
    module = _load()
    commands = _subcommands(module)
    assert set(commands) == set(module.COMMAND_CONTRACTS)

    # Every top-level key of a JSON answer, and the keys inside it, are named in
    # the contract of that command, on a ledger where every optional key shows.
    rich = _ledger(
        _block(
            "r1",
            "running",
            write_set=["shared"],
            session="abc",
            session_dir="/s",
            worktree="/w",
            branch="b",
            overlap="hunks",
        ),
        _block(
            "s1",
            write_set=["shared"],
            overlap="hunks",
            model="m",
            effort="high",
            session="def",
            session_dir="/t",
        ),
        _block("s2", write_set=["shared"]),
        _block("s3", deps=["s2"]),
        _block("h1", "blocked", blocker="human: which?"),
    )
    samples = {
        "check": run(rich, "check"),
        "ready": run(rich, "ready"),
        "epics": run(rich, "epics"),
        "next": run(rich, "next", *caps()),
        "explain": run(rich, "explain", "s1"),
        "set": run(rich, "set", "r1", "status", "done"),
        "answer": run(rich, "answer", "h1", "ok"),
        "compare": run(TASKS, "compare", str(tmp_path / "tasks.toml")),
    }
    assert {"session", "overlaps", "model", "effort"} <= set(_keys(samples["next"][1]))
    assert {"with", "detail"} <= set(_keys(samples["set"][1]))
    for name, (_, result) in samples.items():
        for key in _keys(result):
            # The ids of the sample tasks are data, not keys.
            if (
                key.isidentifier()
                and key.islower()
                and not re.fullmatch(r"[rt]?s?\d", key)
            ):
                assert re.search(rf"\b{key}\b", module.COMMAND_CONTRACTS[name]), (
                    f"{name} contract misses the key {key}"
                )


def _attempt_resume(
    tmp_path: pathlib.Path, rows: list[str], raw: str = ""
) -> tuple[str, str]:
    """The resume row of a running r1 and its history, after a raw log and rows."""

    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(_ledger(_block("r1", "running", title="running")))
    if raw:
        (tmp_path / "events").mkdir()
        (tmp_path / "events" / "r1.jsonl").write_text(raw)
    for row in rows:
        module.append_event(tasks, "r1", *row.split("|", 1))
    text = module.resume(
        module.Graph(module.parse(tasks.read_text())[0]),
        limits=module.Limits(rw=None, ro=None, budget=None),
        tasks_path=tasks,
    )
    line = next(x for x in text.splitlines() if x.startswith("  r1"))
    return line, "\n".join(
        module.format_event(e) for e in module.read_events(tasks, "r1")
    )


def test_launch_and_return_events_render_as_one_line_each(tmp_path: pathlib.Path):
    line, history = _attempt_resume(
        tmp_path,
        [
            "launch|mode=background agent=a1 model=m host=box pane=p7 round=1",
            'return|{"verdict": "ok", "commit": "abc1234"}',
            "note|free text here",
        ],
    )
    lines = history.splitlines()
    assert lines[0].endswith(
        "launch \u00b7 mode=background \u00b7 agent=a1 \u00b7 model=m \u00b7 host=box \u00b7 pane=p7 \u00b7 round=1"
    )
    assert lines[1].endswith("return \u00b7 verdict=ok \u00b7 commit=abc1234")
    assert lines[2].endswith("note free text here")
    assert "attempt=finished" in line


def test_resume_reports_a_launch_without_a_return_as_orphan(tmp_path: pathlib.Path):
    module = _load()
    old = "2020-01-01T00:00:00+00:00"
    events = [
        {"ts": old, "event": "launch", "detail": "host=box pane=p7"},
        {"ts": old, "event": "heartbeat", "detail": ""},
    ]
    assert module.attempt_state(events) == "orphan"
    now = module.datetime.datetime.now(module.datetime.timezone.utc).isoformat()
    fresh = [*events, {"ts": now, "event": "heartbeat", "detail": ""}]
    assert module.attempt_state(fresh) == "alive"
    line, _ = _attempt_resume(tmp_path, ["launch|host=box pane=p7"])
    assert "attempt=alive host=box pane=p7" in line


ANSWERED_TASKS = _with(TASKS, "t2", answer="use A")


def _log_answer(tmp_path, hours: float, task_id="t2"):
    when = datetime.datetime.now().astimezone() - datetime.timedelta(hours=hours)
    with (tmp_path / "steering.log").open("a") as log:
        log.write(f"{when.isoformat(timespec='seconds')} {task_id} answer\n")


def test_next_lists_a_new_answer_once_and_peek_keeps_it(run, tmp_path):
    """Without a stamp only the last 24 hours count; then once, by the stamp."""

    def answered(*args: str, text: str = ANSWERED_TASKS) -> list:
        return ids(run(text, "next", *args, *caps())[1]["answered"])

    _log_answer(tmp_path, 30, "t3")
    _log_answer(tmp_path, 23)
    old = _with(ANSWERED_TASKS, "t3", answer="old")
    assert answered("--peek", text=old) == answered("--peek", text=old) == ["t2"]
    assert not (tmp_path / ".next-seen").exists()
    first = run(ANSWERED_TASKS, "next", *caps())[1]["answered"]
    assert (first[0]["answer"], first[0]["status"]) == ("use A", "pending")
    assert answered() == []
    _log_answer(tmp_path, 1)
    assert answered() == ["t2"], "an answer logged after the last call"
    _log_answer(tmp_path, 0)
    assert answered(text=TASKS) == []


def test_next_leaves_the_stamp_when_a_later_step_fails(tmp_path, monkeypatch):
    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(ANSWERED_TASKS)
    _log_answer(tmp_path, 0)

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "removable_count", boom)
    with pytest.raises(RuntimeError):
        module.run([str(tasks), "next", *caps()], io.StringIO())
    assert not (tmp_path / ".next-seen").exists()


REPLANNING = _ledger(_epic("E1", title="epic"), _block("a", parent="E1"), _block("b"))


def test_write_replan_request_writes_one_file_and_leaves_the_ledger_alone(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REPLANNING)
    before = path.stat()

    request = module.write_replan_request(
        path, "epic", "E1", ' split "a" \u00e8 ', seen_mtime_ns=before.st_mtime_ns
    )

    assert request.parent == tmp_path / "replan-requests"
    assert re.fullmatch(r"\d{8}T\d{6}Z\.toml", request.name)
    assert tomllib.loads(request.read_text()) == {
        "scope": "epic",
        "epic": "E1",
        "note": 'split "a" \u00e8',
    }
    assert [p.name for p in request.parent.iterdir()] == [request.name]
    assert path.read_text() == REPLANNING
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert not (tmp_path / "steering.log").exists()


def test_write_replan_request_refuses_a_bad_request_and_writes_nothing(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REPLANNING)
    seen = path.stat().st_mtime_ns

    for args, message in [
        (("everything", "", ""), "unknown scope"),
        (("epic", "a", ""), "not an epic"),
        (("remaining", "E1", ""), "takes no epic"),
        (("remaining", "", "one\ntwo"), "one line"),
    ]:
        with pytest.raises(ValueError, match=message):
            module.write_replan_request(path, *args, seen_mtime_ns=seen)
    with pytest.raises(ValueError, match="changed since it was read"):
        module.write_replan_request(path, "remaining", "", "", seen_mtime_ns=seen - 1)

    assert not (tmp_path / "replan-requests").exists()


def test_next_awaiting_lists_the_pending_replan_requests_and_not_the_handled_ones(
    run, tmp_path: pathlib.Path
):
    _, result = run(REPLANNING, "next", "--awaiting")
    assert result["replan_requests"] == []

    folder = tmp_path / "replan-requests"
    (folder / "done").mkdir(parents=True)
    for name, body in [
        ("20260102T000000Z", 'scope = "remaining"\nnote = "later"\n'),
        ("20260101T000000Z", 'scope = "epic"\nepic = "E1"\n'),
        ("20260103T000000Z", "scope = "),
        ("done/20250101T000000Z", 'scope = "remaining"\n'),
    ]:
        (folder / f"{name}.toml").write_text(body)
    requests = run(REPLANNING, "next", "--awaiting")[1]["replan_requests"]
    got = [
        (r["request"][16:32], r.get("epic"), r.get("note"), "error" in r)
        for r in requests
    ]
    assert got == [
        ("20260101T000000Z", "E1", "", False),
        ("20260102T000000Z", "", "later", False),
        ("20260103T000000Z", None, None, True),
    ]


HUNK_COMMENT = {
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


def _second_comment() -> dict:
    return {
        **HUNK_COMMENT,
        "noteId": "user:1794402060751-2",
        "newRange": [12, 14],
        "body": "rename it",
    }


def test_save_feedback_draft_keeps_the_export_and_never_replaces_a_ready_file(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REVIEWING)
    seen = path.stat().st_mtime_ns
    file = tmp_path / "feedback" / "r1.json"

    assert module.save_feedback_draft(path, "r1", [HUNK_COMMENT])
    assert json.loads(file.read_text()) == {"ready": False, "comments": [HUNK_COMMENT]}
    assert not module.save_feedback_draft(path, "r1", [HUNK_COMMENT])
    assert module.save_feedback_draft(path, "r1", [HUNK_COMMENT, _second_comment()])
    assert module.feedback_state(path) == {"r1": (2, False)}

    assert module.send_feedback(path, "r1", seen_mtime_ns=seen) == 2
    assert module.feedback_state(path) == {"r1": (2, True)}
    assert not module.save_feedback_draft(path, "r1", [HUNK_COMMENT])
    assert json.loads(file.read_text())["comments"] == [HUNK_COMMENT, _second_comment()]
    assert path.read_text() == REVIEWING
    assert [p.name for p in file.parent.iterdir()] == ["r1.json"]


def test_save_feedback_draft_leaves_out_the_handled_comments_and_an_empty_export_keeps_the_draft(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REVIEWING)
    module.save_feedback_draft(path, "r1", [HUNK_COMMENT])
    handled = tmp_path / "feedback" / "done"
    handled.mkdir()
    (tmp_path / "feedback" / "r1.json").rename(handled / "r1.20260102T000000Z.json")

    # The comment is still in the hunk session, but the orchestrator handled it.
    assert not module.save_feedback_draft(path, "r1", [HUNK_COMMENT])
    assert module.feedback_state(path) == {}
    assert module.save_feedback_draft(path, "r1", [HUNK_COMMENT, _second_comment()])
    assert json.loads((tmp_path / "feedback" / "r1.json").read_text())["comments"] == [
        _second_comment()
    ]

    # A new hunk session has no comments yet: the draft stays.
    assert not module.save_feedback_draft(path, "r1", [])
    assert module.feedback_state(path) == {"r1": (1, False)}


def test_send_feedback_refuses_what_it_cannot_send_and_writes_nothing(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REVIEWING)
    seen = path.stat().st_mtime_ns

    with pytest.raises(ValueError, match="no draft comments"):
        module.send_feedback(path, "r1", seen_mtime_ns=seen)
    module.save_feedback_draft(path, "r1", [HUNK_COMMENT])
    with pytest.raises(ValueError, match="changed since it was read"):
        module.send_feedback(path, "r1", seen_mtime_ns=seen - 1)
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        module.send_feedback(path, "nope", seen_mtime_ns=seen)
    module.send_feedback(path, "r1", seen_mtime_ns=seen)
    with pytest.raises(ValueError, match="sent already"):
        module.send_feedback(path, "r1", seen_mtime_ns=seen)

    path.write_text(REVIEWING.replace('"in_rev"', '"pending"'))
    (tmp_path / "feedback" / "r1.json").write_text('{"ready": false, "comments": [{}]}')
    with pytest.raises(ValueError, match="not in_progress or in_rev"):
        module.send_feedback(path, "r1", seen_mtime_ns=path.stat().st_mtime_ns)
    assert json.loads((tmp_path / "feedback" / "r1.json").read_text())["ready"] is False


def test_next_awaiting_lists_the_sent_feedback_as_findings_and_never_a_draft(
    run, tmp_path: pathlib.Path
):
    _, result = run(REVIEWING, "next", "--awaiting")
    assert result["feedback"] == []

    folder = tmp_path / "feedback"
    (folder / "done").mkdir(parents=True)
    for name, ready, comments in [
        ("a", False, [HUNK_COMMENT]),
        ("b", True, [HUNK_COMMENT, _second_comment()]),
        ("c", True, [{"body": "no line"}]),
        ("done/e", True, [HUNK_COMMENT]),
    ]:
        (folder / f"{name}.json").write_text(
            json.dumps({"ready": ready, "comments": comments})
        )
    (folder / "d.json").write_text("{")
    _, result = run(REVIEWING, "next", "--awaiting")

    assert [f["task"] for f in result["feedback"]] == ["b", "c", "d"]
    assert result["feedback"][0] == {
        "task": "b",
        "file": "feedback/b.json",
        "comments": [
            {"path": "m.py", "line": 9, "text": "use type hints"},
            {"path": "m.py", "line": 12, "text": "rename it"},
        ],
    }
    assert "newRange" in result["feedback"][1]["error"]
    assert "error" in result["feedback"][2]


def test_feedback_batch_converts_each_comment_for_hunk_apply_and_counts_the_skipped(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REVIEWING)
    module.save_feedback_draft(
        path, "r1", [HUNK_COMMENT, {"noteId": "x", "body": "no file"}]
    )

    batch, skipped = module.feedback_batch(path, "r1")

    assert batch == {
        "comments": [
            {
                "filePath": "m.py",
                "newLine": 9,
                "summary": "use type hints",
                "author": "user",
            }
        ]
    }
    assert skipped == 1
    with pytest.raises(ValueError, match="no saved feedback"):
        module.feedback_batch(path, "other")


def test_compare_refuses_a_proposal_that_changes_or_removes_a_frozen_task(compare):
    rows = [
        ("a", "done", []),
        ("b", "in_progress", []),
        ("c", "in_rev", []),
        ("d", "pending", []),
        ("e", "blocked", []),
    ]
    old = _plan(*rows)
    code, result = compare(old, old.replace('title = "d"', 'title = "renamed"'))
    assert code == 0

    code, result = compare(
        old,
        old.replace('title = "a"', 'title = "x"').replace(
            '"b"\nstatus = "in_progress"', '"b"\nstatus = "done"'
        ),
    )
    assert code == 2
    assert [e.split(":")[0] for e in result["errors"]] == ["a", "b"]
    assert "title" in result["errors"][0]
    assert "status" in result["errors"][1]

    removed = _plan(*(r for r in rows if r[0] != "c"))
    code, result = compare(old, removed)
    assert code == 2
    assert result["errors"] == ["c: in_rev and frozen, but the proposal removes it"]


NEEDS = _ledger(_leaf("t1", "a", needs=["gpu"], write_set=["a"]))
MACHINES = (
    '[machine.small]\nneeds = ["cores"]\nssh = "secret-target"\ntransport = "ssh"\n'
    '[machine.big]\nneeds = ["cores", "gpu"]\nssh = "secret-target"\ntransport = "herdr"\n'
)


def test_needs_round_trips_and_bad_values_fail(run):
    assert run(NEEDS, "next", *caps())[1]["start"][0]["needs"] == ["gpu"]
    for needs, error in [
        ('[""]', "an entry of 'needs' is empty"),
        ('"gpu"', "'needs' must be a list of strings"),
    ]:
        code, result = run(NEEDS.replace('["gpu"]', needs), "check")
        assert (code, result["errors"]) == (2, [f"t1: {error}"])


def test_remote_event_shape_is_validated_and_resume_shows_it(tmp_path):
    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(NEEDS)

    def event(detail: str) -> int:
        return module.run([str(tasks), "event", "t1", "remote", detail], io.StringIO())

    for detail in ["state=bogus", "state=running machine=box.example.org", "free text"]:
        assert event(detail) == 2, detail
    assert event("state=running machine=big path=/r/t1") == 0
    lines = _resume_text(tmp_path, NEEDS, LEDGER)
    assert "REMOTE (1)" in lines
    assert any("state=running machine=big" in x and "human check" in x for x in lines)
    assert event("state=done machine=big") == 0
    lines = _resume_text(tmp_path, NEEDS, LEDGER)
    assert any("state=done" in x and "human check" not in x for x in lines)


def test_check_warns_when_a_host_field_looks_like_a_host_name(run):
    run(NEEDS, "event", "t1", "launch", "host=big agent=a")
    assert not any("host=" in w for w in run(NEEDS, "check")[1].get("warnings", []))
    run(NEEDS, "event", "t1", "launch", "host=box.example.org")
    warnings = run(NEEDS, "check")[1]["warnings"]
    assert any("host=box.example.org" in w for w in warnings)


def test_machine_prints_the_match_and_opens_no_connection(run, tmp_path, monkeypatch):
    import socket

    def refuse(*args, **kwargs):
        msg = "the machine command must not connect"
        raise AssertionError(msg)

    for name in ("run", "Popen", "check_output", "check_call", "call"):
        monkeypatch.setattr(subprocess, name, refuse)
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    machines = tmp_path / "machines.toml"
    machines.write_text(MACHINES)
    code, result = run(NEEDS, "machine", "t1", "--map", str(machines))
    assert (code, result) == (
        0,
        {"id": "t1", "needs": ["gpu"], "machine": "big", "transport": "herdr"},
    )
    tpu = NEEDS.replace('"gpu"', '"tpu"')
    result = run(tpu, "machine", "t1", "--map", str(machines))[1]
    assert (result["machine"], result["transport"]) == (None, None)
    code, result = run(NEEDS, "machine", "t1")
    assert code == 2 and "no machine map" in result["errors"][0]


def _register(tmp_path: pathlib.Path, *args: str) -> tuple[int, str]:
    out = io.StringIO()
    code = _load().run([str(tmp_path / "tasks.toml"), "register", *args], out)
    return code, out.getvalue()


def test_register_adds_an_in_progress_task_and_prints_the_gate(tmp_path: pathlib.Path):
    """The task, its brief and the gate with the first-action instruction exist."""

    (tmp_path / "tasks.toml").write_text(TASKS)
    code, text = _register(
        tmp_path, "t9", "--title", "Fix", "--parent", "E1", "--write-set", "src/z.py"
    )
    assert code == 0
    assert text.startswith("[ledger:t9]\n")
    for part in [
        str(tmp_path / "briefs" / "t9.md"),
        'grep tasks.toml for id "t9"',
        "NO LEDGER TASK: <what is missing>",
    ]:
        assert part in text

    tasks = tomllib.loads((tmp_path / "tasks.toml").read_text())["task"]
    added = next(t for t in tasks if t["id"] == "t9")
    assert (added["status"], added["write_set"]) == ("in_progress", ["src/z.py"])
    brief = (tmp_path / "briefs" / "t9.md").read_text()
    assert brief.startswith(text)
    for part in ["# Fix\n", "May edit: `src/z.py`", "## Acceptance", "## Return"]:
        assert part in brief


def test_register_refuses_an_invalid_task_and_leaves_the_file_untouched(
    tmp_path: pathlib.Path,
):
    (tmp_path / "tasks.toml").write_text(TASKS)
    code, text = _register(tmp_path, "t9", "--title", "Fix", "--parent", "nope")
    assert code == 2
    assert json.loads(text)["ok"] is False
    assert (tmp_path / "tasks.toml").read_text() == TASKS
    assert not (tmp_path / "briefs" / "t9.md").exists()


def test_attach_records_the_agent_event(run, tmp_path: pathlib.Path):
    code, result = run(TASKS, "attach", "t2", "agent-3")
    assert code == 0
    assert result["event"]["event"] == "agent"
    assert result["event"]["detail"] == "agent-3"
    assert "agent-3" in (tmp_path / "events" / "t2.jsonl").read_text()


def _fresh(tmp_path: pathlib.Path, name: str) -> pathlib.Path:
    """A new empty directory under tmp_path, so a folded case starts from no files."""

    path = tmp_path / name
    path.mkdir()
    return path
