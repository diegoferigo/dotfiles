from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import time
import types
from collections.abc import Generator

import pytest

REPO_ROOT = pathlib.Path(__file__).parent.parent.resolve()
BOOTSTRAP_PY = REPO_ROOT / ".local/bin/dotfiles"

# Symlink to the last fake home for easy post-run inspection.
_LATEST_LINK = pathlib.Path("/tmp/dotfiles-test-latest")

# Preserve the real pixi home so subprocess calls share the package cache
# even though HOME is overridden to a fake test directory.  Without this,
# every `pixi exec` call would treat the fake HOME as a cold cache and
# re-resolve (and potentially re-download) all packages.
_REAL_HOME = pathlib.Path(os.environ.get("HOME", "~")).expanduser()

_REAL_PIXI_HOME = os.environ.get("PIXI_HOME", str(_REAL_HOME / ".pixi"))

# PIXI_CACHE_DIR / RATTLER_CACHE_DIR — pixi resolves the package cache
# independently of PIXI_HOME; preserve whichever is set (or derive the default).
_REAL_PIXI_CACHE_DIR = os.environ.get(
    "PIXI_CACHE_DIR",
    os.environ.get(
        "RATTLER_CACHE_DIR",
        str(_REAL_HOME / ".cache" / "rattler"),
    ),
)


def _find_free_port() -> int:
    """Bind to port 0 and return the OS-assigned free port number."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    """Poll until a TCP connection to 127.0.0.1:port succeeds or timeout is reached."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except (ConnectionRefusedError, OSError):
            time.sleep(0.1)

    raise RuntimeError(f"git daemon did not start on port {port} within {timeout}s")


@pytest.fixture(scope="session")
def git_daemon_url(
    tmp_path_factory: pytest.TempPathFactory,
) -> Generator[str, None, None]:
    """
    Start a local git daemon serving a bare clone of the repo.
    Yields a git://localhost:PORT/dotfiles URL (no network required).
    Scoped to the session so the daemon starts only once.
    """

    serve_dir = tmp_path_factory.mktemp("git_daemon_serve")
    bare_repo = serve_dir / "dotfiles"

    subprocess.run(
        ["git", "clone", "--bare", "--quiet", str(REPO_ROOT), str(bare_repo)],
        check=True,
        capture_output=True,
    )

    port = _find_free_port()
    proc = subprocess.Popen(
        [
            "git",
            "daemon",
            "--reuseaddr",
            "--export-all",
            "--listen=127.0.0.1",
            f"--port={port}",
            f"--base-path={serve_dir}",
            str(serve_dir),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    _wait_for_port(port)

    yield f"git://127.0.0.1:{port}/dotfiles"

    proc.terminate()
    proc.wait()


@pytest.fixture()
def fake_home(tmp_path: pathlib.Path, dotfiles_module: types.ModuleType) -> pathlib.Path:
    """
    Create an isolated fake HOME directory, seeded with the regular dotfiles
    from /etc/skel (e.g. .bashrc, which the tests expect to pre-exist) and a
    symlink to the pixi binary.  Updates /tmp/dotfiles-test-latest for easy
    post-run inspection.
    """

    home = tmp_path / "home"
    home.mkdir()

    # Seed with the regular files from skel only.  We deliberately skip
    # directories: on GitHub runners /etc/skel is ~4.5 GB because it contains
    # toolchain trees (.ghcup, .rustup, .dotnet, ...), and .ghcup is a symlink
    # to /usr/local/.ghcup that shutil.copytree dereferences, copying ~3.7 GB
    # into every test HOME and filling the runner disk.  The tests only need
    # the plain dotfiles (a pre-existing .bashrc), not the toolchains.
    skel = pathlib.Path("/etc/skel")
    if skel.is_dir():
        for item in skel.iterdir():
            if item.is_file() and not item.is_symlink():
                shutil.copy2(item, home / item.name)

    # Symlink the pixi binary so bootstrap.py's shebang can resolve it.
    # A symlink (not a copy) keeps each fake HOME tiny: the binary is ~75 MB
    # and copying it into every test HOME quickly fills the disk (pytest keeps
    # the last runs), which is enough to exhaust a CI runner.
    pixi_src = dotfiles_module.find_pixi()
    pixi_dst = home / ".pixi" / "bin" / "pixi"
    pixi_dst.parent.mkdir(parents=True)
    pixi_dst.symlink_to(pixi_src)

    # Update the "latest" symlink for easy manual inspection after a run.
    if _LATEST_LINK.is_symlink() or _LATEST_LINK.exists():
        _LATEST_LINK.unlink()
    _LATEST_LINK.symlink_to(home)

    return home


@pytest.fixture(params=["local", "git-daemon"])
def repo_uri(
    request: pytest.FixtureRequest,
    tmp_path_factory: pytest.TempPathFactory,
    git_daemon_url: str,
) -> str:
    """
    Parametrized fixture covering two repo source scenarios:

    - local:       file://<REPO_ROOT>       working tree (case 2: local clone + ./bootstrap.sh)
    - git-daemon:  git://127.0.0.1:<port>   local git daemon (case 1: curl | bash from a server)
    """

    if request.param == "local":
        return f"file://{REPO_ROOT}"

    # git-daemon
    return git_daemon_url


def run_bootstrap(
    home: pathlib.Path,
    uri: str,
    *extra_args: str,
    check: bool = False,
) -> subprocess.CompletedProcess[str]:
    """
    Run bootstrap.py against a fake HOME directory with the given repo URI.

    The pixi binary symlinked into the fake home is prepended to PATH so the
    script's shebang (pixi exec ...) can resolve correctly.
    """

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PIXI_HOME"] = _REAL_PIXI_HOME
    env["PIXI_CACHE_DIR"] = _REAL_PIXI_CACHE_DIR
    env["DOTFILES_SKIP_TOOLS"] = "1"
    env["PATH"] = f"{home / '.pixi' / 'bin'}:{env.get('PATH', '')}"

    return subprocess.run(
        [str(BOOTSTRAP_PY), "--repo-uri", uri, *extra_args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def run_dotfiles(
    home: pathlib.Path,
    *args: str,
    check: bool = False,
    unset_env: tuple[str, ...] = (),
) -> subprocess.CompletedProcess[str]:
    """
    Run the dotfiles script with arbitrary args (no --repo-uri injected).

    Use this for subcommands like 'dotfiles git <args>' that don't need a repo URI.
    Pass ``unset_env`` to remove specific environment variables from the subprocess env.
    """

    env = os.environ.copy()
    env["HOME"] = str(home)
    env["PIXI_HOME"] = _REAL_PIXI_HOME
    env["PIXI_CACHE_DIR"] = _REAL_PIXI_CACHE_DIR
    env["DOTFILES_SKIP_TOOLS"] = "1"
    env["PATH"] = f"{home / '.pixi' / 'bin'}:{env.get('PATH', '')}"
    for key in unset_env:
        env.pop(key, None)

    return subprocess.run(
        [str(BOOTSTRAP_PY), *args],
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


@pytest.fixture(scope="session")
def dotfiles_module() -> types.ModuleType:
    """
    Load the dotfiles script as a Python module (no subprocess/shebang overhead).

    Used by unit tests that call DotfilesRepo, write_manifest, uninstall etc.
    directly.  The pixi dev environment already has all required dependencies
    (gitpython, rich), so no subprocess is needed.
    """

    loader = importlib.machinery.SourceFileLoader("dotfiles", str(BOOTSTRAP_PY))
    spec = importlib.util.spec_from_file_location("dotfiles", BOOTSTRAP_PY, loader=loader)
    assert spec is not None and spec.loader is not None

    mod = importlib.util.module_from_spec(spec)
    # Register in sys.modules BEFORE exec_module so that typing.get_type_hints()
    # can resolve string annotations (caused by `from __future__ import annotations`)
    # when dataclasses processes InitVar fields inside the module.
    sys.modules["dotfiles"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod
