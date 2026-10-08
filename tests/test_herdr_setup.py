from __future__ import annotations

import pathlib
import subprocess

import pytest
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".local/libexec/dotfiles/herdr-setup.sh"

SKILL = "---\nname: herdr\n---\n\nbody\n"

# Stands in for herdr: the integration is "current" once the marker file exists.
FAKE_HERDR = """#!/bin/sh
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
    "integration status")
        if [ -e "$FAKE_STATE/installed" ]; then echo "copilot: current (v3)"; else echo "copilot: not installed"; fi
        ;;
    "integration install")
        [ -n "$FAKE_FAIL" ] && exit 3
        touch "$FAKE_STATE/installed"
        ;;
    "--skill ")
        printf '%s' "$FAKE_SKILL"
        ;;
esac
"""


@pytest.fixture
def env(tmp_path: pathlib.Path) -> dict[str, str]:
    home = tmp_path / "home"
    (home / ".pixi/bin").mkdir(parents=True)
    herdr = home / ".pixi/bin/herdr"
    herdr.write_text(FAKE_HERDR)
    herdr.chmod(0o755)
    state = tmp_path / "state"
    state.mkdir()
    return {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "FAKE_LOG": str(tmp_path / "herdr.log"),
        "FAKE_STATE": str(state),
        "FAKE_SKILL": SKILL,
    }


def _run(env: dict[str, str], **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT)], env={**env, **extra}, capture_output=True, text=True
    )


def _calls(env: dict[str, str]) -> list[str]:
    log = pathlib.Path(env["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def _skill_path(env: dict[str, str]) -> pathlib.Path:
    return pathlib.Path(env["HOME"]) / ".agents/skills/herdr/SKILL.md"


def test_installs_the_hook_and_writes_the_skill(env: dict[str, str]) -> None:
    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "integration install copilot" in _calls(env)
    assert _skill_path(env).read_text() == SKILL


def test_second_run_changes_nothing(env: dict[str, str]) -> None:
    _run(env)
    pathlib.Path(env["FAKE_LOG"]).unlink()

    result = _run(env)

    assert result.returncode == 0
    assert result.stdout == ""
    assert "integration install copilot" not in _calls(env)


def test_updated_skill_replaces_the_old_one(env: dict[str, str]) -> None:
    _run(env)

    _run(env, FAKE_SKILL=SKILL + "more\n")

    assert _skill_path(env).read_text() == SKILL + "more\n"


def test_unexpected_skill_output_keeps_the_existing_skill(
    env: dict[str, str],
) -> None:
    _run(env)

    result = _run(env, FAKE_SKILL="not a skill\n")

    assert result.returncode == 1
    assert "did not print a skill file" in result.stderr
    assert _skill_path(env).read_text() == SKILL


def test_failing_hook_install_fails_but_still_writes_the_skill(
    env: dict[str, str],
) -> None:
    result = _run(env, FAKE_FAIL="1")

    assert result.returncode == 1
    assert _skill_path(env).read_text() == SKILL


def test_does_nothing_without_herdr(env: dict[str, str]) -> None:
    (pathlib.Path(env["HOME"]) / ".pixi/bin/herdr").unlink()

    result = _run(env)

    assert result.returncode == 0
    assert not _skill_path(env).exists()
