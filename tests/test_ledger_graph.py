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
    *, rw: int | None = 3, ro: int | None = 3, budget: int | None = 6, note: str | None = "test"
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


@pytest.mark.parametrize("missing", ["rw", "ro", "budget"])
def test_next_exits_2_when_a_cap_is_missing(run, tmp_path, missing):
    code, result = run(TASKS, "next", *caps(**{missing: None}))
    assert code == 2
    assert f"--{missing}" in result["errors"][0]
    assert "nproc" in result["errors"][0]
    assert not (tmp_path / "caps.log").exists()


@pytest.mark.parametrize("note", [None, " "])
def test_next_exits_2_without_a_caps_note(run, tmp_path, note):
    code, result = run(TASKS, "next", *caps(note=note))
    assert code == 2
    assert "--caps-note" in result["errors"][0]
    assert not (tmp_path / "caps.log").exists()


def test_next_logs_the_caps_and_the_note(run, tmp_path):
    code, _ = run(TASKS, "next", *caps(rw=2, ro=1, budget=4, note="load 1/14"))
    assert code == 0
    line = (tmp_path / "caps.log").read_text().splitlines()[-1]
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\S+ rw=2 ro=1 budget=4 load 1/14", line)


def test_next_pick_needs_no_caps_note_and_logs_one_when_given(run, tmp_path):
    code, result = run(PARALLEL, "next", "--pick", "w1", *caps(note=None))
    assert code == 0
    assert [t["id"] for t in result["start"]] == ["w1"]
    assert not (tmp_path / "caps.log").exists()

    code, _ = run(PARALLEL, "next", "--pick", "w1", *caps(note="why"))
    assert code == 0
    assert (tmp_path / "caps.log").read_text().endswith(" why\n")


def test_a_refused_pick_writes_no_caps_log(run, tmp_path):
    code, _ = run(PARALLEL, "next", "--pick", "w1,nope", *caps(note="why"))
    assert code == 2
    assert not (tmp_path / "caps.log").exists()


def test_next_awaiting_prints_only_the_questions_and_needs_no_caps(run, tmp_path):
    code, result = run(BLOCKED, "next", "--awaiting")
    assert code == 0
    assert set(result) == {"awaiting_human", "awaiting_event", "replan_requests", "feedback"}
    assert result["awaiting_human"]
    assert not (tmp_path / "caps.log").exists()


def test_next_orders_by_priority_and_skips_write_set_conflicts(run):
    code, result = run(TASKS, "next", *caps())
    assert code == 0
    assert [t["id"] for t in result["start"]] == ["t3", "t4"]
    assert result["skipped"][0]["id"] == "t2"
    assert result["skipped"][0]["with"] == "t3"
    assert result["running"] == [{"id": "t6", "worktree": None, "branch": None}]


def test_slots_is_an_alias_of_rw_and_the_running_writers_fill_it(run):
    """The running t6 takes one of the two writer slots, so only t3 starts."""

    _, result = run(TASKS, "next", "--slots", "2", *caps(rw=None))
    assert [t["id"] for t in result["start"]] == ["t3"]
    assert {"id": "t4", "reason": "no free slot"} in result["skipped"]
    _, same = run(TASKS, "next", *caps(rw=2))
    assert same["start"] == result["start"]


def test_the_two_caps_are_counted_apart(run):
    """A writer cap leaves the read-only tasks alone, and the other way round."""

    _, result = run(PARALLEL, "next", *caps(ro=1))
    assert [t["id"] for t in result["start"]] == ["w1", "r1"]
    assert {"id": "r2", "reason": "no free read-only slot"} in result["skipped"]

    _, result = run(PARALLEL, "next", *caps(rw=1))
    assert [t["id"] for t in result["start"]] == ["w1", "r1", "r2"]
    assert {"id": "w2", "reason": "no free slot"} in result["skipped"]


def test_a_cap_that_is_not_a_positive_integer_is_refused(run):
    """A flag is held to the same rule as the header key it overrides."""

    code, result = run(PARALLEL, "next", *caps(rw=0))
    assert code == 2
    assert result["errors"] == ["--rw must be a positive integer"]


def test_the_budget_counts_the_footprints_and_a_heavy_task_starts_alone(run):
    """w1 weighs 3, so it starts alone under a budget of 2 and fills a budget of 3."""

    _, result = run(PARALLEL, "next", *caps(budget=2))
    assert [t["id"] for t in result["start"]] == ["w1"]
    assert any(
        s["id"] == "r1" and s["reason"] == "no budget left" for s in result["skipped"]
    )

    _, result = run(PARALLEL, "next", *caps(budget=4))
    assert [t["id"] for t in result["start"]] == ["w1", "r1"]


def test_a_task_heavier_than_the_budget_keeps_the_wave_for_itself(run):
    """w1 cannot fit while r1 runs, so nothing lighter starts ahead of it."""

    text = PARALLEL.replace(
        'id = "r1"\ntitle = "reviewer one"\nstatus = "pending"',
        'id = "r1"\ntitle = "reviewer one"\nstatus = "running"',
    )
    _, result = run(text, "next", *caps(budget=2))
    assert result["start"] == []
    assert any(
        s["id"] == "w1" and s["reason"] == "no budget left" for s in result["skipped"]
    )
    assert {
        "id": "w2",
        "reason": "budget reserved",
        "with": "w1",
        "detail": "w1 needs the whole budget",
    } in result["skipped"]


def test_a_running_task_holds_its_footprint_and_its_exclusive_names(run):
    """w1 runs, so it takes the gpu and 3 of the 4 units of the budget."""

    text = PARALLEL.replace(
        'id = "w1"\ntitle = "writer one"\nstatus = "pending"',
        'id = "w1"\ntitle = "writer one"\nstatus = "running"',
    )
    _, result = run(text, "next", *caps(budget=4))
    assert [t["id"] for t in result["start"]] == ["r1"]
    assert {
        "id": "w2",
        "reason": "exclusive resource",
        "with": "w1",
        "detail": "gpu",
    } in result["skipped"]


def test_two_tasks_never_share_an_exclusive_name(run):
    """w1 and w2 both want the gpu, so the second one waits."""

    _, result = run(PARALLEL, "next", *caps())
    assert [t["id"] for t in result["start"]] == ["w1", "r1", "r2"]
    assert any(s["id"] == "w2" and s["detail"] == "gpu" for s in result["skipped"])


def test_the_header_holds_the_caps_and_a_flag_overrides_one(run):
    """A flag of next replaces the header cap of the same name."""

    text = PARALLEL.replace("format = 1", "format = 1\nrw = 1\nro = 1")
    _, result = run(text, "next", *caps(rw=1, ro=1, budget=9))
    assert [t["id"] for t in result["start"]] == ["w1", "r1"]
    assert result["limits"] == {"rw": 1, "ro": 1, "budget": 9}

    _, result = run(text, "next", *caps(rw=1, ro=2, budget=9))
    assert [t["id"] for t in result["start"]] == ["w1", "r1", "r2"]


def test_pick_keeps_a_subset_and_refuses_anything_else(run):
    """An arbiter may only reduce the list the script computed."""

    code, result = run(PARALLEL, "next", "--pick", "w1,r2", *caps(note=None))
    assert code == 0
    assert [t["id"] for t in result["start"]] == ["w1", "r2"]

    code, result = run(PARALLEL, "next", "--pick", "w1,w2", *caps(note=None))
    assert code == 2
    assert any("cannot start w2 now" in e for e in result["errors"])

    code, result = run(PARALLEL, "next", "--pick", ",", *caps(note=None))
    assert code == 2
    assert any("--pick needs at least one id" in e for e in result["errors"])


def test_running_task_holds_its_write_set_and_paths_are_normalized(run):
    text = TASKS.replace('write_set = ["docs/**"]', 'write_set = ["./lib/y.py"]')
    _, result = run(text, "next", *caps())
    assert "t4" not in [t["id"] for t in result["start"]]
    assert any(s["id"] == "t4" and s["with"] == "t6" for s in result["skipped"])


def test_dep_on_an_epic_waits_for_the_epic_itself(run):
    text = TASKS.replace(
        'status = "pending"\nparent = "E1"\npriority = "P1"',
        'status = "done"\nparent = "E1"\npriority = "P1"',
    )
    text = text.replace(
        'status = "pending"\nparent = "E1"\npriority = "P0"',
        'status = "done"\nparent = "E1"\npriority = "P0"',
    )
    _, result = run(text, "next", *caps())
    assert {"id": "t5", "blocked_by": ["E1"]} in result["waiting"]
    _, result = run(
        text.replace(
            'status = "pending"\npriority = "P1"\nmilestone',
            'status = "done"\npriority = "P1"\nmilestone',
        ),
        "next",
        *caps(),
    )
    assert "t5" in [t["id"] for t in result["start"]]


def test_a_blocked_epic_blocks_its_leaves(run):
    text = TASKS.replace(
        'status = "pending"\npriority = "P1"\nmilestone',
        'status = "blocked"\npriority = "P1"\nmilestone',
    )
    _, result = run(text, "next", *caps())
    assert {"id": "t2", "blocked_by": ["E1"]} in result["waiting"]


def test_an_epic_without_children_is_never_scheduled(run):
    text = (
        TASKS
        + '\n[[task]]\nid = "E2"\ntype = "epic"\ntitle = "later"\nstatus = "pending"\n'
    )
    _, result = run(text, "ready")
    assert "E2" not in result["ready"]


def test_epic_progress_and_inverse_edges(run):
    _, epics = run(TASKS, "epics")
    assert epics["epics"][0]["leaves_done"] == 1
    assert epics["epics"][0]["children_done"] is False
    _, why = run(TASKS, "explain", "t1")
    assert why["blocking"] == ["t2", "t3"]
    _, why = run(TASKS, "explain", "t2")
    assert why["ready"] is True
    assert why["wave"] == 2


def test_wave_of_a_dependent_of_an_epic_is_after_its_children(run):
    _, why = run(TASKS, "explain", "t5")
    assert why["wave"] == 4


def test_children_inherit_the_deps_of_their_epic(run):
    text = TASKS.replace(
        'priority = "P1"\nmilestone = "m1"',
        'priority = "P1"\nmilestone = "m1"\ndeps = ["t4"]',
    )
    _, result = run(text, "next", *caps())
    assert {"id": "t2", "blocked_by": ["t4"]} in result["waiting"]


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
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
        ('priority = "P3"', 'priority = "P7"', "priority must be one of"),
        ('status = "done"', 'status = "finished"', "status must be one of"),
        ('title = "f"', 'title = "f"\nwriteset = ["x"]', "unknown key 'writeset'"),
        ('write_set = ["lib/**"]', "", "set write_set"),
        ('write_set = ["lib/**"]', "write_set = []", "set write_set"),
        (
            "read_only = true",
            'read_only = true\nwrite_set = ["x"]',
            "exclude each other",
        ),
        ('title = "f"', "", "empty title"),
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
        (
            'title = "f"',
            'title = "f"\nsession = " "',
            "'session' must be a non-empty string",
        ),
        (
            'title = "f"',
            'title = "f"\nsession_dir = "/s"',
            "'session_dir' needs a 'session'",
        ),
        (
            'title = "f"',
            'title = "f"\nblocker = ""',
            "'blocker' needs status",
        ),
        (
            'status = "running"',
            'status = "blocked"\nblocker = ""',
            "empty blocker",
        ),
        ('title = "f"', 'title = "f"\nanswer = ""', "empty answer"),
        ('id = "t6"', 'id = "t4"', "duplicate id"),
        (
            'title = "f"',
            'title = "f"\nfootprint = 0',
            "'footprint' must be a positive integer",
        ),
        (
            'title = "f"',
            'title = "f"\nfootprint = true',
            "'footprint' must be a positive integer",
        ),
        (
            'title = "f"',
            'title = "f"\nexclusive = ["gpu", " "]',
            "an entry of 'exclusive' is empty",
        ),
        ('title = "f"', 'title = "f"\neffort = "max"', "'effort' must be one of"),
        ('title = "f"', 'title = "f"\nmodel = "two words"', "'model' must be one id"),
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
    ],
)
def test_check_reports_errors(run, old, new, message):
    code, result = run(TASKS.replace(old, new, 1), "check")
    assert code == 2
    assert any(message in e for e in result["errors"])


def test_model_and_effort_are_accepted_and_next_reports_them(run):
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


def test_cli_follows_a_symlink_and_reads_the_file(tmp_path: pathlib.Path):
    real = tmp_path / "real.toml"
    real.write_text(TASKS)
    link = tmp_path / "tasks.toml"
    link.symlink_to(real)
    done = subprocess.run(
        [sys.executable, str(SCRIPT), str(link), "ready"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(done.stdout)["ready"] == ["t3", "t2", "t4"]


def test_milestone_is_inherited_from_the_epic_and_breaks_priority_ties(run):
    text = TASKS.replace('priority = "P3"', 'priority = "P0"')
    _, result = run(text, "next", *caps())
    assert [t["id"] for t in result["start"]] == ["t3", "t4"]
    assert result["start"][0]["milestone"] == "m1"


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


def test_tree_shows_marks_ready_tasks_and_blockers(run_text):
    lines = [line for line in run_text(TASKS, "tree", "--all") if line]
    assert lines[0].split() == ["●", "f", "·", "P1", "t6"]
    assert lines[1].split() == ["┬", "━━──────", "1/3", "epic", "·", "P1", "E1", "·", "m1"]
    assert lines[2].split() == ["├─", "✔", "a", "·", "P2", "t1"]
    assert lines[3].split() == ["├─", "▶", "c", "·", "P0", "t3", "·", "blocks", "1"]
    assert lines[4].split() == ["└─", "▶", "b", "·", "P1", "t2", "·", "blocks", "1", "·", "waits", "t3", "(files)"]
    assert any(line.split()[:5] == ["○", "e", "·", "P2", "t5"] for line in lines)
    assert any(line.endswith("e · P2 t5 · waits E1 (dep)") for line in lines)
    assert any(line.split()[:5] == ["●", "f", "·", "P1", "t6"] for line in lines)


def test_board_folds_running_statuses_into_in_progress(run_text):
    lines = run_text(TASKS, "board")
    assert lines[0] == "In progress (1)"
    assert lines.index("Todo (4)") < lines.index("Done (1)")
    assert "  t5  e  P2  waits E1 (dep)" in lines


def test_board_lists_the_todo_cards_in_pick_up_order(run_text):
    lines = run_text(TASKS, "board")
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


def test_resume_flags_a_running_task_without_an_agent_id(tmp_path):
    text = TASKS.replace('status = "running"', 'status = "running"\nworktree = "/w/t6"')
    ledger = LEDGER.replace("| t6 | 1 | impl | agent-7 | running |\n", "")
    lines = _resume_text(tmp_path, text, ledger, constraints=True)
    assert any(line.startswith("  t6  agent=?  /w/t6") for line in lines)


def test_resume_reports_the_missing_constraints_file(tmp_path):
    lines = _resume_text(tmp_path, TASKS, LEDGER)
    assert lines[1].endswith("constraints.md  (MISSING, ask the human)")


def test_resume_without_a_ledger_still_prints_the_graph(tmp_path):
    lines = _resume_text(tmp_path, TASKS)
    assert lines[0].endswith("ORCHESTRATION.md  (missing)")
    assert lines[1].startswith("CONSTRAINTS none named")
    assert "DECISIONS (last 0)" in lines
    assert lines[-1] == "NEXT (none in the ledger)"


def test_resume_shows_the_question_and_the_blocker(tmp_path):
    text = TASKS + """
[[task]]
id = "t7"
title = "g"
status = "blocked"
blocker = "needs the token"
write_set = ["g/**"]

[[task]]
id = "t8"
title = "h"
status = "blocked"
blocker = "human: which branch?"
write_set = ["h/**"]
"""
    lines = _resume_text(tmp_path, text, LEDGER, constraints=True)
    assert "  t7  blocked: needs the token" in lines
    assert "  t8  human: which branch?" in lines


def test_resume_survives_an_undecodable_ledger(tmp_path):
    (tmp_path / "ORCHESTRATION.md").write_bytes(b"\xff\xfe")
    tasks = tmp_path / "tasks.toml"
    tasks.write_text(TASKS)
    out = io.StringIO()
    assert _load().run([str(tasks), "resume"], out) == 0
    assert out.getvalue().splitlines()[0].endswith("(unreadable)")


def test_resume_reads_a_constraints_path_with_spaces(tmp_path):
    ledger = LEDGER.replace("constraints.md", "my notes.md")
    (tmp_path / "my notes.md").write_text("rules")
    lines = _resume_text(tmp_path, TASKS, ledger)
    assert lines[1] == (
        f"CONSTRAINTS {tmp_path / 'my notes.md'}  (read it before acting)"
    )


def test_resume_clips_long_blockers_questions_and_waits(tmp_path):
    long = "x" * 200
    text = (
        TASKS
        + f"""
[[task]]
id = "t7"
title = "g"
status = "blocked"
blocker = "{long}"
write_set = ["g/**"]

[[task]]
id = "t8"
title = "h"
status = "blocked"
blocker = "human: {long}"
write_set = ["h/**"]
"""
    )
    lines = _resume_text(tmp_path, text, LEDGER, constraints=True)
    for prefix in ("  t7  blocked: ", "  t8  human: "):
        (line,) = (item for item in lines if item.startswith(prefix))
        assert len(line) == 100
        assert line.endswith("...")


@pytest.mark.parametrize("row", ["| t6 | 1 | impl | pending | pending |", "| t6 | 1 | impl | dead-1 | stale |"])
def test_resume_does_not_print_a_placeholder_or_stale_id(tmp_path, row):
    text = TASKS.replace('status = "running"', 'status = "running"\nworktree = "/w/t6"')
    ledger = LEDGER.replace("| t6 | 1 | impl | agent-7 | running |", row)
    lines = _resume_text(tmp_path, text, ledger, constraints=True)
    assert any(line.startswith("  t6  agent=?  /w/t6") for line in lines)


def test_resume_from_stdin_does_not_pretend_to_read_a_ledger(monkeypatch):
    monkeypatch.setattr(sys, "stdin", io.StringIO(TASKS))
    out = io.StringIO()
    assert _load().run(["-", "resume"], out) == 0
    lines = out.getvalue().splitlines()
    assert lines[0].endswith("ledger=-  (needs a tasks.toml path, not stdin)")
    assert lines[1] == "CONSTRAINTS unknown  (no ledger was read)"
    assert lines[-1] == "NEXT (none in the ledger)"


def test_resume_next_skips_placeholders_and_headings(tmp_path):
    ledger = LEDGER.split("## Next actions")[0] + (
        "## Next actions\n<the exact steps>\n### now\nintro\n- Do the thing.\n"
    )
    assert _resume_text(tmp_path, TASKS, ledger)[-1] == "NEXT Do the thing."
    bare = LEDGER.split("## Next actions")[0] + "## Next actions\n<the steps>\nplain\n"
    assert _resume_text(tmp_path, TASKS, bare)[-1] == "NEXT plain"


def test_session_key_shows_in_next_and_resume(run, tmp_path):
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
        for line in _resume_text(tmp_path, text)
    )


def test_next_and_resume_help_mention_the_session_key(capsys):
    for command in ("next", "resume"):
        with pytest.raises(SystemExit):
            _load().run(["tasks.toml", command, "--help"], io.StringIO())
        assert "session" in capsys.readouterr().out


def test_resume_help_describes_the_digest(capsys):
    with pytest.raises(SystemExit):
        _load().run(["tasks.toml", "resume", "--help"], io.StringIO())
    text = " ".join(capsys.readouterr().out.split())
    for part in ("READY", "RUNNING", "BLOCKED", "DECISIONS", "CONSTRAINTS", "NEXT"):
        assert part in text


def test_tree_hides_done_tasks_unless_all_is_given(run_text):
    lines = run_text(TASKS, "tree")
    assert not any(_label(line)[1] == "t1" for line in lines)
    assert any(_label(line)[1] == "t1" for line in run_text(TASKS, "tree", "--all"))


def test_tree_epic_limits_the_subtree(run_text):
    lines = run_text(TASKS, "tree", "--epic", "E1")
    assert _words(lines[0])[:2] == ["P1", "E1"]
    assert not any(" t4 " in line for line in lines)


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


OMITTED = [
    ("id", "a"),
    ("row", 1),
    ("type", "task"),
    ("status", ""),
    ("deps", []),
    ("integrate_after", []),
    ("parent", ""),
    ("priority", "P2"),
    ("milestone", ""),
    ("write_set", None),
    ("read_only", False),
    ("footprint", 1),
    ("exclusive", []),
    ("needs", []),
    ("title", ""),
    ("blocker", ""),
    ("answer", ""),
    ("worktree", ""),
    ("branch", ""),
    ("base", ""),
    ("stack", ""),
    ("overlap", ""),
    ("model", ""),
    ("effort", ""),
    ("session", ""),
    ("session_dir", ""),
    ("similar", []),
    ("done_at", ""),
    ("human_review", False),
    ("has_priority", False),
]


def test_task_build_fills_every_omitted_key_in_field_order():
    task = _load().Task.build(1, {"id": "a"})
    assert list(dataclasses.asdict(task).items()) == OMITTED


def test_task_build_takes_every_given_key_as_is():
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
    }
    task = module.Task.build(7, block)
    expected = {key: block.get(key, value) for key, value in OMITTED}
    assert dataclasses.asdict(task) == expected | {"row": 7, "has_priority": True}
    assert task.deps is block["deps"]
    assert module.KEYS == {"id", *module.COMPARED} == set(block)


def test_task_build_keeps_its_defaults_apart():
    module = _load()
    first, second = (module.Task.build(n, {"id": "a"}) for n in (1, 2))
    for key in ("deps", "exclusive", "similar"):
        getattr(first, key).append("x")
        assert getattr(second, key) == []
    assert second == module.Task.build(2, {"id": "a", "priority": "P2"})


def test_task_build_tells_an_empty_write_set_from_none_and_reads_a_numeric_stack():
    module = _load()
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


@pytest.mark.parametrize(
    ("args", "error"),
    [
        (
            ["frobnicate"],
            "ledger_graph.py: error: argument command: invalid choice: 'frobnicate'"
            " (choose from check, ready, epics, tree, board, resume, cleanup, next,"
            " explain, set, answer, event, register, attach, machine, compare)",
        ),
        ([], "ledger_graph.py: error: the following arguments are required: command"),
        (
            ["explain"],
            "ledger_graph.py tasks explain: error: the following arguments are required: id",
        ),
        (
            ["set", "t1"],
            "ledger_graph.py tasks set: error: the following arguments are required:"
            " field, value",
        ),
        (
            ["tree", "--color", "purple"],
            "ledger_graph.py tasks tree: error: argument --color: invalid choice:"
            " 'purple' (choose from auto, always, never)",
        ),
        (
            ["next", "--rw", "x"],
            "ledger_graph.py tasks next: error: argument --rw: invalid int value: 'x'",
        ),
        (
            ["tree", "--max-blocked", "x"],
            "ledger_graph.py tasks tree: error: argument --max-blocked:"
            " invalid int value: 'x'",
        ),
    ],
)
def test_cli_usage_errors_exit_2_on_stderr_only(tmp_path, args, error):
    (tmp_path / "tasks.toml").write_text(TASKS)
    done = _cli(tmp_path, "tasks.toml", *args)
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.startswith("usage: ledger_graph.py")
    assert done.stderr.splitlines()[-1] == error


@pytest.mark.parametrize(
    ("text", "args", "errors"),
    [
        (None, ["explain", "t1"], ["cannot read missing.toml: [Errno 2] No such file"
                                   " or directory: '{tmp}/missing.toml'"]),
        (TASKS, ["explain", "nope"], ["unknown id 'nope'"]),
        (TASKS, ["event", "nope"], ["unknown id 'nope'"]),
        (TASKS, ["tree", "--epic", "nope"], ["unknown id 'nope'"]),
        (TASKS, ["tree", "--max-blocked", "-1"], ["--max-blocked must not be negative"]),
        (TASKS, ["tree", "--width", "0"], ["--width must be a positive integer"]),
        (TASKS, ["set", "t1", "title", "x"],
         ["only 'priority' and 'status' can be set, not 'title'"]),
        (TASKS, ["set", "t1", "priority", "P9"],
         ["unknown priority 'P9', use one of P0, P1, P2, P3"]),
        (
            'format = 1\n[[task]]\nid = "x"\n',
            ["check"],
            [
                "x: empty title",
                "x: status must be one of ['blocked', 'deferred', 'done',"
                " 'in_progress', 'in_rev', 'pending', 'returned', 'running']",
            ],
        ),
        (TASKS, ["compare", "missing.toml"], ["cannot read missing.toml: [Errno 2] No"
                                              " such file or directory:"
                                              " '{tmp}/missing.toml'"]),
    ],
)
def test_cli_invalid_input_exits_2_with_json_on_stdout_only(tmp_path, text, args, errors):
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
    assert "cleanup: failed merged: refused" in (
        tmp_path / "ORCHESTRATION.md"
    ).read_text()


def test_an_epic_without_children_is_not_done_by_its_children(run):
    text = (
        TASKS
        + '\n[[task]]\nid = "E2"\ntype = "epic"\ntitle = "later"\nstatus = "pending"\n'
    )
    _, result = run(text, "epics")
    assert result["epics"][-1]["children_done"] is False


def test_tree_colors_only_when_asked(run_text):
    assert "\033[" not in "\n".join(run_text(TASKS, "tree", "--color", "auto"))
    assert "\033[" in "\n".join(run_text(TASKS, "tree", "--color", "always"))


def test_tree_marks_next_picks_inherited_blockers_and_blocked_epics(run_text):
    text = (
        'format = 1\n[[task]]\nid = "g"\ntitle = "gate"\nstatus = "pending"\nwrite_set = ["g"]\n'
        '[[task]]\nid = "E"\ntype = "epic"\ntitle = "epic"\nstatus = "blocked"\ndeps = ["g"]\n'
        '[[task]]\nid = "a"\ntitle = "a"\nstatus = "done"\nparent = "E"\nwrite_set = ["a"]\n'
        '[[task]]\nid = "b"\ntitle = "b"\nstatus = "pending"\nparent = "E"\nwrite_set = ["b"]\n'
    )
    lines = run_text(text, "tree", "--all")
    assert any(line.split()[:5] == ["▶", "gate", "·", "P2", "g"] for line in lines)
    assert any(_words(line)[:2] == ["P2", "E"] for line in lines)
    assert any("inherited E,g" in line for line in lines)


BLOCKED = TASKS.replace(
    'id = "t2"\ntitle = "b"\nstatus = "pending"',
    'id = "t2"\ntitle = "b"\nstatus = "blocked"\nblocker = "human: which API?"',
)
def test_next_lists_the_leaves_that_wait_on_an_event(run):
    text = TASKS.replace(
        'id = "t2"\ntitle = "b"\nstatus = "pending"',
        'id = "t2"\ntitle = "b"\nstatus = "blocked"\nblocker = "event: review on 1a2b3c4"',
    )
    _, result = run(text, "next", *caps())
    assert result["awaiting_event"] == [{"id": "t2", "event": "review on 1a2b3c4"}]
    assert result["awaiting_human"] == []
    assert result["blocked"] == ["t2"]


WORKTREE = BLOCKED.replace(
    'milestone = "m1"', 'milestone = "m1"\nworktree = "/w/E1"\nbranch = "e1"'
)


def test_next_lists_the_questions_that_wait_on_a_human(run):
    _, result = run(BLOCKED, "next", *caps())
    assert result["awaiting_human"] == [{"id": "t2", "question": "which API?"}]
    assert [t["id"] for t in result["start"]] == ["t3", "t4"]
    assert result["blocked"] == ["t2"]


def test_next_resolves_the_worktree_of_the_epic_for_its_leaves(run):
    _, result = run(WORKTREE, "next", *caps())
    start = {t["id"]: t for t in result["start"]}
    assert start["t3"]["worktree"] == "/w/E1"
    assert start["t3"]["branch"] == "e1"
    assert start["t4"]["worktree"] is None
    assert start["t4"]["branch"] is None


def test_next_resolves_each_workspace_key_from_its_nearest_owner(run):
    text = WORKTREE.replace(
        'id = "t3"\ntitle = "c"', 'id = "t3"\ntitle = "c"\nworktree = "/w/t3"'
    )
    _, result = run(text, "next", *caps())
    start = {t["id"]: t for t in result["start"]}
    assert start["t3"]["worktree"] == "/w/t3"
    assert start["t3"]["branch"] == "e1"


def test_tree_shows_the_branch_and_only_that_a_human_is_awaited(run_text):
    """The question stays in the inbox, and the branch is the end of the row."""

    lines = run_text(WORKTREE, "tree", "--all")
    row = next(line for line in lines if "✖" in line and " t2" in line)
    assert row.endswith("b · P1 t2 · blocks 1 · awaiting you · @ e1")
    assert not any("which API" in line or "wt:" in line for line in lines)
    assert not any("@" in line for line in lines if _label(line)[1] == "t4")


COLLAPSED = """format = 1

[[task]]
id = "g"
title = "gate"
status = "pending"
write_set = ["g"]

[[task]]
id = "E"
type = "epic"
title = "epic"
status = "pending"
deps = ["g"]

[[task]]
id = "a"
title = "first"
status = "pending"
parent = "E"
write_set = ["a"]

[[task]]
id = "b"
title = "second"
status = "pending"
parent = "E"
deps = ["a"]
write_set = ["b"]
"""


@pytest.mark.parametrize(
    ("status", "shown"),
    [("pending", True), ("blocked", True), ("in_progress", False), ("in_rev", False)],
)
def test_waits_is_only_for_a_task_that_has_not_started(run_text, status, shown):
    """A started task has its deps in its base, so its row does not say waits."""

    text = TASKS.replace('deps = ["E1"]', 'deps = ["t2", "t3"]')
    text = text.replace(
        'id = "t5"\ntitle = "e"\nstatus = "pending"',
        f'id = "t5"\ntitle = "e"\nstatus = "{status}"',
    )
    row = _row(run_text(text, "tree", "--all"), "t5")
    assert ("waits t2,t3" in row) is shown


def test_waits_shows_two_ids_and_the_count_of_the_others(run_text):
    text = TASKS.replace('deps = ["E1"]', 'deps = ["t2", "t3", "t4"]')
    row = _row(run_text(text, "tree", "--all"), "t5")
    assert "waits t2,t3 +1" in row


def test_an_epic_without_a_priority_or_an_open_leaf_shows_dashes(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("none", status="done"),
            _block("none1", "done", parent="none"),
            _epic("last", priority="P3"),
            _block("last1", parent="last"),
        ]
    )
    lines = run_text(text, "tree", "--all", "--width", "300")
    assert [i for i in _order(lines) if i in {"none", "last"}] == ["none", "last"]
    assert any(_label(line) == ("--", "none") for line in lines)


def test_an_epic_without_a_priority_shows_the_one_it_sorts_by(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("derived"),
            _block("d1", parent="derived", priority="P0"),
            _block("d2", parent="derived", priority="P3"),
            _epic("top", priority="P0"),
            _block("t1", parent="top"),
        ]
    )
    lines = run_text(text, "tree", "--width", "300")
    shown = {i: p for p, i in map(_label, _rows(lines))}
    assert shown["derived"] == "P0"
    assert shown["top"] == "P0"
    assert [i for i in _order(lines) if i in {"derived", "top"}] == ["derived", "top"]


def test_an_open_epic_without_an_open_leaf_sorts_as_open_with_the_default(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("accept"),
            _block("a1", "done", parent="accept"),
            _epic("empty"),
            _epic("p3", priority="P3"),
            _block("p3a", parent="p3"),
            _epic("p1", priority="P1"),
            _block("p1a", parent="p1"),
        ]
    )
    lines = run_text(text, "tree", "--width", "300")
    roots = [i for i in _order(lines) if i in {"accept", "empty", "p3", "p1"}]
    assert roots == ["p1", "empty", "p3"]
    assert any(_label(line) == ("P2", "empty") for line in lines)


def test_a_milestone_shows_on_a_task_only_when_it_differs_from_its_epic(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("E", milestone="m1"),
            _block("same", parent="E", milestone="m1"),
            _block("other", parent="E", milestone="m2"),
            _block("inherits", parent="E"),
            _block("loose", milestone="m3"),
            _block("bare"),
        ]
    )
    lines = run_text(text, "tree", "--width", "300")
    rows = {}
    for line in lines:
        w = _words(line)
        if len(w) > 2 and w[0] in SYMBOLS:
            rows[w[2]] = w
        elif len(w) > 1 and re.fullmatch(r"P\d|--", w[0]):
            rows[w[1]] = w
    assert "m1" in rows["E"]
    assert "m1" not in rows["same"] and "m1" not in rows["inherits"]
    assert "m2" in rows["other"]
    assert "m3" in rows["loose"]
    assert not {"m1", "m2", "m3"} & set(rows["bare"])


def test_done_tasks_and_epics_sort_first_in_reverse_file_order_when_shown(run_text):
    text = "format = 1\n" + "".join(
        [
            _block("busy", "running", priority="P0"),
            _block("late", "done", priority="P3"),
            _epic("E", priority="P0"),
            _block("e1", "running", parent="E"),
            _epic("old", status="done"),
            _block("old1", "done", parent="old"),
            _block("early", "done", priority="P0"),
        ]
    )
    lines = run_text(text, "tree", "--all", "--width", "300")
    top = [i for i in _order(lines) if i in {"busy", "late", "E", "old", "early"}]
    assert top == ["early", "old", "late", "busy", "E"]
    assert any(_label(line) == ("--", "old") for line in lines)
    hidden = run_text(text, "tree", "--width", "300")
    assert _order(hidden)[:2] == ["busy", "E"]


def test_a_ready_task_held_by_a_write_set_says_held_by(run_text):
    """t2 and t3 overlap: t3 starts, t2 keeps its ready mark and says who holds it."""

    lines = run_text(TASKS, "tree", "--all", "--width", "300")
    held = _row(lines, "t2")
    assert "▶" in held
    assert held.endswith("waits t3 (files)")
    assert "(files)" not in _row(lines, "t3")
    assert "waits X (files): ready, but its write_set overlaps" in "\n".join(lines)
    assert "  t2  b  P1  waits t3 (files)" in run_text(TASKS, "board")


def test_a_running_task_holds_before_a_conflicting_ready_one(run_text):
    """The running task is named, and a deps wait still says waits, not held by."""

    text = TASKS.replace(
        'id = "t4"\ntitle = "d"\nstatus = "pending"',
        'id = "t4"\ntitle = "d"\nstatus = "in_progress"',
    ).replace('write_set = ["docs/**"]', 'write_set = ["src/b/x.py"]', 1)
    lines = run_text(text, "tree", "--all", "--width", "300")
    assert _row(lines, "t3").endswith("waits t4 (files)")
    assert _row(lines, "t2").endswith("waits t4 (files)")
    assert "(files)" not in _row(lines, "t5")


def test_the_tree_draws_no_wave_bar(run_text):
    """Siblings sort by the one key and no row carries a bar."""

    lines = run_text(TASKS, "tree", "--all")
    assert not any("┃" in line for line in lines)


def test_an_epic_that_only_waits_collapses_to_its_own_row(run_text):
    """It shows what it hides, and --all or --epic opens it again."""

    lines = run_text(COLLAPSED, "tree")
    row = next(n for n, line in enumerate(lines) if _label(line)[1] == "E")
    assert lines[row].split() == [
        "─",
        "────────",
        "0/2",
        "epic",
        "·",
        "P2",
        "E",
        "·",
        "2",
        "tasks",
        "hidden",
        "·",
        "waits",
        "g",
        "(dep)",
    ]
    assert not any(_label(line)[1] == "a" for line in lines)
    assert any(_label(line)[1] == "a" for line in run_text(COLLAPSED, "tree", "--all"))
    assert any(
        _label(line)[1] == "a" for line in run_text(COLLAPSED, "tree", "--epic", "E")
    )


NESTED = """format = 1

[[task]]
id = "E"
type = "epic"
title = "top"
status = "pending"

[[task]]
id = "S1"
type = "epic"
title = "first branch"
status = "pending"
parent = "E"

[[task]]
id = "a"
title = "a title long enough to wrap below the third level"
status = "pending"
parent = "S1"
write_set = ["a"]

[[task]]
id = "b"
title = "second"
status = "pending"
parent = "S1"
deps = ["a"]
write_set = ["b"]

[[task]]
id = "S2"
type = "epic"
title = "other branch"
status = "pending"
parent = "E"

[[task]]
id = "c"
title = "third"
status = "pending"
parent = "S2"
write_set = ["c"]

[[task]]
id = "S3"
type = "epic"
title = "last branch"
status = "pending"
parent = "E"
deps = ["S1"]

[[task]]
id = "d"
title = "fourth"
status = "pending"
parent = "S3"
write_set = ["d"]
"""


def test_an_epic_inside_an_epic_keeps_every_rule(run_text):
    """Order, collapse, indent and width hold three levels down."""

    lines = run_text(NESTED, "tree", "--width", "60")
    assert max(len(line) for line in lines) <= 60

    # S3 waits for S1 and hides its only task.
    assert not any("┃" in line for line in lines)
    shut = next(n for n, line in enumerate(lines) if " S3 " in line)
    assert "1 task hidden" in " ".join(lines[shut : shut + 2])
    assert not any(" d " in line for line in lines)
    assert any(" d " in line for line in run_text(NESTED, "tree", "--all"))

    row = next(n for n, line in enumerate(lines) if " a " in line)
    assert "│  ├─ " in lines[row]
    assert "…" in lines[row]


@pytest.mark.parametrize("width", [20, 32, 48, 60, 80, 120])
def test_no_row_is_wider_than_the_width(run_text, width: int):
    """Five levels of epics and a 60 character id still fit the width."""

    lines = run_text(_deep_ledger(), "tree", "--all", "--width", str(width))
    assert max(len(line) for line in lines) <= width
    if width >= 32:
        assert any("x" * 10 in line for line in lines)


MARKED = """format = 1

[[task]]
id = "gate"
title = "gate"
status = "pending"
write_set = ["g"]

[[task]]
id = "other"
title = "other"
status = "pending"
write_set = ["o"]

[[task]]
id = "push-stack"
title = "Push the task graph layers as draft PRs"
status = "blocked"
blocker = "human: which remote?"
deps = ["gate", "other"]
branch = "push-branch"
write_set = ["p"]
"""


def test_a_title_with_a_newline_or_tabs_is_one_row(run_text):
    text = MARKED.replace(
        'title = "other"', 'title = "first\\nsecond\\t  long\\r\\nlast"'
    )
    lines = run_text(text, "tree", "--width", "100")
    assert "first second long last" in _row(lines, "other")
    assert len(_rows(lines)) == 3


def _two_epics(many: int) -> str:
    blocks = ['format = 1\n']
    for epic, count in (("A", many), ("B", 2)):
        blocks.append(
            f'[[task]]\nid = "{epic}"\ntype = "epic"\ntitle = "Epic {epic}"\nstatus = "pending"\n'
        )
        for n in range(count):
            status = "done" if n == 0 else "pending"
            blocks.append(
                f'[[task]]\nid = "{epic}{n}"\ntitle = "leaf"\nstatus = "{status}"\n'
                f'parent = "{epic}"\nwrite_set = ["{epic}{n}"]\n'
            )
    blocks.append('[[task]]\nid = "float"\ntitle = "floating"\nstatus = "pending"\nwrite_set = ["f"]\n')
    return "\n".join(blocks)


def _epic_with(statuses: list[str]) -> str:
    blocks = ['format = 1\n\n[[task]]\nid = "E"\ntitle = "epic"\ntype = "epic"\nstatus = "pending"\n']
    for n, status in enumerate(statuses):
        blocks.append(
            f'[[task]]\nid = "L{n}"\ntitle = "leaf"\nstatus = "{status}"\n'
            f'parent = "E"\nwrite_set = ["L{n}"]\n'
        )
    return "\n".join(blocks)


def test_the_bar_adds_a_segment_for_the_started_leaves(run_text):
    statuses = ["done", "in_progress", "in_rev", "pending"]
    row = _row(run_text(_epic_with(statuses), "tree", "--width", "100"), "E")
    assert row.startswith("┬ ━━━━━━── 1/4 ")
    ranges = [["pending"] * 4, ["done", "pending"], ["in_progress"] * 3 + ["done"]]
    for leaves, bar in zip(ranges, ["────────", "━━━━────", "━━━━━━━━"], strict=True):
        row = _row(run_text(_epic_with(leaves), "tree", "--width", "100"), "E")
        assert row.split()[1] == bar
    assert _row(run_text(_epic_with([]), "tree", "--width", "100"), "E").split()[1] == "────────"


def test_the_bar_colors_done_green_and_started_yellow():
    ledger_graph = _load()
    card = ledger_graph._legend(plain=False, styled=True)
    key = next(p for p in card if "in progress" in p.plain)
    start = key.plain.index("━")
    console = ledger_graph.rich.console.Console()
    assert [str(key.get_style_at_offset(console, start + n)) for n in (0, 1)] == [
        "dim green",
        "dim yellow",
    ]


def test_the_bar_starts_the_epic_row_and_the_count_is_not_padded(run_text):
    lines = run_text(_two_epics(12), "tree", "--width", "100")
    a = _row(lines, "A")
    b = _row(lines, "B")
    assert a.startswith("┬ ──────── 1/12 Epic A") and b.startswith("┬ ━━━━──── 1/2 Epic B")
    assert any(line.startswith("├─ ▶ leaf · P2 A1") for line in lines)
    assert _row(lines, "float").split()[:5] == ["▶", "floating", "·", "P2", "float"]


def test_without_color_the_epic_keeps_px_after_a_plain_bar(run_text, monkeypatch):
    monkeypatch.setenv("NO_COLOR", "1")
    lines = run_text(_two_epics(2), "tree", "--color", "always", "--width", "100")
    plain = [re.sub(r"\033\[[0-9;]*m", "", line) for line in lines]
    assert next(line for line in plain if "Epic A" in line).startswith(
        "┬ ━━━━──── 1/2 Epic A · P2 A"
    )


def test_an_epic_without_leaves_shows_an_empty_bar_and_stays_visible(run_text, monkeypatch):
    text = (
        _two_epics(2)
        + '\n[[task]]\nid = "E2"\ntype = "epic"\ntitle = "later"\nstatus = "pending"\n'
    )
    lines = run_text(text, "tree", "--width", "100")
    empty = _row(lines, "E2")
    assert empty.startswith("─ ──────── 0/0 later")
    assert empty.index("later") == _row(lines, "B").index("Epic B")
    narrow = _row(run_text(text, "tree", "--width", "30"), "E2")
    assert narrow.startswith("─ ──────── 0/0")
    monkeypatch.setenv("NO_COLOR", "1")
    colored = run_text(text, "tree", "--color", "always", "--width", "100")
    plain = [re.sub(r"\033\[[0-9;]*m", "", line) for line in colored]
    assert next(line for line in plain if "later" in line).startswith("─ ──────── 0/0 later")


def test_a_narrow_width_shortens_the_title_and_keeps_the_bar(run_text):
    row = run_text(_two_epics(2), "tree", "--width", "24")[0]
    assert len(row) <= 24
    assert row.startswith("┬ ━━━━──── 1/2 ")
    assert row.endswith("· P2 A")
    assert "…" in row


def test_a_very_narrow_width_drops_the_connector_before_the_bar(run_text):
    row = run_text(_two_epics(2), "tree", "--width", "20")[0]
    assert len(row) <= 20
    assert "┬" not in row
    assert row.startswith("━━━━──── 1/2 ")


def test_the_connector_joins_the_epic_to_its_children_in_their_column(run_text):
    lines = run_text(_two_epics(2), "tree", "--width", "36")
    a = next(i for i, line in enumerate(lines) if "Epic A" in line)
    assert lines[a].index("┬") == lines[a + 1].index("└─") == 0
    assert all(len(line) <= 36 for line in lines)


def test_a_nested_epic_has_its_connector_in_the_column_of_its_children(run_text):
    text = (
        'format = 1\n[[task]]\nid = "O"\ntype = "epic"\ntitle = "outer"\nstatus = "pending"\n'
        '[[task]]\nid = "I"\ntype = "epic"\ntitle = "inner"\nstatus = "pending"\nparent = "O"\n'
        '[[task]]\nid = "t"\ntitle = "leaf"\nstatus = "pending"\nparent = "I"\nwrite_set = ["t"]\n'
    )
    lines = run_text(text, "tree", "--width", "80")
    inner = _row(lines, "I")
    assert inner.startswith("└─ ┬ ")
    leaf = _row(lines, "t")
    assert leaf.index("└─") == inner.index("┬") == 3


def test_a_hidden_epic_has_two_blanks_and_counters_start_together(run_text, monkeypatch):
    lines = run_text(_two_epics(12), "tree", "--all", "--width", "100")
    a, b = _row(lines, "A"), _row(lines, "B")
    assert a.index("1/12") == b.index("1/2")
    monkeypatch.setenv("NO_COLOR", "1")
    plain = run_text(_two_epics(2), "tree", "--color", "always", "--width", "100")
    plain = [re.sub(r"\033\[[0-9;]*m", "", line) for line in plain]
    assert next(line for line in plain if "Epic A" in line).startswith("┬ ")


def test_an_epic_title_is_drawn_in_capitals(run_text, monkeypatch):
    monkeypatch.delenv("LEDGER_PLAIN_EPIC")
    lines = run_text(_two_epics(2), "tree", "--all", "--width", "100")
    assert "EPIC A" in _row(lines, "A")
    assert "leaf" in _row(lines, "A0")


def _floating(*ids: str) -> str:
    return "".join(
        f'[[task]]\nid = "{i}"\ntitle = "t {i}"\nstatus = "pending"\nwrite_set = ["{i}"]\n\n'
        for i in ids
    )


def test_a_blank_line_separates_an_epic_from_top_level_tasks_only(run_text):
    head = "format = 1\n\n"
    epic = '[[task]]\nid = "E"\ntype = "epic"\ntitle = "epic"\nstatus = "pending"\n\n'
    leaf = '[[task]]\nid = "L"\ntitle = "leaf"\nstatus = "pending"\nparent = "E"\nwrite_set = ["L"]\n\n'
    other = epic.replace('"E"', '"F"') + leaf.replace('"E"', '"F"').replace('"L"', '"M"')

    def blanks(text: str) -> list[int]:
        lines = run_text(head + text, "tree", "--width", "100")
        return [n for n, line in enumerate(_rows(lines)) if not line]

    assert blanks(_floating("a", "b")) == []
    assert blanks(_floating("a") + epic + leaf + _floating("b")) == [1, 4]
    assert blanks(epic + leaf + other) == [2]


def test_plain_epic_env_keeps_the_title_as_written(run_text):
    lines = run_text(_two_epics(2), "tree", "--all", "--width", "100")
    assert "Epic A" in _row(lines, "A")


def test_the_epic_row_color_is_unchanged_by_capitals(run_text, monkeypatch):
    colored = run_text(_two_epics(2), "tree", "--color", "always", "--width", "100")
    monkeypatch.delenv("LEDGER_PLAIN_EPIC")
    small = run_text(_two_epics(2), "tree", "--color", "always", "--width", "100")
    sgr = lambda rows: [re.findall(r"\033\[[0-9;]*m", r) for r in rows]
    assert sgr(colored) == sgr(small)


def test_the_legend_lists_every_mark_whatever_the_tree_shows(run_text):
    """The legend of a tree without worktrees or deferred tasks is the same."""

    for lines in (run_text(TASKS, "tree", "--width", "300"), run_text(WORKTREE, "tree", "--all", "--width", "300")):
        plain = "\n".join(lines)
        assert "@ the branch of the task" in plain
        assert "◌ deferred" in plain
        assert "blocks N" in plain
        assert "waits X (dep)" in plain
        assert "⧉" in plain and "◆" in plain and "✎N draft" in plain
        assert "PX before the id: its priority" in plain
        assert "wt:" not in plain


def test_the_legend_keys_the_prefix_the_italic_and_the_glyph_in_color(run_text):
    """The key of a colored tree shows the same text as the plain one, plus style."""

    colored = run_text(WORKTREE, "tree", "--all", "--width", "300", "--color", "always")
    text = "\n".join(colored)
    assert "id color: P0 red, P1 yellow, P2 blue, P3 grey" in re.sub(
        r"\033\[[0-9;]*m", "", text
    )
    assert "bold running" in text
    assert "\033[3mitalic" in text
    assert "starts soon" in text
    assert "\ue0a0 the branch of the task" in text


def test_bold_marks_in_a_title_become_bold_and_never_show(run_text):
    text = TASKS.replace('title = "e"', 'title = "fix **the cache** now"')
    colored = "\n".join(run_text(text, "tree", "--all", "--color", "always"))
    assert "**" not in colored
    assert re.search(r"\033\[(1|2;1|1;2)mthe cache\033\[0m", colored)
    plain = "\n".join(run_text(text, "tree", "--all"))
    assert "fix the cache now" in plain
    assert "**" not in plain


@pytest.mark.parametrize(
    ("title", "shown", "bold"),
    [
        ("use 2**10 slots, see **README**", "use 2**10 slots, see README", ["README"]),
        ("**** then **x**", "**** then x", ["x"]),
        ("***triple*** and **a**", "***triple*** and a", ["a"]),
        ("**a** **b** end", "a b end", ["a", "b"]),
        ("x **unbalanced", "x **unbalanced", []),
    ],
)
def test_bold_marks_need_flanking_asterisks(title: str, shown: str, bold: list[str]):
    text = _load()._title(title, italic=False)
    assert text.plain == shown
    assert [
        text.plain[s.start : s.end] for s in text.spans if s.style == "bold"
    ] == bold


def test_next_is_italic_in_color_and_has_no_mark_in_plain_text(run_text):
    """The picked task is italic and has no label, so plain text shows no mark."""

    colored = run_text(TASKS, "tree", "--all", "--color", "always")
    picked = next(line for line in colored if "t3" in line)
    assert "\033[3mc\033[0m" in picked
    assert "<- next" not in picked
    waiting = next(line for line in colored if "t5" in line)
    assert "\033[3m" not in waiting

    plain = run_text(TASKS, "tree", "--all")
    assert _row(plain, "t3").endswith("c · P0 t3 · blocks 1")
    assert "<- next" not in "\n".join(plain)
    assert "\033[" not in "\n".join(plain)


def _italic_ids(lines: list[str]) -> set[str]:
    """The ids of the rows of a colored tree whose title is italic."""

    return set(re.findall(r"\033\[3m(\w+)\033\[0m", "\n".join(_rows(lines))))


def test_italic_marks_what_starts_now_or_when_the_running_work_ends(run_text):
    """Startable, held by a write set or a cap, or waiting only on running work."""

    text = "format = 1\n" + "".join(
        [
            _block("run", "in_progress"),
            _block("after", deps="run").replace('"run"', '["run"]'),
            _block("rev", "in_rev"),
            _block("behind", deps="rev").replace('"rev"', '["rev"]'),
            _block("slot"),
            _block("hold").replace('["hold"]', '["slot"]'),
        ]
    )
    lines = run_text(text, "tree", "--color", "always", "--width", "300")
    assert _italic_ids(lines) == {"after", "behind", "slot", "hold"}


def test_italic_skips_what_waits_on_pending_blocked_or_human_work(run_text):
    """A task behind a pending or blocked task is not italic, even next to a running one."""

    text = "format = 1\n" + "".join(
        [
            _block("run", "in_progress"),
            _block("mid", deps="run").replace('"run"', '["run"]'),
            _block("late", deps="mid").replace('"mid"', '["mid"]'),
            _block("stuck", "blocked", blocker="human: which one?"),
            _block("behind", deps="stuck").replace('"stuck"', '["stuck"]'),
            _block("both", deps="run").replace('"run"', '["run", "mid"]'),
        ]
    )
    lines = run_text(text, "tree", "--color", "always", "--width", "300")
    assert _italic_ids(lines) == {"mid"}


def test_italic_follows_an_epic_dep_whose_open_leaves_all_run(run_text):
    """An epic is running when every open leaf is, and a pending leaf stops that."""

    text = "format = 1\n" + "".join(
        [
            _epic("busy"),
            _block("b1", "in_progress", parent="busy"),
            _block("b2", "done", parent="busy"),
            _epic("mixed"),
            _block("m1", "in_progress", parent="mixed"),
            _block("m2", parent="mixed"),
            _block("onbusy", deps="busy").replace('"busy"', '["busy"]'),
            _block("onmixed", deps="mixed").replace('"mixed"', '["mixed"]'),
        ]
    )
    lines = run_text(text, "tree", "--color", "always", "--width", "300")
    assert _italic_ids(lines) == {"onbusy", "m2"}


def test_no_color_keeps_the_plain_markers(run_text, monkeypatch):
    """NO_COLOR with a forced terminal drops the colors but keeps the italic."""

    monkeypatch.setenv("NO_COLOR", "1")
    lines = run_text(TASKS, "tree", "--all", "--color", "always")
    text = re.sub(r"\033\[[0-9;]*m", "", "\n".join(lines))
    assert "38;5;" not in "\n".join(lines)
    assert "P0 t3" in text
    assert "P1 t2" in text
    assert "\033[3mc\033[0m" in "\n".join(lines)
    assert "<- next" not in text


def test_without_color_every_row_has_a_priority_prefix_and_aligned_ids(
    run_text, monkeypatch
):
    """NO_COLOR prints PX before the id, an epic at depth included."""

    monkeypatch.setenv("NO_COLOR", "1")
    text = "format = 1\n" + "".join(
        [
            _epic("E", priority="P0"),
            _epic("S", parent="E", priority="P3"),
            _block("a", parent="S", priority="P1"),
            _block("b", parent="E"),
        ]
    )
    lines = run_text(text, "tree", "--color", "always", "--width", "300")
    rows = _rows(lines)
    assert [_label(re.sub(r"\033\[[0-9;]*m", "", row))[0] for row in rows] == [
        "P0",
        "P2",
        "P3",
        "P1",
    ]
    assert any("PX before the id" in line for line in lines)


def test_a_narrow_row_cuts_the_title_and_never_the_id(run_text):
    text = "format = 1\n" + _block("long-id").replace(
        'title = "long-id"', 'title = "a very long title indeed"'
    )
    for width in range(16, 40):
        row = _row(run_text(text, "tree", "--width", str(width)), "long-id")
        assert len(row) <= width
        assert row.endswith("P2 long-id")


def test_the_legend_explains_the_priority_colors(run_text, monkeypatch):
    """The legend names the colors, or the PX prefix when there are none."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    colored = "\n".join(run_text(TASKS, "tree", "--color", "always", "--width", "300"))
    assert "id color: P0 red, P1 yellow, P2 blue, P3 grey" in re.sub(
        r"\033\[[0-9;]*m", "", colored
    )
    assert "bold running" in colored


def test_the_legend_paints_p0_to_p3_in_the_id_colors(monkeypatch):
    """Four distinct styles on the priority names, none without colors."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    ledger_graph = _load()
    key = ledger_graph._priority_key()
    assert key.plain == "id color: P0 red, P1 yellow, P2 blue, P3 grey"
    styles = {
        name: str(span.style)
        for span in key.spans
        for name in ledger_graph.PRIORITY_STYLES
        if key.plain[span.start : span.end] == name
    }
    assert styles == ledger_graph.PRIORITY_STYLES
    assert len(set(styles.values())) == 4
    plain = ledger_graph._legend(plain=True, styled=False)
    assert all("P0" not in p.plain for p in plain)


def test_the_title_is_bold_for_a_running_task_and_italic_for_next(run_text, monkeypatch):
    """Bold goes on the title of an in_progress task, never on its id."""

    monkeypatch.delenv("NO_COLOR", raising=False)
    text = "format = 1\n" + "".join(
        [
            _block("run", "in_progress"),
            _block("rdy"),
            _block("blk", "blocked"),
            '\n[[task]]\nid = "wait"\ntitle = "wait"\nstatus = "pending"\n'
            'deps = ["run"]\nwrite_set = ["wait"]\n',
        ]
    )
    shown = "\n".join(run_text(text, "tree", "--color", "always", "--width", "300"))
    shown = shown.split("\n\n")[0]
    assert "\033[1mrun\033[0m" in shown
    assert "\033[1mrun" not in shown.split("run", 1)[0]
    assert shown.count("\033[1m") == 1
    assert "\033[3mrdy\033[0m" in shown
    assert shown.count("\033[3m") == 2
    assert "\033[3mwait\033[0m" in shown


def test_board_shows_the_question_first_in_its_column(run_text):
    lines = run_text(WORKTREE, "board")
    todo = next(n for n, line in enumerate(lines) if line.startswith("Todo ("))
    assert lines[todo + 1] == "  t2  b  P1  awaiting you: which API?  wt:e1"


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


def test_set_priority_keeps_comments_and_key_order(steered: pathlib.Path):
    module = _load()
    module.set_priority(steered, "g2", "P0", seen_mtime_ns=steered.stat().st_mtime_ns)
    text = steered.read_text()
    assert 'priority = "P0"' in text
    assert "# The gate." in text
    assert "# the first one" in text
    assert text.index('priority = "P0"') < text.index('deps = ["g1"]')
    assert not list(steered.parent.glob("*.tmp"))


def test_concurrent_writers_keep_the_file_valid(steered: pathlib.Path):
    import threading

    module = _load()
    failures: list[BaseException] = []

    def attempt(task_id: str, priority: str) -> bool:
        try:
            module.set_priority(
                steered, task_id, priority, seen_mtime_ns=steered.stat().st_mtime_ns
            )
        except ValueError as error:
            if "changed" not in str(error):
                failures.append(error)
            return "changed" not in str(error)
        else:
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
    by_id = {t.id: t for t in tasks}
    assert by_id["g1"].priority == "P0"
    assert by_id["g2"].priority == "P1"
    assert not list(steered.parent.glob("*.tmp"))


def test_a_failing_write_leaves_no_temporary_file(
    steered: pathlib.Path, monkeypatch: pytest.MonkeyPatch
):
    module = _load()
    before = steered.read_text()

    def boom(*_args, **_kwargs):
        msg = "disk full"
        raise OSError(msg)

    monkeypatch.setattr(module.os, "fsync", boom)
    with pytest.raises(OSError, match="disk full"):
        module.set_priority(
            steered, "g2", "P0", seen_mtime_ns=steered.stat().st_mtime_ns
        )

    assert steered.read_text() == before
    assert not list(steered.parent.glob("*.tmp"))


def test_set_priority_adds_the_line_after_the_id(steered: pathlib.Path):
    module = _load()
    module.set_priority(steered, "g1", "P1", seen_mtime_ns=steered.stat().st_mtime_ns)
    lines = steered.read_text().splitlines()
    assert lines[lines.index('id = "g1"') + 1] == 'priority = "P1"'
    assert lines[lines.index('id = "g2"') + 1] == 'title = "two"'


def test_set_priority_appends_a_steering_log_line(steered: pathlib.Path):
    module = _load()
    module.set_priority(steered, "g2", "P3", seen_mtime_ns=steered.stat().st_mtime_ns)
    log = (steered.parent / "steering.log").read_text().splitlines()
    assert len(log) == 1
    assert log[0].endswith("g2 P2 -> P3")


def test_set_priority_refuses_an_unknown_id_and_an_unknown_priority(
    steered: pathlib.Path,
):
    module = _load()
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        module.set_priority(
            steered, "nope", "P0", seen_mtime_ns=steered.stat().st_mtime_ns
        )
    with pytest.raises(ValueError, match="unknown priority 'P9'"):
        module.set_priority(
            steered, "g2", "P9", seen_mtime_ns=steered.stat().st_mtime_ns
        )
    assert steered.read_text() == STEERED


def test_set_priority_refuses_a_file_that_changed_meanwhile(steered: pathlib.Path):
    module = _load()
    with pytest.raises(ValueError, match="changed since it was read"):
        module.set_priority(steered, "g2", "P0", seen_mtime_ns=1)
    assert steered.read_text() == STEERED


def test_set_priority_refuses_a_file_check_would_refuse(steered: pathlib.Path):
    module = _load()
    steered.write_text(STEERED.replace("format = 1", "format = 2"))
    with pytest.raises(ValueError, match="unsupported format"):
        module.set_priority(
            steered, "g2", "P0", seen_mtime_ns=steered.stat().st_mtime_ns
        )


def test_set_priority_writes_the_target_of_a_symlink(tmp_path: pathlib.Path):
    module = _load()
    real = tmp_path / "ledger"
    real.mkdir()
    target = real / "tasks.toml"
    target.write_text(STEERED)
    link = tmp_path / "tasks.toml"
    link.symlink_to(target)

    module.set_priority(link, "g2", "P1", seen_mtime_ns=link.stat().st_mtime_ns)

    assert link.is_symlink()
    assert 'priority = "P1"' in target.read_text()
    assert (real / "steering.log").exists()
    assert not (tmp_path / "steering.log").exists()


def test_set_command_writes_the_priority(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g2", "priority", "P0")
    assert code == 0
    assert result == {"ok": True, "id": "g2", "priority": "P0"}
    assert 'priority = "P0"' in (tmp_path / "tasks.toml").read_text()


def test_set_command_refuses_any_other_field(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g2", "title", "x")
    assert code == 2
    assert "only 'priority' and 'status' can be set, not 'title'" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == STEERED


def test_set_priority_command_prints_no_next(run):
    _, result = run(STEERED, "set", "g2", "priority", "P0")
    assert "start" not in result


def test_set_done_writes_the_status_and_answers_with_next(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g1", "status", "done")
    assert code == 0
    assert list(result) == ["ok", "id", "status", "limits", "start", "skipped"]
    assert (result["ok"], result["id"], result["status"]) == (True, "g1", "done")
    assert [t["id"] for t in result["start"]] == ["g2"]
    text = (tmp_path / "tasks.toml").read_text()
    match = re.search(r'^done_at = "([^"]+)"$', text, re.MULTILINE)
    assert match
    stamp = match.group(1)
    assert text == STEERED.replace(
        'title = "gate"      # the first one\nstatus = "pending"',
        'title = "gate"      # the first one\nstatus = "done"',
    ).replace('id = "g1"\n', f'id = "g1"\ndone_at = "{stamp}"\n', 1)
    log = (tmp_path / "steering.log").read_text().splitlines()
    assert log[0].endswith("g1 status pending -> done")


def test_integrate_after_lets_a_task_start_but_not_finish(run, tmp_path: pathlib.Path):
    text = (
        'format = 1\n\n[[task]]\nid = "a"\ntitle = "a"\nstatus = "in_progress"\n'
        'read_only = true\n\n[[task]]\nid = "b"\ntitle = "b"\nstatus = "pending"\n'
        'read_only = true\nintegrate_after = ["a"]\n'
    )
    _, result = run(text, "ready")
    assert result["ready"] == ["b"]
    code, result = run(text.replace('"pending"', '"in_progress"'), "set", "b", "status", "done")
    assert code == 2
    assert "integrates after a" in result["errors"][0]
    run_after(run, tmp_path, "a", "done")
    code, _ = run_after(run, tmp_path, "b", "done")
    assert code == 0


def test_check_refuses_a_bad_integrate_after(run):
    base = (
        'format = 1\n\n[[task]]\nid = "a"\ntitle = "a"\nstatus = "pending"\n'
        'read_only = true\nintegrate_after = ["%s"]\n\n[[task]]\nid = "b"\n'
        'title = "b"\nstatus = "pending"\nread_only = true\nintegrate_after = ["a"]\n'
    )
    code, result = run(base % "x", "check")
    assert code == 2
    assert "integrate_after 'x' is not another task" in result["errors"][0]
    code, result = run(base % "b", "check")
    assert code == 2
    assert "integrate_after cycle" in result["errors"][0]


def test_set_done_stamps_done_at_and_leaving_done_removes_it(run, tmp_path: pathlib.Path):
    run(STEERED, "set", "g1", "status", "done")
    text = (tmp_path / "tasks.toml").read_text()
    assert re.search(r'^done_at = "\d{4}-\d\d-\d\dT[\d:]+[+-]\d\d:\d\d"$', text, re.MULTILINE)
    code, _ = run_after(run, tmp_path, "g1", "pending")
    assert code == 0
    assert "done_at" not in (tmp_path / "tasks.toml").read_text()


def run_after(run, tmp_path: pathlib.Path, task_id: str, status: str):
    """Set a status on the file a test already wrote, without rewriting it."""

    module = _load()
    out = io.StringIO()
    code = module.run([str(tmp_path / "tasks.toml"), "set", task_id, "status", status], out)
    return code, json.loads(out.getvalue())


def _done_blocks(*stamps: str | None) -> str:
    return "format = 1\n\n" + "".join(
        f'[[task]]\nid = "t{n}"\ntitle = "t"\nstatus = "done"\nread_only = true\n'
        + (f'done_at = "{stamp}"\n' if stamp else "")
        + "\n"
        for n, stamp in enumerate(stamps)
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
    assert "t0: 'done_at' must be an ISO 8601 time with an offset" in result["errors"][0]
    code, _ = run_bare(_done_blocks("2026-01-02T10:00:00"), "check")
    assert code == 2


def test_set_in_progress_prints_only_the_change(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g1", "status", "in_progress")
    assert code == 0
    assert result == {"ok": True, "id": "g1", "status": "in_progress"}
    assert 'status = "in_progress"' in (tmp_path / "tasks.toml").read_text()


@pytest.mark.parametrize(("status", "start"), [("pending", ["g1"]), ("blocked", [])])
def test_set_blocked_and_pending_free_capacity_and_print_next(run, status, start):
    """g1 is running and g2 waits on it: a pending g1 starts again, a blocked one not."""

    text = STEERED.replace(
        'id = "g1"\ntitle = "gate"      # the first one\nstatus = "pending"',
        'id = "g1"\ntitle = "gate"      # the first one\nstatus = "in_progress"',
    )
    code, result = run(text, "set", "g1", "status", status)
    assert code == 0
    assert result["status"] == status
    assert [t["id"] for t in result["start"]] == start
    assert result["skipped"] == []


def test_set_status_no_next_silences_the_answer(run, tmp_path: pathlib.Path):
    code, result = run(STEERED, "set", "g1", "status", "done", "--no-next")
    assert code == 0
    assert result == {"ok": True, "id": "g1", "status": "done"}
    assert 'status = "done"' in (tmp_path / "tasks.toml").read_text()


@pytest.mark.parametrize(
    ("cap", "start", "skipped"),
    [
        ("rw = 1\n", ["w2", "r1", "r2"], None),
        ("ro = 1\n", ["w2", "r1"], {"id": "r2", "reason": "no free read-only slot"}),
        ("budget = 1\n", ["w2"], {"id": "r1", "reason": "no budget left"}),
    ],
)
def test_set_status_answers_with_the_caps_of_the_header(run, cap, start, skipped):
    """w1 is done and its gpu is free, so w2 and the reviewers compete for the caps."""

    _, free = run(PARALLEL, "set", "w1", "status", "done")
    assert [t["id"] for t in free["start"]] == ["w2", "r1", "r2"]
    assert free["limits"] == {"rw": None, "ro": None, "budget": None}
    assert free["skipped"] == []

    capped_text = PARALLEL.replace("format = 1\n", f"format = 1\n{cap}", 1)
    _, capped = run(capped_text, "set", "w1", "status", "done")
    assert {k: v for k, v in capped["limits"].items() if v} == {cap.split()[0]: 1}
    assert [t["id"] for t in capped["start"]] == start
    if skipped:
        assert skipped in [
            {"id": s["id"], "reason": s["reason"]} for s in capped["skipped"]
        ]


def test_set_status_answers_with_the_writer_cap_of_the_header(run):
    """Three writers are ready and the header holds rw = 1."""

    text = "format = 1\nrw = 1\n" + "".join(
        f'\n[[task]]\nid = "{i}"\ntitle = "{i}"\nstatus = "pending"\nwrite_set = ["{i}"]\n'
        for i in ("a", "b", "c")
    )
    _, result = run(text, "set", "a", "status", "done")
    assert [t["id"] for t in result["start"]] == ["b"]
    assert result["skipped"] == [{"id": "c", "reason": "no free slot"}]


def test_set_status_answers_with_the_state_after_the_write(run):
    """The finished task is not started again, and the tasks that wait on it are."""

    _, result = run(STEERED, "set", "g1", "status", "done")
    assert "g1" not in [t["id"] for t in result["start"]]
    assert result["skipped"] == []


def test_set_status_refuses_an_unknown_status_and_an_unknown_id(
    run, tmp_path: pathlib.Path
):
    code, result = run(STEERED, "set", "g1", "status", "finished")
    assert code == 2
    assert "unknown status 'finished'" in result["errors"][0]
    code, result = run(STEERED, "set", "nope", "status", "done")
    assert code == 2
    assert "unknown id 'nope'" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == STEERED


def test_set_status_is_a_no_op_when_the_status_is_the_same(steered: pathlib.Path):
    module = _load()
    before = steered.stat().st_mtime_ns
    module.set_status(steered, "g1", "pending", seen_mtime_ns=before)
    assert steered.stat().st_mtime_ns == before
    assert not (steered.parent / "steering.log").exists()


def test_set_status_refuses_a_file_that_changed_meanwhile(steered: pathlib.Path):
    module = _load()
    with pytest.raises(ValueError, match="changed since it was read"):
        module.set_status(steered, "g1", "done", seen_mtime_ns=1)
    assert steered.read_text() == STEERED


def test_set_status_refuses_a_task_blocked_on_a_human(run, tmp_path: pathlib.Path):
    code, result = run(ASKED, "set", "g1", "status", "pending")
    assert code == 2
    assert "blocked on a human" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == ASKED


REVIEWING = """format = 1

[[task]]
id = "r1"
title = "review me"
status = "in_rev"
write_set = ["r"]
worktree = "/w/r1"
branch = "r1"
human_review = true
"""


def test_set_human_review_writes_only_its_line_and_clearing_restores_the_file(
    steered: pathlib.Path,
):
    module = _load()
    module.set_human_review(steered, "g2", True, seen_mtime_ns=steered.stat().st_mtime_ns)
    assert steered.read_text() == STEERED.replace('id = "g2"\n', 'id = "g2"\nhuman_review = true\n')
    module.set_human_review(steered, "g2", False, seen_mtime_ns=steered.stat().st_mtime_ns)
    assert steered.read_text() == STEERED
    log = (steered.parent / "steering.log").read_text().splitlines()
    assert [line.split(maxsplit=1)[1] for line in log[-2:]] == [
        "g2 human_review marked",
        "g2 human_review cleared",
    ]


def test_set_human_review_refuses_a_stale_file_an_unknown_id_and_a_done_task(
    steered: pathlib.Path,
):
    module = _load()
    with pytest.raises(ValueError, match="changed since it was read"):
        module.set_human_review(steered, "g2", True, seen_mtime_ns=1)
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        module.set_human_review(steered, "nope", True, seen_mtime_ns=steered.stat().st_mtime_ns)
    steered.write_text(STEERED.replace('status = "pending"\nwrite_set = ["g"]', 'status = "done"\nwrite_set = ["g"]'))
    before = steered.read_text()
    with pytest.raises(ValueError, match="'g1' is done"):
        module.set_human_review(steered, "g1", True, seen_mtime_ns=steered.stat().st_mtime_ns)
    assert steered.read_text() == before


def test_set_done_is_refused_on_a_task_marked_for_human_review(run, tmp_path: pathlib.Path):
    code, result = run(REVIEWING, "set", "r1", "status", "done")
    assert code == 2
    assert "marked for human review" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == REVIEWING
    code, _ = run_after(run, tmp_path, "r1", "in_progress")
    assert code == 0


def test_next_awaiting_lists_a_marked_task_in_review_with_its_command(run, tmp_path):
    worktree = tmp_path / "w" / "r1"
    worktree.mkdir(parents=True)
    _, result = run(REVIEWING.replace("/w/r1", str(worktree)), "next", "--awaiting")
    assert result["awaiting_human"] == [
        {"id": "r1", "review": f"cd {worktree} && hunk diff integration...r1"}
    ]
    pending = REVIEWING.replace('"in_rev"', '"pending"')
    _, result = run(pending, "next", "--awaiting")
    assert result["awaiting_human"] == []


def test_review_command_falls_back_when_the_worktree_is_missing(run, tmp_path):
    """A missing worktree runs from the repository root, or from nowhere without one."""
    under_worktrees = tmp_path / "repo" / ".worktrees" / "r1"
    _, result = run(REVIEWING.replace("/w/r1", str(under_worktrees)), "next", "--awaiting")
    assert result["awaiting_human"][0]["review"] == (
        f"cd {tmp_path / 'repo'} && hunk diff integration...r1"
    )
    _, result = run(REVIEWING, "next", "--awaiting")
    assert result["awaiting_human"][0]["review"] == "hunk diff integration...r1"


def test_review_command_starts_at_the_base_of_the_task_then_of_its_epic(run):
    epic = """format = 1

[[task]]
id = "e"
type = "epic"
title = "epic"
status = "in_rev"
base = "layer"

[[task]]
id = "own"
parent = "e"
title = "own"
status = "in_rev"
write_set = ["a"]
worktree = "/w/own"
branch = "own"
base = "mine"
human_review = true

[[task]]
id = "inherit"
parent = "e"
title = "inherit"
status = "in_rev"
write_set = ["b"]
worktree = "/w/inherit"
branch = "inherit"
human_review = true
"""
    _, result = run(epic, "next", "--awaiting")
    reviews = {a["id"]: a["review"] for a in result["awaiting_human"]}
    assert reviews["own"].endswith("hunk diff mine...own")
    assert reviews["inherit"].endswith("hunk diff layer...inherit")
    _, result = run(REVIEWING, "next", "--awaiting")
    assert result["awaiting_human"][0]["review"].endswith("hunk diff integration...r1")


def test_check_refuses_an_empty_or_spaced_base(run):
    for value, message in (("", "non-empty string"), ("a b", "no whitespace")):
        code, result = run(REVIEWING.replace('branch = "r1"', f'branch = "r1"\nbase = "{value}"'), "check")
        assert code == 2
        assert any(message in e for e in result["errors"])


def test_check_warns_about_a_done_task_that_is_still_marked(run):
    code, result = run(REVIEWING.replace('"in_rev"', '"done"'), "check")
    assert code == 0
    assert [w for w in result["warnings"] if "human_review" in w] == [
        "r1: done but still marked human_review, the human clears the mark in the board"
    ]
    code, result = run(REVIEWING.replace("= true", '= "yes"'), "check")
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


def test_set_command_refuses_an_unknown_id(run):
    code, result = run(STEERED, "set", "nope", "priority", "P0")
    assert code == 2
    assert "unknown id 'nope'" in result["errors"][0]


def test_set_priority_is_a_no_op_when_the_priority_is_the_same(steered: pathlib.Path):
    module = _load()
    before = steered.stat().st_mtime_ns
    module.set_priority(steered, "g2", "P2", seen_mtime_ns=before)
    assert steered.stat().st_mtime_ns == before
    assert steered.read_text() == STEERED
    assert not (steered.parent / "steering.log").exists()


def test_set_priority_reads_a_header_with_a_comment(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(
        STEERED.replace('[[task]]\nid = "g2"', '[[task]] # note\nid = "g2"')
    )
    module.set_priority(path, "g2", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
    text = path.read_text()
    assert "[[task]] # note" in text
    assert 'priority = "P0"' in text


def test_set_priority_tells_ids_that_are_prefixes_apart(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
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


def test_set_priority_refuses_an_edit_inside_a_multiline_string(
    tmp_path: pathlib.Path,
):
    module = _load()
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
        assert not list(tmp_path.glob("*.tmp"))


def test_set_priority_keeps_crlf_line_endings(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_bytes(STEERED.replace("\n", "\r\n").encode())
    module.set_priority(path, "g2", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
    expected = STEERED.replace('priority = "P2"', 'priority = "P0"').replace(
        "\n", "\r\n"
    )
    assert path.read_bytes() == expected.encode()


def test_set_priority_keeps_the_mode_of_the_file(steered: pathlib.Path):
    module = _load()
    steered.chmod(0o600)
    module.set_priority(steered, "g2", "P0", seen_mtime_ns=steered.stat().st_mtime_ns)
    assert steered.stat().st_mode & 0o777 == 0o600


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


def test_answer_blocker_unblocks_the_task_and_keeps_the_rest(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(
        asked, "g1", "use option B", seen_mtime_ns=asked.stat().st_mtime_ns
    )
    assert asked.read_text() == ANSWERED
    assert "# The gate." in asked.read_text()
    assert "# the first one" in asked.read_text()
    assert not list(asked.parent.glob("*.tmp"))


def test_answer_blocker_keeps_crlf_line_endings(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_bytes(ASKED.replace("\n", "\r\n").encode())
    module.answer_blocker(
        path, "g1", "use option B", seen_mtime_ns=path.stat().st_mtime_ns
    )
    assert path.read_bytes() == ANSWERED.replace("\n", "\r\n").encode()


def test_answer_blocker_escapes_the_text_it_writes(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(
        asked, "g1", 'take "B", not C:\\x', seen_mtime_ns=asked.stat().st_mtime_ns
    )
    tasks, errors = module.parse(asked.read_text())
    assert errors == []
    assert tasks[0].answer == 'take "B", not C:\\x'
    assert tasks[0].status == "pending"
    assert tasks[0].blocker == ""


def test_answer_blocker_writes_accents_and_emoji_as_they_are(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(
        asked, "g1", "perché B 🙂", seen_mtime_ns=asked.stat().st_mtime_ns
    )
    assert 'answer = "perché B 🙂"' in asked.read_text()
    tasks, errors = module.parse(asked.read_text())
    assert errors == []
    assert tasks[0].answer == "perché B 🙂"


def test_answer_blocker_keeps_the_comment_of_a_line_it_rewrites(
    tmp_path: pathlib.Path,
):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(
        ASKED.replace('status = "blocked"', 'status = "blocked"  # for now').replace(
            'blocker = "human: option A or B?"',
            'blocker = "human: option A or B?"  # asked twice',
        )
    )
    module.answer_blocker(path, "g1", "B", seen_mtime_ns=path.stat().st_mtime_ns)
    text = path.read_text()
    assert 'status = "pending"  # for now' in text
    assert "# asked twice" not in text


def test_set_priority_keeps_the_comment_of_the_line(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(STEERED.replace('priority = "P2"', 'priority = "P2"  # raised'))
    module.set_priority(path, "g2", "P0", seen_mtime_ns=path.stat().st_mtime_ns)
    assert 'priority = "P0"  # raised' in path.read_text()


def test_rewrite_block_leaves_a_hash_inside_a_string_alone(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(ASKED.replace("option A or B?", "option #1 or #2?"))
    module.answer_blocker(path, "g1", "B", seen_mtime_ns=path.stat().st_mtime_ns)
    assert "#1" not in path.read_text()


def test_answer_blocker_appends_a_steering_log_line(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(asked, "g1", "B", seen_mtime_ns=asked.stat().st_mtime_ns)
    log = (asked.parent / "steering.log").read_text().splitlines()
    assert len(log) == 1
    assert log[0].endswith("g1 answer")


def test_answer_blocker_keeps_the_mode_of_the_file(asked: pathlib.Path):
    module = _load()
    asked.chmod(0o600)
    module.answer_blocker(asked, "g1", "B", seen_mtime_ns=asked.stat().st_mtime_ns)
    assert asked.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ('blocker = "human: option A or B?"', 'blocker = "ci: the runner is down"'),
        ('status = "blocked"\nblocker = "human: option A or B?"', 'status = "pending"'),
    ],
)
def test_answer_blocker_refuses_a_task_that_is_not_blocked_on_a_human(
    asked: pathlib.Path, old, new
):
    module = _load()
    asked.write_text(ASKED.replace(old, new))
    before = asked.read_text()
    with pytest.raises(ValueError, match="not blocked on a human"):
        module.answer_blocker(asked, "g1", "B", seen_mtime_ns=asked.stat().st_mtime_ns)
    assert asked.read_text() == before


def test_answer_blocker_refuses_a_file_that_changed_meanwhile(asked: pathlib.Path):
    module = _load()
    with pytest.raises(ValueError, match="changed since it was read"):
        module.answer_blocker(asked, "g1", "B", seen_mtime_ns=1)
    assert asked.read_text() == ASKED


def test_answer_blocker_writes_an_answer_on_several_lines(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(
        asked, "g1", "a\nb\n\nc", seen_mtime_ns=asked.stat().st_mtime_ns
    )
    assert 'answer = "a\\nb\\n\\nc"' in asked.read_text()
    tasks, errors = module.parse(asked.read_text())
    assert not errors
    assert tasks[0].answer == "a\nb\n\nc"


def test_resume_shows_a_multi_line_answer_on_one_line(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(asked, "g1", "a\nb", seen_mtime_ns=asked.stat().st_mtime_ns)
    text = module.resume(
        module.Graph(module.parse(asked.read_text())[0]),
        limits=module.Limits(rw=None, ro=None, budget=None),
        tasks_path=asked,
    )
    assert any(x.startswith("  g1  ") and x.endswith("a b") for x in text.splitlines())


def test_answer_blocker_refuses_an_answer_with_a_control_character(
    asked: pathlib.Path,
):
    module = _load()
    for text in ("a\x07b", "a\x7fb", "a\rb", "a\u2028b", "a\u2029b"):
        with pytest.raises(ValueError, match="contains a control character"):
            module.answer_blocker(
                asked, "g1", text, seen_mtime_ns=asked.stat().st_mtime_ns
            )
        assert asked.read_text() == ASKED


def test_answer_blocker_keeps_a_tab_in_the_answer(asked: pathlib.Path):
    module = _load()
    module.answer_blocker(asked, "g1", "a\tb", seen_mtime_ns=asked.stat().st_mtime_ns)
    assert 'answer = "a\\tb"' in asked.read_text()


def test_answer_blocker_refuses_an_unknown_id(asked: pathlib.Path):
    module = _load()
    with pytest.raises(ValueError, match="unknown id 'nope'"):
        module.answer_blocker(
            asked, "nope", "B", seen_mtime_ns=asked.stat().st_mtime_ns
        )
    assert asked.read_text() == ASKED


def test_answer_command_writes_the_answer(run, tmp_path: pathlib.Path):
    code, result = run(ASKED, "answer", "g1", "use option B")
    assert code == 0
    assert result == {"ok": True, "id": "g1", "answer": "use option B"}
    assert (tmp_path / "tasks.toml").read_text() == ANSWERED


def test_answer_command_refuses_a_task_that_is_not_blocked(run, tmp_path):
    code, result = run(ASKED, "answer", "g2", "B")
    assert code == 2
    assert "not blocked on a human" in result["errors"][0]
    assert (tmp_path / "tasks.toml").read_text() == ASKED


def _block(task_id: str, status: str = "pending", **keys: str) -> str:
    """A task block, with an epic type when type is "epic" and strings in keys."""

    lines = [f'id = "{task_id}"', f'title = "{task_id}"', f'status = "{status}"']
    lines += [f'{key} = "{value}"' for key, value in keys.items()]
    if keys.get("type") != "epic":
        lines.append(f'write_set = ["{task_id}"]')
    return "\n[[task]]\n" + "\n".join(lines) + "\n"


def _order(lines: list[str]) -> list[str]:
    """The ids of the rows of a tree, in the order they are drawn."""

    rows = (_label(line) for line in _rows(lines))
    return [label[1] for label in rows if label[1]]


def _epic(task_id: str, **keys: str) -> str:
    return _block(task_id, type="epic", **keys)


def test_epics_sort_by_priority_and_an_unset_one_takes_its_best_open_leaf(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("late", priority="P3"),
            _block("late1", parent="late", priority="P0"),
            _epic("low", priority="P2"),
            _block("low1", parent="low"),
            _epic("derived"),
            _block("derived1", parent="derived", priority="P0"),
            _block("derived2", "done", parent="derived", priority="P0"),
            _epic("top", priority="P0"),
            _block("top1", parent="top", priority="P3"),
            _block("loose", priority="P1"),
        ]
    )
    # An open leaf with a priority gives its epic one, and a done leaf does not.
    lines = run_text(text, "tree", "--width", "300")
    roots = [
        i for i in _order(lines) if i in {"late", "low", "derived", "top", "loose"}
    ]
    assert roots == ["derived", "top", "loose", "low", "late"]


def test_epics_with_the_same_priority_sort_by_milestone(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("none", priority="P1"),
            _block("none1", parent="none"),
            _epic("second", priority="P1", milestone="m2"),
            _block("second1", parent="second"),
            _epic("first", priority="P1", milestone="m1"),
            _block("first1", parent="first"),
        ]
    )
    lines = run_text(text, "tree", "--width", "300")
    assert [i for i in _order(lines) if i in {"none", "second", "first"}] == [
        "first",
        "second",
        "none",
    ]


def test_nested_epics_and_leaves_sort_by_the_same_key(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("top"),
            _block("l1", parent="top", priority="P3"),
            _epic("S1", parent="top", priority="P3"),
            _block("s1", parent="S1"),
            _block("l2", parent="top", priority="P0"),
            _epic("S2", parent="top", priority="P0"),
            _epic("T1", parent="S2", priority="P3"),
            _block("t1", parent="T1"),
            _epic("T2", parent="S2", priority="P0"),
            _block("t2", parent="T2"),
            _epic("S3", parent="top", priority="P1"),
            _block("s3", parent="S3"),
        ]
    )
    lines = run_text(text, "tree", "--all", "--width", "300")
    children = [i for i in _order(lines) if i in {"l1", "l2", "S1", "S2", "S3"}]
    assert children == ["l2", "S2", "S3", "l1", "S1"]
    deeper = [i for i in _order(lines) if i in {"T1", "T2"}]
    assert deeper == ["T2", "T1"]


def test_compare_lists_added_removed_and_changed_tasks(compare):
    old = _plan(("a", "pending", []), ("b", "pending", ["a"]), ("c", "pending", []))
    new = _plan(("a", "done", []), ("b", "pending", []), ("d", "pending", ["b"]))
    code, result = compare(old, new)
    assert code == 0
    assert result["added"] == ["d"]
    assert result["removed"] == ["c"]
    assert result["changed"] == [
        {"id": "a", "fields": {"status": ["pending", "done"]}},
        {"id": "b", "fields": {"deps": [["a"], []]}},
    ]


def test_compare_counts_the_waves_of_the_open_leaves_only(compare):
    old = _plan(("a", "done", []), ("b", "pending", ["a"]), ("c", "pending", ["b"]))
    new = _plan(("a", "done", []), ("b", "pending", ["a"]), ("c", "pending", []))
    _, result = compare(old, new)
    assert result["before"]["waves"] == [["b"], ["c"]]
    assert result["after"]["waves"] == [["b", "c"]]
    assert result["before"]["ready"] == ["b"]
    assert result["after"]["ready"] == ["b", "c"]


def test_compare_names_what_each_gate_holds_back(compare):
    old = _plan(
        ("a", "pending", []),
        ("g", "pending", ["a"], "read_only"),
        ("b", "pending", ["g"]),
        ("c", "pending", ["b"]),
    )
    new = _plan(
        ("a", "pending", []),
        ("g", "pending", ["a"], "read_only"),
        ("b", "pending", ["a"]),
        ("c", "pending", ["b"]),
    )
    _, result = compare(old, new)
    assert result["before"]["gates"] == {"g": ["b", "c"]}
    assert result["after"]["gates"] == {}
    assert result["before"]["rounds"] == [["a"], ["g"], ["b"], ["c"]]
    assert result["after"]["rounds"] == [["a"], ["b", "g"], ["c"]]


def test_compare_simulates_the_rounds_without_caps_and_with_write_sets(compare):
    old = _plan(("a", "pending", []), ("b", "pending", []), ("c", "pending", ["a"]))
    new = old.replace('write_set = ["b"]', 'write_set = ["a"]')
    _, result = compare(old, new)
    assert result["before"]["rounds"] == [["a", "b"], ["c"]]
    assert result["after"]["rounds"] == [["a"], ["b", "c"]]


def test_compare_finishes_running_tasks_first_and_reports_the_stuck(compare):
    plan = _plan(
        ("a", "in_progress", []),
        ("b", "pending", ["a"]),
        ("h", "blocked", []),
        ("c", "pending", ["h"]),
    )
    _, result = compare(plan, plan)
    assert result["before"]["rounds"] == [["b"]]
    assert result["before"]["stuck"] == ["h", "c"]
    assert result["added"] == result["removed"] == result["changed"] == []


def test_compare_refuses_a_new_file_that_is_not_valid(compare):
    code, result = compare(
        _plan(("a", "pending", [])), 'format = 1\n[task]\nid = "x"\n'
    )
    assert code == 2
    assert "new.toml: 'task' must be an array" in result["errors"][0]


def _raw(task_id: str, status: str, deps: list[str], *extra: str) -> str:
    lines = [f'id = "{task_id}"', f'title = "{task_id}"', f'status = "{status}"']
    lines += [f"deps = {json.dumps(deps)}", *extra]
    return "\n[[task]]\n" + "\n".join(lines) + "\n"


def test_compare_reports_every_key_of_a_task(compare):
    old = "format = 1\n" + _raw("a", "pending", [], 'write_set = ["a"]')
    old += _raw("e", "pending", [], 'write_set = ["e"]')
    old += _raw("b", "blocked", [], 'write_set = ["b"]')
    new = "format = 1\n" + _raw(
        "a",
        "pending",
        [],
        'parent = "e"',
        'milestone = "m1"',
        "footprint = 3",
        'exclusive = ["gpu"]',
        "read_only = true",
    )
    new += _raw("e", "pending", [], 'type = "epic"')
    new += _raw("b", "blocked", [], 'write_set = ["b"]', 'blocker = "human: which?"')
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


def test_compare_lists_the_same_keys_as_the_file_format():
    module = _load()
    assert {*module.COMPARED, "id"} == module.KEYS


def test_compare_reports_a_header_change(compare):
    task = _raw("a", "pending", [], 'write_set = ["a"]')
    old = 'format = 1\nrw = 2\n[model]\nworker = "m1"\n[effort]\nworker = "low"\n' + task
    new = 'format = 1\nrw = 3\n[model]\nworker = "m2"\n[effort]\nworker = "low"\n' + task
    _, result = compare(old, new)
    assert result["header"] == {"rw": [2, 3], "model.worker": ["m1", "m2"]}
    assert result["changed"] == []

    _, same = compare(old, old)
    assert same["header"] == {}


def test_compare_treats_list_keys_as_sets(compare):
    old = "format = 1\n" + _raw("x", "pending", [], 'write_set = ["x"]')
    old += _raw("y", "pending", [], 'write_set = ["y"]')
    old += _raw("a", "pending", ["x", "y"], 'write_set = ["a", "b"]', 'exclusive = ["g", "h"]')
    new = "format = 1\n" + _raw("x", "pending", [], 'write_set = ["x"]')
    new += _raw("y", "pending", [], 'write_set = ["y"]')
    new += _raw("a", "pending", ["y", "x"], 'write_set = ["b", "a"]', 'exclusive = ["h", "g"]')
    _, result = compare(old, new)
    assert result["changed"] == []


def test_compare_shows_a_priority_added_to_a_task_without_one(compare):
    old = "format = 1\n" + _raw("a", "pending", [], 'write_set = ["a"]')
    new = "format = 1\n" + _raw("a", "pending", [], 'write_set = ["a"]', 'priority = "P2"')
    _, result = compare(old, new)
    assert len(result["changed"]) == 1
    assert result["changed"][0]["id"] == "a"
    assert set(result["changed"][0]["fields"]) == {"priority"}
    assert result["changed"][0]["fields"]["priority"][0] is None


def test_compare_parks_the_deferred_leaves(compare):
    plan = "format = 1\n" + "".join(
        [
            _raw("a", "pending", [], 'write_set = ["a"]'),
            _raw("d", "deferred", [], 'write_set = ["d"]'),
            _raw("c", "pending", ["d"], 'write_set = ["c"]'),
        ]
    )
    _, result = compare(plan, plan)
    schedule = result["before"]
    assert schedule["waves"] == [["a", "c"]]
    assert schedule["stuck"] == ["c"]
    assert "d" not in [i for wave in schedule["rounds"] for i in wave]


def test_the_epic_tree_shows_a_nested_epic_with_only_deferred_leaves(run_text):
    plan = "format = 1\n" + "".join(
        [
            _raw("E", "pending", [], 'type = "epic"'),
            _raw("N", "pending", [], 'type = "epic"', 'parent = "E"'),
            _raw("a", "pending", [], 'parent = "E"', "read_only = true"),
            _raw("p", "deferred", [], 'parent = "N"', "read_only = true"),
        ]
    )
    shown = "\n".join(run_text(plan, "tree", "--epic", "E"))
    assert "N" in shown
    assert "p" in shown
    default = "\n".join(run_text(plan, "tree"))
    assert "◌" in default
    assert "hidden" not in default


def test_compare_does_not_count_an_open_epic_as_a_wave(compare):
    plan = "format = 1\n" + "".join(
        [
            _raw("E", "pending", [], 'type = "epic"'),
            _raw("a", "pending", [], 'parent = "E"', 'write_set = ["a"]'),
            _raw("c", "pending", ["a"], 'write_set = ["c"]'),
            _raw("b", "pending", ["E"], 'write_set = ["b"]'),
        ]
    )
    _, result = compare(plan, plan)
    assert result["before"]["waves"] == [["a"], ["c", "b"]]
    assert result["before"]["rounds"] == [["a"], ["c", "b"]]


def test_compare_does_not_count_a_nested_epic_as_a_wave(compare):
    plan = "format = 1\n" + "".join(
        [
            _raw("E", "pending", [], 'type = "epic"'),
            _raw("F", "pending", [], 'type = "epic"', 'parent = "E"'),
            _raw("a", "pending", [], 'parent = "F"', 'write_set = ["a"]'),
            _raw("b", "pending", ["E"], 'write_set = ["b"]'),
        ]
    )
    _, result = compare(plan, plan)
    assert result["before"]["waves"] == [["a"], ["b"]]


def test_compare_closes_an_epic_whose_leaves_are_all_done_from_round_one(compare):
    plan = "format = 1\n" + "".join(
        [
            _raw("E", "pending", [], 'type = "epic"'),
            _raw("a", "done", [], 'parent = "E"', 'write_set = ["a"]'),
            _raw("b", "pending", ["E"], 'write_set = ["b"]'),
            _raw("x", "pending", [], 'write_set = ["x"]'),
        ]
    )
    _, result = compare(plan, plan)
    assert result["before"]["rounds"] == [["b", "x"]]
    assert result["before"]["stuck"] == []


def test_compare_starts_the_dependents_of_a_running_task_in_round_two(compare):
    plan = _plan(
        ("a", "in_progress", []),
        ("b", "pending", ["a"]),
        ("x", "pending", []),
    )
    _, result = compare(plan, plan)
    assert result["before"]["ready"] == ["x"]
    assert result["before"]["rounds"] == [["x"], ["b"]]


def _plan(*rows: tuple) -> str:
    """A tasks.toml of leaves that each write a file named after their id."""

    blocks = ["format = 1\n"]
    for task_id, status, deps, *kind in rows:
        write = "read_only = true" if kind else f'write_set = ["{task_id}"]'
        blocks.append(
            f'\n[[task]]\nid = "{task_id}"\ntitle = "{task_id}"\n'
            f'status = "{status}"\ndeps = {json.dumps(deps)}\n{write}\n'
        )
    return "".join(blocks)


def test_check_warns_about_a_task_that_holds_up_too_many(run):
    code, result = run(_bottleneck_ledger(), "check")

    assert code == 0
    assert result["warnings"] == [
        "g9 holds up 4 open tasks (limit 3): d1 (deps), d2 (deps), s1 (write_set), "
        "s2 (write_set)"
    ]

    _, loose = run(_bottleneck_ledger(), "check", "--max-blocked", "4")
    assert "warnings" not in loose


def test_explain_lists_what_a_task_holds_up(run):
    _, result = run(_bottleneck_ledger(), "explain", "g9")

    assert result["holds_up"] == {
        "d1": "deps",
        "d2": "deps",
        "s1": "write_set",
        "s2": "write_set",
    }
    assert "g9 holds up 4 open tasks" in result["warning"]

    _, quiet = run(_bottleneck_ledger(), "explain", "d1")
    assert quiet["holds_up"] == {"d2": "deps"} and quiet["warning"] is None


def test_a_shared_write_set_counts_against_one_task_only(run):
    _, result = run(_bottleneck_ledger(), "explain", "s1")

    assert result["holds_up"] == {"s2": "write_set"}


def test_tree_and_board_show_the_blocker_count(run_text):
    tree = "\n".join(run_text(_bottleneck_ledger(), "tree", "--color", "never"))
    board = "\n".join(run_text(_bottleneck_ledger(), "board"))

    rows = {_label(line)[1]: line for line in tree.split("\n\n")[0].splitlines()}
    assert "blocks 2" in rows["g9"]
    assert "blocks 1" in rows["d1"]
    assert "blocks" not in rows["d2"]
    assert "blocks N: tasks waiting on it" in tree
    card = next(line for line in board.splitlines() if "blocks 4" in line)
    assert card.startswith("  g9")
    assert "blocks 4" not in "\n".join(
        run_text(_bottleneck_ledger(), "board", "--max-blocked", "4")
    )


def test_max_blocked_must_not_be_negative(run):
    code, result = run(_bottleneck_ledger(), "check", "--max-blocked", "-1")

    assert code == 2
    assert "--max-blocked" in result["errors"][0]


def test_a_write_set_hold_includes_the_tasks_behind_the_held_one(run):
    ledger = _chain(
        ("a", "running", 'write_set = ["x.py"]'),
        ("b", "pending", 'write_set = ["x.py"]'),
        ("c", "pending", 'deps = ["b"]\nwrite_set = ["c.py"]'),
        ("d", "pending", 'deps = ["b"]\nwrite_set = ["d.py"]'),
        ("e", "pending", 'deps = ["c"]\nwrite_set = ["e.py"]'),
        ("f", "pending", 'write_set = ["x.py"]'),
    )

    _, result = run(ledger, "explain", "a")

    assert result["holds_up"] == {
        "b": "write_set",
        "f": "write_set",
        "c": "deps",
        "d": "deps",
        "e": "deps",
    }
    assert result["warning"] is not None


def test_the_tiebreak_follows_the_priority_order_of_next(run):
    ledger = _chain(
        ("low", "pending", 'write_set = ["x.py"]'),
        ("high", "pending", 'priority = "P0"\nwrite_set = ["x.py"]'),
    )

    _, low = run(ledger, "explain", "low")
    _, high = run(ledger, "explain", "high")

    assert low["holds_up"] == {}
    assert high["holds_up"] == {"low": "write_set"}


def test_two_running_tasks_that_share_files_hold_nothing_up(run):
    ledger = _chain(
        ("a", "running", 'write_set = ["x.py"]'),
        ("b", "running", 'write_set = ["x.py"]'),
    )

    assert run(ledger, "explain", "a")[1]["holds_up"] == {}
    assert run(ledger, "explain", "b")[1]["holds_up"] == {}


def test_a_pending_task_is_held_by_every_running_task_that_overlaps_it(run):
    ledger = _chain(
        ("a", "running", 'write_set = ["x.py"]'),
        ("b", "running", 'write_set = ["x.py"]'),
        ("c", "pending", 'write_set = ["x.py"]'),
    )

    assert run(ledger, "explain", "a")[1]["holds_up"] == {"c": "write_set"}
    assert run(ledger, "explain", "b")[1]["holds_up"] == {"c": "write_set"}


def test_an_epic_or_a_done_task_holds_nothing_up(run):
    ledger = (
        'format = 1\n\n[[task]]\nid = "E"\ntype = "epic"\ntitle = "E"\n'
        'status = "pending"\n'
        + _chain(
            ("done", "done", 'parent = "E"\nwrite_set = ["x.py"]'),
            ("later", "pending", 'parent = "E"\nwrite_set = ["x.py"]'),
        ).removeprefix("format = 1\n")
    )

    assert run(ledger, "explain", "E")[1]["holds_up"] == {}
    assert run(ledger, "explain", "done")[1]["holds_up"] == {}


def _chain(*rows: tuple[str, str, str]) -> str:
    """A ledger of leaves given as (id, status, extra keys)."""

    return "format = 1\n" + "".join(
        f'\n[[task]]\nid = "{i}"\ntitle = "{i}"\nstatus = "{status}"\n{extra}\n'
        for i, status, extra in rows
    )


def _bottleneck_ledger() -> str:
    """One running task that holds two tasks by deps and two by a shared file."""

    def block(task_id: str, status: str, extra: str) -> str:
        return (
            f'\n[[task]]\nid = "{task_id}"\ntitle = "{task_id}"\n'
            f'status = "{status}"\n{extra}'
        )

    return "".join(
        [
            "format = 1\n",
            block("g9", "running", 'write_set = ["lib/shared.py"]\n'),
            block("d1", "pending", 'deps = ["g9"]\nwrite_set = ["d1/**"]\n'),
            block("d2", "pending", 'deps = ["d1"]\nwrite_set = ["d2/**"]\n'),
            block("s1", "pending", 'write_set = ["lib/shared.py"]\n'),
            block("s2", "pending", 'write_set = ["lib/shared.py"]\n'),
        ]
    )


def _deep_ledger() -> str:
    """Four nested epics over two leaves, one of them with a 60 character id."""

    blocks = ["format = 1\n"]
    for depth in range(1, 5):
        parent = f'parent = "E{depth - 1}"\n' if depth > 1 else ""
        blocks.append(
            f'\n[[task]]\nid = "E{depth}"\ntype = "epic"\n'
            f'title = "level {depth} of a tree that goes down a long way"\n'
            f'status = "pending"\n{parent}'
        )

    for name, mark in (("x" * 60, "a"), ("short", "b")):
        blocks.append(
            f'\n[[task]]\nid = "{name}"\n'
            f'title = "a leaf with a title long enough to wrap more than once"\n'
            f'status = "pending"\nparent = "E4"\nwrite_set = ["{mark}"]\n'
        )

    return "".join(blocks)


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


def test_resume_has_no_stacks_section_without_a_stack(tmp_path):
    assert not any(line.startswith("STACKS") for line in _resume_text(tmp_path, TASKS))


@pytest.mark.parametrize(
    ("stack", "message"),
    [
        ('"  "', "non-empty name or a number"),
        ("0", "non-empty name or a number"),
        ("true", "non-empty name or a number"),
        ("[1]", "non-empty name or a number"),
    ],
)
def test_check_refuses_a_bad_stack_key(run, stack, message):
    code, result = run(STACKED.replace('stack = "auth"', f"stack = {stack}"), "check")
    assert code == 2
    assert any(message in e for e in result["errors"])


def test_check_refuses_a_stack_key_on_a_task(run):
    code, result = run(STACKED.replace('id = "e"', 'id = "e"\nstack = "x"'), "check")
    assert code == 2
    assert "e: 'stack' is only valid on an epic" in result["errors"]


def test_check_warns_when_epics_nest_deeper_than_github_allows(run):
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


def test_check_refuses_a_stack_whose_leaves_share_a_branch(run):
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


def test_a_numeric_stack_equals_the_same_name(tmp_path):
    text = STACKED.replace('stack = "auth"', "stack = 12")
    lines = _resume_text(tmp_path, text)
    assert lines.count("  12") == 1
    start = lines.index("STACKS (1)")
    assert [line.split()[0] for line in lines[start + 2 : start + 5]] == [
        "a",
        "b",
        "d",
    ]


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


def test_cleanup_classifies_each_named_worktree(fleet):
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


def test_cleanup_does_not_list_a_worktree_the_ledger_never_names(fleet):
    repo, ledger = fleet
    assert "stranger" not in _cleanup(repo, ledger, "--tip", "main")[1]


def test_cleanup_without_apply_changes_nothing(fleet):
    repo, ledger = fleet
    _cleanup(repo, ledger, "--tip", "main")
    assert (repo.parent / "merged").is_dir()


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


def test_cleanup_keeps_the_main_checkout(fleet):
    repo, _ = fleet
    ledger = repo.parent / "main.toml"
    ledger.write_text("format = 1\n" + _task("m", "done", repo, "main"))
    _, text = _cleanup(repo, ledger, "--tip", "main")
    assert "wt remove" not in text
    assert "main at" in text and "main checkout" in text


def test_cleanup_keeps_the_current_directory_and_its_parents(fleet, monkeypatch):
    repo, ledger = fleet
    monkeypatch.chdir(repo.parent / "merged")
    _, text = _cleanup(repo, ledger, "--tip", "main")
    assert "wt remove" not in text
    assert "current directory" in text


def test_cleanup_keeps_a_worktree_with_a_running_task(fleet):
    repo, _ = fleet
    ledger = repo.parent / "run.toml"
    ledger.write_text("format = 1\n" + _task("r", "running", repo.parent / "merged", "merged"))
    _, text = _cleanup(repo, ledger, "--tip", "main")
    assert "wt remove" not in text and "r is not done" in text


def test_cleanup_measures_merged_against_the_tip_not_the_cwd(fleet, monkeypatch):
    repo, ledger = fleet
    monkeypatch.chdir(repo.parent / "dirty")
    assert "wt remove ahead" not in _cleanup(repo, ledger)[1]
    assert "wt remove ahead" in _cleanup(repo, ledger, "--tip", "ahead")[1]

    (repo.parent / "ORCHESTRATION.md").write_text("Stack tip: ahead\n")
    assert "wt remove ahead" in _cleanup(repo, ledger)[1]


def test_cleanup_recognises_a_cherry_picked_branch_from_outside_the_repo(
    fleet, monkeypatch
):
    repo, ledger = fleet
    _git(repo, "cherry-pick", "ahead")
    monkeypatch.chdir(repo.parent.parent)
    text = _cleanup(repo.parent / "nowhere", ledger, "--tip", "main")[1]
    assert "wt remove ahead" in text
    assert "wt remove merged" in text
    assert any(" dirty at " in line and "uncommitted" in line for line in text.splitlines())


def test_check_rejects_a_relative_worktree(tmp_path):
    ledger = tmp_path / "tasks.toml"
    ledger.write_text("format = 1\n" + _task("a", "pending", "rel/a", "a"))
    out = io.StringIO()
    assert _load().run([str(ledger), "check"], out) != 0
    assert "'worktree' must be an absolute path" in out.getvalue()


def test_cleanup_reports_a_missing_git_cleanly(fleet, monkeypatch):
    repo, ledger = fleet
    monkeypatch.setenv("PATH", str(repo.parent / "nowhere"))
    code, text = _cleanup(repo, ledger)
    assert code == 1 and text.startswith("failed: cannot list the worktrees")


def test_cleanup_keeps_a_shared_worktree_until_every_task_is_done(tmp_path, fleet):
    repo, _ = fleet
    ledger = tmp_path / "shared.toml"
    ledger.write_text(
        "format = 1\n"
        + "".join(
            _task(n, s, tmp_path / "merged", "merged")
            for n, s in (("s1", "done"), ("s2", "pending"))
        )
    )
    _, text = _cleanup(repo, ledger, "--tip", "main")
    assert "merged at" in text and "s2 is not done" in text
    assert "wt remove" not in text


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


def _dots(lines: list[str]) -> dict[str, str]:
    rows = (_words(line) for line in lines)
    return {w[2]: w[0] for w in rows if len(w) > 2 and w[0] in SYMBOLS}


def test_a_running_task_sorts_before_one_that_has_not_started(run_text):
    text = "format = 1\n" + "".join(
        [
            _block("early", priority="P0", milestone="m1"),
            _block("busy", "in_progress", priority="P3"),
            _block("urgent", priority="P0"),
        ]
    )
    lines = run_text(text, "tree", "--width", "300")
    assert _order(lines) == ["busy", "early", "urgent"]


def test_leaves_sort_by_the_same_key_with_no_wave_order(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("E"),
            _block("first", parent="E", priority="P3"),
            _block("second", parent="E", priority="P0", deps="first"),
            _block("x", parent="E", priority="P1", deps="first"),
            _block("y", "running", parent="E", priority="P3", deps="first"),
        ]
    ).replace('deps = "first"', 'deps = ["first"]')
    lines = run_text(text, "tree", "--width", "300")
    assert [i for i in _order(lines) if i != "E"] == ["y", "second", "x", "first"]


RUNNING_STATUSES = ("running", "in_progress", "in_rev", "returned")


def test_every_running_status_has_a_bold_title_and_blocks_is_not_bold(
    run_text, monkeypatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    text = "format = 1\n" + "".join(
        [_block(f"r{n}", status) for n, status in enumerate(RUNNING_STATUSES)]
        + [_block("idle")]
    )
    shown = "\n".join(run_text(text, "tree", "--color", "always", "--width", "300"))
    shown = shown.split("\n\n")[0]
    for n in range(len(RUNNING_STATUSES)):
        assert f"\033[1mr{n}\033[0m" in shown
    assert "\033[1midle" not in shown

    held = "format = 1\n" + "".join(
        [_block("hub")]
        + [_block(f"w{n}", deps="hub") for n in range(4)]
    ).replace('deps = "hub"', 'deps = ["hub"]')
    row = next(
        line
        for line in run_text(held, "tree", "--color", "always", "--width", "300")
        if "blocks 4" in line
    )
    assert "\033[33mblocks 4\033[0m" in row
    assert "\033[1;33m" not in row


def test_an_epic_is_running_only_from_its_leaves_never_from_its_own_status(
    run_text, monkeypatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    text = "format = 1\n" + "".join(
        [
            _block("early", priority="P2"),
            _epic("E", status="in_progress", priority="P3"),
            _block("e1", parent="E"),
        ]
    )
    lines = run_text(text, "tree", "--width", "300")
    assert _order(lines)[:2] == ["early", "E"]
    assert "E" not in _dots(lines)
    colored = "\n".join(run_text(text, "tree", "--color", "always", "--width", "300"))
    assert "\033[1mE" not in colored.split("\n\n")[0]
    assert "\033[1mepic" not in colored


def test_done_tasks_sort_first_and_no_bar_covers_open_ones(run_text):
    text = "format = 1\n" + "".join(
        [
            _epic("E"),
            _block("a", parent="E"),
            _block("d", "done", parent="E"),
            _block("b", parent="E"),
        ]
    )
    lines = run_text(text, "tree", "--all", "--width", "300")
    assert _order(lines) == ["E", "d", "a", "b"]
    assert not any("┃" in line for line in lines)


def _hunks(*specs: tuple[str, str, str]) -> str:
    """Leaves with their own worktree, the extra lines as the third item."""

    return _chain(
        *(
            (i, s, f'worktree = "/wt/{i}"\nbranch = "{i}"\n{extra}')
            for i, s, extra in specs
        )
    )


HUNKS = 'overlap = "hunks"\nwrite_set = ["x.py"]'


def test_overlap_hunks_lets_next_start_two_worktree_tasks_on_one_file(run):
    ledger = _hunks(("a", "pending", HUNKS), ("b", "pending", HUNKS))

    _, result = run(ledger, "next", *caps())

    assert [t["id"] for t in result["start"]] == ["a", "b"]
    assert result["start"][1]["overlaps"] == ["a"]
    assert result["skipped"] == []


def test_overlap_hunks_holds_for_pending_tasks_that_have_no_worktree_yet(run):
    ledger = _chain(("a", "pending", HUNKS), ("b", "pending", HUNKS))

    _, result = run(ledger, "next", *caps())

    assert [t["id"] for t in result["start"]] == ["a", "b"]


def test_overlap_hunks_ignores_a_running_task_that_shares_the_file(run):
    ledger = _hunks(("a", "running", HUNKS), ("b", "pending", HUNKS))

    _, result = run(ledger, "next", *caps())

    assert [t["id"] for t in result["start"]] == ["b"]
    assert result["start"][0]["overlaps"] == ["a"]


def test_overlap_needs_the_key_on_both_tasks_and_two_worktrees(run):
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


def test_overlap_hunks_keeps_exclusive_and_footprint(run):
    ledger = _hunks(
        ("a", "pending", HUNKS + '\nexclusive = ["db"]'),
        ("b", "pending", HUNKS + '\nexclusive = ["db"]'),
    )

    _, result = run(ledger, "next", *caps())

    assert [t["id"] for t in result["start"]] == ["a"]
    assert result["skipped"][0]["reason"] == "exclusive resource"

    _, capped = run(_hunks(("a", "pending", HUNKS), ("b", "pending", HUNKS)), "next", *caps(rw=1))
    assert [t["id"] for t in capped["start"]] == ["a"]


def test_overlap_hunks_is_not_a_write_set_hold(run):
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


def test_check_notes_a_pair_that_overlaps_on_purpose(run):
    ledger = _hunks(("a", "pending", HUNKS), ("b", "pending", HUNKS))

    code, result = run(ledger, "check")

    assert code == 0
    assert result["notes"] == [
        'a and b: x.py overlaps x.py, on purpose (overlap = "hunks"): '
        "separate worktrees, merged one after the other"
    ]
    assert "notes" not in run(_hunks(("a", "pending", 'write_set = ["x.py"]')), "check")[1]


def test_overlap_must_be_hunks(run):
    code, result = run(_hunks(("a", "pending", 'overlap = "all"\nwrite_set = ["x.py"]')), "check")

    assert code == 2
    assert "'overlap' must be one of ['hunks']" in result["errors"][0]


@pytest.mark.parametrize("value", ['["hunks"]', '{ a = 1 }', "3", "true"])
def test_a_non_string_overlap_is_an_error_not_a_crash(run, value):
    ledger = _hunks(("a", "pending", f'overlap = {value}\nwrite_set = ["x.py"]'))

    code, result = run(ledger, "check")

    assert code == 2
    assert "'overlap' must be a string" in result["errors"][0]


def test_overlap_on_one_task_only_is_still_a_write_set_hold(run):
    ledger = _hunks(
        ("a", "running", HUNKS), ("b", "pending", 'write_set = ["x.py"]')
    )

    _, result = run(ledger, "explain", "a")

    assert result["holds_up"] == {"b": "write_set"}
    assert result["overlaps_on_purpose"] == {}


def test_overlap_in_the_same_worktree_is_still_a_write_set_hold(run):
    ledger = _chain(
        ("a", "running", 'worktree = "/wt/a"\n' + HUNKS),
        ("b", "pending", 'worktree = "/wt/a"\n' + HUNKS),
    )

    _, result = run(ledger, "explain", "a")

    assert result["holds_up"] == {"b": "write_set"}
    assert result["overlaps_on_purpose"] == {}


def test_a_running_overlap_task_without_a_worktree_is_still_a_hold(run):
    ledger = _chain(("a", "running", HUNKS), ("b", "pending", HUNKS))

    _, result = run(ledger, "explain", "a")

    assert result["holds_up"] == {"b": "write_set"}
    assert result["overlaps_on_purpose"] == {}


def test_a_read_only_task_never_overlaps_on_purpose(run):
    ledger = _hunks(
        ("a", "running", HUNKS), ("r", "pending", 'overlap = "hunks"\nread_only = true')
    )

    _, result = run(ledger, "explain", "a")

    assert result["overlaps_on_purpose"] == {}
    assert [t["id"] for t in run(ledger, "next", *caps())[1]["start"]] == ["r"]


def test_a_parallel_sharer_still_holds_the_tasks_behind_it(run):
    ledger = _hunks(
        ("a", "running", HUNKS),
        ("b", "pending", HUNKS),
        ("c", "pending", 'deps = ["b"]\nwrite_set = ["c.py"]'),
    )

    _, result = run(ledger, "explain", "a")

    assert result["holds_up"] == {}
    _, b = run(ledger, "explain", "b")
    assert b["holds_up"] == {"c": "deps"}


def test_check_does_not_ask_for_a_brief_of_an_in_progress_epic(run):
    """An epic has no brief of its own, so only its leaves are checked."""

    plan = (
        'format = 1\n\n[[task]]\nid = "E"\ntype = "epic"\ntitle = "epic"\n'
        'status = "in_progress"\n\n[[task]]\nid = "a"\ntitle = "a"\n'
        'status = "pending"\nparent = "E"\nwrite_set = ["a"]\n'
    )
    _, result = run(plan, "check")
    assert "warnings" not in result


def test_check_warns_about_an_in_progress_task_without_a_brief(run_bare, tmp_path):
    """A task in progress needs briefs/<id>.md, and a blocked one does not."""

    plan = _plan(("a", "in_progress", []), ("b", "pending", ["a"]))
    _, result = run_bare(plan, "check")
    assert result["warnings"] == ["a: in progress without briefs/a.md next to tasks.toml"]

    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "a.md").write_text("## Acceptance\n")
    code, result = run_bare(plan, "check")
    assert code == 0
    assert "warnings" not in result


def test_check_warns_about_a_ready_task_without_a_brief(run_bare):
    _, result = run_bare(_plan(("a", "pending", [])), "check")
    assert result["warnings"] == ["a: ready without briefs/a.md next to tasks.toml"]


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


def test_check_is_silent_about_briefs_of_done_tasks_and_epics(run_bare, tmp_path):
    plan = (
        'format = 1\n\n[[task]]\nid = "E"\ntype = "epic"\ntitle = "epic"\n'
        'status = "pending"\n\n[[task]]\nid = "a"\ntitle = "a"\nstatus = "done"\n'
        'parent = "E"\nwrite_set = ["a"]\ndone_at = "2026-01-02T10:00:00+01:00"\n'
    )
    (tmp_path / "briefs").mkdir()
    (tmp_path / "briefs" / "a.md").write_text("## Change\n")
    (tmp_path / "briefs" / "E.md").write_text("## Change\n")
    _, result = run_bare(plan, "check")
    assert "warnings" not in result
    (tmp_path / "briefs" / "a.md").unlink()
    _, result = run_bare(plan, "check")
    assert "warnings" not in result


def test_check_warns_about_a_title_above_sixty_characters(run):
    """The limit is 60 characters, and the warning exits 0."""

    plan = _plan(("a", "pending", []))
    ok = plan.replace('title = "a"', f'title = "{"x" * 60}"')
    assert "warnings" not in run(ok, "check")[1]

    code, result = run(plan.replace('title = "a"', f'title = "{"x" * 61}"'), "check")
    assert code == 0
    assert result["warnings"] == [
        "a: title has 61 characters, keep it at 60 or fewer and put the detail "
        "in briefs/a.md"
    ]


def _similar(first: str, second: str, extra: str = "", write: str = "x") -> str:
    def block(task_id: str, title: str, more: str) -> str:
        return (
            f'\n[[task]]\nid = "{task_id}"\ntitle = "{title}"\nstatus = "pending"\n'
            f'write_set = ["{write}-{task_id}"]\n{more}'
        )

    return "format = 1\n" + block("kanban-columns", first, extra) + block(
        "l10", second, ""
    )


def test_check_warns_about_two_open_tasks_that_look_alike(run, run_text):
    plan = _similar("Kanban columns on the board", "Board kanban columns")
    code, result = run(plan, "check")
    assert code == 0
    assert len(result["warnings"]) == 1
    assert result["warnings"][0].startswith("kanban-columns and l10 look alike")
    assert not any("look alike" in line for line in run_text(plan, "tree"))


def test_check_does_not_compare_a_task_with_its_dependency(run):
    plan = _similar("Kanban columns", "Kanban columns", 'deps = ["l10"]\n')
    assert "warnings" not in run(plan, "check")[1]


def test_check_lets_similar_silence_a_pair(run):
    plan = _similar("Kanban columns", "Kanban columns", 'similar = ["l10"]\n')
    assert "warnings" not in run(plan, "check")[1]


def test_check_skips_a_deferred_twin(run):
    plan = _similar("Kanban columns", "Kanban columns").replace(
        'status = "pending"', 'status = "deferred"', 1
    )
    assert "warnings" not in run(plan, "check")[1]


def test_check_skips_a_twin_that_is_a_dependency_of_a_dependency(run):
    plan = _similar("Kanban columns", "Kanban columns", 'deps = ["mid"]\n')
    plan += (
        '\n[[task]]\nid = "mid"\ntitle = "other work"\nstatus = "pending"\n'
        'write_set = ["m"]\ndeps = ["l10"]\n'
    )
    assert "warnings" not in run(plan, "check")[1]


@pytest.mark.parametrize("value", ['"nope"', '"kanban-columns"'])
def test_check_refuses_a_similar_id_that_is_not_another_task(run, value):
    plan = _similar("Kanban columns", "Kanban columns", f"similar = [{value}]\n")
    code, result = run(plan, "check")
    assert code == 2
    assert any("similar" in e for e in result["errors"])


@pytest.mark.parametrize(
    ("extra", "message"),
    [
        ('session = "a b"', "'session' must be one token"),
        ('session = "a\\tb"', "'session' must be one token"),
        ('session = "a"\nsession_dir = "/x\\ny"', "must not contain a newline"),
    ],
)
def test_session_and_session_dir_refuse_whitespace_inside(run, extra, message):
    plan = _similar("Alpha", "Beta", extra + "\n")
    code, result = run(plan, "check")
    assert code == 2
    assert any(message in e for e in result["errors"])


def test_session_dir_may_contain_a_space(run):
    plan = _similar("Alpha", "Beta", 'session = "a"\nsession_dir = "/my dir"\n')
    assert run(plan, "check")[0] == 0


def test_check_ignores_unrelated_tasks_and_shared_files_with_no_shared_word(run):
    assert "warnings" not in run(_similar("Add a cache", "Fix the parser"), "check")[1]
    plan = _similar("Add a cache", "Fix the parser").replace(
        'write_set = ["x-l10"]', 'write_set = ["x-kanban-columns"]'
    )
    assert "warnings" not in run(plan, "check")[1]
    shared = plan.replace("Add a cache", "Add token cache").replace(
        "Fix the parser", "Fix token cache"
    )
    assert len(run(shared, "check")[1]["warnings"]) == 1


def test_a_script_shared_on_purpose_with_one_generic_word_is_silent(run):
    plan = _alike(
        ("epic-empty-bar", "Epic empty bar shows done", ["s/x.py"]),
        ("show-deferred", "Show deferred rows", ["s/x.py"]),
    )
    assert "warnings" not in run(plan, "check")[1]
    hunks = plan.replace('status = "pending"', 'status = "pending"\noverlap = "hunks"')
    assert "warnings" not in run(hunks, "check")[1]


def test_both_hunks_silences_a_pair_that_shares_two_words(run):
    plan = _alike(
        ("a1", "Add token cache", ["s/x.py"]), ("b2", "Fix token cache", ["s/x.py"])
    )
    assert len(run(plan, "check")[1]["warnings"]) == 1
    hunks = plan.replace('status = "pending"', 'status = "pending"\noverlap = "hunks"')
    assert "warnings" not in run(hunks, "check")[1]


def _alike(*tasks: tuple[str, str, list[str]]) -> str:
    return "format = 1\n" + "".join(
        f'\n[[task]]\nid = "{i}"\ntitle = "{t}"\nstatus = "pending"\n'
        f"write_set = {json.dumps(w)}\n"
        for i, t, w in tasks
    )


def test_a_shared_doc_and_a_title_word_is_no_warning_but_a_script_is(run):
    docs = _alike(("a1", "Add token cache", ["task-graph.md"]), ("b2", "Fix token cache", ["task-graph.md"]))
    assert "warnings" not in run(docs, "check")[1]
    script = _alike(("a1", "Add token cache", ["s/x.py"]), ("b2", "Fix token cache", ["s/x.py"]))
    assert len(run(script, "check")[1]["warnings"]) == 1
    two = _alike(
        ("a1", "Add token cache", ["task-graph.md", "AGENTS.md"]),
        ("b2", "Fix token cache", ["task-graph.md", "AGENTS.md"]),
    )
    assert len(run(two, "check")[1]["warnings"]) == 1


def test_the_tree_prints_no_warning_and_check_still_does(run, run_text):
    long = "Add the token cache for every parser pass that runs on each file"
    assert len(long) > 60
    text = _alike(("a1", long, ["s/x.py"]), ("b2", "Fix token cache", ["s/x.py"]))
    assert not any(line.startswith("warn") for line in run_text(text, "tree"))
    assert run(text, "check")[1]["warnings"]


PARKED = """format = 1

[[task]]
id = "E1"
type = "epic"
title = "work"
status = "pending"

[[task]]
id = "a"
title = "active"
status = "pending"
parent = "E1"
read_only = true

[[task]]
id = "later"
title = "parked"
status = "deferred"
parent = "E1"
priority = "P0"
write_set = ["x"]

[[task]]
id = "after"
title = "behind parked"
status = "pending"
parent = "E1"
deps = ["later"]
read_only = true

[[task]]
id = "w"
title = "waiting"
status = "pending"
parent = "E1"
deps = ["a"]
read_only = true

[[task]]
id = "E2"
type = "epic"
title = "only parked"
status = "pending"

[[task]]
id = "p2"
title = "parked too"
status = "deferred"
parent = "E2"
read_only = true
"""


def test_check_and_set_accept_deferred(run, tmp_path: pathlib.Path):
    assert run(PARKED, "check")[0] == 0
    code, result = run(PARKED, "set", "a", "status", "deferred")
    assert code == 0
    assert result["ok"] is True
    assert 'status = "deferred"' in (tmp_path / "tasks.toml").read_text()


def test_next_never_starts_or_skips_a_deferred_task(run):
    _, result = run(PARKED, "next", *caps())
    assert [t["id"] for t in result["start"]] == ["a"]
    assert result["skipped"] == []
    assert result["running"] == []
    assert {"id": "after", "blocked_by": ["later"]} in result["waiting"]
    assert "later" not in {w["id"] for w in result["waiting"]}


def test_a_dependent_of_a_deferred_task_is_not_ready_and_explain_names_it(run):
    _, result = run(PARKED, "explain", "after")
    assert result["ready"] is False
    assert result["blocked_by"] == ["later"]
    _, parked = run(PARKED, "explain", "later")
    assert parked["status"] == "deferred"
    assert parked["ready"] is False
    assert parked["holds_up"] == {}
    assert parked["warning"] is None


def test_a_deferred_task_has_no_blocks_note_on_the_board_but_the_tree_counts(run_text):
    lines = run_text(PARKED, "tree", "--all", "--max-blocked", "0")
    assert _row(lines, "later").endswith("later · blocks 1")
    assert "blocks" not in _row(lines, "p2")
    assert not any("blocks" in line for line in run_text(PARKED, "board", "--max-blocked", "0") if "later" in line)


def test_the_tree_marks_a_deferred_task_and_sorts_it_last(run_text):
    lines = run_text(PARKED, "tree", "--epic", "E1")
    ids = [_label(line)[1] for line in _rows(lines)]
    assert ids == ["E1", "a", "after", "w", "later"]
    row = _row(lines, "later")
    assert row.split()[1:6] == ["◌", "parked", "·", "P0", "later"]
    assert "◌ deferred" in "\n".join(lines)


def test_the_deferred_row_is_dim_and_never_italic_or_bold(run_text):
    lines = run_text(PARKED, "tree", "--epic", "E1", "--color", "always")
    row = next(line for line in lines if "parked" in line and "behind" not in line)
    assert "\033[2m" in row
    assert "\033[3m" not in row
    assert "\033[1m" not in row


def test_deferred_leaves_and_their_epic_show_without_all(run_text):
    shown = "\n".join(run_text(PARKED, "tree"))
    assert "E1" in shown
    assert "E2" in shown
    assert "later" in shown
    assert "p2" in shown
    assert "hidden" not in shown
    assert "E2" in "\n".join(run_text(PARKED, "tree", "--epic", "E2"))


DONE_EPICS = (
    "format = 1\n"
    + _epic("EA")
    + _block("a1", "done", parent="EA")
    + _epic("EB")
    + _block("b1", "done", parent="EB")
    + _block("b2", "deferred", parent="EB")
    + _epic("EP")
    + _block("p1", "pending", parent="EP")
    + _epic("EC", parent="EP")
    + _block("c1", "done", parent="EC")
    + _epic("EN", parent="EP")
    + _block("n1", "done", parent="EN")
)


def test_a_finished_epic_is_hidden_without_all(run_text):
    shown = _order(run_text(DONE_EPICS, "tree"))
    assert shown == ["EP", "p1", "EB", "b2"]
    assert {"EA", "EB", "EC", "EN"} <= set(_order(run_text(DONE_EPICS, "tree", "--all")))


def test_epic_opens_a_finished_epic(run_text):
    assert _order(run_text(DONE_EPICS, "tree", "--epic", "EA")) == ["EA"]
    assert _order(run_text(DONE_EPICS, "tree", "--epic", "EC"))[0] == "EC"


def test_a_parent_keeps_its_open_leaves_and_hides_a_finished_child(run_text):
    shown = _order(run_text(DONE_EPICS, "tree"))
    assert "EP" in shown and "p1" in shown
    assert "EC" not in shown


def test_the_board_lists_deferred_cards_last_in_todo(run_text):
    lines = run_text(PARKED, "board")
    start = lines.index(next(x for x in lines if x.startswith("Todo "))) + 1
    ids = [line.split()[0] for line in lines[start : lines.index("", start)]]
    assert ids == ["a", "after", "w", "later", "p2"]
    assert any(line.startswith("  later") and "deferred" in line for line in lines)


def test_the_title_is_dim_only_for_a_task_that_waits_and_is_not_italic(
    run_text, monkeypatch
):
    monkeypatch.delenv("NO_COLOR", raising=False)
    text = "format = 1\n" + "".join(
        [
            _block("run", "in_progress"),
            _block("rdy"),
            _block("pend"),
            _block("far", deps="pend").replace('"pend"', '["pend"]'),
            _block("soon", deps="run").replace('"run"', '["run"]'),
        ]
    )
    lines = run_text(text, "tree", "--color", "always", "--width", "300")
    shown = "\n".join(_rows(lines))
    assert "\033[2mfar\033[0m" in shown
    assert "\033[3msoon\033[0m" in shown
    assert "\033[2msoon" not in shown
    assert "\033[1mrun\033[0m" in shown
    assert "\033[2mrun" not in shown
    assert "\033[2mrdy" not in shown
    assert "dim waits" in "\n".join(lines)


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


def test_help_of_every_command_holds_its_flags_keys_codes_and_an_example(
    run, tmp_path: pathlib.Path
):
    module = _load()
    commands = _subcommands(module)
    assert set(commands) == set(module.COMMAND_CONTRACTS)

    helps = {name: sub.format_help() for name, sub in commands.items()}
    for name, sub in commands.items():
        for action in sub._actions:
            for flag in action.option_strings:
                assert flag in helps[name], f"{name} --help misses {flag}"
        assert "exit codes:" in helps[name]
        assert "example: ledger_graph.py" in helps[name]

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
    assert {"session", "overlaps", "model", "effort"} <= set(
        _keys(samples["next"][1])
    )
    assert {"with", "detail"} <= set(_keys(samples["set"][1]))
    for name, (_, result) in samples.items():
        for key in _keys(result):
            # The ids of the sample tasks are data, not keys.
            if key.isidentifier() and key.islower() and not re.fullmatch(r"[rt]?s?\d", key):
                assert re.search(
                    rf"\b{key}\b", module.COMMAND_CONTRACTS[name]
                ), f"{name} contract misses the key {key}"


def test_the_legend_card_styles_the_title_words_and_keeps_items_whole():
    """bold, italic and dim carry real styles; marks show only when present."""

    ledger_graph = _load()
    card = ledger_graph.legend_card()
    lines = [line.plain for line in card]
    for heading in ("State", "Title", "Priority", "Marks"):
        assert any(line.startswith(f" {heading} ─") for line in lines)
    assert lines[-1] == " l or esc to close"
    assert max(len(line) for line in lines) <= 40
    assert len(lines) < 36
    for word in ("bold", "italic", "dim"):
        line = next(x for x in card if x.plain.startswith(f" {word} "))
        assert [str(s.style) for s in line.spans[:1]] == [word]
        assert line.plain[line.spans[0].start : line.spans[0].end].strip() == word
    text = "\n".join(lines)
    assert all(mark in text for mark in ("⧉", "◆", "✎", "✉", "◌", "blocks 2", "waits X"))
    assert f"{ledger_graph.BRANCH_GLYPH} " in text
    assert "not shown now" not in text


def test_tree_counts_only_the_not_started_tasks_behind_a_task(run_text):
    text = "format = 1\n" + "".join(
        [
            _block("hub", "in_progress"),
            _block("a", deps="hub").replace('"hub"', '["hub"]'),
            _block("b", deps="a").replace('"a"', '["a"]'),
            _block("c", "in_progress", deps="hub").replace('"hub"', '["hub"]'),
            _block("d", "done", deps="hub").replace('"hub"', '["hub"]'),
        ]
    )
    tree = "\n".join(run_text(text, "tree", "--color", "never", "--all"))

    assert tree.count("blocks 2") == 1
    assert tree.count("blocks 1") == 1
    assert tree.count("blocks 0") == 0


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
    return line, "\n".join(module.format_event(e) for e in module.read_events(tasks, "r1"))


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
    assert lines[0].endswith("launch \u00b7 mode=background \u00b7 agent=a1 \u00b7 model=m \u00b7 host=box \u00b7 pane=p7 \u00b7 round=1")
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
    old_line = "\n".join(
        f'{{"ts": "{old}", "task": "r1", "event": "launch", "detail": "host=box pane=p7"}}'
        for _ in range(1)
    )
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
    _log_answer(tmp_path, datetime.datetime.now().astimezone() + datetime.timedelta(seconds=5))
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


def test_next_reports_an_answer_logged_in_the_same_second_as_the_last_call(run, tmp_path):
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


def test_write_replan_request_writes_one_file_and_leaves_the_ledger_alone(tmp_path: pathlib.Path):
    module = _load()
    path = tmp_path / "tasks.toml"
    path.write_text(REPLANNING)
    before = path.stat()

    request = module.write_replan_request(
        path, "epic", "E1", ' split "a" \u00e8 ', seen_mtime_ns=before.st_mtime_ns
    )

    assert request.parent == tmp_path / "replan-requests"
    assert re.fullmatch(r"\d{8}T\d{6}Z\.toml", request.name)
    assert tomllib.loads(request.read_text()) == {"scope": "epic", "epic": "E1", "note": 'split "a" \u00e8'}
    assert [p.name for p in request.parent.iterdir()] == [request.name]
    assert path.read_text() == REPLANNING
    assert path.stat().st_mtime_ns == before.st_mtime_ns
    assert not (tmp_path / "steering.log").exists()


def test_write_replan_request_refuses_a_bad_request_and_writes_nothing(tmp_path: pathlib.Path):
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
    (folder / "20260102T000000Z.toml").write_text('scope = "remaining"\nnote = "later"\n')
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
    assert [r["request"] for r in requests[2:]] == ["replan-requests/20260103T000000Z.toml"]
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
    return {**HUNK_COMMENT, "noteId": "user:1794402060751-2", "newRange": [12, 14], "body": "rename it"}


def test_save_feedback_draft_keeps_the_export_and_never_replaces_a_ready_file(tmp_path: pathlib.Path):
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
    assert json.loads((tmp_path / "feedback" / "r1.json").read_text())["comments"] == [_second_comment()]

    # A new hunk session has no comments yet: the draft stays.
    assert not module.save_feedback_draft(path, "r1", [])
    assert module.feedback_state(path) == {"r1": (1, False)}


def test_send_feedback_refuses_what_it_cannot_send_and_writes_nothing(tmp_path: pathlib.Path):
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
    (folder / "a.json").write_text(json.dumps({"ready": False, "comments": [HUNK_COMMENT]}))
    (folder / "b.json").write_text(json.dumps({"ready": True, "comments": [HUNK_COMMENT, _second_comment()]}))
    (folder / "c.json").write_text(json.dumps({"ready": True, "comments": [{"body": "no line"}]}))
    (folder / "d.json").write_text("{")
    (folder / "done" / "e.json").write_text(json.dumps({"ready": True, "comments": [HUNK_COMMENT]}))
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
    module.save_feedback_draft(path, "r1", [HUNK_COMMENT, {"noteId": "x", "body": "no file"}])

    batch, skipped = module.feedback_batch(path, "r1")

    assert batch == {
        "comments": [{"filePath": "m.py", "newLine": 9, "summary": "use type hints", "author": "user"}]
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
        old, old.replace('title = "a"', 'title = "x"').replace('"b"\nstatus = "in_progress"', '"b"\nstatus = "done"')
    )
    assert code == 2
    assert [e.split(":")[0] for e in result["errors"]] == ["a", "b"]
    assert "title" in result["errors"][0]
    assert "status" in result["errors"][1]

    removed = old.replace('\n[[task]]\nid = "c"\ntitle = "c"\nstatus = "in_rev"\ndeps = []\nwrite_set = ["c"]\n', "")
    assert removed != old
    code, result = compare(old, removed)
    assert code == 2
    assert result["errors"] == ["c: in_rev and frozen, but the proposal removes it"]


def test_the_done_column_lists_the_newest_task_first(tmp_path: pathlib.Path):
    module = _load()
    text = "format = 1\n\n" + "".join(
        f'[[task]]\nid = "{i}"\ntitle = "t"\nstatus = "done"\nread_only = true\n\n' for i in "abc"
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
    assert result == {"id": "t1", "needs": ["gpu"], "machine": "big", "transport": "herdr"}
    assert "secret-target" not in json.dumps(result)
    code, result = run(NEEDS.replace('"gpu"', '"tpu"'), "machine", "t1", "--map", str(machines))
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

    tasks = {t["id"]: t for t in tomllib.loads((tmp_path / "tasks.toml").read_text())["task"]}
    assert tasks["t9"]["status"] == "in_progress"
    assert tasks["t9"]["write_set"] == ["src/z.py"]
    assert "## Acceptance" in (tmp_path / "briefs" / "t9.md").read_text()
    brief = (tmp_path / "briefs" / "t9.md").read_text()
    assert brief.startswith(text)
    assert "# Fix\n" in brief
    assert "May edit: `src/z.py`" in brief
    assert "## Return" in brief


def test_register_refuses_an_invalid_task_and_leaves_the_file_untouched(tmp_path: pathlib.Path):
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
