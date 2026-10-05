from __future__ import annotations

import os
import pathlib
import subprocess

import pytest
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".local/libexec/dotfiles/lazy-tools"

# Stands in for https://hunk.dev/install.sh: installs a stub binary and logs the
# environment it saw, so the tests need no network.
FAKE_INSTALLER = """#!/bin/sh
version="${1:-0.1.0}"
mkdir -p "$HOME/.hunk/bin"
printf '#!/bin/sh\\necho "v%s"\\n' "$version" > "$HOME/.hunk/bin/hunk"
chmod +x "$HOME/.hunk/bin/hunk"
echo "NO_MODIFY_PATH=$HUNK_NO_MODIFY_PATH DO_NOT_TRACK=$DO_NOT_TRACK version=$version" >> "$FAKE_LOG"
"""


@pytest.fixture
def env(tmp_path: pathlib.Path) -> dict[str, str]:
    home = tmp_path / "home"
    home.mkdir()
    installer = tmp_path / "install.sh"
    installer.write_text(FAKE_INSTALLER)
    return {
        "HOME": str(home),
        "XDG_STATE_HOME": str(home / ".local/state"),
        # System tools only: a real hunk under the user's HOME must not be seen.
        "PATH": "/usr/bin:/bin",
        "LAZY_TOOLS_HUNK_INSTALLER_URL": f"file://{installer}",
        "FAKE_LOG": str(tmp_path / "installer.log"),
    }


def _run(
    env: dict[str, str], *args: str, **extra: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        env={**env, **extra},
        capture_output=True,
        text=True,
    )


def _home(env: dict[str, str]) -> pathlib.Path:
    return pathlib.Path(env["HOME"])


def _installer_calls(env: dict[str, str]) -> list[str]:
    log = pathlib.Path(env["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []


def _record(env: dict[str, str]) -> pathlib.Path:
    return pathlib.Path(env["XDG_STATE_HOME"]) / "dotfiles/lazy-tools/hunk"


def _fake_external_hunk(env: dict[str, str]) -> None:
    binary = _home(env) / ".hunk/bin/hunk"
    binary.parent.mkdir(parents=True)
    binary.write_text('#!/bin/sh\necho "v9.9.9"\n')
    binary.chmod(0o755)


def test_script_is_executable() -> None:
    assert os.access(SCRIPT, os.X_OK)


def test_install_installs_a_missing_tool_and_records_it(env: dict[str, str]) -> None:
    result = _run(env, "install")

    assert result.returncode == 0, result.stderr
    assert (_home(env) / ".hunk/bin/hunk").is_file()
    assert "version=0.1.0" in _record(env).read_text()
    # The upstream installer must neither edit the shell files nor phone home.
    assert _installer_calls(env) == ["NO_MODIFY_PATH=1 DO_NOT_TRACK=1 version=0.1.0"]


def test_install_is_the_default_action(env: dict[str, str]) -> None:
    assert _run(env).returncode == 0
    assert _record(env).is_file()


def test_install_twice_runs_the_installer_once(env: dict[str, str]) -> None:
    _ = _run(env, "install")
    result = _run(env, "install")

    assert result.returncode == 0
    assert "managed" in result.stdout
    assert len(_installer_calls(env)) == 1


def test_install_never_upgrades_without_a_pin(env: dict[str, str]) -> None:
    _ = _run(env, "install", HUNK_VERSION="0.1.0")
    result = _run(env, "install")

    assert "0.1.0" in result.stdout
    assert len(_installer_calls(env)) == 1


def test_install_leaves_an_external_tool_alone(env: dict[str, str]) -> None:
    _fake_external_hunk(env)

    result = _run(env, "install", HUNK_VERSION="0.2.0")

    assert result.returncode == 0
    assert "external" in result.stdout
    assert _installer_calls(env) == []
    assert not _record(env).exists()


def test_a_pin_moves_a_managed_install_to_that_version(env: dict[str, str]) -> None:
    _ = _run(env, "install")
    result = _run(env, "install", HUNK_VERSION="0.2.0")

    assert result.returncode == 0, result.stderr
    assert _installer_calls(env)[-1].endswith("version=0.2.0")
    assert "version=0.2.0" in _record(env).read_text()


def test_install_again_after_the_user_removed_the_tool(env: dict[str, str]) -> None:
    _ = _run(env, "install")
    (_home(env) / ".hunk/bin/hunk").unlink()

    result = _run(env, "install")

    assert result.returncode == 0, result.stderr
    assert (_home(env) / ".hunk/bin/hunk").is_file()
    assert len(_installer_calls(env)) == 2


def test_uninstall_removes_what_it_installed(env: dict[str, str]) -> None:
    _ = _run(env, "install")

    result = _run(env, "uninstall")

    assert result.returncode == 0
    assert not (_home(env) / ".hunk").exists()
    assert not _record(env).exists()


def test_uninstall_leaves_an_external_tool_alone(env: dict[str, str]) -> None:
    _fake_external_hunk(env)

    result = _run(env, "uninstall")

    assert result.returncode == 0
    assert (_home(env) / ".hunk/bin/hunk").is_file()


def test_status_reports_missing_managed_and_external(env: dict[str, str]) -> None:
    assert _run(env, "status").stdout.strip() == "hunk: missing"

    _ = _run(env, "install")
    assert _run(env, "status").stdout.strip() == "hunk: managed 0.1.0"

    _ = _run(env, "uninstall")
    _fake_external_hunk(env)
    assert _run(env, "status").stdout.strip() == "hunk: external 9.9.9"


def test_an_installer_that_returns_a_web_page_installs_nothing(
    env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    page = tmp_path / "page.html"
    page.write_text("<html>502 Bad Gateway</html>\n")

    result = _run(env, "install", LAZY_TOOLS_HUNK_INSTALLER_URL=f"file://{page}")

    assert result.returncode == 1
    assert "unexpected hunk installer content" in result.stderr
    assert not (_home(env) / ".hunk").exists()
    assert not _record(env).exists()


def test_an_unreachable_installer_fails_without_a_record(
    env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    result = _run(
        env,
        "install",
        LAZY_TOOLS_HUNK_INSTALLER_URL=f"file://{tmp_path}/missing.sh",
    )

    assert result.returncode == 1
    assert not _record(env).exists()


def test_an_installer_that_installs_nothing_fails_without_a_record(
    env: dict[str, str], tmp_path: pathlib.Path
) -> None:
    noop = tmp_path / "noop.sh"
    noop.write_text("#!/bin/sh\nexit 0\n")

    result = _run(env, "install", LAZY_TOOLS_HUNK_INSTALLER_URL=f"file://{noop}")

    assert result.returncode == 1
    assert "did not install correctly" in result.stderr
    assert not _record(env).exists()


def test_unknown_tool_and_unknown_action_are_rejected(env: dict[str, str]) -> None:
    unknown_tool = _run(env, "install", "nope")
    assert unknown_tool.returncode == 1
    assert "unknown tool: nope" in unknown_tool.stderr

    unknown_action = _run(env, "frobnicate")
    assert unknown_action.returncode == 2
    assert "usage:" in unknown_action.stderr


def test_an_installer_override_that_is_not_a_file_url_is_refused(
    env: dict[str, str],
) -> None:
    result = _run(
        env, "install", LAZY_TOOLS_HUNK_INSTALLER_URL="http://127.0.0.1:1/install.sh"
    )

    assert result.returncode == 1
    assert "must be a file:// URL" in result.stderr
    assert not (_home(env) / ".hunk").exists()
    assert not _record(env).exists()
