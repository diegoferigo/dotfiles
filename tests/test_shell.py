"""Tests for the ephemeral shell (`dotfiles --shell`)."""

from __future__ import annotations

import os
import pathlib
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
        shell_cmd=["bash", "-c", 'echo "$HOME" > "$MARK"; exec sleep 60'],
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
    stand_in = tmp_path / "tty"
    stand_in.write_text("")
    read_fd, write_fd = os.pipe()
    try:
        opened = dotfiles_module._controlling_tty(str(stand_in), read_fd)
        assert opened is not None
        opened.close()
        assert dotfiles_module._controlling_tty(str(tmp_path / "missing"), read_fd) is None
    finally:
        os.close(read_fd)
        os.close(write_fd)


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


def test_purge_cache_refuses_while_a_shell_is_running(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "run" / f"{os.getpid()}-abc").mkdir(parents=True)

    assert dotfiles_module.purge_ephemeral_cache(tmp_path) == 1
    assert (tmp_path / "cache").exists()


def test_purge_cache_removes_cache_and_stale_runs(
    tmp_path: pathlib.Path, dotfiles_module: types.ModuleType
) -> None:
    (tmp_path / "cache").mkdir()
    (tmp_path / "run" / f"{_dead_pid()}-abc").mkdir(parents=True)

    assert dotfiles_module.purge_ephemeral_cache(tmp_path) == 0
    assert not (tmp_path / "cache").exists()
    assert _runs(tmp_path) == []


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

        first.send_signal(signal.SIGHUP)
        first.wait(timeout=30)
        assert not first_home.exists()
        assert second_home.exists()

        second.send_signal(signal.SIGTERM)
        second.wait(timeout=30)
        assert not second_home.exists()
        assert _runs(base) == []
    finally:
        for proc in (first, second):
            if proc.poll() is None:
                proc.kill()
