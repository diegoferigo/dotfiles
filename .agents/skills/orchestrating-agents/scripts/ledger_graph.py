#!/usr/bin/env -S pixi exec --spec python=3.12 --spec rich python
"""Pick the next tasks to run from the task graph of an orchestration ledger.

The graph lives in `tasks.toml`, next to `ORCHESTRATION.md`. The script validates
the file and prints the answer, and every edit is made on the file itself, except
the priority and the status, with `set`, and the reply to a blocker, with
`answer`. A status that frees capacity is answered with `next`. It needs Python
3.11 or newer (`tomllib`) and `rich`, for `tree`.
"""

import argparse
import contextlib
import dataclasses
import datetime
import fcntl
import functools
import graphlib
import io
import ipaddress
import json
import os
import pathlib
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import typing
import unicodedata

import rich.console
import rich.text
import tomllib

FORMAT = 1
RUNNING = {"running", "in_progress", "in_rev", "returned"}
# The status of a task whose diff a human reviews in hunk.
REVIEW_STATUS = "in_rev"
# A task can be looked at in hunk, and steered with feedback, while it runs or once it returns for review.
HUNK_STATUSES = {"in_progress", REVIEW_STATUS}
# The tasks a replan never changes or removes: the finished ones and the ones with a worktree.
FROZEN = {"done", "in_progress", REVIEW_STATUS}
# The folder next to `tasks.toml` that holds the replan requests of the human.
REPLAN_DIR = "replan-requests"
REPLAN_SCOPES = ("remaining", "epic")
# The folder next to `tasks.toml` that holds the comments of the human on the diff of a task in review.
FEEDBACK_DIR = "feedback"
# The orchestrator moves a feedback file it handled here.
FEEDBACK_DONE = "done"
# The ref a diff starts at when the ledger names no stack tip.
DEFAULT_BASE = "integration"
STATUSES = RUNNING | {"pending", "blocked", "done", "deferred"}
TYPES = {"task", "epic"}
PRIORITIES = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
SETTABLE_STATUSES = ("pending", "in_progress", "done", "blocked", "deferred")
# A status that frees capacity: `set` answers with `next` after it.
FREEING_STATUSES = {"pending", "done", "blocked", "deferred"}
DEFAULT_PRIORITY = "P2"
# The id takes the color of its priority, in the names of the terminal palette.
PRIORITY_STYLES = {
    "P0": "red",
    "P1": "yellow",
    "P2": "bright_blue",
    "P3": "bright_black",
}
# What a row shows before the id, without colors, when it has no priority.
NO_PRIORITY = "--"
AWAITING_STYLE = "bold magenta"
BRANCH_GLYPH = "\ue0a0"
BRANCH_ASCII = "@"
BOLD = re.compile(r"(?<![\w*])\*\*(?![\s*])(.+?)(?<![\s*])\*\*(?![\w*])")
DEFAULT_WIDTH = 80
MAX_BLOCKED = 3
MAX_REMOVABLE = 8
WT = "wt"
DETAIL_SEP = " · "
MAX_WAITS = 2
# Columns a title keeps before the detail starts to give way.
MIN_TITLE_ROOM = 6
# The ranks of the detail parts of a row, the highest one is dropped first.
MARK, SUMMARY, WAITS, PLACE = range(4)
MARKS = {"awaiting you"}
# The icons the board adds after the id of a task: the copy of its review command and the human review mark.
COPY_ICON = "⧉"
HUMAN_REVIEW_ICON = "◆"
# The feedback marks the board adds after the id: N comments saved from hunk (a draft), N sent to the orchestrator.
DRAFT_ICON = "✎"
SENT_ICON = "✉"
# The text marks of the legend, always listed. "copy", "review" and "feedback" are icons the board draws.
LEGEND_MARKS = (
    "blocks N: tasks waiting on it",
    "waits X (dep): X is a dep; waits X (files): ready, but its write_set overlaps the one of X",
    f"{COPY_ICON} copy the hunk command, y in overlay",
    f"{HUMAN_REVIEW_ICON} marked for your review, h to toggle",
    f"{DRAFT_ICON}N draft, {SENT_ICON}N sent, f send, F restore",
)
# The icons of the legend card: the icon, its label and its key.
ICON_LEGEND = (
    (COPY_ICON, "copy command", "y"),
    (HUMAN_REVIEW_ICON, "your review", "h"),
    (DRAFT_ICON, "N drafts", "f"),
    (SENT_ICON, "N sent", "F"),
)
# The title styles of the legend: the style and its label.
TITLE_STYLES = (
    ("bold", "running"),
    ("italic", "starts soon"),
    ("dim", "waits"),
)
HUMAN = "human:"
EVENT = "event:"
# The prompt prefix of every `task` call, so an agent without a ledger task stops.
LEDGER_GATE = (
    "[ledger:{id}]\n"
    "Brief: {brief}\n"
    'First action: grep tasks.toml for id "{id}" and check status in_progress and '
    "briefs/{id}.md exists; otherwise return only NO LEDGER TASK: <what is missing> "
    "and do nothing else."
)
GLOB_CHARS = "*?[{"
HEADER = re.compile(r"\[\[task\]\]\s*(#.*)?$")
LIMIT_KEYS = ("rw", "ro", "budget")
# Header tables of the default model and effort per role.
DEFAULT_KEYS = ("model", "effort")
EFFORTS = ("low", "medium", "high", "xhigh")
# GitHub allows 8 levels of sub-issues under the top issue.
MAX_NESTING = 8
# A title says the outcome at a glance, the rest goes in briefs/<id>.md.
MAX_TITLE = 60
# Statuses that are no open work for the schedule of `compare`.
PARKED = ("done", "deferred")
SET_KEYS = ("deps", "integrate_after", "write_set", "exclusive", "similar", "needs")
COMPARED = (
    "type",
    "title",
    "status",
    "priority",
    "milestone",
    "parent",
    "deps",
    "integrate_after",
    "write_set",
    "read_only",
    "footprint",
    "exclusive",
    "needs",
    "blocker",
    "answer",
    "worktree",
    "branch",
    "base",
    "stack",
    "overlap",
    "model",
    "effort",
    "session",
    "session_dir",
    "similar",
    "done_at",
    "human_review",
)
OVERLAPS = {"hunks"}
KEYS = {"id", *COMPARED}
BOARD = (
    ("In progress", RUNNING),
    ("Todo", {"pending", "blocked", "deferred"}),
    ("Done", {"done"}),
)


@dataclasses.dataclass
class Task:
    """A `[[task]]` block of `tasks.toml`."""

    id: str
    row: int
    type: str = "task"
    status: str = ""
    deps: list[str] = dataclasses.field(default_factory=list)
    # Soft edge: the task may start before these are done, but not finish.
    integrate_after: list[str] = dataclasses.field(default_factory=list)
    parent: str = ""
    priority: str = DEFAULT_PRIORITY
    milestone: str = ""
    write_set: list[str] | None = None
    read_only: bool = False
    footprint: int = 1
    exclusive: list[str] = dataclasses.field(default_factory=list)
    needs: list[str] = dataclasses.field(default_factory=list)
    title: str = ""
    blocker: str = ""
    answer: str = ""
    worktree: str = ""
    branch: str = ""
    base: str = ""
    stack: str = ""
    overlap: str = ""
    model: str = ""
    effort: str = ""
    session: str = ""
    session_dir: str = ""
    similar: list[str] = dataclasses.field(default_factory=list)
    done_at: str = ""
    human_review: bool = False
    # Set only to sort epics, so an edit of the priority still compares equal.
    has_priority: bool = dataclasses.field(default=False, compare=False)

    @staticmethod
    def build(row: int, block: dict[str, typing.Any]) -> "Task":

        given = {key: block[key] for key in COMPARED if key in block}
        if "stack" in given:
            given["stack"] = str(given["stack"])
        return Task(block.get("id", ""), row, **given, has_priority="priority" in block)


@dataclasses.dataclass(frozen=True)
class Limits:
    """How much of this machine one scheduling wave may use.

    `rw` caps the writers, `ro` the read-only agents and `budget` the sum of the
    footprints. None is no cap. The file says what the work is, these say what
    the machine affords, so they come from the header of the file or from the
    flags of `next`, never from a task.
    """

    rw: int | None = None
    ro: int | None = None
    budget: int | None = None

    def override(
        self, *, rw: int | None, ro: int | None, budget: int | None
    ) -> "Limits":
        """The same caps, with every flag that was given taking over."""

        given = {"rw": rw, "ro": ro, "budget": budget}
        return dataclasses.replace(
            self, **{k: v for k, v in given.items() if v is not None}
        )


def parse(text: str) -> tuple[list[Task], list[str]]:
    """Read the `[[task]]` blocks of a `tasks.toml`.

    The result is a pair: the tasks, and the list of errors. The tasks are empty
    when there are errors.
    """

    try:
        data = tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        return [], [f"invalid TOML: {exc}"]

    # Refuse a newer format, because an old reader would ignore keys that change the schedule.
    if data.get("format") != FORMAT:
        found = data.get("format")
        return [], [
            f"unsupported format {found!r}, this script reads format {FORMAT}: stop and do not write"
        ]

    errors = [
        f"unknown top-level key '{k}'"
        for k in data
        if k not in {"format", "task", *LIMIT_KEYS, *DEFAULT_KEYS}
    ]
    errors += [
        f"header: '{k}' must be a positive integer"
        for k in LIMIT_KEYS
        if k in data and not _is_positive_int(data[k])
    ]
    errors += _check_defaults(data)
    blocks = data.get("task", [])

    if not isinstance(blocks, list) or not all(isinstance(b, dict) for b in blocks):
        return [], ["'task' must be an array of tables: write [[task]], not [task]"]

    if not blocks:
        errors.append("no [[task]] block")

    for number, block in enumerate(blocks, start=1):
        errors += _check_block(number, block)

    if errors:
        return [], errors

    return [Task.build(n, block) for n, block in enumerate(blocks, start=1)], []


def read_limits(text: str) -> Limits:
    """The caps written in the header of a `tasks.toml` that `parse` accepted.

    The text is read again instead of being carried out of `parse`, because a
    third return value would change the signature `ledger_board.py` and the
    tests call, and only `next` and `resume` need the caps.
    """

    data = tomllib.loads(text)
    return Limits(rw=data.get("rw"), ro=data.get("ro"), budget=data.get("budget"))


def _locked_write(function):
    """Hold an exclusive lock on the ledger from the read to the replace."""

    @functools.wraps(function)
    def wrapper(path, *args, **kwargs):
        resolved = pathlib.Path(path).resolve()
        lock = resolved.with_name(resolved.name + ".lock")
        with lock.open("a") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            return function(resolved, *args, **kwargs)

    return wrapper


def set_priority(
    path: pathlib.Path, task_id: str, priority: str, *, seen_mtime_ns: int
) -> None:
    """Write the priority of one task.

    The write is refused when the priority is unknown, when the file changed
    since it was read, when `check` would refuse the file, when the id does not
    exist, when the block cannot be rewritten, or when the result would differ
    from the file by more than that one priority. Only the `priority` line
    changes: comments, key order and layout survive.
    """

    _set_field(path, task_id, "priority", priority, seen_mtime_ns=seen_mtime_ns)


def set_status(
    path: pathlib.Path, task_id: str, status: str, *, seen_mtime_ns: int
) -> None:
    """Write the status of one task.

    The write is refused for the same reasons as `set_priority`, for a status
    that is not one of `SETTABLE_STATUSES`, and for a task blocked on a human,
    which only `answer_blocker` frees, and a task marked for human review that
    is set to `done`, which only `set_human_review` frees. Leaving `blocked`
    removes the blocker,
    because a blocker needs `status = "blocked"`. Nothing else changes.
    """

    _set_field(path, task_id, "status", status, seen_mtime_ns=seen_mtime_ns)


@_locked_write
def register_task(
    path: pathlib.Path,
    task_id: str,
    fields: dict[str, typing.Any],
    brief: str,
    *,
    seen_mtime_ns: int,
) -> pathlib.Path:
    """Append an `in_progress` task and its brief, and return the brief path.

    The whole result is checked as `check` does before anything is written, so a
    refused task leaves the file and the briefs folder untouched. The fields are
    written as TOML in the order given.
    """

    text, _, _ = _load_for_edit(path, seen_mtime_ns=seen_mtime_ns)

    lines = ["[[task]]", f"id = {json.dumps(task_id)}"]
    lines += [f"{key} = {json.dumps(value)}" for key, value in fields.items()]
    separator = "" if text.endswith("\n\n") else "\n" if text.endswith("\n") else "\n\n"
    new_text = text + separator + "\n".join(lines) + "\n"

    tasks, errors = parse(new_text)
    errors = errors or Graph(tasks).errors
    if errors:
        raise ValueError("; ".join(errors))

    brief_path = path.parent / "briefs" / f"{task_id}.md"
    brief_path.parent.mkdir(exist_ok=True)
    # Mode "x" refuses to replace the brief of another task.
    with brief_path.open("x", encoding="utf-8") as handle:
        handle.write(brief)

    try:
        _replace_file(
            path, new_text, seen_mtime_ns=seen_mtime_ns, entry=f"{task_id} added in_progress"
        )
    except BaseException:
        brief_path.unlink(missing_ok=True)
        raise

    return brief_path


@_locked_write
def _set_field(
    path: pathlib.Path,
    task_id: str,
    field: typing.Literal["priority", "status"],
    value: str,
    *,
    seen_mtime_ns: int,
) -> None:
    """Write the priority or the status of one task, and log the edit.

    Both writes share the checks on the file, so a fix to one reaches the other.
    """

    allowed = sorted(PRIORITIES) if field == "priority" else list(SETTABLE_STATUSES)
    if value not in allowed:
        msg = f"unknown {field} '{value}', use one of {', '.join(allowed)}"
        raise ValueError(msg)

    text, tasks, graph = _load_for_edit(path, seen_mtime_ns=seen_mtime_ns)

    if task_id not in graph.tasks:
        msg = f"unknown id '{task_id}'"
        raise ValueError(msg)

    task = graph.tasks[task_id]
    old = task.priority if field == "priority" else task.status
    if old == value:
        return

    values: dict[str, str | None] = {field: json.dumps(value)}
    changes: dict[str, typing.Any] = {field: value}
    entry = f"{task_id} {old} -> {value}"

    if field == "status":
        if graph.question(task_id) is not None:
            msg = f"'{task_id}' is blocked on a human: answer it with 'answer'"
            raise ValueError(msg)

        if value == "done" and task.human_review:
            msg = (
                f"'{task_id}' is marked for human review: only the human clears the "
                "mark, in the board (h), then set it to done"
            )
            raise ValueError(msg)

        pending_soft = [
            d for d in task.integrate_after if graph.tasks[d].status != "done"
        ]
        if value == "done" and pending_soft:
            msg = (
                f"'{task_id}' integrates after {', '.join(pending_soft)}: "
                "they are not done yet"
            )
            raise ValueError(msg)

        # A blocker needs `status = "blocked"`, so leaving that status drops it.
        if value != "blocked" and task.blocker:
            values["blocker"] = None
            changes["blocker"] = ""

        entry = f"{task_id} status {old} -> {value}"

        # `done_at` records when the task finished, so it follows the status.
        if value == "done":
            stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
            values["done_at"] = json.dumps(stamp)
            changes["done_at"] = stamp
        elif task.done_at:
            values["done_at"] = None
            changes["done_at"] = ""

    new_text = _rewrite_block(text, task_id, values)
    _check_edit(new_text, tasks, task_id, changes, field)
    _replace_file(path, new_text, seen_mtime_ns=seen_mtime_ns, entry=entry)


@_locked_write
def answer_blocker(
    path: pathlib.Path, task_id: str, text: str, *, seen_mtime_ns: int
) -> None:
    """Answer the question of a task blocked on a human, and make it pending again.

    The write is refused when the answer holds a control character other than a
    tab or a line feed (a carriage return included) or a line separator, when the file
    changed since it was read, when `check` would refuse the file, when the id
    does not exist, when the task is not blocked on a `human:` blocker, when the
    block cannot be rewritten, or when the result would differ from the file by
    more than the status, the blocker and the answer of that task. The answer
    may span several lines: they are written escaped, on one line of the file.
    """

    # Cc covers the C0 and C1 ranges and DEL, Zl and Zp the U+2028 and U+2029
    # separators, which a reader would break the line on.
    if any(
        c not in "\t\n" and unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in text
    ):
        msg = "the answer contains a control character"
        raise ValueError(msg)

    content, tasks, graph = _load_for_edit(path, seen_mtime_ns=seen_mtime_ns)

    if task_id not in graph.tasks:
        msg = f"unknown id '{task_id}'"
        raise ValueError(msg)

    if graph.question(task_id) is None:
        msg = f"'{task_id}' is not blocked on a human: only a '{HUMAN}' blocker is answered"
        raise ValueError(msg)

    # The value is a TOML basic string, and json.dumps escapes it the same way.
    new_text = _rewrite_block(
        content,
        task_id,
        {
            "status": '"pending"',
            "blocker": None,
            "answer": json.dumps(text, ensure_ascii=False),
        },
    )
    changes = {"status": "pending", "blocker": "", "answer": text}
    _check_edit(new_text, tasks, task_id, changes, "answer")
    _replace_file(
        path, new_text, seen_mtime_ns=seen_mtime_ns, entry=f"{task_id} answer"
    )


@_locked_write
def set_human_review(
    path: pathlib.Path, task_id: str, marked: bool, *, seen_mtime_ns: int
) -> None:
    """Mark a task for the review of the human, or clear the mark.

    Only the human writes this, from the board: the orchestrator never calls it.
    Marking writes `human_review = true` and clearing deletes the line, so
    nothing but that key changes. Clearing does not mark the task done. The
    write is refused for the same reasons as `set_priority`, and for marking a
    task that is done. Setting the state it already has changes nothing.
    """

    text, tasks, graph = _load_for_edit(path, seen_mtime_ns=seen_mtime_ns)

    if task_id not in graph.tasks:
        msg = f"unknown id '{task_id}'"
        raise ValueError(msg)

    task = graph.tasks[task_id]
    if task.human_review == marked:
        return

    if marked and task.status == "done":
        msg = f"'{task_id}' is done: a done task needs no review"
        raise ValueError(msg)

    new_text = _rewrite_block(
        text, task_id, {"human_review": "true" if marked else None}
    )
    _check_edit(new_text, tasks, task_id, {"human_review": marked}, "human_review")
    entry = f"{task_id} human_review {'marked' if marked else 'cleared'}"
    _replace_file(path, new_text, seen_mtime_ns=seen_mtime_ns, entry=entry)


@_locked_write
def write_replan_request(
    path: pathlib.Path,
    scope: str,
    epic: str,
    note: str,
    *,
    seen_mtime_ns: int,
) -> pathlib.Path:
    """Ask the orchestrator to replan, with a new file in `replan-requests/`.

    Only the human writes this, from the board: the orchestrator never calls it.
    The file is named after the UTC time and holds `scope`, `epic` (only for the
    scope `epic`) and an optional one-line `note`. It is written once and never
    replaced, and `tasks.toml` is not changed. The write is refused when the
    scope is unknown, when the scope `epic` names no epic or another scope
    names one, when the note is not one line, when the file changed since it
    was read, when `check` would refuse the file, or when a request of the same
    second exists. It returns the path of the request.
    """

    if scope not in REPLAN_SCOPES:
        msg = f"unknown scope '{scope}': use {' or '.join(REPLAN_SCOPES)}"
        raise ValueError(msg)

    # Cc covers the C0 and C1 ranges and DEL, Zl and Zp the U+2028 and U+2029
    # separators, which a reader would break the line on.
    note = note.strip()
    if any(unicodedata.category(c) in {"Cc", "Zl", "Zp"} for c in note):
        msg = "the note must be one line, without control characters"
        raise ValueError(msg)

    _, _, graph = _load_for_edit(path, seen_mtime_ns=seen_mtime_ns)

    if scope == "epic":
        if epic not in graph.tasks or graph.tasks[epic].type != "epic":
            msg = f"'{epic}' is not an epic: scope epic needs the id of one"
            raise ValueError(msg)
    elif epic:
        msg = f"scope '{scope}' takes no epic"
        raise ValueError(msg)

    # The value is a TOML basic string, and json.dumps escapes it the same way.
    lines = [f"scope = {json.dumps(scope)}"]
    if epic:
        lines.append(f"epic = {json.dumps(epic)}")
    if note:
        lines.append(f"note = {json.dumps(note, ensure_ascii=False)}")

    folder = path.parent / REPLAN_DIR
    folder.mkdir(exist_ok=True)
    stamp = datetime.datetime.now(datetime.UTC).strftime("%Y%m%dT%H%M%SZ")
    target = folder / f"{stamp}.toml"

    descriptor, name = tempfile.mkstemp(dir=folder, prefix=f"{stamp}.", suffix=".tmp")
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write("\n".join(lines) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        # A hard link fails when the name exists, where a rename would replace it.
        try:
            os.link(temporary, target)
        except FileExistsError:
            msg = f"a replan request of {stamp} exists: try again in a second"
            raise ValueError(msg) from None
    finally:
        temporary.unlink(missing_ok=True)

    _fsync_dir(folder)

    return target


def replan_requests(tasks_path: pathlib.Path | None) -> list[dict[str, typing.Any]]:
    """The pending replan requests of the human, oldest first.

    A request moved to `replan-requests/done/` is handled, so only the files
    right in the folder count. A file that is not valid TOML is listed with its
    `error`, so it does not hide.
    """

    if tasks_path is None:
        return []

    found = []
    for file in sorted((tasks_path.parent / REPLAN_DIR).glob("*.toml")):
        entry: dict[str, typing.Any] = {"request": f"{REPLAN_DIR}/{file.name}"}
        try:
            data = tomllib.loads(file.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            found.append({**entry, "error": str(exc)})
            continue
        found.append(
            {
                **entry,
                "scope": data.get("scope"),
                "epic": data.get("epic", ""),
                "note": data.get("note", ""),
            }
        )

    return found


def feedback_comments(text: str) -> list[dict[str, typing.Any]] | None:
    """The comments of `hunk session comment list --json`, as hunk wrote them, or None when the text is not that export."""

    try:
        data = json.loads(text)
    except ValueError:
        return None

    comments = data.get("comments") if isinstance(data, dict) else None
    if not isinstance(comments, list) or not all(isinstance(c, dict) for c in comments):
        return None

    return comments


def _read_feedback(file: pathlib.Path) -> dict[str, typing.Any] | None:
    """The content of a feedback file, or None when it is missing or not one."""

    try:
        data = json.loads(file.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None

    if not isinstance(data, dict) or not isinstance(data.get("comments"), list):
        return None

    return data


def _write_feedback(file: pathlib.Path, data: dict[str, typing.Any]) -> None:
    """Replace a feedback file in one step, so a reader sees the old content or the new one."""

    file.parent.mkdir(exist_ok=True)
    descriptor, name = tempfile.mkstemp(dir=file.parent, prefix=f"{file.name}.", suffix=".tmp")
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            json.dump(data, handle, indent=2, ensure_ascii=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())

        temporary.replace(file)
    finally:
        temporary.unlink(missing_ok=True)

    _fsync_dir(file.parent)


def _fsync_dir(folder: pathlib.Path) -> None:
    """Flush a folder, so a rename or a new file in it survives a crash."""

    directory = os.open(folder, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _handled_notes(folder: pathlib.Path, task_id: str) -> set[str]:
    """The ids of the comments of a task in the files the orchestrator moved to `done/`."""

    notes: set[str] = set()
    for file in sorted((folder / FEEDBACK_DONE).glob(f"{task_id}*.json")):
        if file.name != f"{task_id}.json" and not file.name.startswith(f"{task_id}."):
            continue

        data = _read_feedback(file)
        for comment in data["comments"] if data else []:
            if isinstance(comment, dict) and isinstance(comment.get("noteId"), str):
                notes.add(comment["noteId"])

    return notes


@_locked_write
def save_feedback_draft(
    path: pathlib.Path, task_id: str, comments: list[dict[str, typing.Any]]
) -> bool:
    """Save the comments of the human on a task as its draft, and say whether the file changed.

    Only the board writes this, from the poll of the hunk session: the
    orchestrator never calls it. The draft is `feedback/<id>.json`, next to
    `tasks.toml`, with `"ready": false` and the comments as hunk exported them.
    It keeps the comments that are not in a file of `feedback/done/` and it
    changes nothing when none is left, so the comments of a new hunk session
    that has none yet do not wipe the draft. A file that is ready is never
    replaced, and `tasks.toml` is not changed.
    """

    folder = path.parent / FEEDBACK_DIR
    file = folder / f"{task_id}.json"
    handled = _handled_notes(folder, task_id)
    fresh = [c for c in comments if c.get("noteId") not in handled]
    if not fresh:
        return False

    current = _read_feedback(file)
    if current and (current.get("ready") is True or current["comments"] == fresh):
        return False

    _write_feedback(file, {"ready": False, "comments": fresh})
    return True


@_locked_write
def send_feedback(path: pathlib.Path, task_id: str, *, seen_mtime_ns: int) -> int:
    """Mark the draft of a task ready and return its number of comments.

    Only the human writes this, from the board: the orchestrator never calls it.
    It sets `"ready": true` in `feedback/<id>.json`, which `next --awaiting`
    lists from then on. The write is refused when the file changed since it
    was read, when `check` would refuse the file, when the id does not exist,
    when the task is not `in_progress` or `in_rev`, when it has no draft or no comment, and when
    the draft is ready already. `tasks.toml` is not changed.
    """

    _, _, graph = _load_for_edit(path, seen_mtime_ns=seen_mtime_ns)

    if task_id not in graph.tasks:
        msg = f"unknown id '{task_id}'"
        raise ValueError(msg)

    if graph.tasks[task_id].status not in HUNK_STATUSES:
        msg = f"'{task_id}' is not in_progress or in_rev: only those take feedback"
        raise ValueError(msg)

    file = path.parent / FEEDBACK_DIR / f"{task_id}.json"
    data = _read_feedback(file)
    if data is None or not data["comments"]:
        msg = f"'{task_id}' has no draft comments: write them in hunk first"
        raise ValueError(msg)

    if data.get("ready") is True:
        msg = f"the feedback of '{task_id}' is sent already"
        raise ValueError(msg)

    _write_feedback(file, {**data, "ready": True})
    return len(data["comments"])


def feedback_state(tasks_path: pathlib.Path | None) -> dict[str, tuple[int, bool]]:
    """The number of comments and whether they are sent, by task id, of the files right in `feedback/`."""

    if tasks_path is None:
        return {}

    state = {}
    for file in sorted((tasks_path.parent / FEEDBACK_DIR).glob("*.json")):
        data = _read_feedback(file)
        if data is not None:
            state[file.stem] = (len(data["comments"]), data.get("ready") is True)

    return state


def feedback_ready(tasks_path: pathlib.Path | None) -> list[dict[str, typing.Any]]:
    """The feedback the human sent, by task id.

    Only the files right in `feedback/` that are `"ready": true` count: a
    draft is never read as a finding, and a file moved to `feedback/done/` is
    handled. A comment is `{path, line, text}`, the file, the first line of the
    new side and the body of the note. A file that is not valid, or that holds
    a comment without them, is listed with its `error`, so it does not hide.
    """

    if tasks_path is None:
        return []

    found = []
    for file in sorted((tasks_path.parent / FEEDBACK_DIR).glob("*.json")):
        entry: dict[str, typing.Any] = {"task": file.stem, "file": f"{FEEDBACK_DIR}/{file.name}"}
        data = _read_feedback(file)
        if data is None:
            found.append({**entry, "error": "not a feedback file: expected a JSON object with a comments list"})
            continue

        if data.get("ready") is not True:
            continue

        try:
            comments = [_feedback_finding(c) for c in data["comments"]]
        except ValueError as exc:
            found.append({**entry, "error": str(exc)})
            continue

        found.append({**entry, "comments": comments})

    return found


def _feedback_finding(comment: typing.Any) -> dict[str, typing.Any]:
    """The path, line and text of a comment, or a ValueError when it lacks one."""

    line = comment.get("newRange") if isinstance(comment, dict) else None
    if (
        not isinstance(comment, dict)
        or not isinstance(comment.get("filePath"), str)
        or not isinstance(comment.get("body"), str)
        or not isinstance(line, list)
        or not line
        or not isinstance(line[0], int)
    ):
        msg = "a comment lacks its filePath, newRange or body"
        raise ValueError(msg)

    return {"path": comment["filePath"], "line": line[0], "text": comment["body"]}


def feedback_batch(tasks_path: pathlib.Path, task_id: str) -> tuple[dict[str, typing.Any], int]:
    """The `hunk session comment apply --stdin` batch of the saved feedback of a task, and how many comments it skips.

    Each comment becomes `{filePath, newLine, summary, author}`, with the first
    line of its `newRange` and its body. A comment without a file, a line or a
    body is skipped. It raises ValueError when the task has no feedback file.
    """

    data = _read_feedback(tasks_path.parent / FEEDBACK_DIR / f"{task_id}.json")
    if data is None:
        msg = f"'{task_id}' has no saved feedback"
        raise ValueError(msg)

    batch = []
    for comment in data["comments"]:
        try:
            finding = _feedback_finding(comment)
        except ValueError:
            continue

        item = {"filePath": finding["path"], "newLine": finding["line"], "summary": finding["text"]}
        if isinstance(comment.get("author"), str):
            item["author"] = comment["author"]
        batch.append(item)

    return {"comments": batch}, len(data["comments"]) - len(batch)


def frozen_changes(old: "Graph", new: "Graph") -> list[str]:
    """What a proposal changes or removes of the tasks a replan never touches.

    The frozen tasks are the ones in `FROZEN`, by their status in the old
    graph, so a task the proposal moves out of `in_progress` is still reported.
    """

    errors = []
    for task_id in old.order:
        before = old.tasks[task_id]
        if before.status not in FROZEN:
            continue

        if task_id not in new.tasks:
            errors.append(f"{task_id}: {before.status} and frozen, but the proposal removes it")
            continue

        after = new.tasks[task_id]
        fields = [n for n in COMPARED if _value(before, n) != _value(after, n)]
        if fields:
            errors.append(
                f"{task_id}: {before.status} and frozen, but the proposal changes "
                f"{', '.join(fields)}"
            )

    return errors


class Graph:
    """The validated task graph, with epics and the leaves that do the work."""

    def __init__(self, tasks: list[Task]) -> None:

        self.tasks = {t.id: t for t in tasks}
        self.order = [t.id for t in tasks]
        self.errors = self._validate(tasks)
        self.children: dict[str, list[str]] = {i: [] for i in self.order}
        self.leaves: list[str] = []
        self.deps: dict[str, set[str]] = {}
        self.dependents: dict[str, set[str]] = {}
        self.waves: dict[str, int] = {}

        if self.errors:
            return

        for t in tasks:
            if t.parent:
                self.children[t.parent].append(t.id)

        self.leaves = [i for i in self.order if self.tasks[i].type == "task"]
        self._build()
        self.errors += self._shared_branches()

    def _shared_branches(self) -> list[str]:
        """The leaves of a stack that resolve the same branch, which is two tasks in one layer."""

        seen: dict[tuple[str, str], str] = {}
        errors = []
        for i in self.leaves:
            stack, branch = self.stack(i), self.workspace(i)[1]
            if not (stack and branch):
                continue
            if (first := seen.setdefault((stack, branch), i)) != i:
                errors.append(
                    f"{i}: shares branch '{branch}' with {first} in stack '{stack}', "
                    "one task is one layer: set a branch on each leaf"
                )
        return errors

    def _validate(self, tasks: list[Task]) -> list[str]:

        errors: list[str] = []
        ids = [t.id for t in tasks]

        for t in tasks:
            if ids.count(t.id) > 1:
                errors.append(f"{t.id}: duplicate id")
            errors += [
                f"{t.id}: dep '{d}' does not exist" for d in t.deps if d not in ids
            ]
            errors += [
                f"{t.id}: integrate_after '{d}' is not another task"
                for d in t.integrate_after
                if d not in ids or d == t.id
            ]
            errors += [
                f"{t.id}: similar '{s}' is not another task"
                for s in t.similar
                if s not in ids or s == t.id
            ]
            if t.parent and t.parent not in ids:
                errors.append(f"{t.id}: parent '{t.parent}' does not exist")
            if t.type == "task" and not t.write_set and not t.read_only:
                errors.append(
                    f"{t.id}: set write_set, or read_only = true when it writes nothing"
                )
            if t.read_only and t.write_set:
                errors.append(f"{t.id}: read_only and write_set exclude each other")

        # Walk up the parents only when they all exist, otherwise the walk cannot end.
        if errors:
            return errors

        for t in tasks:
            if t.parent and self.tasks[t.parent].type != "epic":
                errors.append(f"{t.id}: parent '{t.parent}' is not an epic")

            seen, cur = {t.id}, t.parent
            while cur:
                if cur in seen:
                    errors.append(f"{t.id}: parent cycle through '{cur}'")
                    break
                seen.add(cur)
                cur = self.tasks[cur].parent

        return errors

    def ancestors(self, task_id: str) -> list[str]:
        """The parent, the parent of the parent, and so on."""

        found = []
        cur = self.tasks[task_id].parent
        while cur:
            found.append(cur)
            cur = self.tasks[cur].parent
        return found

    def leaves_under(self, task_id: str) -> set[str]:
        """The task itself when it is a leaf, otherwise all the leaves below it."""

        if self.tasks[task_id].type == "task":
            return {task_id}
        return set().union(*(self.leaves_under(c) for c in self.children[task_id]))

    def _build(self) -> None:

        # A task inherits the dependencies of its epics, and an epic also waits
        # for its children, so a dependency on an epic means its acceptance is done.
        for i in self.order:
            owners = [i, *self.ancestors(i)]
            self.deps[i] = {d for o in owners for d in self.tasks[o].deps} | set(
                self.children[i]
            )

        try:
            order = list(graphlib.TopologicalSorter(self.deps).static_order())
        except graphlib.CycleError as exc:
            self.errors.append(f"dependency cycle: {' -> '.join(exc.args[1])}")
            return

        soft = {i: self.deps[i] | set(self.tasks[i].integrate_after) for i in self.order}
        try:
            graphlib.TopologicalSorter(soft).prepare()
        except graphlib.CycleError as exc:
            self.errors.append(
                f"integrate_after cycle: {' -> '.join(exc.args[1])}"
            )
            return

        # The static order lists dependencies first, so every wave is known in one pass.
        for i in order:
            self.waves[i] = 1 + max((self.waves[d] for d in self.deps[i]), default=0)

        self.dependents = {i: set() for i in self.order}
        for i, deps in self.deps.items():
            for d in deps:
                self.dependents[d].add(i)

    def stack(self, task_id: str) -> str:
        """The stack of the closest epic that sets one, or an empty string."""

        owners = [task_id, *self.ancestors(task_id)]
        return next((s for o in owners if (s := self.tasks[o].stack)), "")

    def warnings(self) -> list[str]:
        """What `check` reports without refusing the file."""

        nesting = [
            f"{i}: nests {depth} levels deep, GitHub sub-issues stop at {MAX_NESTING}"
            for i in self.order
            if (depth := len(self.ancestors(i))) > MAX_NESTING
        ]
        long_titles = [
            f"{i}: title has {len(self.tasks[i].title)} characters, keep it at "
            f"{MAX_TITLE} or fewer and put the detail in briefs/{i}.md"
            for i in self.order
            if len(self.tasks[i].title) > MAX_TITLE
        ]
        unstamped = [
            f"{i}: done without done_at, set it to when the task finished"
            for i in self.order
            if self.tasks[i].status == "done" and not self.tasks[i].done_at
        ]
        marked_done = [
            f"{i}: done but still marked human_review, the human clears the mark in the board"
            for i in self.order
            if self.tasks[i].status == "done" and self.tasks[i].human_review
        ]
        return nesting + long_titles + unstamped + marked_done + similar_warnings(self)

    def missing_briefs(self, ledger_dir: pathlib.Path) -> list[str]:
        """Warnings about the briefs/<id>.md of the leaves that are not done.

        A leaf in progress or ready needs one, and a brief needs an `Acceptance`
        heading (see reference/task-brief.md).
        """

        ready = set(self.ready())
        found = []
        for i in self.leaves:
            status = self.tasks[i].status
            if status == "done":
                continue
            brief = ledger_dir / "briefs" / f"{i}.md"
            if not brief.is_file():
                if status == "in_progress":
                    found.append(f"{i}: in progress without briefs/{i}.md next to tasks.toml")
                elif i in ready:
                    found.append(f"{i}: ready without briefs/{i}.md next to tasks.toml")
                continue
            text = brief.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"^#+[ \t]+Acceptance[ \t]*$", text, re.MULTILINE):
                found.append(f"{i}: briefs/{i}.md has no Acceptance heading")
        return found

    def inbox_warnings(self, ledger_dir: pathlib.Path) -> list[str]:
        """Warnings about the inbox files of the tasks that wait on a human.

        A file that lists options needs a `Recommendation` heading, so the human
        always sees which option the orchestrator advises (reference/board-tui.md).
        """

        found = []
        for i in self.tasks:
            if self.question(i) is None:
                continue
            inbox = ledger_dir / "inbox" / f"{i}.md"
            if not inbox.is_file():
                continue
            text = inbox.read_text(encoding="utf-8", errors="replace")
            if not re.search(r"^#+[ \t]+(Recommendation|Raccomandazione)[ \t]*$", text, re.MULTILINE):
                found.append(f"{i}: inbox/{i}.md has no Recommendation heading")
        return found

    def milestone(self, task_id: str) -> str:
        """The milestone of the task, or the one of its closest epic."""

        owners = [task_id, *self.ancestors(task_id)]
        return next((m for o in owners if (m := self.tasks[o].milestone)), "")

    def workspace(self, task_id: str) -> tuple[str, str]:
        """The worktree and branch of the task, or the ones of its closest epic.

        The two keys resolve on their own, so a leaf can set its own worktree
        and still take the branch of its epic.
        """

        owners = [task_id, *self.ancestors(task_id)]
        worktree = next((w for o in owners if (w := self.tasks[o].worktree)), "")
        branch = next((b for o in owners if (b := self.tasks[o].branch)), "")
        return worktree, branch

    def base(self, task_id: str) -> str:
        """The ref the task forked from, or the one of its closest epic, or ""."""

        owners = [task_id, *self.ancestors(task_id)]
        return next((b for o in owners if (b := self.tasks[o].base)), "")

    def parallel(self, first: str, second: str) -> bool:
        """True when two leaves share files on purpose, in separate worktrees.

        Both set `overlap = "hunks"`, so a write_set overlap is not a conflict:
        the orchestrator merges them in turn. A pending task has no worktree
        yet, the key promises it gets its own. A task that already runs needs
        one, and two worktrees must differ.
        """

        a, b = self.tasks[first], self.tasks[second]
        if first == second or not a.overlap == b.overlap == "hunks":
            return False
        worktrees = []
        for leaf in (first, second):
            worktree = self.workspace(leaf)[0]
            if not worktree and self.tasks[leaf].status in RUNNING:
                return False
            worktrees.append(worktree)
        return not (worktrees[0] and worktrees[0] == worktrees[1])

    def overlaps_on_purpose(self, leaf: str) -> dict[str, str]:
        """The open leaves that share a path with the leaf and run in parallel with it."""

        task = self.tasks[leaf]
        if task.type != "task" or task.status == "done" or not task.write_set:
            return {}
        return {
            other: why
            for other in self.leaves
            if self.tasks[other].status != "done"
            and self.parallel(leaf, other)
            and (why := overlap(task.write_set, self.tasks[other].write_set or []))
        }

    def question(self, task_id: str) -> str | None:
        """The question of a task blocked on a human, or None when there is none."""

        task = self.tasks[task_id]
        if task.status != "blocked" or not task.blocker.startswith(HUMAN):
            return None
        return task.blocker[len(HUMAN) :].strip()

    def reviewing(self, task_id: str) -> bool:
        """Whether the task is in review and marked for the human to review."""

        task = self.tasks[task_id]
        return task.human_review and task.status == REVIEW_STATUS

    def awaits_you(self, task_id: str) -> bool:
        """Whether the task waits on the human: a question to answer or a review."""

        return self.question(task_id) is not None or self.reviewing(task_id)

    def blocked_by(self, task_id: str) -> list[str]:
        """The dependencies that are not done, and the epics above that are blocked."""

        unmet = {d for d in self.deps[task_id] if self.tasks[d].status != "done"}
        halted = {
            a for a in self.ancestors(task_id) if self.tasks[a].status == "blocked"
        }
        return sorted(unmet | halted)

    def blocking(self, task_id: str) -> list[str]:
        """The tasks that depend directly on the task, without its own epics."""

        return sorted(self.dependents[task_id] - set(self.ancestors(task_id)))

    def downstream(self, leaf: str) -> list[str]:
        """The not done leaves that depend on the leaf, directly or not."""

        found: set[str] = set()
        todo = [leaf]
        while todo:
            new = self.dependents[todo.pop()] - found
            found |= new
            todo += new
        return [i for i in self.leaves if i in found and self.tasks[i].status != "done"]

    def holds_up(self, leaf: str) -> dict[str, str]:
        """The open leaves that wait on the leaf, each with why: deps or write_set.

        A leaf that is not done holds up the open leaves behind it through the
        deps, directly or not. It also holds up the pending leaves that share a
        path with its write set, and the leaves behind those through the deps,
        when it is running or `next` would start it first (see _urgency). A
        pending leaf shared with two running tasks is held by both.
        """

        task = self.tasks[leaf]
        if task.type != "task" or task.status in {"done", "deferred"}:
            return {}

        held = dict.fromkeys(self.downstream(leaf), "deps")
        if not task.write_set:
            return held

        above = self._upstream(leaf)
        sharers = [
            other
            for other in self.leaves
            if other != leaf
            and other not in held
            and other not in above
            and self.tasks[other].status == "pending"
            and (task.status in RUNNING or self._urgency(leaf) < self._urgency(other))
            and overlap(task.write_set, self.tasks[other].write_set or [])
            and not self.parallel(leaf, other)
        ]
        for other in sharers:
            held[other] = "write_set"
        for other in sharers:
            for behind in self.downstream(other):
                if behind != leaf and behind not in above:
                    held.setdefault(behind, "deps")
        return held

    def bottlenecks(self, limit: int) -> list[tuple[str, dict[str, str]]]:
        """The leaves that hold up more than limit open leaves, with the held ones."""

        found = ((i, self.holds_up(i)) for i in self.leaves)
        return [(i, held) for i, held in found if len(held) > limit]

    def _upstream(self, leaf: str) -> set[str]:
        found: set[str] = set()
        todo = [leaf]
        while todo:
            new = self.deps[todo.pop()] - found
            found |= new
            todo += new
        return found

    def unblocks(self, leaf: str) -> int:
        """The number of not done leaves that depend on the leaf, directly or not."""

        return len(self.downstream(leaf))

    def ready(self) -> list[str]:
        """The pending leaves with nothing left to wait for, most urgent first."""

        ready = [
            i
            for i in self.leaves
            if self.tasks[i].status == "pending" and not self.blocked_by(i)
        ]
        return sorted(ready, key=self._urgency)

    def _urgency(self, leaf: str) -> tuple[int, str, int, int]:
        milestone = self.milestone(leaf)
        return (
            PRIORITIES[self.tasks[leaf].priority],
            milestone or "~",
            -self.unblocks(leaf),
            self.tasks[leaf].row,
        )


def overlap(first: list[str], second: list[str]) -> str | None:
    """Tell why two write sets may touch the same path.

    The globs are compared by the normalized literal path before their first
    wildcard, so a doubtful case counts as an overlap.
    """

    def prefix(glob: str) -> str:
        glob = posixpath.normpath(glob)
        cuts = [glob.find(c) for c in GLOB_CHARS if c in glob]
        return glob[: min(cuts)] if cuts else glob

    for a in first:
        for b in second:
            if prefix(a).startswith(prefix(b)) or prefix(b).startswith(prefix(a)):
                return f"{a} overlaps {b}"
    return None


def _skip(
    leaf: str, reason: str, *, with_: str | None = None, detail: str | None = None
) -> dict[str, str]:
    """A task `next` does not start, with the task it yields to and why."""

    entry = {"id": leaf, "reason": reason}
    if with_ is not None:
        entry["with"] = with_
    if detail is not None:
        entry["detail"] = detail
    return entry


def select_next(
    graph: Graph, *, limits: Limits, base: str = DEFAULT_BASE
) -> dict[str, typing.Any]:
    """Choose the ready leaves to start, without conflicts and inside the caps.

    `base` is the ref the review command of `awaiting_human` starts its diff at.
    """

    running = [i for i in graph.leaves if graph.tasks[i].status in RUNNING]
    held = {i: graph.tasks[i].write_set or [] for i in running}
    taken = {name: i for i in running for name in graph.tasks[i].exclusive}
    readers = sum(graph.tasks[i].read_only for i in running)
    writers = len(running) - readers
    used = sum(graph.tasks[i].footprint for i in running)
    start, skipped = [], []
    reserved = ""

    for leaf in graph.ready():
        task = graph.tasks[leaf]
        cap = limits.ro if task.read_only else limits.rw
        taken_slots = readers if task.read_only else writers

        # A task that needs the whole budget keeps the wave, or it would starve.
        if reserved:
            detail = f"{reserved} needs the whole budget"
            skipped.append(_skip(leaf, "budget reserved", with_=reserved, detail=detail))
            continue

        if cap is not None and taken_slots >= cap:
            reason = "no free read-only slot" if task.read_only else "no free slot"
            skipped.append(_skip(leaf, reason))
            continue

        # A task heavier than the whole budget may still start, but only alone.
        if limits.budget is not None and used and used + task.footprint > limits.budget:
            left = max(limits.budget - used, 0)
            detail = f"footprint {task.footprint}, {left} of {limits.budget} left"
            skipped.append(_skip(leaf, "no budget left", detail=detail))
            reserved = leaf if task.footprint > limits.budget else ""
            continue

        shared = next((name for name in task.exclusive if name in taken), None)
        if shared:
            skipped.append(
                _skip(leaf, "exclusive resource", with_=taken[shared], detail=shared)
            )
            continue

        write_set = task.write_set or []
        conflict = next(
            (
                (o, why)
                for o, ws in held.items()
                if not graph.parallel(leaf, o) and (why := overlap(write_set, ws))
            ),
            None,
        )
        if conflict:
            owner, why = conflict
            skipped.append(_skip(leaf, "write set conflict", with_=owner, detail=why))
            continue

        held[leaf] = write_set
        taken.update(dict.fromkeys(task.exclusive, leaf))
        used += task.footprint
        readers += 1 if task.read_only else 0
        writers += 0 if task.read_only else 1
        start.append(
            {
                "id": leaf,
                "priority": task.priority,
                "milestone": graph.milestone(leaf),
                "unblocks": graph.unblocks(leaf),
                "read_only": task.read_only,
                "footprint": task.footprint,
                **_workspace_of(graph, leaf),
                **{k: v for k in DEFAULT_KEYS if (v := getattr(task, k))},
                **_session_of(task),
                **({"needs": task.needs} if task.needs else {}),
                **(
                    {"overlaps": sorted(shared)}
                    if (shared := graph.overlaps_on_purpose(leaf).keys() & held.keys())
                    else {}
                ),
            }
        )

    waiting = [
        {"id": i, "blocked_by": graph.blocked_by(i)}
        for i in graph.leaves
        if graph.tasks[i].status == "pending" and graph.blocked_by(i)
    ]
    blocked = [i for i in graph.leaves if graph.tasks[i].status == "blocked"]
    awaiting_human = [
        {"id": i, "question": question}
        for i in blocked
        if (question := graph.question(i)) is not None
    ]
    # A task marked for review has no question, so it carries the command instead.
    awaiting_human += [
        {"id": i, "review": review_command(graph, i, base)}
        for i in graph.leaves
        if graph.reviewing(i)
    ]
    awaiting_event = [
        {"id": i, "event": graph.tasks[i].blocker[len(EVENT) :].strip()}
        for i in blocked
        if graph.tasks[i].blocker.startswith(EVENT)
    ]
    return {
        "limits": dataclasses.asdict(limits),
        "start": start,
        "skipped": skipped,
        "running": [
            {"id": i, **_workspace_of(graph, i), **_session_of(graph.tasks[i])}
            for i in running
        ],
        "waiting": waiting,
        "blocked": blocked,
        "awaiting_human": awaiting_human,
        "awaiting_event": awaiting_event,
    }


def answered_since(
    graph: Graph, tasks_path: pathlib.Path | None
) -> tuple[list[dict[str, str]], int]:
    """The answers logged in `steering.log` since the last `next`, and the new stamp.

    The stamp `.next-seen` holds the byte offset of `steering.log` that `next` last
    read, so an answer logged in the same second is never lost. Without a usable
    stamp, the answers of the last 24 hours. Only `write_next_seen` moves the stamp.
    """

    if tasks_path is None:
        return [], 0
    try:
        data = (tasks_path.parent / "steering.log").read_bytes()
    except OSError:
        data = b""
    offset = 0
    since = datetime.datetime.now().astimezone() - datetime.timedelta(hours=24)
    with contextlib.suppress(OSError, ValueError):
        seen = int((tasks_path.parent / ".next-seen").read_text().strip())
        if 0 <= seen <= len(data):
            offset, since = seen, datetime.datetime.min.replace(tzinfo=datetime.timezone.utc)

    found: dict[str, dict[str, str]] = {}
    for line in data[offset:].decode("utf-8", errors="replace").splitlines():
        parts = line.split()
        if len(parts) != 3 or parts[2] != "answer" or parts[1] not in graph.tasks:
            continue
        try:
            when = datetime.datetime.fromisoformat(parts[0])
        except ValueError:
            continue
        task = graph.tasks[parts[1]]
        if when.tzinfo is None:
            when = when.astimezone()
        if when > since and task.answer:
            found[task.id] = {
                "id": task.id,
                "answer": task.answer,
                "time": parts[0],
                "status": task.status,
            }
    return list(found.values()), len(data)


def write_next_seen(tasks_path: pathlib.Path, offset: int) -> None:
    """Write the `.next-seen` stamp atomically. `next` is its only consumer."""

    stamp = tasks_path.parent / ".next-seen"
    temporary = stamp.with_name(stamp.name + ".tmp")
    temporary.write_text(f"{offset}\n")
    temporary.replace(stamp)


def file_holds(graph: Graph) -> dict[str, str]:
    """The ready leaves `next` holds back on a write_set, each with its holder."""

    skipped = select_next(graph, limits=Limits())["skipped"]
    return {s["id"]: s["with"] for s in skipped if s["reason"] == "write set conflict"}


def epics(graph: Graph) -> list[dict[str, typing.Any]]:
    """Progress of every epic."""

    result = []
    for i in (i for i in graph.order if graph.tasks[i].type == "epic"):
        leaves = graph.leaves_under(i)
        done = [leaf for leaf in leaves if graph.tasks[leaf].status == "done"]
        result.append(
            {
                "id": i,
                "leaves": len(leaves),
                "leaves_done": len(done),
                "children_done": len(leaves) > 0 and len(done) == len(leaves),
                "status": graph.tasks[i].status,
                "milestone": graph.milestone(i),
            }
        )
    return result


def bottleneck_warnings(graph: Graph, *, max_blocked: int = MAX_BLOCKED) -> list[str]:
    """One line for each task that holds up more than max_blocked open tasks."""

    return [
        _bottleneck_line(i, held, max_blocked=max_blocked)
        for i, held in graph.bottlenecks(max_blocked)
    ]


STOP_WORDS = {
    "a", "an", "and", "as", "by", "default", "epic", "epics", "for", "in",
    "of", "on", "show", "shows", "task", "tasks", "the", "to", "with",
}  # fmt: skip
SIMILAR_JACCARD = 0.5
SIMILAR_MIN_SHARED = 2
SIMILAR_MIN_WORD_LENGTH = 4


def similar_warnings(graph: Graph) -> list[str]:
    """One line per pair of open leaves that look alike, to catch a duplicate."""

    return [
        f"{a.id} and {b.id} look alike ({why}): a duplicate? "
        f'If not, set similar = ["{b.id}"] on {a.id}'
        for a, b, why, _ in _similar_pairs(graph)
    ]


def _similar_pairs(graph: Graph) -> list[tuple[Task, Task, str, str]]:
    """The pairs of open leaves that look alike: both tasks, why, shared file."""

    def words(task: Task) -> set[str]:
        found = re.findall(r"[a-z0-9]+", f"{task.id} {task.title}".lower())
        return set(found) - STOP_WORDS

    def tops(task: Task) -> set[str]:
        return {posixpath.normpath(g) for g in task.write_set or []}

    def related(a: Task, b: Task) -> bool:
        # Only leaves are compared, so one is never the parent of the other.
        return (
            a.id in graph._upstream(b.id)
            or b.id in graph._upstream(a.id)
            or b.id in a.similar
            or a.id in b.similar
            or a.overlap == b.overlap == "hunks"
        )

    open_leaves = [
        t for i in graph.leaves if (t := graph.tasks[i]).status not in PARKED
    ]
    found: list[tuple[Task, Task, str, str]] = []
    for n, a in enumerate(open_leaves):
        for b in open_leaves[n + 1 :]:
            if related(a, b):
                continue
            wa, wb = words(a), words(b)
            shared = wa & wb
            significant = [w for w in shared if len(w) >= SIMILAR_MIN_WORD_LENGTH]
            if len(significant) < SIMILAR_MIN_SHARED:
                continue
            score = len(shared) / len(wa | wb) if wa | wb else 0
            files = sorted(tops(a) & tops(b))
            # A doc every task of a skill writes says little about a duplicate.
            strong = [f for f in files if not f.endswith(".md")]
            if score >= SIMILAR_JACCARD:
                why = f"titles share {score:.0%} of their words"
                name = ""
            elif shared and (strong or len(files) >= 2):
                why = f"both write {(strong or files)[0]} and share a title word"
                name = posixpath.basename((strong or files)[0])
            else:
                continue
            found.append((a, b, why, name))
    return found


def purposeful_overlaps(graph: Graph) -> list[str]:
    """One line per open pair that shares files on purpose, for `check`."""

    return [
        f"{a} and {b}: {why}, on purpose (overlap = \"hunks\"): "
        "separate worktrees, merged one after the other"
        for n, a in enumerate(graph.leaves)
        for b, why in graph.overlaps_on_purpose(a).items()
        if graph.leaves.index(b) > n
    ]


def explain(
    graph: Graph, task_id: str, *, max_blocked: int = MAX_BLOCKED
) -> dict[str, typing.Any]:
    """Say why a task is or is not ready."""

    held = graph.holds_up(task_id)
    return {
        "id": task_id,
        "type": graph.tasks[task_id].type,
        "status": graph.tasks[task_id].status,
        "parent": graph.tasks[task_id].parent,
        "priority": graph.tasks[task_id].priority,
        "wave": graph.waves[task_id],
        "blocked_by": graph.blocked_by(task_id),
        "blocking": graph.blocking(task_id),
        "ready": task_id in graph.ready(),
        "holds_up": held,
        "overlaps_on_purpose": graph.overlaps_on_purpose(task_id),
        "warning": (
            _bottleneck_line(task_id, held, max_blocked=max_blocked)
            if len(held) > max_blocked
            else None
        ),
    }


def events_file(tasks_path: pathlib.Path, task_id: str) -> pathlib.Path:
    """The event log of a task: `events/<id>.jsonl` next to `tasks.toml`."""

    return tasks_path.parent / "events" / f"{task_id}.jsonl"


def append_event(
    tasks_path: pathlib.Path, task_id: str, event: str, detail: str = ""
) -> dict[str, str]:
    """Append one event to the log of a task and return it.

    The log only records: nothing reads it to refuse a change of status. A line
    is written once and never rewritten, so a new field is backward compatible.
    """

    record = {
        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
        "task": task_id,
        "event": event,
        "detail": detail,
    }
    path = events_file(tasks_path, task_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(record) + "\n")
    return record


def read_events(tasks_path: pathlib.Path, task_id: str) -> list[dict]:
    """The events of a task in file order; a missing log is empty.

    A line that is not a JSON object is skipped, so a torn write loses one event
    and not the log.
    """

    try:
        lines = events_file(tasks_path, task_id).read_text().splitlines()
    except (OSError, UnicodeDecodeError):
        return []
    found = []
    for line in lines:
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            found.append(value)
    return found


ATTEMPT_FIELDS = (
    "mode", "agent", "agent_name", "model", "effort", "host", "session", "pane",
    "pid", "worktree", "branch", "round", "verdict", "commit",
)
HEARTBEAT_STALE_SECONDS = 30 * 60


def event_fields(event: dict) -> dict[str, str] | None:
    """The structured fields in the detail of an event, None for free text.

    The detail is a JSON object or space separated `key=value` pairs (shell
    quoting allowed). Anything else is free-form and stays a string.
    """

    detail = str(event.get("detail", "")).strip()
    if detail.startswith("{"):
        try:
            value = json.loads(detail)
        except ValueError:
            return None
        if isinstance(value, dict):
            return {str(k): str(v) for k, v in value.items()}
        return None
    try:
        tokens = shlex.split(detail)
    except ValueError:
        return None
    if not tokens or not all("=" in t and not t.startswith("=") for t in tokens):
        return None
    return dict(t.split("=", 1) for t in tokens)


def format_event(event: dict, separator: str = " \u00b7 ") -> str:
    """One readable line: `<ts> <event>` then the fields joined by dots."""

    head = " ".join(str(event.get(k, "")) for k in ("ts", "event")).strip()
    fields = event_fields(event)
    if fields is None:
        return " ".join([head, str(event.get("detail", ""))]).strip()
    known = [k for k in ATTEMPT_FIELDS if k in fields]
    rest = [k for k in fields if k not in ATTEMPT_FIELDS]
    return separator.join([head, *(f"{k}={fields[k]}" for k in (*known, *rest))])


REMOTE_STATES = ("running", "done", "acked")
REMOTE_FIELDS = ("state", "machine", "path")
MAP_ENV = "LEDGER_MACHINE_MAP"
TRANSPORTS = ("herdr", "ssh")


def looks_like_host(value: str) -> bool:
    """True for a dotted name or an IP address: a map label has neither."""

    if "." in value:
        return True
    try:
        ipaddress.ip_address(value.strip("[]"))
    except ValueError:
        return False
    return True


def remote_errors(detail: str) -> list[str]:
    """Why the detail of a `remote` event is refused, empty when it is valid.

    It needs `state` (running, done or acked). `machine` is a label of the
    per-user map and `path` the run directory; both are optional.
    """

    fields = event_fields({"detail": detail})
    if fields is None:
        return ["a remote event needs key=value details, like state=running machine=<label>"]
    errors = [
        f"remote event: unknown field '{k}', use {', '.join(REMOTE_FIELDS)}"
        for k in fields
        if k not in REMOTE_FIELDS
    ]
    if fields.get("state") not in REMOTE_STATES:
        errors.append(f"remote event: 'state' must be one of {list(REMOTE_STATES)}")
    errors += [
        f"remote event: '{key}' is empty"
        for key in ("machine", "path")
        if key in fields and not fields[key].strip()
    ]
    if looks_like_host(fields.get("machine", "")):
        errors.append("remote event: 'machine' must be a label of the map, not a host name")
    return errors


def last_remote(events: list[dict]) -> dict[str, str] | None:
    """The fields of the newest valid `remote` event, None when there is none."""

    for e in reversed(events):
        if e.get("event") == "remote" and (fields := event_fields(e)) is not None:
            return fields
    return None


def host_warnings(graph: "Graph", tasks_path: pathlib.Path) -> list[str]:
    """A warning for each event whose `host` or `machine` looks like a host name."""

    return [
        f"{task_id}: event '{e.get('event')}' has {key}={fields[key]}, "
        "a map label is expected, not a host name"
        for task_id in graph.tasks
        for e in read_events(tasks_path, task_id)
        if (fields := event_fields(e) or {})
        for key in ("host", "machine")
        if key in fields and looks_like_host(fields[key])
    ]


def read_machine_map(path: pathlib.Path) -> dict[str, dict]:
    """The `[machine.<label>]` tables of the per-user map; ValueError if invalid."""

    try:
        data = tomllib.loads(path.read_text())
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        msg = f"cannot read the machine map {path}: {exc}"
        raise ValueError(msg) from exc
    machines = data.get("machine", {})
    if not isinstance(machines, dict) or not all(
        isinstance(v, dict) for v in machines.values()
    ):
        msg = f"{path}: 'machine' must hold one table per label"
        raise ValueError(msg)
    for label, entry in machines.items():
        needs = entry.get("needs", [])
        if not (isinstance(needs, list) and all(isinstance(n, str) for n in needs)):
            msg = f"{path}: machine.{label}.needs must be a list of strings"
            raise ValueError(msg)
        if entry.get("transport") not in TRANSPORTS:
            msg = f"{path}: machine.{label}.transport must be one of {list(TRANSPORTS)}"
            raise ValueError(msg)
    return machines


def pick_machine(needs: list[str], machines: dict[str, dict]) -> dict[str, typing.Any]:
    """The first machine, in file order, whose needs cover all of `needs`.

    The ssh target never leaves the map: only the label and the transport do.
    """

    for label, entry in machines.items():
        if set(needs) <= set(entry.get("needs", [])):
            return {"machine": label, "transport": entry["transport"]}
    return {"machine": None, "transport": None}


def attempt_state(events: list[dict]) -> str:
    """`alive`, `finished` or `orphan` from the last launch, heartbeat, return.

    No launch gives an empty string. A return after the last launch means
    finished. Otherwise the attempt is alive while its newest launch or
    heartbeat is younger than HEARTBEAT_STALE_SECONDS, else orphan.
    """

    now = datetime.datetime.now(datetime.timezone.utc)
    launch = max((i for i, e in enumerate(events) if e.get("event") == "launch"), default=None)
    if launch is None:
        return ""
    after = events[launch:]
    if any(e.get("event") == "return" for e in after):
        return "finished"
    for e in reversed(after):
        if e.get("event") in ("launch", "heartbeat"):
            try:
                ts = datetime.datetime.fromisoformat(str(e.get("ts")))
            except ValueError:
                continue
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=datetime.timezone.utc)
            age = (now - ts).total_seconds()
            return "alive" if age < HEARTBEAT_STALE_SECONDS else "orphan"
    return "orphan"


def _last_event_text(tasks_path: pathlib.Path | None, task_id: str) -> str:
    """`  attempt=<state> host=.. pane=..` and `  last=<event line>` for the digest."""

    if tasks_path is None or not (events := read_events(tasks_path, task_id)):
        return ""
    text = ""
    if state := attempt_state(events):
        launch = next(e for e in reversed(events) if e.get("event") == "launch")
        fields = event_fields(launch) or {}
        where = "".join(f" {k}={fields[k]}" for k in ("host", "pane") if k in fields)
        text = f"  attempt={state}{where}"
        if state == "orphan":
            text += " (do not auto-resume, see ledger.md)"
    return text + "  " + _clip(f"last={format_event(events[-1], ' ')}")


def _worktree_state(worktree: str, base: str) -> str:
    """`  missing`, or `  clean|dirty N ahead N` of a running task's worktree.

    `unknown` replaces the state when a git call fails. No worktree prints nothing.
    """

    if not worktree:
        return ""
    path = pathlib.Path(worktree).expanduser()
    if not path.is_dir():
        return "  missing"
    code, status = _git(path, "status", "--short")
    ahead_code, ahead = _git(path, "rev-list", "--count", f"{base}..HEAD")
    if code != 0 or ahead_code != 0:
        return "  unknown"
    changed = len(status.splitlines())
    return f"  {f'dirty {changed}' if changed else 'clean'} ahead {ahead}"


def resume(
    graph: Graph,
    *,
    limits: Limits,
    tasks_path: pathlib.Path | None = None,
) -> str:
    """The re-entry digest for a person entering a session after a compaction.

    It is plain text, one task per line: the leaves that can start, the running
    ones with their agent id, what is blocked, the last decisions and the first
    next action of the ledger, the standing constraints file to read, and the
    stacks. A stack lists the leaves that resolve a branch, in file order,
    which is the order of its layers. The ledger is the `ORCHESTRATION.md` next
    to `tasks.toml`; without it the digest still prints the graph and says the
    ledger is missing.
    """

    ledger = tasks_path.parent / "ORCHESTRATION.md" if tasks_path else None
    note = ""
    sections: dict[str, list[str]] = {}
    header = ""
    if ledger is not None:
        try:
            header, sections = _read_ledger(ledger.read_text())
        except FileNotFoundError:
            note = "  (missing)"
        except (OSError, UnicodeDecodeError):
            note = "  (unreadable)"
    else:
        note = "  (needs a tasks.toml path, not stdin)"

    nxt = select_next(graph, limits=limits)
    slug = tasks_path.parent.name if tasks_path else "-"
    lines = [
        f"RESUME {slug}  tasks={tasks_path or '-'}  ledger={ledger or '-'}{note}"
    ]

    named = re.search(r"Constraints:\s*(.+?)(?:\.(?=\s|$)|$)", header)
    if ledger is None:
        lines.append("CONSTRAINTS unknown  (no ledger was read)")
    elif named:
        path = ledger.parent / named.group(1)
        state = "read it before acting" if path.is_file() else "MISSING, ask the human"
        lines.append(f"CONSTRAINTS {path}  ({state})")
    else:
        lines.append("CONSTRAINTS none named in the ledger header  (ask the human)")

    agents = _agents_of(sections.get("Active agent registry", []))
    lines.append(f"READY ({len(nxt['start'])})")
    lines += [
        f"  {t['id']}  {t['priority']}  unblocks {t['unblocks']}  "
        f"{t['worktree'] or '-'}{_session_text(graph.tasks[t['id']])}"
        for t in nxt["start"]
    ]
    lines.append(f"RUNNING ({len(nxt['running'])})")
    lines += [
        f"  {t['id']}  agent={agents.get(t['id'], '?')}  "
        f"{t['worktree'] or '-'}  {t['branch'] or '-'}"
        f"{_worktree_state(t['worktree'], ledger_base(tasks_path))}"
        f"{_session_text(graph.tasks[t['id']])}"
        f"{_last_event_text(tasks_path, t['id'])}"
        for t in nxt["running"]
    ]

    asked = {t["id"]: t["question"] for t in nxt["awaiting_human"] if "question" in t}
    held = [
        _clip(f"  {i}  human: {asked[i]}")
        if i in asked
        else _clip(f"  {i}  blocked: {graph.tasks[i].blocker or '?'}")
        for i in nxt["blocked"]
    ]
    held += [
        _clip(f"  {t['id']}  waits {', '.join(t['blocked_by'])} (dep)")
        for t in nxt["waiting"]
    ]
    lines.append(f"BLOCKED ({len(held)})")
    lines += held
    answered, _ = answered_since(graph, tasks_path)
    lines.append(f"ANSWERED ({len(answered)})")
    lines += [
        _clip(f"  {a['id']}  {a['time']}  {a['status']}  {' '.join(a['answer'].split())}")
        for a in answered
    ]

    bullets = [
        b
        for line in sections.get("Decisions & rationale", [])
        if (b := re.sub(r"^\s*[-*]\s+", "", line)) != line
    ][-3:]
    lines.append(f"DECISIONS (last {len(bullets)})")
    lines += [f"  {_clip(b)}" for b in bullets]

    remote = {
        i: fields
        for i in graph.tasks
        if tasks_path and (fields := last_remote(read_events(tasks_path, i)))
    }
    if remote:
        lines.append(f"REMOTE ({len(remote)})")
        for i, fields in remote.items():
            where = "".join(f" {k}={fields[k]}" for k in ("machine", "path") if k in fields)
            check = "  needs a human check: no update since it started" if fields.get("state") == "running" else ""
            lines.append(_clip(f"  {i}  state={fields.get('state', '?')}{where}{check}"))

    stacks: dict[str, list[str]] = {}
    for i in graph.leaves:
        name = graph.stack(i)
        workspace = _workspace_of(graph, i)
        if name and workspace["branch"]:
            stacks.setdefault(name, []).append(
                f"{i}  {graph.tasks[i].status}  "
                f"{workspace['worktree'] or '-'}  {workspace['branch']}"
            )
    if stacks:
        lines.append(f"STACKS ({len(stacks)})")
        for name, layers in stacks.items():
            lines.append(f"  {name}")
            lines += [f"    {_clip(layer)}" for layer in layers]

    actions = [
        line
        for line in sections.get("Next actions", [])
        if line.strip() and not line.lstrip().startswith(("<", "#"))
    ]
    item = r"^\s*(?:[-*]|\d+[.)])\s+"
    first = next((line for line in actions if re.match(item, line)), None)
    first = re.sub(item, "", first if first is not None else (actions or [""])[0])
    lines.append(f"NEXT {_clip(first) if first else '(none in the ledger)'}")
    removable = removable_count(graph, pathlib.Path.cwd(), ledger_tip(tasks_path))
    if note := cleanup_note(removable, MAX_REMOVABLE):
        lines.append(note)
    return "\n".join(lines)


def cleanup_note(count: int, threshold: int) -> str | None:
    """The line that tells how many worktrees can go, or None when none can."""

    if not count:
        return None
    note = f"CLEANUP {count} worktrees can be removed: run cleanup"
    return f"{note} (WARNING: more than {threshold})" if count > threshold else note


def removable_count(
    graph: Graph, repo: pathlib.Path, tip: str | None = None
) -> int:
    """How many worktrees `cleanup` would remove, 0 when git cannot tell."""

    try:
        return sum(e["class"] == "remove" for e in cleanup(graph, repo=repo, tip=tip))
    except (OSError, subprocess.SubprocessError):
        return 0


def ledger_tip(tasks_path: pathlib.Path | None) -> str | None:
    """The ref after `Stack tip:` in the ORCHESTRATION.md next to tasks.toml."""

    if tasks_path is None:
        return None
    try:
        text = (tasks_path.parent / "ORCHESTRATION.md").read_text()
    except (OSError, UnicodeDecodeError):
        return None
    found = re.search(r"^\s*(?:[-*]\s+)?Stack tip:\s*`?([^\s`]+)", text, re.MULTILINE)
    return found.group(1) if found else None


def ledger_base(tasks_path: pathlib.Path | None) -> str:
    """The ref the work of a task is compared with: the `Stack tip:` of the ledger, else `integration`."""

    return ledger_tip(tasks_path) or DEFAULT_BASE


def review_command(graph: Graph, task_id: str, base: str) -> str:
    """The command that opens the diff of an `in_progress` or `in_rev` task in hunk, or "" for any other task.

    A task qualifies when its status is in `HUNK_STATUSES` (`in_progress` or
    `in_rev`) and it resolves both a worktree and a branch. `base` is the ref
    the diff starts at, see `ledger_base`; the `base` key of the task, or of its
    closest epic, wins over it. When the worktree directory does not
    exist, the command runs from the repository root (the parent of the
    `.worktrees` component) or, without one, from the current directory.
    """

    if graph.tasks[task_id].status not in HUNK_STATUSES:
        return ""

    worktree, branch = graph.workspace(task_id)
    if not worktree or not branch:
        return ""

    diff = f"hunk diff {graph.base(task_id) or base}...{branch}"
    path = pathlib.Path(worktree).expanduser()
    if path.exists():
        return f"cd {worktree} && {diff}"

    # The diff reads refs, so any directory of the repository works.
    parts = path.parts
    if ".worktrees" in parts:
        root = pathlib.Path(*parts[: parts.index(".worktrees")])
        return f"cd {root} && {diff}"
    return diff


def cleanup(
    graph: Graph, *, repo: pathlib.Path, tip: str | None = None
) -> list[dict]:
    """Classify the worktrees and branches the ledger names, without changing them.

    The class is `remove` for a worktree whose tasks are all done, whose branch
    has every commit in the stack tip (an ancestor or cherry-picked, by
    `git cherry`) and whose tree is clean, `keep` (with a
    `reason`) for the main checkout, the current directory, unmerged commits, a
    dirty tree or an open task, and `orphan` for a worktree or branch that the
    ledger names but git no longer matches. A worktree the ledger never names
    is not listed. The tip is resolved in the main checkout, so the branch the
    caller happens to have checked out does not count. It defaults to the
    `HEAD` of the main checkout.
    """

    owners: dict[tuple[str, str], list[str]] = {}
    for task_id in graph.leaves:
        worktree, branch = graph.workspace(task_id)
        if worktree and branch:
            given = pathlib.Path(worktree).expanduser()
            path = str((given if given.is_absolute() else repo / given).resolve())
            owners.setdefault((path, branch), []).append(task_id)

    found = {path: _repo_of(pathlib.Path(path)) for path, _ in owners}
    fallback = next((r for r in found.values() if r), repo)
    classes = (
        _classify(graph, ids, path, branch, repo=found[path] or fallback, tip=tip)
        for (path, branch), ids in owners.items()
    )
    return [e for e in classes if e["class"] != "gone"]


def _classify(
    graph: Graph,
    ids: list[str],
    path: str,
    branch: str,
    *,
    repo: pathlib.Path,
    tip: str | None,
) -> dict:
    """The class of one worktree, from the state of git and the ledger right now."""

    entry = {"worktree": path, "branch": branch, "tasks": ids}

    def decide(name: str, reason: str = "") -> dict:
        return {**entry, "class": name, "reason": reason}

    registered = _worktrees(repo)
    main = next(iter(registered), "")
    ref = f"refs/heads/{branch}"
    open_ids = [i for i in ids if graph.tasks[i].status != "done"]
    cwd = pathlib.Path.cwd().resolve()

    if path not in registered:
        if _git(repo, "rev-parse", "--verify", "--quiet", ref)[0] == 0:
            return decide("orphan", "the worktree is gone but the branch is still there")
        return decide("gone", "")

    if registered[path] != branch:
        return decide("orphan", f"the worktree checks out '{registered[path]}', not '{branch}'")
    if path == main:
        return decide("keep", "main checkout")
    if branch.startswith("-"):
        return decide("keep", "the branch name starts with a dash")
    if cwd == pathlib.Path(path) or pathlib.Path(path) in cwd.parents:
        return decide("keep", "current directory")
    if open_ids:
        return decide("keep", f"{', '.join(open_ids)} is not done")

    code, changes = _git(pathlib.Path(path), "status", "--porcelain")
    if code != 0:
        return decide("keep", "cannot read the status")
    if changes:
        return decide("keep", "uncommitted changes")

    tip_ref = tip or "HEAD"
    code, tip_sha = _git(pathlib.Path(main), "rev-parse", "--verify", f"{tip_ref}^{{commit}}")
    if code != 0:
        return decide("keep", f"the tip '{tip_ref}' does not resolve")
    code, picked = _git(repo, "cherry", tip_sha, ref)
    if code != 0:
        return decide("keep", "cannot compare the branch with the tip")
    if any(line.startswith("+") for line in picked.splitlines()):
        return decide("keep", f"commits that '{tip_ref}' does not have")
    return decide("remove")


def cleanup_report(found: list[dict], *, threshold: int) -> str:
    """The text of `cleanup`: the `wt remove` commands, then each keep and orphan."""

    removable = [e for e in found if e["class"] == "remove"]
    lines = [f"remove ({len(removable)})"]
    lines += [f"  wt remove {e['branch']}" for e in removable]
    for name in ("keep", "orphan"):
        group = [e for e in found if e["class"] == name]
        lines.append(f"{name} ({len(group)})")
        lines += [f"  {e['branch']} at {e['worktree']}: {e['reason']}" for e in group]
    if note := cleanup_note(len(removable), threshold):
        lines.append(note)
    return "\n".join(lines)


def cleanup_apply(
    graph: Graph,
    found: list[dict],
    *,
    repo: pathlib.Path,
    tip: str | None,
    ledger: pathlib.Path | None,
    out,
) -> int:
    """Remove the `remove` class with `wt remove`, one line each, and log it.

    Each entry is classified again right before its removal, because the ledger
    or the tree may have changed since the report. A refusal or a failure is
    reported and logged, the rest still runs, and the exit code is 1 at the end.
    A `keep` and an `orphan` are never touched.
    """

    failures = 0
    for entry in (e for e in found if e["class"] == "remove"):
        branch, path = entry["branch"], entry["worktree"]
        now = _classify(graph, entry["tasks"], path, branch, repo=repo, tip=tip)
        if now["class"] != "remove":
            _report(out, ledger, f"skipped {branch}: now {now['class']}, {now['reason']}")
            continue

        try:
            done = subprocess.run(  # noqa: S603
                [WT, "remove", "--foreground", "--", branch],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError as exc:
            failures += 1
            _report(out, ledger, f"failed {branch}: cannot run {WT}: {exc}")
            continue

        if done.returncode:
            failures += 1
            _report(out, ledger, f"failed {branch}: {done.stderr.strip()}")
        else:
            _report(out, ledger, f"removed {branch} at {path}")
    return 1 if failures else 0


def _report(out, ledger: pathlib.Path | None, line: str) -> None:
    """Print a result line and append it to the ledger when there is one."""

    out.write(f"{line}\n")
    if ledger is None or not ledger.exists():
        return
    day = datetime.date.today().isoformat()
    try:
        with ledger.open("a") as handle:
            handle.write(f"- {day} cleanup: {line}\n")
    except OSError as exc:
        out.write(f"cannot write {ledger}: {exc}\n")


def _repo_of(path: pathlib.Path) -> pathlib.Path | None:
    """The git repository that holds a worktree path, from its nearest existing parent."""

    here = next((p for p in (path, *path.parents) if p.is_dir()), None)
    if here is None:
        return None
    code, top = _git(here, "rev-parse", "--show-toplevel")
    return pathlib.Path(top) if code == 0 and top else None


def _git(cwd: pathlib.Path, *args: str) -> tuple[int, str]:
    """The exit code and the stdout of a git command, 127 when git is missing."""

    try:
        done = subprocess.run(  # noqa: S603
            ["git", "-C", str(cwd), *args],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return 127, ""
    return done.returncode, done.stdout.strip()


def _worktrees(repo: pathlib.Path) -> dict[str, str]:
    """The path and the checked-out branch ('' when detached) of each worktree.

    The first one is the main checkout. It raises OSError when git cannot list them.
    """

    code, listing = _git(repo, "worktree", "list", "--porcelain")
    if code != 0:
        msg = f"cannot list the worktrees of {repo}"
        raise OSError(msg)
    found: dict[str, str] = {}
    path = ""
    for line in listing.splitlines():
        if line.startswith("worktree "):
            path = str(pathlib.Path(line[9:]).resolve())
            found[path] = ""
        elif line.startswith("branch refs/heads/"):
            found[path] = line[18:]
    return found


def _header(text: str) -> dict[str, typing.Any]:
    """The header values `compare` diffs: the model and effort tables and the caps."""

    data = tomllib.loads(text)
    flat: dict[str, typing.Any] = {k: data.get(k) for k in ("rw", "ro", "budget")}
    for table in ("model", "effort"):
        value = data.get(table)
        for role, setting in (value if isinstance(value, dict) else {}).items():
            flat[f"{table}.{role}"] = setting

    return flat


def _value(task: Task, name: str) -> typing.Any:
    """The compared value of a field: the list keys that are sets compare as sets."""

    value = getattr(task, name)
    return sorted(value) if name in SET_KEYS and value is not None else value


def compare(
    old: Graph, new: Graph, *, old_text: str = "", new_text: str = ""
) -> dict[str, typing.Any]:
    """What a replanned graph changes, and what the schedule looks like on both.

    `header` lists the changes of the `[model]` and `[effort]` tables and of
    `rw`, `ro` and `budget` as `{key: [old, new]}`, and it is empty when the
    texts are not given. `write_set`, `deps` and `exclusive` compare as sets, so
    an edit of their order is no change. An explicit priority added to a task
    that had none is a change. The schedule counts the open leaves only and has
    no caps, so it shows how much parallelism the shape of the graph allows and
    not what one machine affords. A deferred leaf is parked: it is not open, so
    it is in no wave and never stuck. A gate is an open read-only leaf that
    other open leaves wait for.
    """

    changed = []
    for i in (i for i in old.order if i in new.tasks):
        before, after = old.tasks[i], new.tasks[i]
        fields = {
            name: [getattr(before, name), getattr(after, name)]
            for name in COMPARED
            if _value(before, name) != _value(after, name)
        }
        if before.has_priority != after.has_priority and "priority" not in fields:
            fields["priority"] = [
                before.priority if before.has_priority else None,
                after.priority if after.has_priority else None,
            ]
        if fields:
            changed.append({"id": i, "fields": fields})

    old_header, new_header = (_header(old_text), _header(new_text)) if old_text else ({}, {})
    header = {
        key: [old_header.get(key), new_header.get(key)]
        for key in [*old_header, *(k for k in new_header if k not in old_header)]
        if old_header.get(key) != new_header.get(key)
    }

    return {
        "added": [i for i in new.order if i not in old.tasks],
        "removed": [i for i in old.order if i not in new.tasks],
        "header": header,
        "changed": changed,
        "before": _schedule(old),
        "after": _schedule(new),
    }


def state_symbol(status: str, *, busy: bool = False, ready: bool = False) -> rich.text.Text:
    """The colored symbol of a state, as the tree and the briefs list show it."""

    if status == "done":
        return rich.text.Text("✔", "green")
    if status == "blocked":
        return rich.text.Text("✖", "red")
    if status == "deferred":
        return rich.text.Text("◌", "dim")
    if busy:
        return rich.text.Text("●", "yellow")
    if ready:
        return rich.text.Text("▶", "bold cyan")
    return rich.text.Text("○", "dim")


def _epic_bar(graph: Graph, task_id: str) -> tuple[rich.text.Text, str]:
    """The bar of an epic and its done/total counter, for the start of its row."""

    leaves = graph.leaves_under(task_id)
    if not leaves:
        return rich.text.Text("─" * 8, "dim"), "0/0"

    done = sum(graph.tasks[leaf].status == "done" for leaf in leaves)
    started = sum(
        graph.tasks[leaf].status in {"in_progress", "in_rev"} for leaf in leaves
    )
    filled = 8 * done // len(leaves)
    active = 8 * (done + started) // len(leaves) - filled
    text = rich.text.Text.assemble(
        ("━" * filled, "green"),
        ("━" * active, "yellow"),
        ("─" * (8 - filled - active), "dim"),
    )
    return text, f"{done}/{len(leaves)}"


def _ident(task_id: str, shown: str | None, *, plain: bool, deferred: bool) -> rich.text.Text:
    """The id of a row, colored by its priority, or after "PX" without colors."""

    if plain:
        return rich.text.Text.assemble((shown or NO_PRIORITY, "dim"), " ", task_id)
    ident = rich.text.Text()
    ident.append(task_id, PRIORITY_STYLES.get(shown or "", "dim"))
    if deferred:
        ident.stylize("dim")
    return ident


def _summary_parts(
    graph: Graph, task_id: str, *, hidden: int, milestones: bool
) -> list["_Part"]:
    """The parts right after the title: an epic not broken down, the milestone
    and the tasks a collapsed epic hides."""

    task = graph.tasks[task_id]
    parts: list[_Part] = []
    if task.type == "epic":
        if not graph.leaves_under(task_id):
            parts.append(_Part(rich.text.Text("not broken down", "dim"), SUMMARY))
        milestone = graph.milestone(task_id)
    else:
        # A milestone the epic above already shows is not repeated.
        milestone = task.milestone
        above = graph.milestone(task.parent) if task.parent else ""
        if milestone == above:
            milestone = ""
    if milestone and milestones:
        parts.append(_Part(rich.text.Text(milestone, "dark_cyan"), SUMMARY))
    if hidden:
        text = rich.text.Text(f"{_count(hidden, 'task')} hidden", "dim")
        parts.append(_Part(text, SUMMARY))
    return parts


def _wait_parts(
    graph: Graph, task_id: str, *, held_by: str | None, plain: bool
) -> tuple[list["_Part"], set[str]]:
    """The parts after the summary: what the task blocks and waits for and the
    branch it runs in, with the legend marks they use."""

    task = graph.tasks[task_id]
    parts: list[_Part] = []
    marks: set[str] = set()
    words = [] if task.status == "done" else _remarks(graph, task_id)
    if held_by is not None and task.type != "epic":
        marks.add("held")
        words.append(f"waits {held_by} (files)")
    if task.type != "epic" and task.status != "done":
        waiting_behind = [
            i
            for i in graph.downstream(task_id)
            if graph.tasks[i].status in {"pending", "blocked", "deferred"}
        ]
        if waiting_behind:
            marks.add("blocks")
            held = f"blocks {len(waiting_behind)}"
            parts.append(_Part(rich.text.Text(held, "yellow"), WAITS))
    parts += [
        _Part(rich.text.Text(w, "dim"), MARK if w in MARKS else WAITS) for w in words
    ]

    place = _branch_of(graph, task_id)
    if place:
        marks.add("branch")
        glyph = BRANCH_ASCII if plain else BRANCH_GLYPH
        parts.append(_Part(rich.text.Text(f"{glyph} {place}", "dim"), PLACE))
    return parts, marks


def tree(
    graph: Graph,
    *,
    show_done: bool = False,
    root: str | None = None,
    color: bool = False,
    width: int = DEFAULT_WIDTH,
    milestones: bool = True,
    legend_into: list[rich.text.Text] | None = None,
) -> str:
    """Draw the epics and their tasks inside a terminal of width columns.

    With legend_into the legend is not drawn: its pieces go to that list.


    A row is "art symbol title id detail" on one line, never wrapped. An epic is
    a section header: it has no symbol, no bold or italic title, no `blocks N`
    and no `held by`, and only a task has a status. The
    symbol gives the state and the color of the id the priority (P0 red, P1
    yellow, P2 blue, P3 grey, dim when it has none). Without colors (`color`
    off or NO_COLOR) every id has "PX" before it instead, so
    color is never the only mark. The title keeps the default
    color, bold for a running task (the same test as the yellow dot, see
    _running), and italic for a task that starts as soon as it can (see
    _starts_soon), the only mark it has. Italic is an attribute and not a color, so
    NO_COLOR keeps it, and only a plain text output (no terminal, or `color`
    off) shows no mark. The id follows the title after " · ", and the detail
    parts follow the id, each after " · ", never aligned to the right: the
    progress of an epic, its milestone, what the task waits for, the branch it
    runs in. A row too long for the width first drops the branch, what the
    task waits for and the summary of an epic, in that order, until its title
    keeps a few columns, then ends the title with an ellipsis (see _draw), so
    the id and `awaiting you` stay visible. Only the indent of a
    deeply nested epic wider than the pane can cut the id. Without colors the
    branch glyph is "@". The
    epics, the floating tasks and the children of an epic all sort the same
    way: done first, then running (a floating task before an epic), then by
    priority, state, milestone and file order (see _rank). An epic with no task
    running, ready or awaiting a human collapses to its own row, which says how many
    tasks it hides. Epics nest, and the order and the collapse apply
    to the children of every epic at any depth. Done tasks are hidden unless
    show_done is set, root limits the tree to the subtree of one task and
    expands every epic in it, and color turns the ANSI colors on or off (rich
    still honors NO_COLOR), and milestones off leaves the milestone out of every
    row (the board does it by default). A task that is not done and has N not started
    tasks behind it through the deps says "blocks N" after its title.
    """

    ready = set(graph.ready())
    picked = _starts_soon(graph)
    file_held = file_holds(graph)
    expand = show_done or root is not None
    console = _console(width, color)
    plain = console.color_system is None or console.no_color
    styled = console.color_system is not None
    rows: list[_Row] = []
    epic_heads: list[tuple[int, rich.text.Text, rich.text.Text, str]] = []
    joined: set[int] = set()
    collapsed = 0

    def walk(task_id: str, prefix: str, *, last: bool, top: bool) -> None:
        nonlocal collapsed

        task = graph.tasks[task_id]
        art = "" if top else "└─ " if last else "├─ "
        busy = _running(graph, task_id)
        # An epic is a section header: only a task has a status.
        header = task.type == "epic"
        head = rich.text.Text.assemble((prefix + art, "dim"))
        if not header:
            head.append_text(state_symbol(task.status, busy=busy, ready=task_id in ready))
            head.append(" ")
        ident = _ident(
            task_id,
            _priority(graph, task_id),
            plain=plain,
            deferred=task.status == "deferred",
        )
        if header:
            # The bar goes where a task has its symbol, once the counters are known.
            counter, text = _epic_bar(graph, task_id)
            epic_heads.append((len(rows), head, counter, text))

        children = [
            c
            for c in graph.children[task_id]
            if show_done or not _finished(graph, c)
        ]
        shut = 0 if expand else _collapsible(graph, task_id, ready=ready)
        collapsed += 1 if shut else 0
        if header and children and not shut:
            joined.add(len(rows))

        detail, _ = _wait_parts(
            graph, task_id, held_by=file_held.get(task_id), plain=plain
        )
        parts = _summary_parts(graph, task_id, hidden=shut, milestones=milestones)
        parts += detail
        title = _title(
            task.title,
            italic=task_id in picked and styled,
            bold=busy and not header,
            dim=task.status == "deferred",
        )
        waits = _dim_title(task, ready=task_id in ready, soon=task_id in picked, busy=busy)
        if header:
            _epic_title(title)
        elif waits:
            title.stylize("dim")
        rows.append(_Row(head, title, ident, parts))
        if shut:
            return

        ordered = _ranked(graph, children, ready=ready)
        extra = "" if top else "   " if last else "│  "
        for n, child in enumerate(ordered):
            walk(child, prefix + extra, last=n == len(ordered) - 1, top=False)

    roots = [i for i in graph.order if not graph.tasks[i].parent]
    roots = [root] if root else _ranked(graph, roots, ready=ready)
    previous = ""
    for top_id in roots:
        if show_done or root or not _finished(graph, top_id):
            start = len(rows)
            walk(top_id, "", last=True, top=True)
            # Epics stand apart from the top-level tasks around them, and the
            # top-level tasks stay together.
            if previous and "epic" in (graph.tasks[previous].type, graph.tasks[top_id].type):
                rows[start] = dataclasses.replace(rows[start], gap=True)
            previous = top_id

    for index, head, counter, text in epic_heads:
        # The connector sits where the children draw theirs, and goes first
        # when the pane is too narrow for the bar.
        bar_room = counter.cell_len + 2 + len(text)
        tight = (
            head.cell_len + 2 + bar_room + 1 + len(DETAIL_SEP) + rows[index].ident.cell_len
            > width
        )
        if not tight:
            head.append("┬ " if index in joined else "─ ", "dim")
        head.append_text(counter)
        head.append(f" {text}", "dim")
        head.append(" ")
        rows[index] = dataclasses.replace(rows[index], head=head)

    legend = _legend(plain=plain, styled=styled)
    if legend_into is not None:
        legend_into.extend(legend)
        legend = []
    notes = []
    if collapsed:
        notes += [f"{_count(collapsed, 'epic')} collapsed, --epic ID opens one"]
    return _draw(console, rows, legend, notes, width=width)


def board_cards(graph: Graph, statuses: set[str]) -> list[str]:
    """The leaves of a board column in pick-up order.

    A card that waits on a human comes first, then the ready ones in the order
    `next` starts them, then the rest in file order. The done ones come newest
    first by `done_at`, and the ones without it after them in reverse file
    order, because a task is appended to the file when it is created.
    """

    ready = {leaf: n for n, leaf in enumerate(graph.ready())}
    cards = [i for i in graph.leaves if graph.tasks[i].status in statuses]
    if statuses == {"done"}:
        return sorted(cards, key=lambda i: _done_order(graph, i))
    return sorted(
        cards,
        key=lambda i: (
            graph.tasks[i].status == "deferred",
            graph.question(i) is None,
            not graph.reviewing(i),
            ready.get(i, len(ready)),
        ),
    )


def next_start(graph: Graph) -> str:
    """The task `next` would start first with no caps, or an empty string."""

    start = select_next(graph, limits=Limits())["start"]
    return start[0]["id"] if start else ""


def board(graph: Graph, *, max_blocked: int = MAX_BLOCKED) -> str:
    """Group the leaves in the columns of a GitHub Project board.

    The fine statuses fold into "In progress", and "blocked" is shown on the
    card, with the question when it waits on a human. A card that waits on a
    human comes first in its column, so it is visible without scrolling. A card
    that holds up more than max_blocked open tasks says "blocks N".
    """

    lines: list[str] = []
    file_held = file_holds(graph)
    for column, statuses in BOARD:
        cards = board_cards(graph, statuses)
        lines.append(f"{column} ({len(cards)})")

        for i in cards:
            task = graph.tasks[i]
            waiting = (
                graph.blocked_by(i) if task.status not in {"done", "deferred"} else []
            )
            question = graph.question(i)
            branch = graph.workspace(i)[1]
            note = "  deferred" if task.status == "deferred" else ""
            note += f"  waits {','.join(waiting)} (dep)" if waiting else ""
            note += f"  awaiting you: {question}" if question is not None else ""
            note += "  awaiting you: review" if graph.reviewing(i) else ""
            note += f"  waits {file_held[i]} (files)" if i in file_held else ""
            held = len(graph.holds_up(i))
            note += f"  blocks {held}" if held > max_blocked else ""
            note += f"  wt:{branch}" if branch else ""
            lines.append(f"  {i}  {task.title}  {task.priority}{note}")

        lines.append("")

    return "\n".join(lines).rstrip()

EXIT_CODES = """\
exit codes:
  0  success
  1  cleanup: git failed, or a wt remove of --apply failed
  2  the tasks file, an id, a flag value or a write is invalid; the reason is
     printed as JSON {"ok": false, "errors": [...]}"""

# The contract of every command, shown by `<command> --help`: an agent runs the
# scripts without reading them. A new command, flag or output key is added here
# in the same change (tests/test_skill_scripts.py fails when one is missing).
COMMAND_CONTRACTS = {
    "check": """\
output (JSON): ok, tasks, leaves, warnings (only when there are any), notes
  (purposeful overlaps, only when there are any)
example: ledger_graph.py tasks.toml check""",
    "ready": """\
output (JSON): ready (the ids of the leaves that can start now, most urgent first)
example: ledger_graph.py tasks.toml ready""",
    "epics": """\
output (JSON): epics, a list of id, status, milestone, leaves, leaves_done,
  children_done
example: ledger_graph.py tasks.toml epics""",
    "tree": """\
output (text): epics and tasks as a tree, wrapped to --width; read the legend
  in the reference/board-tui.md of the skill
example: ledger_graph.py tasks.toml tree --all --epic E1 --color never""",
    "board": """\
output (text): the leaves in the columns of a GitHub Project board
example: ledger_graph.py tasks.toml board""",
    "resume": """\
output (text): READY, RUNNING, BLOCKED, ANSWERED, DECISIONS, CONSTRAINTS and NEXT
example: ledger_graph.py tasks.toml resume""",
    "event": """\
output (JSON): with an event, ok and event (ts, task, event, detail); without
  one, events (the lines of the log, oldest first)
example: ledger_graph.py tasks.toml event t1 commit abc1234""",
    "register": """\
output (text): the ledger gate, to paste at the start of the `task` prompt: the
  line [ledger:<id>], the brief path and the first-action instruction that makes
  an agent without an in_progress task and a brief return NO LEDGER TASK. The task
  is appended as in_progress with its brief (from the skeleton of task-brief.md, with the gate first, unless
  --brief-from); an invalid task leaves the file untouched (exit 2)
example: ledger_graph.py tasks.toml register t9 --title 'Fix the lock' --parent E1 --write-set src/lock.py --brief-from brief.md""",
    "attach": """\
output (JSON): ok and event (ts, task, event agent, detail the agent id); the same as `event <id> agent <agent-id>`
example: ledger_graph.py tasks.toml attach t9 agent-3""",
    "machine": """\
output (JSON): id, needs, machine and transport (both null when no machine of the
  map covers the needs); read-only, it opens no connection and prints no host
example: ledger_graph.py tasks.toml machine t1 --map ~/machines.toml""",
    "cleanup": """\
output (text): the remove, keep and orphan worktrees and branches, and the
  wt remove commands
example: ledger_graph.py tasks.toml cleanup --tip integration --apply""",
    "next": """\
output (JSON): limits (rw, ro, budget), start (the tasks to start: id,
  priority, milestone, unblocks, read_only, footprint, needs, branch, worktree, model,
  effort, session, session_dir, overlaps), skipped (id, reason, with, detail),
  running (id, branch, worktree, session, session_dir), waiting (id, blocked_by), blocked (ids), awaiting_human (id,
  question for a human blocker, or id, review: the hunk command, for an in_rev task marked human_review), awaiting_event (id, event), answered (id, answer, time, status:
  the answers since the last next, .next-seen holds the steering.log offset
  and only next reads it, 24 hours on the first run, --peek leaves the stamp), cleanup (remove, warning, only when a
  worktree can go), and with --awaiting also replan_requests (request, scope, epic, note: the pending files of
  replan-requests/ next to the tasks file, oldest first, or request and error for an invalid one) and feedback
  (task, file, comments: the files of feedback/ next to the tasks file that the human sent, each comment {path,
  line, text}; a draft is never listed, move a handled file to feedback/done/; or task, file and error for an invalid one)
example: ledger_graph.py tasks.toml next --rw 2 --ro 1 --budget 4 --caps-note "load 1/14"
  (--rw, --ro, --budget and --caps-note are required, --pick needs no note)""",
    "explain": """\
output (JSON): id, type, status, priority, parent, ready, wave, blocked_by,
  blocking, holds_up, overlaps_on_purpose, warning
example: ledger_graph.py tasks.toml explain t2""",
    "set": """\
output (JSON): ok, id, the field written, warning (a status written to done also stamps done_at), and after a status that
  frees capacity limits (rw, ro, budget), start and skipped as `next` prints
  them (start: id, priority, milestone, unblocks, read_only, footprint, branch,
  worktree, model, effort, session, session_dir, overlaps; skipped: id, reason, with, detail)
example: ledger_graph.py tasks.toml set t2 priority P0""",
    "answer": """\
output (JSON): ok, id, answer
example: ledger_graph.py tasks.toml answer t2 'use option A'""",
    "compare": """\
output (JSON): added, removed, header, changed (id, fields), before and after
  (waves, ready, gates, rounds, stuck); exit 2 with the ids when the proposal
  changes or removes a frozen task (done, in_progress or in_rev)
example: ledger_graph.py tasks.toml compare tasks.proposed.toml""",
}


def build_parser() -> argparse.ArgumentParser:

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("tasks", help="the tasks.toml file, or - for stdin")
    commands = parser.add_subparsers(dest="command", required=True)
    check_ = commands.add_parser(
        "check",
        help="validate the format, ids, parents, deps, integrate_after and cycles, the optional "
        "model and effort keys, the optional base ref of a task (the start of "
        "its review diff), and warn about a task that holds up more than "
        "--max-blocked open tasks",
    )
    commands.add_parser("ready", help="leaves that can start now, most urgent first")
    commands.add_parser("epics", help="progress of every epic")
    tree_ = commands.add_parser("tree", help="epics and tasks as a text tree")
    tree_.add_argument("--all", action="store_true", help="also show the done tasks")
    tree_.add_argument("--epic", help="show only the subtree of this task id")
    tree_.add_argument(
        "--width",
        type=int,
        help="columns to fit, default the terminal when stdout is one, else 80",
    )
    tree_.add_argument(
        "--color",
        choices=("auto", "always", "never"),
        default="auto",
        help="ANSI colors",
    )
    board_ = commands.add_parser(
        "board", help="leaves in the columns of a GitHub Project board"
    )
    commands.add_parser(
        "resume",
        help="re-entry digest as text: READY and RUNNING (agent ids, Pi session "
        "keys) under the caps, BLOCKED, the last 3 DECISIONS, the CONSTRAINTS "
        "file to read and the first NEXT action, from ORCHESTRATION.md next to "
        "tasks.toml",
        description="Print the re-entry digest as text: READY and RUNNING "
        "(agent ids, and the session and session_dir keys of a task when set) "
        "under the caps, BLOCKED, the last 3 DECISIONS, the "
        "CONSTRAINTS file to read and the first NEXT action, from the "
        "ORCHESTRATION.md next to tasks.toml.",
    )
    cleanup_ = commands.add_parser(
        "cleanup",
        help="classify the worktrees and branches the ledger names (remove, keep, "
        "orphan); --apply runs wt remove for the remove class",
        description="Classify every worktree and branch the ledger names, "
        "without changing anything: remove (done, merged into the tip, clean), "
        "keep (with the reason) and orphan. Print the wt remove commands. "
        "--apply removes the remove class with wt remove.",
    )
    cleanup_.add_argument(
        "--apply", action="store_true", help="run wt remove for the remove class"
    )
    cleanup_.add_argument(
        "--repo", default=".", help="the git repository to inspect (default .)"
    )
    cleanup_.add_argument(
        "--tip",
        default=None,
        help="the ref a branch must be merged into, resolved in the main checkout "
        "(default the Stack tip: line of ORCHESTRATION.md, else the main HEAD)",
    )
    cleanup_.add_argument(
        "--max-removable",
        type=int,
        default=MAX_REMOVABLE,
        help=f"warn above this many remove candidates (default {MAX_REMOVABLE})",
    )
    next_ = commands.add_parser(
        "next",
        help="pick the tasks to start now (with the optional session and "
        "session_dir keys of Pi)",
        description="Pick the tasks to start now. A task with the optional "
        "session and session_dir keys of Pi reports them.",
    )
    next_.add_argument(
        "--rw", type=int, help="cap on running writers, default the header or no cap"
    )
    next_.add_argument("--slots", dest="rw", type=int, help="alias of --rw")
    next_.add_argument(
        "--ro",
        type=int,
        help="cap on running read-only tasks, default the header or no cap",
    )
    next_.add_argument(
        "--budget",
        type=int,
        help="cap on the sum of the footprints, default the header or no cap",
    )
    next_.add_argument(
        "--awaiting",
        action="store_true",
        help="print only awaiting_human, awaiting_event, replan_requests and feedback, no caps needed",
    )
    next_.add_argument(
        "--peek",
        action="store_true",
        help="print answered without moving the .next-seen stamp (only next reads it)",
    )
    next_.add_argument(
        "--caps-note",
        help="why these caps fit the load, logged to caps.log, not needed with --pick",
    )
    next_.add_argument(
        "--pick", help="comma separated ids to keep, a subset of what start would hold"
    )
    explain_ = commands.add_parser(
        "explain",
        help="why a task is or is not ready, and the open tasks it holds up",
    )
    explain_.add_argument("id")
    for command in (check_, tree_, board_, explain_):
        command.add_argument(
            "--max-blocked",
            type=int,
            default=MAX_BLOCKED,
            help="warn about a task that holds up more open tasks than this, "
            f"through deps or a shared write_set (default {MAX_BLOCKED})",
        )
    set_ = commands.add_parser(
        "set",
        help="write the priority or the status of a task, and print what to start next "
        "(done is refused while an integrate_after task is not done)",
    )
    set_.add_argument("id")
    set_.add_argument("field", help="the field to write: 'priority' or 'status'")
    set_.add_argument(
        "value", help="P0, P1, P2 or P3, or pending, in_progress, done or blocked"
    )
    set_.add_argument(
        "--no-next",
        action="store_true",
        help="do not print start and skipped after a status that frees capacity",
    )
    answer_ = commands.add_parser(
        "answer", help="answer a task blocked on a human and make it pending"
    )
    answer_.add_argument("id")
    answer_.add_argument("text", help="the answer, which may span several lines")
    event_ = commands.add_parser(
        "event",
        help="append an event to the log of a task, or print the log",
    )
    event_.add_argument("id")
    event_.add_argument(
        "event",
        nargs="?",
        help="the kind to append (status, agent, commit, verdict, ...); omit to print",
    )
    event_.add_argument("detail", nargs="?", default="", help="one line of detail")
    register_ = commands.add_parser(
        "register",
        help="register an in_progress task and its brief, and print the ledger gate prompt",
    )
    register_.add_argument("new_id", metavar="id", help="the id of the new task")
    register_.add_argument("--title", required=True)
    register_.add_argument("--parent", required=True)
    register_.add_argument("--deps", nargs="*", default=[])
    register_.add_argument("--write-set", nargs="*", default=[])
    register_.add_argument("--read-only", action="store_true")
    register_.add_argument("--model")
    register_.add_argument("--effort")
    register_.add_argument(
        "--brief-from", help="the brief: a file, or - for stdin; defaults to an empty Change"
    )
    attach_ = commands.add_parser(
        "attach", help="record the agent that runs a task (event agent)"
    )
    attach_.add_argument("id")
    attach_.add_argument("agent_id")
    machine_ = commands.add_parser(
        "machine",
        help="the machine label and transport the per-user map picks for a task",
    )
    machine_.add_argument("id")
    machine_.add_argument(
        "--map", help=f"the per-user machine map; defaults to ${MAP_ENV}"
    )
    compare_ = commands.add_parser(
        "compare", help="what another tasks.toml changes and how it schedules"
    )
    compare_.add_argument("new", help="the replanned tasks.toml to compare against")
    parser.formatter_class = argparse.RawDescriptionHelpFormatter
    parser.epilog = EXIT_CODES
    for name, text in COMMAND_CONTRACTS.items():
        sub = commands.choices[name]
        sub.formatter_class = argparse.RawDescriptionHelpFormatter
        sub.epilog = text + "\n" + EXIT_CODES
    return parser


@dataclasses.dataclass(frozen=True)
class _Call:
    """A parsed command line with the ledger it reads, given to a command."""

    args: argparse.Namespace
    text: str
    graph: Graph
    out: typing.TextIO

    @property
    def tasks_file(self) -> pathlib.Path | None:
        """The resolved tasks file, or None when the ledger comes from stdin."""

        return None if self.args.tasks == "-" else pathlib.Path(self.args.tasks).resolve()

    @property
    def max_blocked(self) -> int:
        return getattr(self.args, "max_blocked", MAX_BLOCKED)


def _parse_graph(text: str) -> Graph | list[str]:
    """The graph of a ledger text, or its parse errors, or else its graph errors."""

    tasks, errors = parse(text)
    if errors:
        return errors
    graph = Graph(tasks)
    return graph.errors or graph


def _emit(out: typing.TextIO, result: dict) -> int:
    """Write the result of a command as JSON and return the exit code 0."""

    out.write(json.dumps(result, indent=2) + "\n")
    return 0


def _write_with_mtime(
    tasks: str, write: typing.Callable[..., object], *values: str
) -> None:
    """Run a write on the tasks file, which compares and swaps on its mtime."""

    # Read the mtime right before the write, to compare and swap on it.
    path = pathlib.Path(tasks).resolve()
    write(path, *values, seen_mtime_ns=path.stat().st_mtime_ns)


def _run_tree(call: _Call) -> int:
    args = call.args
    if args.width is not None and not _is_positive_int(args.width):
        return _fail(call.out, ["--width must be a positive integer"])

    color = {"auto": call.out.isatty(), "always": True, "never": False}[args.color]
    text = tree(
        call.graph,
        show_done=args.all,
        root=args.epic,
        color=color,
        width=_terminal_width(call.out, args.width),
    )
    call.out.write(text + "\n")
    return 0


def _run_board(call: _Call) -> int:
    call.out.write(board(call.graph, max_blocked=call.max_blocked) + "\n")
    return 0


def check_warnings(
    graph: Graph,
    tasks_file: pathlib.Path | None,
    *,
    max_blocked: int = MAX_BLOCKED,
) -> list[str]:
    """The warnings `check` prints, in its order."""

    warnings = graph.warnings() + bottleneck_warnings(graph, max_blocked=max_blocked)
    if tasks_file:
        warnings += graph.missing_briefs(tasks_file.parent)
        warnings += graph.inbox_warnings(tasks_file.parent)
        warnings += host_warnings(graph, tasks_file)
    return warnings


def _run_check(call: _Call) -> int:
    graph = call.graph
    result: dict = {"ok": True, "tasks": len(graph.tasks), "leaves": len(graph.leaves)}
    warnings = check_warnings(graph, call.tasks_file, max_blocked=call.max_blocked)
    if warnings:
        result["warnings"] = warnings
    if notes := purposeful_overlaps(graph):
        result["notes"] = notes
    return _emit(call.out, result)


def _caps_errors(args: argparse.Namespace, flags: dict[str, int | None]) -> list[str]:
    """Why the caps of `next` are refused: absent, without a note, or not positive."""

    if absent := [f"--{flag}" for flag, value in flags.items() if value is None]:
        return [
            f"missing {', '.join(absent)}: decide the caps first, "
            "after running nproc, uptime, free -h and df -h <path>"
        ]
    if args.pick is None and not (args.caps_note or "").strip():
        return ['missing --caps-note "<reason>": say why these caps fit the load']
    return [
        f"--{flag} must be a positive integer"
        for flag, value in flags.items()
        if value is not None and not _is_positive_int(value)
    ]


def _pick(start: list[dict], pick: str) -> list[dict]:
    """The tasks of start that --pick names, or a ValueError if it names another."""

    picked = [i.strip() for i in pick.split(",") if i.strip()]
    startable = [t["id"] for t in start]
    allowed = ", ".join(startable) or "nothing"
    if not picked:
        msg = f"--pick needs at least one id: next starts {allowed}"
        raise ValueError(msg)
    if missing := [i for i in picked if i not in startable]:
        msg = f"cannot start {', '.join(missing)} now: next starts {allowed}"
        raise ValueError(msg)
    return [t for t in start if t["id"] in picked]


def _run_next(call: _Call) -> int:
    args, graph, out = call.args, call.graph, call.out
    base = ledger_base(call.tasks_file)
    if args.awaiting:
        everything = select_next(
            graph, limits=Limits(rw=None, ro=None, budget=None), base=base
        )
        return _emit(
            out,
            {
                **{key: everything[key] for key in ("awaiting_human", "awaiting_event")},
                "replan_requests": replan_requests(call.tasks_file),
                "feedback": feedback_ready(call.tasks_file),
            },
        )
    flags = {"rw": args.rw, "ro": args.ro, "budget": args.budget}
    if errors := _caps_errors(args, flags):
        return _fail(out, errors)

    result = select_next(graph, limits=read_limits(call.text).override(**flags), base=base)
    if args.pick is not None:
        try:
            result["start"] = _pick(result["start"], args.pick)
        except ValueError as exc:
            return _fail(out, [str(exc)])
    tasks_file = call.tasks_file
    result["answered"], seen_offset = answered_since(graph, tasks_file)
    if args.caps_note and tasks_file:
        stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
        caps = f"rw={args.rw} ro={args.ro} budget={args.budget}"
        with (tasks_file.parent / "caps.log").open("a") as handle:
            handle.write(f"{stamp} {caps} {args.caps_note.strip()}\n")
    tip = ledger_tip(tasks_file)
    if count := removable_count(graph, pathlib.Path.cwd(), tip):
        result["cleanup"] = {
            "remove": count,
            "warning": cleanup_note(count, MAX_REMOVABLE)
            if count > MAX_REMOVABLE
            else None,
        }
    _emit(out, result)
    if tasks_file is not None and not args.peek:
        write_next_seen(tasks_file, seen_offset)
    return 0


def _run_cleanup(call: _Call) -> int:
    args, out = call.args, call.out
    repo = pathlib.Path(args.repo).resolve()
    tasks_file = call.tasks_file
    tip = args.tip or ledger_tip(tasks_file)
    try:
        found = cleanup(call.graph, repo=repo, tip=tip)
    except OSError as exc:
        out.write(f"failed: {exc}\n")
        return 1
    out.write(cleanup_report(found, threshold=args.max_removable) + "\n")
    if not args.apply:
        return 0
    ledger = tasks_file.parent / "ORCHESTRATION.md" if tasks_file else None
    return cleanup_apply(call.graph, found, repo=repo, tip=tip, ledger=ledger, out=out)


def _run_compare(call: _Call) -> int:
    new = call.args.new
    try:
        new_text = pathlib.Path(new).resolve().read_text()
    except OSError as exc:
        return _fail(call.out, [f"cannot read {new}: {exc}"])

    new_graph = _parse_graph(new_text)
    if isinstance(new_graph, list):
        return _fail(call.out, [f"{new}: {e}" for e in new_graph])
    if errors := frozen_changes(call.graph, new_graph):
        return _fail(call.out, errors)
    result = compare(call.graph, new_graph, old_text=call.text, new_text=new_text)
    return _emit(call.out, result)


def _run_resume(call: _Call) -> int:
    limits = read_limits(call.text)
    call.out.write(resume(call.graph, limits=limits, tasks_path=call.tasks_file) + "\n")
    return 0


def _run_event(call: _Call) -> int:
    args = call.args
    if call.tasks_file is None:
        return _fail(call.out, ["event needs a tasks.toml path, not stdin"])
    if args.event is None:
        return _emit(call.out, {"events": read_events(call.tasks_file, args.id)})
    if args.event == "remote" and (errors := remote_errors(args.detail)):
        return _fail(call.out, errors)
    try:
        record = append_event(call.tasks_file, args.id, args.event, args.detail)
    except OSError as exc:
        return _fail(call.out, [str(exc)])
    return _emit(call.out, {"ok": True, "event": record})


def _run_attach(call: _Call) -> int:
    if call.tasks_file is None:
        return _fail(call.out, ["attach needs a tasks.toml path, not stdin"])
    try:
        record = append_event(call.tasks_file, call.args.id, "agent", call.args.agent_id)
    except OSError as exc:
        return _fail(call.out, [str(exc)])
    return _emit(call.out, {"ok": True, "event": record})


def _run_register(call: _Call) -> int:
    args, out = call.args, call.out
    if call.tasks_file is None:
        return _fail(out, ["register needs a tasks.toml path, not stdin"])

    brief_path = call.tasks_file.parent / "briefs" / f"{args.new_id}.md"
    gate = LEDGER_GATE.format(id=args.new_id, brief=brief_path)
    try:
        if args.brief_from is None:
            change = ""
        elif args.brief_from == "-":
            change = sys.stdin.read()
        else:
            change = pathlib.Path(args.brief_from).read_text(encoding="utf-8")
    except OSError as exc:
        return _fail(out, [str(exc)])
    brief = _register_brief(gate, args.title, change, args.write_set)

    fields: dict[str, typing.Any] = {
        "title": args.title,
        "status": "in_progress",
        "parent": args.parent,
        **({"deps": args.deps} if args.deps else {}),
        **({"write_set": args.write_set} if args.write_set else {}),
        **({"read_only": True} if args.read_only else {}),
        **({"model": args.model} if args.model else {}),
        **({"effort": args.effort} if args.effort else {}),
    }
    try:
        _write_with_mtime(
            args.tasks, register_task, args.new_id, fields, brief
        )
    except (OSError, ValueError) as exc:
        return _fail(out, [str(exc)])

    out.write(gate + "\n")
    return 0


def _register_brief(gate: str, title: str, change: str, write_set: list[str]) -> str:
    """The text of briefs/<id>.md: the ledger gate, then the skeleton of reference/task-brief.md."""

    files = ", ".join(f"`{pattern}`" for pattern in write_set) or "none"
    return (
        f"{gate}\n\n# {title}\n\n"
        f"## Change\n\n{change.strip()}\n\n"
        "## Acceptance\n\n- \n\n"
        f"## Constraints\n\n- May edit: {files}.\n- See briefs/_common.md rules.\n\n"
        "## Return\n\n"
        "The commit SHA, `git diff --stat`, the commands run with their summary lines, "
        "the brief mismatches (empty if none) and the open questions (empty if none).\n"
    )


def _run_machine(call: _Call) -> int:
    args = call.args
    task = call.graph.tasks[args.id]
    if not task.needs:
        return _fail(call.out, [f"{args.id} has no 'needs': nothing to match"])
    given = args.map or os.environ.get(MAP_ENV)
    if not given:
        return _fail(call.out, [f"no machine map: pass --map or set {MAP_ENV}"])
    try:
        machines = read_machine_map(pathlib.Path(given).expanduser())
    except ValueError as exc:
        return _fail(call.out, [str(exc)])
    return _emit(call.out, {"id": args.id, "needs": task.needs, **pick_machine(task.needs, machines)})


def _next_after_write(path: pathlib.Path) -> dict:
    """What `next` starts once a status is written, or a warning if it cannot run.

    The write is done, so a failure to read the file again is only a warning:
    failing here would hide a status that was written.
    """

    try:
        new_text = path.read_text()
        new_tasks, new_errors = parse(new_text)
        new_graph = Graph(new_tasks)
        problems = [*new_errors, *new_graph.errors]
        if not problems:
            chosen = select_next(new_graph, limits=read_limits(new_text))
            return {key: chosen[key] for key in ("limits", "start", "skipped")}
    except (OSError, ValueError) as exc:
        problems = [str(exc)]
    return {"warning": f"status written, but next could not run: {'; '.join(problems)}"}


def _run_set(call: _Call) -> int:
    args, out = call.args, call.out
    if args.field not in {"priority", "status"}:
        return _fail(out, [f"only 'priority' and 'status' can be set, not '{args.field}'"])
    if args.no_next and args.field != "status":
        return _fail(out, ["--no-next only applies to status"])

    write = set_priority if args.field == "priority" else set_status
    try:
        _write_with_mtime(args.tasks, write, args.id, args.value)
    except (OSError, ValueError) as exc:
        return _fail(out, [str(exc)])
    result = {"ok": True, "id": args.id, args.field: args.value}
    if args.field == "status" and args.value in FREEING_STATUSES and not args.no_next:
        result |= _next_after_write(pathlib.Path(args.tasks).resolve())
    return _emit(out, result)


def _run_answer(call: _Call) -> int:
    args = call.args
    try:
        _write_with_mtime(args.tasks, answer_blocker, args.id, args.text)
    except (OSError, ValueError) as exc:
        return _fail(call.out, [str(exc)])
    return _emit(call.out, {"ok": True, "id": args.id, "answer": args.text})


_COMMANDS: dict[str, typing.Callable[[_Call], int]] = {
    "tree": _run_tree,
    "board": _run_board,
    "check": _run_check,
    "ready": lambda call: _emit(call.out, {"ready": call.graph.ready()}),
    "next": _run_next,
    "cleanup": _run_cleanup,
    "compare": _run_compare,
    "epics": lambda call: _emit(call.out, {"epics": epics(call.graph)}),
    "resume": _run_resume,
    "event": _run_event,
    "machine": _run_machine,
    "register": _run_register,
    "attach": _run_attach,
    "explain": lambda call: _emit(
        call.out, explain(call.graph, call.args.id, max_blocked=call.max_blocked)
    ),
    "set": _run_set,
    "answer": _run_answer,
}


def run(argv: list[str], out: typing.TextIO = sys.stdout) -> int:
    """Run a command and write its result to out.

    The exit code is 0 on success, and 2 when the file or the task id is invalid.
    """

    args = build_parser().parse_args(argv)

    # Resolve the path first, because the session folder only holds a symlink.
    try:
        text = (
            sys.stdin.read()
            if args.tasks == "-"
            else pathlib.Path(args.tasks).resolve().read_text()
        )
    except OSError as exc:
        return _fail(out, [f"cannot read {args.tasks}: {exc}"])

    graph = _parse_graph(text)
    if isinstance(graph, list):
        return _fail(out, graph)

    target = getattr(args, "id", None) or getattr(args, "epic", None)
    if target and target not in graph.tasks:
        return _fail(out, [f"unknown id '{target}'"])

    call = _Call(args, text, graph, out)
    if call.max_blocked < 0:
        return _fail(out, ["--max-blocked must not be negative"])

    return _COMMANDS[args.command](call)


@dataclasses.dataclass(frozen=True)
class _Part:
    """A piece of the detail of a row and the rank it is dropped by."""

    text: rich.text.Text
    rank: int


@dataclasses.dataclass(frozen=True)
class _Row:
    """One task of the tree, before it is fitted to the width."""

    head: rich.text.Text
    title: rich.text.Text
    ident: rich.text.Text
    trailing: list[_Part]
    gap: bool = False


def _epic_title(title: rich.text.Text) -> None:
    """Draw the title of an epic row dim, in capitals unless LEDGER_PLAIN_EPIC=1."""

    if os.environ.get("LEDGER_PLAIN_EPIC") != "1":
        title.plain = title.plain.upper()
    title.stylize("dim")


def _read_ledger(text: str) -> tuple[str, dict[str, list[str]]]:
    """The header line with `Constraints:` and the lines of each `##` section."""

    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    header = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = sections.setdefault(line[3:].strip(), [])
        elif current is not None:
            current.append(line)
        elif "Constraints:" in line:
            header = line
    return header, sections


def _agents_of(registry: list[str]) -> dict[str, str]:
    """The agent id of the last registry row of each task.

    A `pending` placeholder, an empty id or a `stale` row gives `?`, the orphan
    to reconcile first.
    """

    agents: dict[str, str] = {}
    for line in registry:
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if line.lstrip().startswith("|") and len(cells) > 3 and cells[0] not in (
            "task",
            "",
        ) and not set(cells[0]) <= {"-"}:
            stale = len(cells) > 4 and cells[4] == "stale"
            placeholder = cells[3] in ("", "pending", "?")
            agents[cells[0]] = "?" if stale or placeholder else cells[3]
    return agents


def _clip(text: str) -> str:
    return text if len(text) <= 100 else text[:97] + "..."


def _dim_title(task: Task, *, ready: bool, soon: bool, busy: bool) -> bool:
    """Whether the title is dim: a task that waits and does not start soon."""

    return (
        task.type != "epic"
        and task.status not in ("done", "blocked", "deferred")
        and not (ready or soon or busy)
    )


def _title(
    text: str, *, italic: bool, bold: bool = False, dim: bool = False
) -> rich.text.Text:
    """The title in the default color, with the **x** spans of the text in bold.

    The asterisks of a span do not show, so bold is free to highlight a word.
    Bold marks the title of a task in progress and italic the one of a task
    that starts as soon as it can. A newline or a run of spaces in the text shows as
    one space, so a title is one line.
    """

    text = " ".join(text.split())
    title = rich.text.Text()
    end = 0
    for match in BOLD.finditer(text):
        title.append(text[end : match.start()])
        title.append(match.group(1), "bold")
        end = match.end()
    title.append(text[end:])
    if bold:
        title.stylize("bold")
    if italic:
        title.stylize("italic")
    if dim:
        title.stylize("dim")

    return title


def _branch_of(graph: Graph, task_id: str) -> str:
    """The branch a task shows, or "" when it has no place to show.

    A task that is done shows it only while its worktree still exists, and the
    others show the branch alone too, falling back to the name of the worktree.
    """

    worktree, branch = graph.workspace(task_id)
    if graph.tasks[task_id].status == "done" and not worktree:
        return ""

    return branch or posixpath.basename(worktree.rstrip("/"))


def _ids(ids: list[str]) -> str:
    """The first ids of a list and the count of the others, to keep a row short."""

    shown = ",".join(ids[:MAX_WAITS])
    return f"{shown} +{len(ids) - MAX_WAITS}" if len(ids) > MAX_WAITS else shown


def _remarks(graph: Graph, task_id: str) -> list[str]:
    """What a task that is not done waits for, runs as and asks of a human.

    The status of a running task is not one of them, its marker says it.
    """

    task = graph.tasks[task_id]

    # The children of an epic are its deps too, and the rows below say so.
    waiting = [w for w in graph.blocked_by(task_id) if w not in graph.children[task_id]]
    own = [w for w in waiting if w in task.deps]
    inherited = [w for w in waiting if w not in task.deps]
    # A task that started already has its deps in its base.
    not_started = task.status in {"pending", "deferred", "blocked"}
    words = [f"waits {_ids(own)} (dep)"] if own and not_started else []
    words += [f"inherited {_ids(inherited)}"] if inherited else []
    words += [task.status] if task.status in {"in_rev", "returned"} else []
    words += ["awaiting you"] if graph.awaits_you(task_id) else []
    return words


def _legend(*, plain: bool, styled: bool) -> list[rich.text.Text]:
    """The key of every mark of the tree, the board and the overview, by piece."""

    states = "✔ done ● running ▶ ready ○ waiting ✖ blocked ◌ deferred"
    pieces = [rich.text.Text(states, "dim")]

    shows = {"bold": not plain, "italic": styled, "dim": styled}
    styles = [f"{style} {label}" for style, label in TITLE_STYLES if shows[style]]
    if styles:
        pieces.append(_styles_line(styles))

    keys = [
        rich.text.Text("PX before the id: its priority", "dim")
        if plain
        else _priority_key()
    ]
    key = rich.text.Text("━", "green")
    key.append("━", "yellow")
    key.append(" done and in progress leaves of an epic", "dim")
    keys.append(key)
    pieces.append(rich.text.Text("; ", "dim").join(keys))

    pieces += [rich.text.Text(text, "dim") for text in LEGEND_MARKS]
    glyph = BRANCH_ASCII if plain else BRANCH_GLYPH
    pieces.append(rich.text.Text(f"{glyph} the branch of the task", "dim"))

    return pieces


def legend_card() -> list[rich.text.Text]:
    """The legend of an overlay, 40 columns wide: sections of a sample drawn as in the rows and a short label."""

    width = 36
    room = 12

    def row(sample: rich.text.Text, label: str, hint: str = "") -> rich.text.Text:

        line = rich.text.Text(" ")
        line.append_text(sample)
        line.append(" " * (room - sample.cell_len))
        line.append(label, "dim")
        if hint:
            line.append(" " * (width - 1 - line.cell_len - len(hint)))
            line.append(hint, "dim")
        return line

    def pair(left: rich.text.Text, right: rich.text.Text) -> rich.text.Text:

        line = rich.text.Text(" ")
        for n, (sample, label) in enumerate((left, right)):
            if n:
                line.append(" " * (room + 8 - line.cell_len))
            line.append_text(sample)
            line.append(f"  {label}", "dim")
        return line

    states = [
        (state_symbol("done"), "done"),
        (state_symbol("pending", busy=True), "running"),
        (state_symbol("pending", ready=True), "ready"),
        (state_symbol("pending"), "waiting"),
        (state_symbol("blocked"), "blocked"),
        (state_symbol("deferred"), "deferred"),
    ]
    sections: list[tuple[str, list[rich.text.Text]]] = [
        ("State", [pair(*states[n : n + 2]) for n in range(0, len(states), 2)])
    ]

    sections.append(
        (
            "Title",
            [row(rich.text.Text(style, style), label) for style, label in TITLE_STYLES],
        )
    )

    priority = rich.text.Text()
    for n, (name, style) in enumerate(PRIORITY_STYLES.items()):
        priority.append(" " if n else "")
        priority.append(name, style)
    sections.append(("Priority", [row(priority, "id color")]))

    bar = rich.text.Text.assemble(
        ("━━", "green"), ("━━", "yellow"), ("────", "dim")
    )
    marks = [
        row(bar, "epic progress"),
        row(rich.text.Text("blocks 2", "yellow"), "tasks waiting"),
        row(rich.text.Text("waits X", "dim"), "dep or files"),
        row(rich.text.Text(BRANCH_GLYPH + " ", "dim"), "the branch"),
    ]
    icon_styles = {HUMAN_REVIEW_ICON: AWAITING_STYLE}
    marks += [
        row(rich.text.Text(icon, icon_styles.get(icon, "")), label, hint)
        for icon, label, hint in ICON_LEGEND
    ]
    sections.append(("Marks", marks))

    card: list[rich.text.Text] = []
    for heading, lines in sections:
        rule = rich.text.Text.assemble(
            (f" {heading} ", "bold"), ("─" * (width - len(heading) - 3), "dim")
        )
        card.append(rule)
        card += lines
        card.append(rich.text.Text(""))
    card.append(rich.text.Text(" l or esc to close", "dim"))
    return card


def _priority_key() -> rich.text.Text:
    """The priority colors, each name in the style the ids take."""

    key = rich.text.Text("id color: ", "dim")
    names = zip(PRIORITY_STYLES, ("red", "yellow", "blue", "grey"), strict=True)
    for n, (priority, name) in enumerate(names):
        key.append(", " if n else "", "dim")
        key.append(priority, PRIORITY_STYLES[priority])
        key.append(f" {name}", "dim")
    return key


def _styles_line(styles: list[str]) -> rich.text.Text:
    """The key of the title styles, with the italic word in italic."""

    line = rich.text.Text()
    for n, style in enumerate(styles):
        line.append(", " if n else "", "dim")
        if style.startswith("italic "):
            line.append("italic", "italic")
            style = style[len("italic") :]
        line.append(style, "dim")
    return line


def _starts_soon(graph: Graph) -> set[str]:
    """The leaves that start now or as soon as the running work ends.

    A leaf starts now when `next` picks it. It starts soon when `next` skips it
    for a reason that running work lifts (a write set conflict, a cap, a
    resource), or when every dependency it waits on is running. An epic is
    running when it has open leaves and all of them have a running status. A leaf
    that waits on a pending or blocked task, or on a human, is not in the set.
    """

    def running(task_id: str) -> bool:

        if graph.tasks[task_id].type != "epic":
            return graph.tasks[task_id].status in RUNNING
        open_leaves = [
            i for i in graph.leaves_under(task_id) if graph.tasks[i].status != "done"
        ]
        return bool(open_leaves) and all(
            graph.tasks[i].status in RUNNING for i in open_leaves
        )

    chosen = select_next(graph, limits=Limits())
    soon = {c["id"] for c in chosen["start"]} | {c["id"] for c in chosen["skipped"]}
    soon |= {
        i
        for i in graph.leaves
        if graph.tasks[i].status == "pending"
        and (unmet := graph.blocked_by(i))
        and all(running(d) for d in unmet)
    }
    return soon


def _schedule(graph: Graph) -> dict[str, typing.Any]:
    """The waves, the leaves ready now, the gates and the simulated rounds."""

    open_leaves = [i for i in graph.leaves if graph.tasks[i].status not in PARKED]
    rounds, stuck = _rounds(graph)
    return {
        "waves": _open_waves(graph, open_leaves),
        "ready": [t["id"] for t in select_next(graph, limits=Limits())["start"]],
        "gates": {i: graph.downstream(i) for i in open_leaves if _is_gate(graph, i)},
        "rounds": rounds,
        "stuck": stuck,
    }


def _is_gate(graph: Graph, task_id: str) -> bool:

    return graph.tasks[task_id].read_only and bool(graph.downstream(task_id))


def _open_waves(graph: Graph, ids: list[str]) -> list[list[str]]:
    """The ids grouped by their depth over the open leaves they wait for.

    A dependency on an epic stands for the open leaves below it, so an epic is
    not a level of its own.
    """

    depths: dict[str, int] = {}

    def depth(task_id: str) -> int:
        if task_id not in depths:
            behind = set().union(*(graph.leaves_under(d) for d in graph.deps[task_id]))
            open_leaves = [i for i in behind if graph.tasks[i].status not in PARKED]
            depths[task_id] = 1 + max(map(depth, open_leaves), default=0)
        return depths[task_id]

    groups: dict[int, list[str]] = {}
    for i in ids:
        groups.setdefault(depth(i), []).append(i)
    return [groups[d] for d in sorted(groups)]


def _rounds(graph: Graph) -> tuple[list[list[str]], list[str]]:
    """Start what `select_next` picks with no caps, finish it, and repeat.

    The tasks that already run finish with the first round, so the tasks that
    wait for them start in the second one. When the first pass starts nothing,
    they finish alone and no round is counted for them. The second value is
    the open leaves that never start, for example behind a human blocker.
    """

    tasks = [graph.tasks[i] for i in graph.order]
    rounds: list[list[str]] = []

    while True:
        tasks = _close_epics(tasks)
        current = Graph(tasks)
        picked = select_next(current, limits=Limits())
        started = [t["id"] for t in picked["start"]]
        finished = {*started, *(t["id"] for t in picked["running"])}
        if not finished:
            return rounds, [
                i for i in current.leaves if current.tasks[i].status not in PARKED
            ]

        rounds += [started] if started else []
        tasks = [
            dataclasses.replace(t, status="done") if t.id in finished else t
            for t in tasks
        ]


def _close_epics(tasks: list[Task]) -> list[Task]:
    """The tasks with every epic marked done whose leaves are all done.

    An epic without children is never done by its children.
    """

    graph = Graph(tasks)
    done = {i for i in graph.leaves if graph.tasks[i].status == "done"}
    return [
        dataclasses.replace(t, status="done")
        if t.type == "epic"
        and graph.leaves_under(t.id) <= done
        and graph.children[t.id]
        else t
        for t in tasks
    ]


def _finished(graph: Graph, task_id: str) -> bool:
    """Whether a task is done, or an epic with children whose leaves are all done."""

    task = graph.tasks[task_id]
    return task.status == "done" or (
        task.type == "epic"
        and bool(graph.leaves_under(task_id))
        and all(graph.tasks[i].status == "done" for i in graph.leaves_under(task_id))
    )


def _awake(graph: Graph, task_id: str, *, ready: set[str]) -> bool:
    """Whether a task, or a leaf of its epic, is running, ready or awaiting a human."""

    return any(
        graph.tasks[i].status in RUNNING or i in ready or graph.question(i) is not None
        for i in (
            {task_id}
            if graph.tasks[task_id].type != "epic"
            else graph.leaves_under(task_id)
        )
    )


def _priority(graph: Graph, task_id: str) -> str | None:
    """The priority a task or an epic shows, None for a done epic without one.

    An epic without its own key takes the best of its leaves that are not done.
    With none left it keeps the default, unless it is done: then it has no
    priority, and the tree shows `--` for it.
    """

    task = graph.tasks[task_id]
    if task.type != "epic" or task.has_priority:
        return task.priority

    best = min(
        (
            graph.tasks[i].priority
            for i in graph.leaves_under(task_id)
            if graph.tasks[i].status != "done"
        ),
        key=PRIORITIES.__getitem__,
        default=None,
    )
    if best is not None:
        return best
    return None if task.status == "done" else task.priority


def _running(graph: Graph, task_id: str) -> bool:
    """Whether a task, or a leaf under its epic, has a running status.

    An epic is never running from its own status, only from its leaves.
    """

    return any(
        graph.tasks[i].status in RUNNING
        for i in (
            graph.leaves_under(task_id)
            if graph.tasks[task_id].type == "epic"
            else [task_id]
        )
    )


def _rank(
    graph: Graph, task_id: str, *, ready: set[str]
) -> tuple[int, int, int, str, int]:
    """The sort key of a task or an epic in the tree, the lowest comes first.

    The parts are the group, the priority (see _priority), the state, the
    milestone as `next` sorts it and the file order. The group is -1 for what
    is done, which shows above everything when the tree shows done tasks,
    newest first by `done_at` (see _done_order), 0 for a running floating task or leaf, 1 for a
    running epic, 2 for the rest, whatever their priority (see _running), and 3
    for a deferred leaf, which sorts after the waiting ones.
    The state is 0 for what is ready or awaiting a human and 1 for what waits.
    An epic takes its state from its leaves.
    """

    task = graph.tasks[task_id]
    if task.status == "done":
        unstamped, when, row = _done_order(graph, task_id)
        return -1, unstamped, when, "", row

    name = _priority(graph, task_id)
    priority = len(PRIORITIES) if name is None else PRIORITIES[name]
    state = 0 if _awake(graph, task_id, ready=ready) else 1

    if task.status == "deferred":
        group = 3
    elif not _running(graph, task_id):
        group = 2
    elif task.type == "epic":
        group = 1
    else:
        group = 0

    return group, priority, state, graph.milestone(task_id) or "~", task.row


def _ranked(graph: Graph, ids: list[str], *, ready: set[str]) -> list[str]:
    """The ids sorted by _rank."""

    return sorted(ids, key=lambda i: _rank(graph, i, ready=ready))


def _collapsible(graph: Graph, task_id: str, *, ready: set[str]) -> int:
    """How many leaves an epic may hide, or zero when it stays open.

    An epic stays open when one of its leaves is running, ready or awaiting a
    human, because those are the ones a human looks at, and it also stays open
    when it has nothing left to hide. The count is the leaves of its whole
    subtree that are not done.
    """

    if graph.tasks[task_id].type != "epic":
        return 0

    if _awake(graph, task_id, ready=ready):
        return 0
    leaves = [graph.tasks[i].status for i in graph.leaves_under(task_id)]
    # A deferred leaf is a todo worth seeing, so its epic stays open.
    if "deferred" in leaves:
        return 0
    return sum(status != "done" for status in leaves)


def _bottleneck_line(task_id: str, held: dict[str, str], *, max_blocked: int) -> str:
    """The warning for a task that holds up more open tasks than the limit."""

    names = ", ".join(f"{i} ({why})" for i, why in held.items())
    return f"{task_id} holds up {len(held)} open tasks (limit {max_blocked}): {names}"


def _count(number: int, name: str) -> str:
    """A number and what it counts, plural when it is not one."""

    return f"{number} {name}{'' if number == 1 else 's'}"


def _console(width: int, color: bool) -> rich.console.Console:
    """The console the tree is printed to, a string the width wide."""

    return rich.console.Console(
        file=io.StringIO(), force_terminal=color, width=width, soft_wrap=True
    )


def _room(columns: int, parts: list[_Part]) -> int:
    """The columns left for a title once the detail parts take theirs."""

    if not parts:
        return columns

    joined = sum(p.text.cell_len for p in parts) + len(DETAIL_SEP) * len(parts)
    return columns - joined


def _draw(
    console: rich.console.Console,
    rows: list[_Row],
    legend: list[rich.text.Text],
    notes: list[str],
    *,
    width: int,
) -> str:
    """Fit every row to one line and return the text of the tree.

    A row is the head, the title, the id and the detail parts, each after a
    dot. When
    the line is too wide, the detail gives way first until the title keeps
    MIN_TITLE_ROOM columns: the branch goes first, then what the task waits
    for, then the summary of an epic, and `awaiting you` stays.
    Then the title ends with an ellipsis, and a title with no column left is
    a single ellipsis. Only a head and its marks wider than the width, which
    takes a deeply nested epic in a very narrow pane, are cut at the end, and
    the id can go with them, so no line is ever wider than the width.
    """

    for row in rows:
        if row.gap:
            console.print()
        kept = list(row.trailing)
        lead = row.head.cell_len + len(DETAIL_SEP) + row.ident.cell_len

        while (room := _room(width - lead, kept)) < MIN_TITLE_ROOM:
            worst = max((p.rank for p in kept), default=MARK)
            if worst == MARK:
                break
            kept.remove(next(p for p in reversed(kept) if p.rank == worst))

        detail = rich.text.Text()
        for part in kept:
            detail = rich.text.Text.assemble(detail, (DETAIL_SEP, "dim"), part.text)

        title = row.title
        if room < 1:
            title = rich.text.Text("…")
        elif title.cell_len > room:
            title = title.copy()
            title.truncate(room, overflow="ellipsis")

        line = rich.text.Text.assemble(
            row.head, title, (DETAIL_SEP, "dim"), row.ident, detail
        )
        line.truncate(width, overflow="ellipsis")
        console.print(line)

    console.print()
    flowed: list[rich.text.Text] = []
    for piece in legend:
        flowed += _wrap(console, piece, width)
    for note in notes:
        flowed += _wrap(console, rich.text.Text(f"({note})", "dim"), width)
    for line in flowed:
        console.print(line)
    return "\n".join(line.rstrip() for line in console.file.getvalue().splitlines())


def _wrap(
    console: rich.console.Console, text: rich.text.Text, width: int
) -> list[rich.text.Text]:
    """The text cut in lines of at most width columns.

    A word longer than the width is folded, so the caller may rely on the
    width whatever the text holds.
    """

    return list(text.wrap(console, max(width, 1), overflow="fold"))


def _terminal_width(out: typing.TextIO, asked: int | None) -> int:
    """The columns the tree fits in: the flag, the terminal, or the default."""

    if asked:
        return asked
    if not out.isatty():
        return DEFAULT_WIDTH

    return shutil.get_terminal_size((DEFAULT_WIDTH, 24)).columns


def _workspace_of(graph: Graph, task_id: str) -> dict[str, str | None]:
    """The resolved worktree and branch of a task, as JSON fields."""

    worktree, branch = graph.workspace(task_id)
    return {"worktree": worktree or None, "branch": branch or None}


def _session_of(task: Task) -> dict[str, str]:
    """The Pi session key of a task and its session dir, only when set."""

    return {
        k: v for k in ("session", "session_dir") if (v := getattr(task, k))
    }


def _session_text(task: Task) -> str:
    """The session key as `  session=... session_dir=...` for the digest."""

    return "".join(f"  {k}={v}" for k, v in _session_of(task).items())


def _is_positive_int(value: typing.Any) -> bool:
    """True when the value is a whole number above zero.

    A boolean is an int in Python and is refused, because `footprint = true`
    is a mistake, not a weight of one.
    """

    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _fail(out: typing.TextIO, errors: list[str]) -> int:
    """Write the errors as JSON and return the exit code 2."""

    out.write(json.dumps({"ok": False, "errors": errors}, indent=2) + "\n")
    return 2


def _rewrite_block(text: str, task_id: str, values: dict[str, str | None]) -> str:
    """Return the text with some lines of one `[[task]]` block rewritten.

    Every value is already written as TOML. A None deletes the line, a key
    without a line is inserted right after the `id` line, and a rewritten line
    keeps its trailing comment.
    """

    lines = text.splitlines(keepends=True)
    starts = [n for n, line in enumerate(lines) if HEADER.match(line.rstrip("\r\n"))]

    for start, stop in zip(starts, [*starts[1:], len(lines)], strict=True):
        block = lines[start:stop]
        id_row = _key_row(block, "id")
        if id_row is None or tomllib.loads(block[id_row]).get("id") != task_id:
            continue

        source = block[id_row]
        indent = " " * (len(source) - len(source.lstrip()))
        ending = "\r\n" if source.endswith("\r\n") else "\n"

        for key, value in values.items():
            row = _key_row(block, key)
            if value is None:
                if row is not None:
                    del block[row]
                continue
            if row is None:
                # The rows move with every edit, so the id row is looked up again.
                block.insert(
                    _key_row(block, "id") + 1, f"{indent}{key} = {value}{ending}"
                )
            else:
                block[row] = f"{indent}{key} = {value}{_comment(block[row])}{ending}"

        return "".join(lines[:start] + block + lines[stop:])

    msg = f"cannot find the [[task]] block of '{task_id}': edit the file by hand"
    raise ValueError(msg)


def _load_for_edit(
    path: pathlib.Path, *, seen_mtime_ns: int
) -> tuple[str, list[Task], Graph]:
    """The text, the tasks and the graph of a file that nobody changed since it was read."""

    if path.stat().st_mtime_ns != seen_mtime_ns:
        msg = f"{path.name} changed since it was read: reload and try again"
        raise ValueError(msg)

    with path.open(encoding="utf-8", newline="") as handle:
        text = handle.read()

    tasks, errors = parse(text)
    if errors:
        msg = "; ".join(errors)
        raise ValueError(msg)

    graph = Graph(tasks)
    if graph.errors:
        msg = "; ".join(graph.errors)
        raise ValueError(msg)

    return text, tasks, graph


def _replace_file(
    path: pathlib.Path, new_text: str, *, seen_mtime_ns: int, entry: str
) -> None:
    """Replace the file with the new text, and log the edit in `steering.log`."""

    descriptor, name = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    temporary = pathlib.Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(new_text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(path.stat().st_mode & 0o7777)

        # Check the mtime again, because reading and rewriting took time.
        if path.stat().st_mtime_ns != seen_mtime_ns:
            msg = f"{path.name} changed while it was rewritten: reload and try again"
            raise ValueError(msg)  # noqa: TRY301

        temporary.replace(path)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    _fsync_dir(path.parent)

    stamp = datetime.datetime.now().astimezone().isoformat(timespec="seconds")
    with (path.parent / "steering.log").open("a", encoding="utf-8") as log:
        log.write(f"{stamp} {entry}\n")


def _check_edit(
    new_text: str,
    tasks: list[Task],
    task_id: str,
    changes: dict[str, typing.Any],
    what: str,
) -> None:
    """Refuse an edit that changed anything but those fields of one task.

    The lines are rewritten without a TOML scanner, so a `[[task]]` header or a
    key inside a multi-line string could be edited by mistake. Parsing the
    result and comparing it with the expected tasks catches that.
    """

    expected = [
        dataclasses.replace(t, **changes) if t.id == task_id else t for t in tasks
    ]
    found, errors = parse(new_text)

    if errors or found != expected:
        msg = f"the edit would change more than the {what} of '{task_id}': edit the file by hand"
        raise ValueError(msg)


def _comment(line: str) -> str:
    """The spacing and the trailing `# comment` of a line, or an empty string.

    A `#` inside a string is not a comment, so the first cut that leaves valid
    TOML behind is the real one.
    """

    body = line.rstrip("\r\n")

    for cut in (n for n, c in enumerate(body) if c == "#"):
        try:
            tomllib.loads(body[:cut])
        except tomllib.TOMLDecodeError:
            continue
        return body[len(body[:cut].rstrip()) :]

    return ""


def _key_row(block: list[str], key: str) -> int | None:
    """The row of the single `key = ...` line of a block, or None when there is none."""

    rows = [n for n, line in enumerate(block) if re.match(rf"\s*{key}\s*=", line)]
    if len(rows) > 1:
        msg = f"more than one '{key}' line in a [[task]] block: edit the file by hand"
        raise ValueError(msg)

    return rows[0] if rows else None


def _check_model(name: str, model: typing.Any, effort: typing.Any) -> list[str]:
    """Check a model id and an effort level, where None is not set.

    The id is a preference that the harness may not list, and a newer id than
    this script knows is valid, so it is only checked to be one word.
    """

    errors = []
    if isinstance(model, str) and (not model.strip() or model.split() != [model]):
        errors.append(f"{name}: 'model' must be one id without spaces")
    if isinstance(effort, str) and effort not in EFFORTS:
        errors.append(f"{name}: 'effort' must be one of {list(EFFORTS)}")
    return errors


def _check_defaults(data: dict[str, typing.Any]) -> list[str]:
    """Check the header tables `[model]` and `[effort]`: one value per role."""

    errors = []
    for key in DEFAULT_KEYS:
        table = data.get(key, {})
        if not isinstance(table, dict) or not all(
            isinstance(v, str) for v in table.values()
        ):
            errors.append(f"header: '{key}' must be a table of role = string")
            continue
        for role, value in table.items():
            errors += _check_model(
                f"header {key}.{role}",
                value if key == "model" else None,
                value if key == "effort" else None,
            )
    return errors


def _done_time(text: str) -> datetime.datetime | None:
    """The time of a `done_at` value, or None when it is not ISO 8601 with an offset."""

    try:
        found = datetime.datetime.fromisoformat(text)
    except ValueError:
        return None
    return found if found.tzinfo is not None else None


def _done_order(graph: Graph, task_id: str) -> tuple[int, int, int]:
    """The sort key of a done task, newest first: stamped ones, then reverse file order."""

    task = graph.tasks[task_id]
    when = _done_time(task.done_at) if task.done_at else None
    if when is None:
        return 1, 0, -task.row
    return 0, -int(when.timestamp()), -task.row


def _check_block(number: int, block: dict[str, typing.Any]) -> list[str]:
    """Check the keys and the types of one `[[task]]` block."""

    name = block.get("id") or f"block {number}"
    errors = [f"{name}: unknown key '{k}'" for k in block if k not in KEYS]
    lists = ("deps", "integrate_after", "write_set", "exclusive", "similar", "needs")
    strings = sorted(KEYS - {*lists, "read_only", "human_review", "footprint", "stack"})

    errors += [
        f"{name}: '{k}' must be a string"
        for k in strings
        if not isinstance(block.get(k, ""), str)
    ]
    errors += [
        f"{name}: '{k}' must be a list of strings"
        for k in lists
        if not (
            isinstance(block.get(k, []), list)
            and all(isinstance(v, str) for v in block.get(k, []))
        )
    ]

    errors += [f"{name}: empty {key}" for key in ("id", "title") if not block.get(key)]
    choices = (
        ("status", "", STATUSES),
        ("priority", DEFAULT_PRIORITY, PRIORITIES),
        ("type", "task", TYPES),
    )
    errors += [
        f"{name}: {key} must be one of {sorted(allowed)}"
        for key, default, allowed in choices
        if block.get(key, default) not in allowed
    ]
    if not isinstance(block.get("read_only", False), bool):
        errors.append(f"{name}: 'read_only' must be a boolean")
    if not isinstance(block.get("human_review", False), bool):
        errors.append(f"{name}: 'human_review' must be a boolean")
    overlap_value = block.get("overlap", "hunks")
    if isinstance(overlap_value, str) and overlap_value not in OVERLAPS:
        errors.append(f"{name}: 'overlap' must be one of {sorted(OVERLAPS)}")
    done_at = block.get("done_at")
    if isinstance(done_at, str) and done_at and _done_time(done_at) is None:
        errors.append(
            f"{name}: 'done_at' must be an ISO 8601 time with an offset, "
            "like 2026-01-31T09:30:00+01:00"
        )
    if not _is_positive_int(block.get("footprint", 1)):
        errors.append(f"{name}: 'footprint' must be a positive integer")

    errors += _check_model(name, block.get("model"), block.get("effort"))

    worktree = block.get("worktree")
    if (
        isinstance(worktree, str)
        and worktree
        and not (worktree.startswith("~") or pathlib.PurePath(worktree).is_absolute())
    ):
        errors.append(f"{name}: 'worktree' must be an absolute path")

    for key in ("session", "session_dir", "base"):
        value = block.get(key)
        if isinstance(value, str) and (not value.strip() or value != value.strip()):
            errors.append(f"{name}: '{key}' must be a non-empty string without edges")
    if isinstance(block.get("session"), str) and re.search(r"\s", block["session"]):
        errors.append(f"{name}: 'session' must be one token, with no whitespace")
    if isinstance(block.get("base"), str) and re.search(r"\s", block["base"]):
        errors.append(f"{name}: 'base' must be one ref, with no whitespace")
    if isinstance(block.get("session_dir"), str) and re.search(
        r"[\r\n]", block["session_dir"]
    ):
        errors.append(f"{name}: 'session_dir' must not contain a newline")
    if "session_dir" in block and "session" not in block:
        errors.append(f"{name}: 'session_dir' needs a 'session'")

    if "stack" in block:
        stack = block["stack"]
        if block.get("type", "task") != "epic":
            errors.append(f"{name}: 'stack' is only valid on an epic")
        elif not (
            (isinstance(stack, str) and stack.strip()) or _is_positive_int(stack)
        ):
            errors.append(f"{name}: 'stack' must be a non-empty name or a number")

    exclusive = block.get("exclusive", [])
    if isinstance(exclusive, list) and any(
        isinstance(v, str) and not v.strip() for v in exclusive
    ):
        errors.append(f"{name}: an entry of 'exclusive' is empty")
    needs = block.get("needs", [])
    if isinstance(needs, list) and any(
        isinstance(v, str) and not v.strip() for v in needs
    ):
        errors.append(f"{name}: an entry of 'needs' is empty")
    if "blocker" in block and block.get("status") != "blocked":
        errors.append(f"{name}: 'blocker' needs status = \"blocked\"")
    errors += [
        f"{name}: empty {key}" for key in ("blocker", "answer") if key in block and not block[key]
    ]

    return errors


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
