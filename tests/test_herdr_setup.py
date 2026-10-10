from __future__ import annotations

import pathlib
import re
import subprocess

import pytest
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".local/libexec/dotfiles/herdr-setup.sh"

_PLUGIN_REF_MATCH = re.search(
    r"^worktrunk_plugin_ref=(\w+)$", SCRIPT.read_text(), re.M
)
assert _PLUGIN_REF_MATCH is not None
PLUGIN_REF = _PLUGIN_REF_MATCH.group(1)

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
        mkdir -p "$HOME/.copilot"
        echo "herdr-added" >> "$HOME/.copilot/settings.json"
        ;;
    "plugin list")
        [ -e "$FAKE_STATE/plugin" ] && echo "- worktrunk (Worktrunk) enabled [github:devashish2203/herdr-worktrunk@$(cat "$FAKE_STATE/plugin")]"
        ;;
    "plugin install")
        while [ $# -gt 0 ]; do [ "$1" = "--ref" ] && echo "$2" > "$FAKE_STATE/plugin"; shift; done
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
    wt = home / ".pixi/bin/wt"
    wt.write_text("#!/bin/sh\n")
    wt.chmod(0o755)
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


def test_tracked_hook_entry_is_not_repeated_by_the_install(
    env: dict[str, str],
) -> None:
    """Herdr appends its own entry; the tracked settings file is put back."""

    settings = pathlib.Path(env["HOME"]) / ".copilot/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("tracked herdr-agent-state.sh\n")

    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert "integration install copilot" in _calls(env)
    assert settings.read_text() == "tracked herdr-agent-state.sh\n"


def test_settings_without_the_hook_keep_what_herdr_wrote(
    env: dict[str, str],
) -> None:
    """Without a tracked entry the install result is kept."""

    settings = pathlib.Path(env["HOME"]) / ".copilot/settings.json"

    _run(env)

    assert settings.read_text() == "herdr-added\n"


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


def test_installs_the_worktrunk_plugin_at_the_pinned_ref(env: dict[str, str]) -> None:
    result = _run(env)

    assert result.returncode == 0, result.stderr
    assert (pathlib.Path(env["FAKE_STATE"]) / "plugin").read_text().strip() == (
        PLUGIN_REF
    )
    assert any(
        call.startswith("plugin install") and "devashish2203/herdr-worktrunk" in call
        for call in _calls(env)
    )


def test_pinned_plugin_is_not_reinstalled(env: dict[str, str]) -> None:
    _run(env)
    pathlib.Path(env["FAKE_LOG"]).unlink()

    _run(env)

    assert not any(call.startswith("plugin install") for call in _calls(env))


def test_plugin_at_another_ref_is_replaced(env: dict[str, str]) -> None:
    plugin = pathlib.Path(env["FAKE_STATE"]) / "plugin"
    plugin.write_text("0000000\n")

    _run(env)

    assert plugin.read_text().strip() == PLUGIN_REF


def test_plugin_is_skipped_without_worktrunk(env: dict[str, str]) -> None:
    (pathlib.Path(env["HOME"]) / ".pixi/bin/wt").unlink()

    result = _run(env)

    assert result.returncode == 0
    assert not any(call.startswith("plugin install") for call in _calls(env))
