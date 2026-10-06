from __future__ import annotations

import getpass
import pathlib
import subprocess

import pytest
from conftest import REPO_ROOT

OPEN_ZED = REPO_ROOT / ".agents/skills/opening-zed/scripts/open-zed.sh"
HUNK_COMMAND = REPO_ROOT / ".agents/skills/opening-hunk/scripts/hunk-command.sh"
SSH_CONNECTION = "192.0.2.10 50000 198.51.100.10 22"


@pytest.fixture
def fake_path(tmp_path: pathlib.Path) -> pathlib.Path:
    bindir = tmp_path / "bin"
    bindir.mkdir()
    # A plain shell as every ancestor: the tests must not depend on running
    # inside an SSH login.
    ps = bindir / "ps"
    ps.write_text('#!/bin/sh\ncase "$*" in *comm*) echo bash;; *) echo ;; esac\n')
    hunk = bindir / "hunk"
    hunk.write_text("#!/bin/sh\necho v1\n")
    for tool in (ps, hunk):
        tool.chmod(0o755)
    return bindir


def _run(
    script: pathlib.Path,
    fake_path: pathlib.Path,
    *args: str,
    **env: str,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(script), *args],
        env={"PATH": f"{fake_path}:/usr/bin:/bin", "HOME": str(fake_path), **env},
        capture_output=True,
        text=True,
        cwd=fake_path,
    )


def test_open_zed_prints_the_command_in_a_local_session(
    fake_path: pathlib.Path,
) -> None:
    result = _run(OPEN_ZED, fake_path, "--print", "/srv")

    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["context: local", "zed /srv"]


def test_open_zed_prints_the_ssh_url_over_ssh(fake_path: pathlib.Path) -> None:
    result = _run(OPEN_ZED, fake_path, "/srv", SSH_CONNECTION=SSH_CONNECTION)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "context: ssh"
    assert lines[-1].startswith("zed ssh://")
    assert lines[-1].endswith("@198.51.100.10/srv")


def test_open_zed_keeps_a_non_default_port(fake_path: pathlib.Path) -> None:
    result = _run(
        OPEN_ZED,
        fake_path,
        "/srv",
        SSH_CONNECTION="192.0.2.10 50000 198.51.100.10 2222",
    )

    assert result.stdout.splitlines()[-1].endswith("@198.51.100.10:2222/srv")


def test_open_zed_leaves_the_port_to_a_host_alias(fake_path: pathlib.Path) -> None:
    result = _run(
        OPEN_ZED,
        fake_path,
        "/srv",
        SSH_CONNECTION="192.0.2.10 50000 198.51.100.10 2222",
        ZED_OPEN_REMOTE_HOST="ws",
    )

    assert result.stdout.splitlines()[-1].endswith("@ws/srv")


def test_open_zed_brackets_an_ipv6_host(fake_path: pathlib.Path) -> None:
    result = _run(
        OPEN_ZED,
        fake_path,
        "/srv",
        SSH_CONNECTION="2001:db8::1 50000 2001:db8::2 22",
    )

    assert result.stdout.splitlines()[-1].endswith("@\\[2001:db8::2\\]/srv")


def test_open_zed_quotes_a_path_with_spaces(
    fake_path: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    target = tmp_path / "a b"
    target.mkdir()

    result = _run(OPEN_ZED, fake_path, "--print", str(target))

    assert result.stdout.splitlines()[-1].startswith("zed ")
    assert "\\ " in result.stdout


def test_hunk_command_prints_the_local_command(fake_path: pathlib.Path) -> None:
    result = _run(HUNK_COMMAND, fake_path, "/srv")

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert lines[0] == "context: local"
    assert lines[-1] == f"cd /srv && {fake_path}/hunk diff"


def test_hunk_command_wraps_the_command_in_ssh_over_ssh(
    fake_path: pathlib.Path,
) -> None:
    result = _run(
        HUNK_COMMAND,
        fake_path,
        "/srv",
        SSH_CONNECTION="192.0.2.10 50000 198.51.100.10 2222",
    )

    last = result.stdout.splitlines()[-1]
    assert last.startswith("ssh -t -p 2222 ")
    assert "@198.51.100.10 'cd /srv && " in last


def test_hunk_command_leaves_the_port_to_a_host_alias(fake_path: pathlib.Path) -> None:
    result = _run(
        HUNK_COMMAND,
        fake_path,
        "/srv",
        SSH_CONNECTION="192.0.2.10 50000 198.51.100.10 2222",
        HUNK_REMOTE_HOST="ws",
    )

    assert result.stdout.splitlines()[-1].startswith("ssh -t ")
    assert " -p " not in result.stdout.splitlines()[-1]


def test_hunk_command_quotes_a_hostile_host_override(fake_path: pathlib.Path) -> None:
    result = _run(
        HUNK_COMMAND,
        fake_path,
        "/srv",
        SSH_CONNECTION=SSH_CONNECTION,
        HUNK_REMOTE_HOST="host; touch /srv/pwned; #",
    )

    last = result.stdout.splitlines()[-1]
    user = getpass.getuser()
    assert last.startswith(f"ssh -t '{user}@host; touch /srv/pwned; #' 'cd /srv && ")


def test_hunk_command_rejects_a_non_numeric_port(fake_path: pathlib.Path) -> None:
    result = _run(
        HUNK_COMMAND,
        fake_path,
        "/srv",
        SSH_CONNECTION="192.0.2.10 50000 198.51.100.10 22;id",
    )

    assert result.returncode == 1
    assert "invalid SSH port" in result.stderr


def test_hunk_command_fails_when_hunk_is_missing(fake_path: pathlib.Path) -> None:
    (fake_path / "hunk").unlink()

    result = _run(HUNK_COMMAND, fake_path, "/srv")

    assert result.returncode == 1
    assert "hunk is not installed" in result.stderr
