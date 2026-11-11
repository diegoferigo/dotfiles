from __future__ import annotations

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
TASKS = """format = 1

[[task]]
id = "E1"
type = "epic"
title = "epic"
status = "pending"
priority = "P1"
milestone = "m1"

[[task]]
id = "t1"
title = "a"
status = "done"
parent = "E1"
write_set = ["src/a.py"]

[[task]]
id = "t2"
title = "b"
status = "pending"
parent = "E1"
priority = "P1"
deps = ["t1"]
write_set = ["src/b/**"]

[[task]]
id = "t3"
title = "c"
status = "pending"
parent = "E1"
priority = "P0"
deps = ["t1"]
write_set = ["src/b/x.py"]

[[task]]
id = "t4"
title = "d"
status = "pending"
priority = "P3"
write_set = ["docs/**"]

[[task]]
id = "t5"
title = "e"
status = "pending"
deps = ["E1"]
read_only = true

[[task]]
id = "t6"
title = "f"
status = "running"
priority = "P1"
write_set = ["lib/**"]
"""
PARALLEL = """format = 1

[[task]]
id = "w1"
title = "writer one"
status = "pending"
priority = "P0"
write_set = ["src/one/**"]
footprint = 3
exclusive = ["gpu"]

[[task]]
id = "w2"
title = "writer two"
status = "pending"
priority = "P1"
write_set = ["src/two/**"]
exclusive = ["gpu"]

[[task]]
id = "r1"
title = "reviewer one"
status = "pending"
priority = "P1"
read_only = true

[[task]]
id = "r2"
title = "reviewer two"
status = "pending"
priority = "P2"
read_only = true
"""


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

    _, result = run(PARALLEL, "next", *caps(ro=1))
    assert ids(result["start"]) == ["w1", "r1"]
    assert {"id": "r2", "reason": "no free read-only slot"} in result["skipped"]

    _, result = run(PARALLEL, "next", *caps(rw=1))
    assert ids(result["start"]) == ["w1", "r1", "r2"]
    assert {"id": "w2", "reason": "no free slot"} in result["skipped"]

    # w1 weighs 3, so it starts alone under a budget of 2 and fills a budget of 3.
    _, result = run(PARALLEL, "next", *caps(budget=2))
    assert ids(result["start"]) == ["w1"]
    assert ("r1", "no budget left") in reasons(result)

    _, result = run(PARALLEL, "next", *caps(budget=4))
    assert ids(result["start"]) == ["w1", "r1"]

    # w1 cannot fit while r1 runs, so nothing lighter starts ahead of it.
    _, result = run(_with(PARALLEL, "r1", status="running"), "next", *caps(budget=2))
    assert result["start"] == []
    assert ("w1", "no budget left") in reasons(result)
    detail = "w1 needs the whole budget"
    skip = {"id": "w2", "reason": "budget reserved", "with": "w1", "detail": detail}
    assert skip in result["skipped"]

    # A running w1 takes the gpu and 3 of the 4 units of the budget.
    _, result = run(_with(PARALLEL, "w1", status="running"), "next", *caps(budget=4))
    assert ids(result["start"]) == ["r1"]
    skip = {"id": "w2", "reason": "exclusive resource", "with": "w1", "detail": "gpu"}
    assert skip in result["skipped"]

    # w1 and w2 both want the gpu, so the second one waits.
    _, result = run(PARALLEL, "next", *caps())
    assert ids(result["start"]) == ["w1", "r1", "r2"]
    assert any(s["id"] == "w2" and s["detail"] == "gpu" for s in result["skipped"])

    # A flag of next replaces the header cap of the same name.
    text = PARALLEL.replace("format = 1", "format = 1\nrw = 1\nro = 1")
    _, result = run(text, "next", *caps(rw=1, ro=1, budget=9))
    assert ids(result["start"]) == ["w1", "r1"]
    assert result["limits"] == {"rw": 1, "ro": 1, "budget": 9}

    _, result = run(text, "next", *caps(rw=1, ro=2, budget=9))
    assert ids(result["start"]) == ["w1", "r1", "r2"]


def test_pick_keeps_a_subset_and_refuses_anything_else(run, tmp_path):
    """An arbiter may only reduce the list the script computed."""

    code, result = run(PARALLEL, "next", "--pick", "w1,r2", *caps(note=None))
    assert code == 0
    assert ids(result["start"]) == ["w1", "r2"]

    code, result = run(PARALLEL, "next", "--pick", "w1,w2", *caps(note=None))
    assert code == 2
    assert any("cannot start w2 now" in e for e in result["errors"])

    code, result = run(PARALLEL, "next", "--pick", ",", *caps(note=None))
    assert code == 2
    assert any("--pick needs at least one id" in e for e in result["errors"])

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
    (
        'status = "running"',
        'status = "blocked"\nblocker = 42',
        "'blocker' must be a string",
    ),
    (
        'title = "f"',
        'title = "f"\nworktree = ["/w"]',
        "'worktree' must be a string",
    ),
    (
        'title = "f"',
        'title = "f"\nsession = 7',
        "'session' must be a string",
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


def _rows(lines: list[str]) -> list[str]:
    """The rows of a tree, without the legend that follows the last blank line."""

    return lines[: len(lines) - 1 - lines[::-1].index("")]


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


def _row(lines: list[str], task_id: str) -> str:
    """The row of a task in a tree: the id follows the title, so no space ends it."""

    return next(line for line in lines if _label(line)[1] == task_id)


def test_tree_and_board_show_the_marks_the_blockers_and_the_pick_up_order(run_text):
    """The tree marks each task and hides done ones without --all, the board folds and orders."""

    lines = [line for line in run_text(TASKS, "tree", "--all") if line]
    assert lines[0].split() == ["●", "f", "·", "P1", "t6"]
    assert lines[1].split() == [
        "┬",
        "━━──────",
        "1/3",
        "epic",
        "·",
        "P1",
        "E1",
        "·",
        "m1",
    ]
    assert lines[2].split() == ["├─", "✔", "a", "·", "P2", "t1"]
    assert lines[3].split() == ["├─", "▶", "c", "·", "P0", "t3", "·", "blocks", "1"]
    assert lines[4].split() == [
        "└─",
        "▶",
        "b",
        "·",
        "P1",
        "t2",
        "·",
        "blocks",
        "1",
        "·",
        "waits",
        "t3",
        "(files)",
    ]
    assert any(line.split()[:5] == ["○", "e", "·", "P2", "t5"] for line in lines)
    assert any(line.endswith("e · P2 t5 · waits E1 (dep)") for line in lines)
    assert any(line.split()[:5] == ["●", "f", "·", "P1", "t6"] for line in lines)
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
    text = TASKS.replace(
        'status = "running"', 'status = "running"\nworktree = "/w/t6"\nbranch = "t6"'
    )
    lines = _resume_text(tmp_path, text, LEDGER, constraints=True)
    assert lines[0].startswith(f"RESUME {tmp_path.name}  tasks=")
    assert lines[1] == (
        f"CONSTRAINTS {tmp_path / 'constraints.md'}  (read it before acting)"
    )
    heads = [line.split()[0] for line in lines if not line.startswith(" ")]
    assert heads == [
        "RESUME",
        "CONSTRAINTS",
        "READY",
        "RUNNING",
        "BLOCKED",
        "ANSWERED",
        "DECISIONS",
        "NEXT",
    ]
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
    assert "DECISIONS (last 0)" in lines
    assert lines[-1] == "NEXT (none in the ledger)"

    spaced = _fresh(tmp_path, "spaced")
    (spaced / "my notes.md").write_text("rules")
    lines = _resume_text(spaced, TASKS, LEDGER.replace("constraints.md", "my notes.md"))
    assert lines[1] == (
        f"CONSTRAINTS {spaced / 'my notes.md'}  (read it before acting)"
    )

    ledger = LEDGER.split("## Next actions")[0] + (
        "## Next actions\n<the exact steps>\n### now\nintro\n- Do the thing.\n"
    )
    assert _resume_text(tmp_path, TASKS, ledger)[-1] == "NEXT Do the thing."
    bare = LEDGER.split("## Next actions")[0] + "## Next actions\n<the steps>\nplain\n"
    assert _resume_text(tmp_path, TASKS, bare)[-1] == "NEXT plain"

    for row in [
        "| t6 | 1 | impl | pending | pending |\n",
        "| t6 | 1 | impl | dead-1 | stale |\n",
        "",
    ]:
        ledger = LEDGER.replace("| t6 | 1 | impl | agent-7 | running |\n", row)
        lines = _resume_text(tmp_path, text, ledger, constraints=True)
        assert any(line.startswith("  t6  agent=?  /w/t6") for line in lines), row


def test_resume_shows_the_blockers_and_the_session_whole_or_clipped(run, tmp_path):
    """resume shows a short blocker whole, clips a long one, and shows the session keys."""

    for blocker in ["needs the token", "x" * 200]:
        text = (
            TASKS
            + f"""
[[task]]
id = "t7"
title = "g"
status = "blocked"
blocker = "{blocker}"
write_set = ["g/**"]

[[task]]
id = "t8"
title = "h"
status = "blocked"
blocker = "human: {blocker}"
write_set = ["h/**"]
"""
        )
        lines = _resume_text(tmp_path, text, LEDGER, constraints=True)
        if len(blocker) < 100:
            assert f"  t7  blocked: {blocker}" in lines
            assert f"  t8  human: {blocker}" in lines
            continue
        for prefix in ("  t7  blocked: ", "  t8  human: "):
            (line,) = (item for item in lines if item.startswith(prefix))
            assert len(line) == 100
            assert line.endswith("...")

    text = TASKS.replace(
        'status = "running"',
        'status = "running"\nsession = "abc"\nsession_dir = "/s"',
    )
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
        (TASKS, ["event", "nope"], ["unknown id 'nope'"]),
        (
            TASKS,
            ["tree", "--max-blocked", "-1"],
            ["--max-blocked must not be negative"],
        ),
        (TASKS, ["tree", "--width", "0"], ["--width must be a positive integer"]),
        (
            'format = 1\n[[task]]\nid = "x"\n',
            ["check"],
            [
                "x: empty title",
                "x: status must be one of ['blocked', 'deferred', 'done',"
                " 'in_progress', 'in_rev', 'pending', 'returned', 'running']",
            ],
        ),
        (
            TASKS,
            ["compare", "missing.toml"],
            [
                "cannot read missing.toml: [Errno 2] No"
                " such file or directory:"
                " '{tmp}/missing.toml'"
            ],
        ),
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


BLOCKED = TASKS.replace(
    'id = "t2"\ntitle = "b"\nstatus = "pending"',
    'id = "t2"\ntitle = "b"\nstatus = "blocked"\nblocker = "human: which API?"',
)


WORKTREE = BLOCKED.replace(
    'milestone = "m1"', 'milestone = "m1"\nworktree = "/w/E1"\nbranch = "e1"'
)


def test_tree_shows_the_branch_and_only_that_a_human_is_awaited(run, run_text):
    """next lists what waits and the nearest worktree, the tree only the branch and the marks."""

    text = TASKS.replace(
        'id = "t2"\ntitle = "b"\nstatus = "pending"',
        'id = "t2"\ntitle = "b"\nstatus = "blocked"\nblocker = "event: review on 1a2b3c4"',
    )
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
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        _apply(steered, "set_priority", "nope", "P0")
    with pytest.raises(ValueError, match="unknown priority 'P9'"):
        _apply(steered, "set_priority", "g2", "P9")
    assert steered.read_text() == STEERED

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


def test_set_command_refuses_any_other_field(run, tmp_path: pathlib.Path):
    """set writes a priority and refuses any field but priority and status."""

    code, result = run(STEERED, "set", "g2", "priority", "P0")
    assert (code, result) == (0, {"ok": True, "id": "g2", "priority": "P0"})
    assert 'priority = "P0"' in (tmp_path / "tasks.toml").read_text()

    code, result = run(STEERED, "set", "g2", "title", "x")
    assert code == 2
    assert "only 'priority' and 'status' can be set, not 'title'" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == STEERED


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
    """set prints the change, or next under the header caps, and refuses a bad status."""

    for status, flags in [("in_progress", []), ("done", ["--no-next"])]:
        code, result = run(STEERED, "set", "g1", "status", status, *flags)
        assert (code, result) == (0, {"ok": True, "id": "g1", "status": status})
        assert f'status = "{status}"' in (tmp_path / "tasks.toml").read_text()

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
        (("nope", "status", "done"), "unknown id 'nope'"),
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


def test_set_priority_refuses_no_next(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g2", "priority", "P0", "--no-next")
    assert code == 2
    assert "--no-next only applies to status" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == STEERED


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


ASKED = """format = 1

# The gate.
[[task]]
id = "g1"
title = "gate"      # the first one
status = "blocked"
blocker = "human: option A or B?"
write_set = ["g"]

[[task]]
id = "g2"
title = "two"
priority = "P2"
status = "pending"
deps = ["g1"]
write_set = ["h"]
"""
ANSWERED = ASKED.replace('id = "g1"\n', 'id = "g1"\nanswer = "use option B"\n').replace(
    'status = "blocked"\nblocker = "human: option A or B?"\n', 'status = "pending"\n'
)


@pytest.fixture
def asked(tmp_path: pathlib.Path):
    path = tmp_path / "tasks.toml"
    path.write_text(ASKED)
    return path


def test_answer_blocker_writes_the_answer_or_refuses_a_bad_one(
    asked: pathlib.Path, tmp_path: pathlib.Path
):
    """answer_blocker unblocks, round-trips, logs, keeps comments and refuses bad input."""

    module = _load()
    for text in ("a\x07b", "a\x7fb", "a\rb", "a\u2028b", "a\u2029b"):
        with pytest.raises(ValueError, match="contains a control character"):
            module.answer_blocker(
                asked, "g1", text, seen_mtime_ns=asked.stat().st_mtime_ns
            )
        assert asked.read_text() == ASKED
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        module.answer_blocker(
            asked, "nope", "B", seen_mtime_ns=asked.stat().st_mtime_ns
        )
    assert asked.read_text() == ASKED

    for old, new in [
        ('blocker = "human: option A or B?"', 'blocker = "ci: the runner is down"'),
        ('status = "blocked"\nblocker = "human: option A or B?"', 'status = "pending"'),
    ]:
        path = _fresh(tmp_path, f"not-{len(new)}") / "tasks.toml"
        path.write_text(ASKED.replace(old, new))
        before = path.read_text()
        with pytest.raises(ValueError, match="not blocked on a human"):
            module.answer_blocker(
                path, "g1", "B", seen_mtime_ns=path.stat().st_mtime_ns
            )
        assert path.read_text() == before

    module.answer_blocker(
        asked, "g1", "use option B", seen_mtime_ns=asked.stat().st_mtime_ns
    )
    assert asked.read_text() == ANSWERED
    assert "# The gate." in asked.read_text()
    assert "# the first one" in asked.read_text()
    assert not list(asked.parent.glob("*.tmp"))
    log = (asked.parent / "steering.log").read_text().splitlines()
    assert len(log) == 1
    assert log[0].endswith("g1 answer")

    for i, text in enumerate(
        ['take "B", not C:\\x', "perché B 🙂", "a\nb\n\nc", "a\tb"]
    ):
        path = _fresh(tmp_path, f"trip{i}") / "tasks.toml"
        path.write_text(ASKED)
        module.answer_blocker(path, "g1", text, seen_mtime_ns=path.stat().st_mtime_ns)
        tasks, errors = module.parse(path.read_text())
        assert errors == []
        assert tasks[0].answer == text
        assert tasks[0].status == "pending"
        assert tasks[0].blocker == ""

    for blocker, gone in [
        ("human: option A or B?", "# asked twice"),
        ("human: option #1 or #2?", "#1"),
    ]:
        path = _fresh(tmp_path, f"comment-{gone}") / "tasks.toml"
        path.write_text(
            ASKED.replace(
                'status = "blocked"', 'status = "blocked"  # for now'
            ).replace(
                'blocker = "human: option A or B?"',
                f'blocker = "{blocker}"  # asked twice',
            )
        )
        module.answer_blocker(path, "g1", "B", seen_mtime_ns=path.stat().st_mtime_ns)
        text = path.read_text()
        assert 'status = "pending"  # for now' in text
        assert gone not in text

    path = _fresh(tmp_path, "multi") / "tasks.toml"
    path.write_text(ASKED)
    module.answer_blocker(path, "g1", "a\nb", seen_mtime_ns=path.stat().st_mtime_ns)
    text = module.resume(
        module.Graph(module.parse(path.read_text())[0]),
        limits=module.Limits(rw=None, ro=None, budget=None),
        tasks_path=path,
    )
    assert any(x.startswith("  g1  ") and x.endswith("a b") for x in text.splitlines())


def test_answer_command_writes_the_answer_or_refuses_a_task_not_blocked(run, tmp_path):
    code, result = run(ASKED, "answer", "g2", "B")
    assert code == 2
    assert "not blocked on a human" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == ASKED

    code, result = run(ASKED, "answer", "g1", "use option B")
    assert code == 0
    assert result == {"ok": True, "id": "g1", "answer": "use option B"}
    assert (tmp_path / "tasks.toml").read_text() == ANSWERED


def _order(lines: list[str]) -> list[str]:
    """The ids of the rows of a tree, in the order they are drawn."""

    rows = (_label(line) for line in _rows(lines))
    return [label[1] for label in rows if label[1]]


def test_compare_reports_the_changes_and_simulates_the_schedule(compare, run_text):
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

    old = _plan(("a", "done", []), ("b", "pending", ["a"]), ("c", "pending", ["b"]))
    _, result = compare(old, _with(old, "c", deps=[]))
    assert (result["before"]["waves"], result["after"]["waves"]) == (
        [["b"], ["c"]],
        [["b", "c"]],
    )
    assert (result["before"]["ready"], result["after"]["ready"]) == (["b"], ["b", "c"])

    old = _plan(
        ("a", "pending", []),
        ("g", "pending", ["a"], "read_only"),
        ("b", "pending", ["g"]),
        ("c", "pending", ["b"]),
    )
    _, result = compare(old, _with(old, "b", deps=["a"]))
    assert (result["before"]["gates"], result["after"]["gates"]) == (
        {"g": ["b", "c"]},
        {},
    )
    assert result["before"]["rounds"] == [["a"], ["g"], ["b"], ["c"]]
    assert result["after"]["rounds"] == [["a"], ["b", "g"], ["c"]]

    old = _plan(("a", "pending", []), ("b", "pending", []), ("c", "pending", ["a"]))
    _, result = compare(old, _with(old, "b", write_set=["a"]))
    assert result["before"]["rounds"] == [["a", "b"], ["c"]]
    assert result["after"]["rounds"] == [["a"], ["b", "c"]]

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

    # A nested epic with only deferred leaves still shows in its epic tree.
    plan = _ledger(
        _epic("E"),
        _epic("N", parent="E"),
        _block("a", parent="E", read_only=True),
        _block("p", "deferred", parent="N", read_only=True),
    )
    assert _order(run_text(plan, "tree", "--epic", "E")) == ["E", "a", "N", "p"]
    default = "\n".join(run_text(plan, "tree"))
    assert "◌" in default
    assert "hidden" not in default

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

    old = _ledger(_block("a"), _block("e"), _block("b", "blocked"))
    keys: dict[str, Any] = {
        "parent": "e",
        "milestone": "m1",
        "footprint": 3,
        "exclusive": ["gpu"],
    }
    new = _ledger(
        _block("a", **keys, write_set=None, read_only=True),
        _epic("e"),
        _block("b", "blocked", blocker="human: which?"),
    )
    _, result = compare(old, new)
    assert result["changed"] == [
        {
            "id": "a",
            "fields": {
                "parent": ["", "e"],
                "milestone": ["", "m1"],
                "write_set": [["a"], None],
                "read_only": [False, True],
                "footprint": [1, 3],
                "exclusive": [[], ["gpu"]],
            },
        },
        {"id": "e", "fields": {"type": ["task", "epic"], "write_set": [["e"], None]}},
        {"id": "b", "fields": {"blocker": ["", "human: which?"]}},
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

    tree = run_text(hub, "tree", "--color", "never")
    assert ["blocks 2" in _row(tree, "g9"), "blocks 1" in _row(tree, "d1")] == [1, 1]
    assert "blocks" not in _row(tree, "d2")
    assert any(x.startswith("  g9") and "blocks 4" in x for x in run_text(hub, "board"))
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
        (held, "b", {"c": ws}),
        (epic, "E", {}),
        (epic, "done", {}),
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

    assert not any(
        line.startswith("STACKS")
        for line in _resume_text(_fresh(tmp_path, "no"), TASKS)
    )
    text = STACKED.replace('stack = "auth"', "stack = 12")
    lines = _resume_text(_fresh(tmp_path, "numeric"), text)
    assert lines.count("  12") == 1
    start = lines.index("STACKS (1)")
    assert [line.split()[0] for line in lines[start + 2 : start + 5]] == [
        "a",
        "b",
        "d",
    ]

    text = STACKED.replace('stack = "12"', "stack = 12")
    lines = _resume_text(tmp_path, text)
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

    for stack in ['"  "', "0", "true", "[1]"]:
        code, result = run(
            STACKED.replace('stack = "auth"', f"stack = {stack}"), "check"
        )
        assert code == 2
        assert any("non-empty name or a number" in e for e in result["errors"])

    code, result = run(STACKED.replace('id = "e"', 'id = "e"\nstack = "x"'), "check")
    assert code == 2
    assert "e: 'stack' is only valid on an epic" in result["errors"]

    code, result = run("format = 1\n" + _task("a", "pending", "rel/a", "a"), "check")
    assert code != 0
    assert any("'worktree' must be an absolute path" in e for e in result["errors"])

    def nested(levels: int) -> str:
        blocks = [
            _epic(f"E{n}", **({"parent": f"E{n - 1}"} if n else {}))
            for n in range(levels)
        ]
        return "format = 1\n" + "".join(blocks)

    code, result = run(nested(9), "check")
    assert code == 0
    assert "warnings" not in result
    code, result = run(nested(10), "check")
    assert code == 0
    assert result["ok"] is True
    assert result["warnings"] == [
        "E9: nests 9 levels deep, GitHub sub-issues stop at 8"
    ]

    shared = STACKED.replace('stack = "auth"', 'stack = "auth"\nbranch = "shared"')
    shared = shared.replace('branch = "auth-a"\n', "").replace(
        'branch = "auth-b"\n', ""
    )
    code, result = run(shared, "check")
    assert code == 2
    assert any(
        "b: shares branch 'shared' with a in stack 'auth'" in e
        for e in result["errors"]
    )

    same = STACKED.replace('branch = "auth-b"', 'branch = "auth-a"')
    code, result = run(same, "check")
    assert code == 2
    assert any("shares branch 'auth-a'" in e for e in result["errors"])

    other = STACKED.replace('branch = "d"', 'branch = "auth-a"')
    assert run(other, "check")[0] == 0


def _git(cwd, *args):
    subprocess.run(
        ["git", "-C", str(cwd), *args],  # noqa: S607
        check=True,
        capture_output=True,
    )


def _task(name, status, worktree, branch):
    return (
        f'\n[[task]]\nid = "{name}"\ntitle = "{name}"\nstatus = "{status}"\n'
        f'write_set = ["{name}"]\nworktree = "{worktree}"\nbranch = "{branch}"\n'
    )


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
    assert any(" ahead at " in line and "commits that 'main'" in line for line in lines)
    assert any(" dirty at " in line and "uncommitted changes" in line for line in lines)
    assert any(" open at " in line and "open is not done" in line for line in lines)
    assert "keep (3)" in lines
    assert "orphan (2)" in lines
    assert any(" swapped at " in line and "'other'" in line for line in lines)
    assert any(" gone at " in line and "worktree is gone" in line for line in lines)
    assert "stranger" not in text
    assert (repo.parent / "merged").is_dir()

    main = repo.parent / "main.toml"
    main.write_text("format = 1\n" + _task("m", "done", repo, "main"))
    _, text = _cleanup(repo, main, "--tip", "main")
    assert "wt remove" not in text
    assert "main at" in text and "main checkout" in text

    running = repo.parent / "run.toml"
    running.write_text(
        "format = 1\n" + _task("r", "running", repo.parent / "merged", "merged")
    )
    _, text = _cleanup(repo, running, "--tip", "main")
    assert "wt remove" not in text and "r is not done" in text

    shared = tmp_path / "shared.toml"
    shared.write_text(
        "format = 1\n"
        + "".join(
            _task(n, s, tmp_path / "merged", "merged")
            for n, s in (("s1", "done"), ("s2", "pending"))
        )
    )
    _, text = _cleanup(repo, shared, "--tip", "main")
    assert "merged at" in text and "s2 is not done" in text
    assert "wt remove" not in text

    with monkeypatch.context() as patch:
        patch.chdir(repo.parent / "merged")
        _, text = _cleanup(repo, ledger, "--tip", "main")
        assert "wt remove" not in text
        assert "current directory" in text

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
    assert any(
        " dirty at " in line and "uncommitted" in line for line in text.splitlines()
    )


def test_cleanup_apply_removes_only_the_remove_class(fleet, monkeypatch):
    repo, ledger = fleet
    module = _load()
    monkeypatch.setattr(
        module, "WT", _fake_wt(repo.parent / "wt", 'git worktree remove "../$4"')
    )
    (repo.parent / "ORCHESTRATION.md").write_text("# Ledger\n")

    out = io.StringIO()
    args = [str(ledger), "cleanup", "--repo", str(repo), "--tip", "main", "--apply"]
    assert module.run(args, out) == 0
    assert f"removed merged at {repo.parent / 'merged'}" in out.getvalue()
    assert not (repo.parent / "merged").exists()
    for kept in ("ahead", "dirty", "open", "swapped"):
        assert (repo.parent / kept).is_dir()
    assert "cleanup: removed merged" in (repo.parent / "ORCHESTRATION.md").read_text()


def test_cleanup_apply_goes_on_after_a_refusal_and_exits_1(fleet, monkeypatch):
    repo, _ = fleet
    base = repo.parent
    _git(repo, "worktree", "add", "-q", "-b", "late", str(base / "late"))
    ledger = base / "two.toml"
    ledger.write_text(
        "format = 1\n"
        + "".join(_task(n, "done", base / n, n) for n in ("merged", "late"))
    )
    (base / "ORCHESTRATION.md").write_text("# Ledger\n")
    module = _load()
    script = '[ "$4" = merged ] && { echo refused >&2; exit 1; }; git worktree remove "../$4"'
    monkeypatch.setattr(module, "WT", _fake_wt(base / "wt", script))

    out = io.StringIO()
    args = [str(ledger), "cleanup", "--repo", str(repo), "--tip", "main", "--apply"]
    assert module.run(args, out) == 1
    assert "failed merged: refused" in out.getvalue()
    assert "removed late" in out.getvalue()
    assert not (base / "late").exists()
    assert "cleanup: failed merged: refused" in (base / "ORCHESTRATION.md").read_text()


def test_cleanup_apply_reports_a_missing_wt(fleet, monkeypatch):
    repo, ledger = fleet
    module = _load()
    monkeypatch.setattr(module, "WT", str(repo.parent / "no-such-wt"))

    out = io.StringIO()
    args = [str(ledger), "cleanup", "--repo", str(repo), "--tip", "main", "--apply"]
    assert module.run(args, out) == 1
    assert "failed merged: cannot run" in out.getvalue()

    monkeypatch.setenv("PATH", str(repo.parent / "nowhere"))
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

    ledger = _hunks(("a", "pending", HUNKS), ("b", "pending", HUNKS))
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["a", "b"]
    assert result["start"][1]["overlaps"] == ["a"]
    assert result["skipped"] == []

    code, result = run(ledger, "check")
    assert code == 0
    assert len(result["notes"]) == 1
    assert result["notes"][0].startswith("a and b:")
    assert "on purpose" in result["notes"][0]
    assert (
        "notes" not in run(_hunks(("a", "pending", 'write_set = ["x.py"]')), "check")[1]
    )

    # Pending tasks without a worktree yet still start together.
    ledger = _chain(("a", "pending", HUNKS), ("b", "pending", HUNKS))
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["a", "b"]

    ledger = _hunks(("a", "running", HUNKS), ("b", "pending", HUNKS))
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["b"]
    assert result["start"][0]["overlaps"] == ["a"]

    plain = 'write_set = ["x.py"]'
    same = _chain(
        ("a", "pending", 'worktree = "/wt/a"\n' + HUNKS),
        ("b", "pending", 'worktree = "/wt/a"\n' + HUNKS),
    )
    running_in_main_tree = _chain(("a", "running", HUNKS), ("b", "pending", HUNKS))
    cases = [
        _hunks(("a", "pending", HUNKS), ("b", "pending", plain)),
        same,
        running_in_main_tree,
    ]
    for ledger in cases:
        _, result = run(ledger, "next", *caps())
        assert [s["reason"] for s in result["skipped"]] == ["write set conflict"]

    ledger = _hunks(
        ("a", "pending", HUNKS + '\nexclusive = ["db"]'),
        ("b", "pending", HUNKS + '\nexclusive = ["db"]'),
    )
    _, result = run(ledger, "next", *caps())
    assert ids(result["start"]) == ["a"]
    assert result["skipped"][0]["reason"] == "exclusive resource"
    _, capped = run(
        _hunks(("a", "pending", HUNKS), ("b", "pending", HUNKS)), "next", *caps(rw=1)
    )
    assert ids(capped["start"]) == ["a"]

    for value, message in [
        ('"all"', "'overlap' must be one of ['hunks']"),
        ('["hunks"]', "'overlap' must be a string"),
        ("{ a = 1 }", "'overlap' must be a string"),
        ("3", "'overlap' must be a string"),
        ("true", "'overlap' must be a string"),
    ]:
        ledger = _hunks(("a", "pending", f'overlap = {value}\nwrite_set = ["x.py"]'))
        code, result = run(ledger, "check")
        assert code == 2
        assert message in result["errors"][0]


def test_overlap_hunks_is_not_a_write_set_hold(run):
    """explain holds a sharer only when the overlap is not on purpose."""

    ledger = _hunks(
        ("a", "running", HUNKS),
        ("b", "pending", HUNKS),
        ("c", "pending", HUNKS),
        ("d", "pending", HUNKS),
        ("e", "pending", HUNKS),
    )
    _, result = run(ledger, "explain", "a")
    assert result["holds_up"] == {}
    assert result["warning"] is None
    assert set(result["overlaps_on_purpose"]) == {"b", "c", "d", "e"}

    # One task only, the same worktree, or a running task without one.
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


def test_check_warns_about_a_started_or_ready_task_without_a_brief(
    run, run_bare, tmp_path
):
    """A task in progress or ready needs briefs/<id>.md, and a blocked one does not."""

    for status, label in [("in_progress", "in progress"), ("pending", "ready")]:
        plan = _plan(("a", status, []), ("b", "pending", ["a"]))
        _, result = run_bare(plan, "check")
        assert result["warnings"] == [
            f"a: {label} without briefs/a.md next to tasks.toml"
        ]

        (tmp_path / "briefs").mkdir()
        (tmp_path / "briefs" / "a.md").write_text("## Acceptance\n")
        code, result = run_bare(plan, "check")
        assert code == 0
        assert "warnings" not in result
        (tmp_path / "briefs" / "a.md").unlink()
        (tmp_path / "briefs").rmdir()

    # An epic has no brief of its own, so only its leaves are checked.
    plan = (
        'format = 1\n\n[[task]]\nid = "E"\ntype = "epic"\ntitle = "epic"\n'
        'status = "in_progress"\n\n[[task]]\nid = "a"\ntitle = "a"\n'
        'status = "pending"\nparent = "E"\nwrite_set = ["a"]\n'
    )
    _, result = run(plan, "check")
    assert "warnings" not in result


def test_check_warns_about_a_brief_without_an_acceptance_heading(run_bare, tmp_path):
    plan = _plan(("a", "in_progress", []), ("b", "pending", ["a"]))
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "a.md").write_text("## Change\nAcceptance is fine.\n")
    (tmp_path / "briefs" / "b.md").write_text("## Change\n")
    _, result = run_bare(plan, "check")
    assert result["warnings"] == [
        "a: briefs/a.md has no Acceptance heading",
        "b: briefs/b.md has no Acceptance heading",
    ]

    # Briefs of done tasks and epics are never checked.
    (tmp_path / "briefs" / "b.md").unlink()
    plan = (
        'format = 1\n\n[[task]]\nid = "E"\ntype = "epic"\ntitle = "epic"\n'
        'status = "pending"\n\n[[task]]\nid = "a"\ntitle = "a"\nstatus = "done"\n'
        'parent = "E"\nwrite_set = ["a"]\ndone_at = "2026-01-02T10:00:00+01:00"\n'
    )
    (tmp_path / "briefs" / "a.md").write_text("## Change\n")
    (tmp_path / "briefs" / "E.md").write_text("## Change\n")
    _, result = run_bare(plan, "check")
    assert "warnings" not in result
    (tmp_path / "briefs" / "a.md").unlink()
    _, result = run_bare(plan, "check")
    assert "warnings" not in result


def test_check_warns_about_an_inbox_file_without_a_recommendation(run_bare, tmp_path):
    plan = (
        'format = 1\n\n[[task]]\nid = "a"\ntitle = "a"\nstatus = "blocked"\n'
        'blocker = "human: which one?"\nwrite_set = ["a"]\n'
    )
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "a.md").write_text("## Acceptance\n")
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    (inbox / "a.md").write_text("## Question\n\n## Options\n")
    _, result = run_bare(plan, "check")
    assert result["warnings"] == ["a: inbox/a.md has no Recommendation heading"]

    (inbox / "a.md").write_text("## Options\n\n## Raccomandazione\n")
    _, result = run_bare(plan, "check")
    assert "warnings" not in result


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
    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(
        'format = 1\n\n[[task]]\nid = "r1"\ntitle = "running"\n'
        'status = "running"\nwrite_set = ["a"]\n'
    )
    events = tmp_path / "events"
    events.mkdir()
    (events / "r1.jsonl").write_text(
        '{"ts": "2026-01-01T00:00:00+00:00", "task": "r1", "event": "agent", "detail": "one"}\n'
        "torn line\n"
        '{"ts": "2026-01-02T00:00:00+00:00", "task": "r1", "event": "commit", "detail": "abc"}\n'
    )
    text = module.resume(
        module.Graph(module.parse(tasks.read_text())[0]),
        limits=module.Limits(rw=None, ro=None, budget=None),
        tasks_path=tasks,
    )
    line = next(x for x in text.splitlines() if x.startswith("  r1"))
    assert "last=2026-01-02T00:00:00+00:00 commit abc" in line


def test_resume_shows_worktree_state(tmp_path: pathlib.Path):
    import subprocess

    module = _load()

    def git(cwd, *args):
        subprocess.run(
            ["git", "-C", str(cwd), "-c", "user.name=t", "-c", "user.email=t@t", *args],  # noqa: S607
            check=True,
            capture_output=True,
        )

    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "integration")
    git(repo, "commit", "-q", "--allow-empty", "-m", "base")
    for name in ("clean", "dirty", "ahead"):
        git(repo, "worktree", "add", "-q", "-b", name, str(tmp_path / name))
    (tmp_path / "dirty" / "f").write_text("x")
    git(tmp_path / "ahead", "commit", "-q", "--allow-empty", "-m", "more")

    rows = "".join(
        f'[[task]]\nid = "{n}"\ntitle = "t"\nstatus = "running"\n'
        f'write_set = ["{n}"]\nworktree = "{tmp_path / n}"\nbranch = "{n}"\n\n'
        for n in ("clean", "dirty", "ahead", "missing")
    )
    tasks = tmp_path / "tasks.toml"
    tasks.write_text("format = 1\n\n" + rows)
    text = module.resume(
        module.Graph(module.parse(tasks.read_text())[0]),
        limits=module.Limits(rw=None, ro=None, budget=None),
        tasks_path=tasks,
    )
    line = {x.split()[0]: x for x in text.splitlines() if x.startswith("  ")}
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
    rich = (
        "format = 1\n"
        '\n[[task]]\nid = "r1"\ntitle = "running"\nstatus = "running"\n'
        'write_set = ["shared"]\nsession = "abc"\nsession_dir = "/s"\n'
        'worktree = "/w"\nbranch = "b"\noverlap = "hunks"\n'
        '\n[[task]]\nid = "s1"\ntitle = "starts"\nstatus = "pending"\n'
        'write_set = ["shared"]\noverlap = "hunks"\nmodel = "m"\n'
        'effort = "high"\nsession = "def"\nsession_dir = "/t"\n'
        '\n[[task]]\nid = "s2"\ntitle = "skipped"\nstatus = "pending"\n'
        'write_set = ["shared"]\n'
        '\n[[task]]\nid = "s3"\ntitle = "waits"\nstatus = "pending"\n'
        'deps = ["s2"]\nwrite_set = ["w"]\n'
        '\n[[task]]\nid = "h1"\ntitle = "asks"\nstatus = "blocked"\n'
        'blocker = "human: which?"\nwrite_set = ["h"]\n'
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


def _attempt_resume(tmp_path: pathlib.Path, rows: list[str]) -> tuple[str, str]:
    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(
        'format = 1\n\n[[task]]\nid = "r1"\ntitle = "running"\n'
        'status = "running"\nwrite_set = ["a"]\n'
    )
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
    old_line = f'{{"ts": "{old}", "task": "r1", "event": "launch", "detail": "host=box pane=p7"}}'
    (tmp_path / "events" / "r1.jsonl").write_text(old_line + "\n")
    tasks = tmp_path / "tasks.toml"
    text = module.resume(
        module.Graph(module.parse(tasks.read_text())[0]),
        limits=module.Limits(rw=None, ro=None, budget=None),
        tasks_path=tasks,
    )
    assert "attempt=orphan host=box pane=p7" in text


ANSWERED_TASKS = TASKS.replace(
    'id = "t2"\ntitle = "b"\nstatus = "pending"',
    'id = "t2"\ntitle = "b"\nstatus = "pending"\nanswer = "use A"',
)


def _log_answer(tmp_path, when, task_id="t2"):
    with (tmp_path / "steering.log").open("a") as log:
        log.write(f"{when.isoformat(timespec='seconds')} {task_id} answer\n")


def test_next_lists_an_answer_once_and_moves_the_stamp(run, tmp_path):
    now = datetime.datetime.now().astimezone()
    _log_answer(tmp_path, now - datetime.timedelta(hours=1))
    _, first = run(ANSWERED_TASKS, "next", *caps())
    assert [a["id"] for a in first["answered"]] == ["t2"]
    assert first["answered"][0]["answer"] == "use A"
    assert first["answered"][0]["status"] == "pending"
    assert (tmp_path / ".next-seen").exists()
    _, second = run(ANSWERED_TASKS, "next", *caps())
    assert second["answered"] == []
    _log_answer(
        tmp_path, datetime.datetime.now().astimezone() + datetime.timedelta(seconds=5)
    )
    _, third = run(ANSWERED_TASKS, "next", *caps())
    assert [a["id"] for a in third["answered"]] == ["t2"]


def test_next_peek_does_not_move_the_stamp(run, tmp_path):
    now = datetime.datetime.now().astimezone()
    _log_answer(tmp_path, now - datetime.timedelta(hours=1))
    _, peeked = run(ANSWERED_TASKS, "next", "--peek", *caps())
    assert [a["id"] for a in peeked["answered"]] == ["t2"]
    assert not (tmp_path / ".next-seen").exists()
    _, again = run(ANSWERED_TASKS, "next", "--peek", *caps())
    assert [a["id"] for a in again["answered"]] == ["t2"]


def test_next_without_a_stamp_lists_only_the_last_24_hours(run, tmp_path):
    now = datetime.datetime.now().astimezone()
    _log_answer(tmp_path, now - datetime.timedelta(hours=30), "t3")
    _log_answer(tmp_path, now - datetime.timedelta(hours=23))
    text = ANSWERED_TASKS.replace(
        'id = "t3"\ntitle = "c"\nstatus = "pending"',
        'id = "t3"\ntitle = "c"\nstatus = "pending"\nanswer = "old"',
    )
    _, result = run(text, "next", *caps())
    assert [a["id"] for a in result["answered"]] == ["t2"]


def test_next_never_lists_a_task_without_an_answer(run, tmp_path):
    _log_answer(tmp_path, datetime.datetime.now().astimezone())
    _, result = run(TASKS, "next", *caps())
    assert result["answered"] == []


def test_next_reports_an_answer_logged_in_the_same_second_as_the_last_call(
    run, tmp_path
):
    now = datetime.datetime.now().astimezone()
    _log_answer(tmp_path, now - datetime.timedelta(hours=1))
    run(ANSWERED_TASKS, "next", *caps())
    _log_answer(tmp_path, now - datetime.timedelta(hours=1))
    _, result = run(ANSWERED_TASKS, "next", *caps())
    assert [a["id"] for a in result["answered"]] == ["t2"]


def test_next_leaves_the_stamp_when_a_later_step_fails(tmp_path, monkeypatch):
    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(ANSWERED_TASKS)
    _log_answer(tmp_path, datetime.datetime.now().astimezone())

    def boom(*_args, **_kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(module, "removable_count", boom)
    with pytest.raises(RuntimeError):
        module.run([str(tasks), "next", *caps()], io.StringIO())
    assert not (tmp_path / ".next-seen").exists()


REPLANNING = """format = 1

[[task]]
id = "E1"
type = "epic"
title = "epic"
status = "pending"

[[task]]
id = "a"
parent = "E1"
title = "a"
status = "pending"
write_set = ["a"]

[[task]]
id = "b"
title = "b"
status = "pending"
write_set = ["b"]
"""


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
        (("epic", "nope", ""), "not an epic"),
        (("epic", "", ""), "not an epic"),
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
    (folder / "20260102T000000Z.toml").write_text(
        'scope = "remaining"\nnote = "later"\n'
    )
    (folder / "20260101T000000Z.toml").write_text('scope = "epic"\nepic = "E1"\n')
    (folder / "20260103T000000Z.toml").write_text("scope = ")
    (folder / "done" / "20250101T000000Z.toml").write_text('scope = "remaining"\n')
    _, result = run(REPLANNING, "next", "--awaiting")
    requests = result["replan_requests"]
    assert requests[:2] == [
        {
            "request": "replan-requests/20260101T000000Z.toml",
            "scope": "epic",
            "epic": "E1",
            "note": "",
        },
        {
            "request": "replan-requests/20260102T000000Z.toml",
            "scope": "remaining",
            "epic": "",
            "note": "later",
        },
    ]
    assert [r["request"] for r in requests[2:]] == [
        "replan-requests/20260103T000000Z.toml"
    ]
    assert "error" in requests[2]


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
    (folder / "a.json").write_text(
        json.dumps({"ready": False, "comments": [HUNK_COMMENT]})
    )
    (folder / "b.json").write_text(
        json.dumps({"ready": True, "comments": [HUNK_COMMENT, _second_comment()]})
    )
    (folder / "c.json").write_text(
        json.dumps({"ready": True, "comments": [{"body": "no line"}]})
    )
    (folder / "d.json").write_text("{")
    (folder / "done" / "e.json").write_text(
        json.dumps({"ready": True, "comments": [HUNK_COMMENT]})
    )
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
    old = _plan(
        ("a", "done", []),
        ("b", "in_progress", []),
        ("c", "in_rev", []),
        ("d", "pending", []),
        ("e", "blocked", []),
    )
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

    removed = old.replace(
        '\n[[task]]\nid = "c"\ntitle = "c"\nstatus = "in_rev"\ndeps = []\nwrite_set = ["c"]\n',
        "",
    )
    assert removed != old
    code, result = compare(old, removed)
    assert code == 2
    assert result["errors"] == ["c: in_rev and frozen, but the proposal removes it"]


def test_the_done_column_lists_the_newest_task_first(tmp_path: pathlib.Path):
    module = _load()
    text = "format = 1\n\n" + "".join(
        f'[[task]]\nid = "{i}"\ntitle = "t"\nstatus = "done"\nread_only = true\n\n'
        for i in "abc"
    )
    tasks, errors = module.parse(text)
    assert not errors
    assert module.board_cards(module.Graph(tasks), {"done"}) == ["c", "b", "a"]


NEEDS = """format = 1

[[task]]
id = "t1"
title = "a"
status = "pending"
needs = ["gpu"]
write_set = ["a"]
"""

MACHINES = """[machine.small]
needs = ["cores"]
ssh = "secret-target"
transport = "ssh"

[machine.big]
needs = ["cores", "gpu"]
ssh = "secret-target"
transport = "herdr"
"""


def test_needs_round_trips_and_bad_values_fail(run):
    code, result = run(NEEDS, "check")
    assert code == 0 and result["ok"]
    code, result = run(NEEDS, "next", *caps())
    assert result["start"][0]["needs"] == ["gpu"]
    code, result = run(NEEDS.replace('["gpu"]', '[""]'), "check")
    assert code == 2 and "t1: an entry of 'needs' is empty" in result["errors"]
    code, result = run(NEEDS.replace('["gpu"]', '"gpu"'), "check")
    assert code == 2 and "t1: 'needs' must be a list of strings" in result["errors"]


def test_remote_event_shape_is_validated_and_resume_shows_it(tmp_path):
    module = _load()
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(NEEDS)

    def event(detail: str) -> int:
        return module.run([str(tasks), "event", "t1", "remote", detail], io.StringIO())

    assert event("state=bogus") == 2
    assert event("state=running machine=box.example.org") == 2
    assert event("state=running machine=10.0.0.1") == 2
    assert event("free text") == 2
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
    assert code == 0
    assert result == {
        "id": "t1",
        "needs": ["gpu"],
        "machine": "big",
        "transport": "herdr",
    }
    assert "secret-target" not in json.dumps(result)
    code, result = run(
        NEEDS.replace('"gpu"', '"tpu"'), "machine", "t1", "--map", str(machines)
    )
    assert code == 0 and result["machine"] is None and result["transport"] is None
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
    assert str(tmp_path / "briefs" / "t9.md") in text
    assert 'grep tasks.toml for id "t9"' in text
    assert "NO LEDGER TASK: <what is missing>" in text

    tasks = {
        t["id"]: t for t in tomllib.loads((tmp_path / "tasks.toml").read_text())["task"]
    }
    assert tasks["t9"]["status"] == "in_progress"
    assert tasks["t9"]["write_set"] == ["src/z.py"]
    assert "## Acceptance" in (tmp_path / "briefs" / "t9.md").read_text()
    brief = (tmp_path / "briefs" / "t9.md").read_text()
    assert brief.startswith(text)
    assert "# Fix\n" in brief
    assert "May edit: `src/z.py`" in brief
    assert "## Return" in brief


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
