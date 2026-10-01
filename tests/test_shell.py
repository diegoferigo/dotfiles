"""Tests for the ephemeral shell (`dotfiles --shell`)."""

from __future__ import annotations

import os
import pathlib
import pty
import signal
import subprocess
import sys
import time
import types

import pytest
from conftest import BOOTSTRAP_PY, REPO_ROOT

_DRIVER = """
import importlib.machinery, importlib.util, os, pathlib, sys

loader = importlib.machinery.SourceFileLoader("dotfiles", sys.argv[1])
spec = importlib.util.spec_from_file_location("dotfiles", sys.argv[1], loader=loader)
mod = importlib.util.module_from_spec(spec)
sys.modules["dotfiles"] = mod
spec.loader.exec_module(mod)
raise SystemExit(
    mod.run_ephemeral_shell(
        base=pathlib.Path(sys.argv[2]),
        repo_uri=sys.argv[3],
        skip_tools=True,
        use_cache=True,
        environ=os.environ,
        shell_cmd=[
            "bash",
            "-c",
            'mkdir -p "$GH_CONFIG_DIR" "$CLOUDSDK_CONFIG" "$(dirname "$RATTLER_AUTH_FILE")" && '
            'touch "$GH_CONFIG_DIR/hosts.yml" "$CLOUDSDK_CONFIG/credentials.db" '
            '"$RATTLER_AUTH_FILE"; '
            'echo $$ > "$MARK.pid"; echo "$HOME" > "$MARK"; exec sleep 60',
        ],
    )
)
"""


def _dead_pid() -> int:
    proc = subprocess.Popen(["true"])
    proc.wait()
    return proc.pid


def _start_session(base: pathlib.Path, marker: pathlib.Path) -> subprocess.Popen[bytes]:
    env = {**os.environ, "MARK": str(marker)}
    return subprocess.Popen(
        [sys.executable, "-c", _DRIVER, str(BOOTSTRAP_PY), str(base), f"file://{REPO_ROOT}"],
        env=env,
    )


def _wait_for(path: pathlib.Path, timeout: float = 90.0) -> None:
    deadline = time.monotonic() + timeout
    while not path.exists():
        assert time.monotonic() < deadline, f"timed out waiting for {path}"
        time.sleep(0.2)


def _process_is_running(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def _runs(base: pathlib.Path) -> list[pathlib.Path]:
    return sorted((base / "run").glob("*"))


def test_ephemeral_base_follows_xdg_cache_home(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    home = tmp_path / "home"
    assert dotfiles_module.ephemeral_base(home, {}) == home / ".cache/diegoferigo-dotfiles"
    xdg = tmp_path / "xdg"
    assert (
        dotfiles_module.ephemeral_base(home, {"XDG_CACHE_HOME": str(xdg)})
        == xdg / "diegoferigo-dotfiles"
    )


def test_ephemeral_env_redirects_every_user_path(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    home, cache = tmp_path / "home", tmp_path / "cache"
    env = dotfiles_module.ephemeral_env(
        home=home,
        cache=cache,
        environ={
            "HOME": "/guest",
            "PIXI_HOME": "/guest/.pixi",
            "XDG_CONFIG_HOME": "/guest/.config",
            "XDG_DATA_HOME": "/guest/.local/share",
            "XDG_STATE_HOME": "/guest/.local/state",
            "PIXI_ENVIRONMENT_NAME": "temp:git",
            "CONDA_PREFIX": "/guest/env",
            "CONDA_SHLVL": "1",
            "PATH": "/usr/bin",
        },
    )
    assert env["HOME"] == str(home)
    assert env["XDG_CACHE_HOME"] == str(cache)
    assert env["RATTLER_CACHE_DIR"] == str(cache / "rattler")
    assert env["PIXI_CACHE_DIR"] == str(cache / "pixi")
    assert env["GH_CONFIG_DIR"] == str(cache / "gh")
    assert env["CLOUDSDK_CONFIG"] == str(cache / "gcloud")
    assert env["RATTLER_AUTH_FILE"] == str(cache / "rattler" / "credentials.json")
    assert env["PATH"] == "/usr/bin"
    for name in (
        "PIXI_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "PIXI_ENVIRONMENT_NAME",
        "CONDA_PREFIX",
        "CONDA_SHLVL",
    ):
        assert name not in env


def test_controlling_tty_is_only_used_when_stdin_is_not_a_terminal(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    master_fd, slave_fd = pty.openpty()
    read_fd, write_fd = os.pipe()
    try:
        tty_path = os.ttyname(slave_fd)
        opened = dotfiles_module._controlling_tty(tty_path, read_fd)
        assert opened is not None
        assert os.isatty(opened.fileno())
        opened.close()
        assert dotfiles_module._controlling_tty(tty_path, slave_fd) is None
        assert dotfiles_module._controlling_tty(str(tmp_path / "missing"), read_fd) is None
    finally:
        for fd in (master_fd, slave_fd, read_fd, write_fd):
            os.close(fd)


def test_sweep_removes_only_runs_of_dead_processes(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    runs = tmp_path / "run"
    dead = runs / f"{_dead_pid()}-abc"
    alive = runs / f"{os.getpid()}-abc"
    for run_dir in (dead, alive):
        (run_dir / "home").mkdir(parents=True)

    removed = dotfiles_module.sweep_stale_runs(tmp_path)

    assert removed == [dead]
    assert not dead.exists()
    assert alive.exists()


def test_shared_auth_is_removed_only_when_no_shell_is_left(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    auth_dirs = [tmp_path / "cache" / name for name in ("gh", "gcloud", "rattler")]
    for auth_dir in auth_dirs:
        auth_dir.mkdir(parents=True)
    live = tmp_path / "run" / f"{os.getpid()}-live"
    live.mkdir(parents=True)
    dotfiles_module._remove_shared_auth_if_unused(tmp_path)
    assert all(auth_dir.exists() for auth_dir in auth_dirs)

    live.rmdir()
    (tmp_path / "run" / f"{_dead_pid()}-stale").mkdir()
    dotfiles_module._remove_shared_auth_if_unused(tmp_path)
    assert not any(auth_dir.exists() for auth_dir in auth_dirs)


@pytest.mark.parametrize("use_cache", [True, False])
def test_shell_runs_in_a_throwaway_home_and_leaves_no_run_behind(
    use_cache: bool,
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    base = tmp_path / "base"
    marker = tmp_path / "marker"
    probe = (
        f'echo "$HOME" > {marker}; '
        'test -f "$HOME/.bashrc" && test -f "$HOME/.local/bin/dotfiles"'
    )

    status = dotfiles_module.run_ephemeral_shell(
        base=base,
        repo_uri=f"file://{REPO_ROOT}",
        skip_tools=True,
        use_cache=use_cache,
        environ=os.environ,
        shell_cmd=["bash", "-c", probe],
    )

    assert status == 0
    session_home = pathlib.Path(marker.read_text().strip())
    assert session_home.parent.parent == base / "run"
    assert not session_home.exists()
    assert _runs(base) == []
    assert (base / "cache").exists() is use_cache


def _clone_with_probe_branch(tmp_path: pathlib.Path) -> pathlib.Path:
    """A clone of the repo with a 'probe' branch that adds one tracked file."""

    repo = tmp_path / "source"
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "clone", "--quiet", str(REPO_ROOT), str(repo)], check=True)
    subprocess.run([*git, "switch", "--quiet", "-c", "probe"], check=True)
    (repo / "probe.txt").write_text("probe\n")
    subprocess.run([*git, "add", "probe.txt"], check=True)
    subprocess.run([*git, "commit", "--quiet", "-m", "probe"], check=True)
    subprocess.run([*git, "switch", "--quiet", "-"], check=True)
    return repo


@pytest.mark.parametrize("branch", [None, "probe"])
def test_shell_clones_the_requested_branch(
    branch: str | None,
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    source = _clone_with_probe_branch(tmp_path)
    marker = tmp_path / "marker"

    status = dotfiles_module.run_ephemeral_shell(
        base=tmp_path / "base",
        repo_uri=f"file://{source}",
        skip_tools=True,
        use_cache=True,
        environ=os.environ,
        shell_cmd=["bash", "-c", f'test -f "$HOME/probe.txt" && echo yes > {marker}; true'],
        branch=branch,
    )

    assert status == 0
    assert marker.exists() is (branch == "probe")


def test_failing_shell_status_is_returned_and_cleaned_up(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    status = dotfiles_module.run_ephemeral_shell(
        base=tmp_path,
        repo_uri=f"file://{REPO_ROOT}",
        skip_tools=True,
        use_cache=True,
        environ=os.environ,
        shell_cmd=["bash", "-c", "exit 7"],
    )

    assert status == 7
    assert _runs(tmp_path) == []


def test_two_sessions_run_side_by_side_and_each_cleans_up_on_hangup(
    tmp_path: pathlib.Path,
) -> None:
    base = tmp_path / "base"
    first_marker, second_marker = tmp_path / "first", tmp_path / "second"
    first = _start_session(base, first_marker)
    second = _start_session(base, second_marker)
    try:
        _wait_for(first_marker)
        _wait_for(second_marker)
        first_home = pathlib.Path(first_marker.read_text().strip())
        second_home = pathlib.Path(second_marker.read_text().strip())
        assert first_home != second_home
        assert first_home.exists() and second_home.exists()
        gh_config, gcloud_config = base / "cache" / "gh", base / "cache" / "gcloud"
        rattler_config = base / "cache" / "rattler"
        assert (gh_config / "hosts.yml").exists()
        assert (gcloud_config / "credentials.db").exists()
        assert (rattler_config / "credentials.json").exists()

        first_shell = int(pathlib.Path(f"{first_marker}.pid").read_text())
        first.send_signal(signal.SIGHUP)
        first.wait(timeout=30)
        assert not _process_is_running(first_shell)
        assert not first_home.exists()
        assert second_home.exists()
        assert (gh_config / "hosts.yml").exists()
        assert (gcloud_config / "credentials.db").exists()
        assert (rattler_config / "credentials.json").exists()

        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)
        assert not second_home.exists()
        assert _runs(base) == []
        assert not any(d.exists() for d in (gh_config, gcloud_config, rattler_config))
    finally:
        for proc in (first, second):
            if proc.poll() is None:
                proc.kill()
