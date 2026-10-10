from __future__ import annotations

import json
import pathlib
import subprocess
import sys

import pytest
from conftest import REPO_ROOT

HOOK = REPO_ROOT / ".copilot/hooks/worktree-guard.py"
LAUNCHER = json.loads((REPO_ROOT / ".copilot/hooks/worktree-guard.json").read_text())["hooks"][
    "preToolUse"
][0]["bash"]


@pytest.mark.parametrize(
    "command",
    [
        "git worktree add ../x -b feat",
        "git -C /repo worktree add .worktrees/x",
        "cd /repo && git worktree add x",
        "git status; git worktree add x",
        "GIT_TRACE=1 git worktree add x",
    ],
)
def test_git_worktree_add_is_denied_with_a_pointer_to_wt(tmp_path: pathlib.Path, command: str) -> None:
    decision = _run_hook(tmp_path, _payload(command)) or {}

    assert decision["permissionDecision"] == "deny"
    assert "wt switch --create" in decision["permissionDecisionReason"]


@pytest.mark.parametrize(
    "command",
    [
        "git worktree list",
        "git worktree remove x",
        "wt switch --create feat --no-cd",
        "echo git worktree add",
        "git commit -m 'avoid git worktree add'",
        "git status",
    ],
)
def test_other_commands_are_left_to_the_normal_flow(tmp_path: pathlib.Path, command: str) -> None:
    assert _run_hook(tmp_path, _payload(command)) is None


def test_other_tools_are_ignored(tmp_path: pathlib.Path) -> None:
    payload = {"toolName": "view", "toolArgs": {"command": "git worktree add x"}}

    assert _run_hook(tmp_path, payload) is None


def test_the_command_is_read_from_a_json_string_and_the_vscode_payload(tmp_path: pathlib.Path) -> None:
    string_args = {"toolName": "bash", "toolArgs": json.dumps({"command": "git worktree add x"})}
    vscode = {"tool_name": "bash", "tool_input": {"command": "git worktree add x"}}

    assert (_run_hook(tmp_path, string_args) or {})["permissionDecision"] == "deny"
    assert (_run_hook(tmp_path, vscode) or {})["permissionDecision"] == "deny"


def test_without_wt_the_command_is_allowed(tmp_path: pathlib.Path) -> None:
    assert _run_hook(tmp_path, _payload("git worktree add x"), with_wt=False) is None


def test_the_escape_variable_allows_the_command(tmp_path: pathlib.Path) -> None:
    payload = _payload("git worktree add x")

    assert _run_hook(tmp_path, payload, extra_env={"DOTFILES_ALLOW_GIT_WORKTREE": "1"}) is None


def test_the_launcher_never_fails_on_bad_input(tmp_path: pathlib.Path) -> None:
    home = _home(tmp_path)
    result = subprocess.run(
        ["bash", "-c", LAUNCHER],
        input="not json",
        env=_env(home, with_wt=True),
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert result.stdout == ""


def _payload(command: str) -> dict:
    return {"toolName": "bash", "toolArgs": {"command": command}}


def _home(tmp_path: pathlib.Path) -> pathlib.Path:
    home = tmp_path / "home"
    (home / ".copilot/hooks").mkdir(parents=True, exist_ok=True)
    return home


def _env(home: pathlib.Path, *, with_wt: bool, extra_env: dict | None = None) -> dict:
    bin_dir = home / "bin"
    bin_dir.mkdir(exist_ok=True)

    if with_wt:
        wt = bin_dir / "wt"
        wt.write_text("#!/bin/sh\n")
        wt.chmod(0o755)

    # The launcher executes the hook through its shebang, which needs pixi, so the
    # test installs a wrapper that runs the hook with the current interpreter instead.
    hook = home / ".copilot/hooks/worktree-guard.py"
    hook.unlink(missing_ok=True)
    hook.write_text(f"#!/bin/sh\nexec {sys.executable} {HOOK}\n")
    hook.chmod(0o755)

    path = f"{bin_dir}:/usr/bin:/bin" if with_wt else "/usr/bin:/bin"
    return {"HOME": str(home), "PATH": path, **(extra_env or {})}


def _run_hook(
    tmp_path: pathlib.Path,
    payload: dict,
    *,
    with_wt: bool = True,
    extra_env: dict | None = None,
) -> dict | None:
    home = _home(tmp_path)
    result = subprocess.run(
        [sys.executable, str(HOOK)],
        input=json.dumps(payload),
        env=_env(home, with_wt=with_wt, extra_env=extra_env),
        capture_output=True,
        text=True,
        check=True,
    )

    return json.loads(result.stdout) if result.stdout.strip() else None
