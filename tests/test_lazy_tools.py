from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import pathlib
import subprocess
import sys
import types

import pytest
from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".local/libexec/dotfiles/lazy-tools"

# Stands in for https://hunk.dev/install.sh: installs a stub binary and logs the
# environment and argument it saw, so the tests need no network.
FAKE_INSTALLER = """#!/bin/sh
[ -n "$FAKE_FAIL" ] && exit 3
version="${1:-0.1.0}"
mkdir -p "$HOME/.hunk/bin"
cat > "$HOME/.hunk/bin/hunk" <<STUB
#!/bin/sh
if [ "\\$1" = skill ]; then echo "$HOME/.hunk/skills/hunk-review/SKILL.md"; exit 0; fi
echo "v${version#v}"
STUB
chmod +x "$HOME/.hunk/bin/hunk"
mkdir -p "$HOME/.hunk/skills/hunk-review"
touch "$HOME/.hunk/skills/hunk-review/SKILL.md"
echo "NO_MODIFY_PATH=$HUNK_NO_MODIFY_PATH DO_NOT_TRACK=$DO_NOT_TRACK ANALYTICS=$HUNK_DISABLE_ANALYTICS version=$version" >> "$FAKE_LOG"
"""


@pytest.fixture(scope="module")
def lazy_tools() -> types.ModuleType:
    loader = importlib.machinery.SourceFileLoader("lazy_tools", str(SCRIPT))
    spec = importlib.util.spec_from_file_location("lazy_tools", SCRIPT, loader=loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture
def env(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> pathlib.Path:
    """Isolated HOME, state dir and PATH with a fake installer; returns the HOME."""

    home = tmp_path / "home"
    home.mkdir()
    (tmp_path / "install.sh").write_text(FAKE_INSTALLER)

    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_STATE_HOME", str(home / ".local/state"))
    # System tools only: a real hunk under the user's HOME must not be seen.
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    monkeypatch.setenv(
        "LAZY_TOOLS_HUNK_INSTALLER_URL", f"file://{tmp_path / 'install.sh'}"
    )
    monkeypatch.setenv("FAKE_LOG", str(tmp_path / "installer.log"))
    monkeypatch.delenv("HUNK_VERSION", raising=False)
    monkeypatch.delenv("FAKE_FAIL", raising=False)
    return home


def test_install_records_the_tool_and_keeps_the_installer_quiet(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """The installer must neither edit the shell files nor phone home."""

    assert lazy_tools.install_tool("hunk")

    assert "version=0.1.0" in _record(env).read_text()
    assert _installer_calls() == [
        "NO_MODIFY_PATH=1 DO_NOT_TRACK=1 ANALYTICS=1 version=0.1.0"
    ]


def test_a_second_install_does_not_rerun_the_installer(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """Without a pin an installed tool is never upgraded."""

    assert lazy_tools.install_tool("hunk")
    assert lazy_tools.install_tool("hunk")

    assert len(_installer_calls()) == 1


def test_an_external_tool_is_left_alone_even_with_a_pin(
    lazy_tools: types.ModuleType,
    env: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A hunk that lazy-tools did not install is never changed or recorded."""

    _fake_hunk(env / ".hunk/bin/hunk", "v9.9.9")
    monkeypatch.setenv("HUNK_VERSION", "0.2.0")

    assert lazy_tools.install_tool("hunk")

    assert "external, left alone" in capsys.readouterr().out
    assert _installer_calls() == []
    assert not _record(env).exists()


def test_a_pin_moves_a_managed_install_and_reaches_the_installer_unchanged(
    lazy_tools: types.ModuleType, env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A v-prefixed pin equal to the installed version does nothing."""

    assert lazy_tools.install_tool("hunk")

    monkeypatch.setenv("HUNK_VERSION", "v0.1.0")
    assert lazy_tools.install_tool("hunk")
    assert len(_installer_calls()) == 1

    monkeypatch.setenv("HUNK_VERSION", "v0.2.0")
    assert lazy_tools.install_tool("hunk")
    assert _installer_calls()[-1].endswith("version=v0.2.0")
    assert "version=0.2.0" in _record(env).read_text()


@pytest.mark.parametrize(
    "failure", ["exit", "html", "nothing", "missing-file", "missing-curl"]
)
def test_a_failing_install_leaves_no_record_and_keeps_an_old_one(
    lazy_tools: types.ModuleType,
    env: pathlib.Path,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Each way the installer can go wrong is reported as an error, never a traceback."""

    page = tmp_path / "page.sh"
    page.write_text("<html>502 Bad Gateway</html>\n" if failure == "html" else "")
    if failure == "nothing":
        page.write_text("#!/bin/sh\nexit 0\n")
    if failure == "exit":
        monkeypatch.setenv("FAKE_FAIL", "1")
    elif failure == "missing-file":
        monkeypatch.setenv("LAZY_TOOLS_HUNK_INSTALLER_URL", f"file://{tmp_path}/no.sh")
    elif failure == "missing-curl":
        (tmp_path / "empty").mkdir()
        monkeypatch.setenv("PATH", str(tmp_path / "empty"))
    else:
        monkeypatch.setenv("LAZY_TOOLS_HUNK_INSTALLER_URL", f"file://{page}")

    assert not lazy_tools.install_tool("hunk")
    assert not _record(env).exists()
    assert not (env / ".hunk").exists()


def test_a_failing_pinned_reinstall_keeps_the_old_record(
    lazy_tools: types.ModuleType, env: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The record of a working managed install survives a failed upgrade."""

    assert lazy_tools.install_tool("hunk")
    monkeypatch.setenv("HUNK_VERSION", "0.2.0")
    monkeypatch.setenv("FAKE_FAIL", "1")

    assert lazy_tools.main(["install"]) == 1

    assert "version=0.1.0" in _record(env).read_text()


def test_a_stale_record_is_dropped_and_the_next_install_reinstalls(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """A tool the user removed by hand is installed again."""

    assert lazy_tools.install_tool("hunk")
    (env / ".hunk/bin/hunk").unlink()

    assert lazy_tools.install_tool("hunk")

    assert (env / ".hunk/bin/hunk").is_file()
    assert len(_installer_calls()) == 2


def test_uninstall_removes_only_what_it_installed(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """An external hunk survives uninstall."""

    assert lazy_tools.install_tool("hunk")
    assert lazy_tools.uninstall_tool("hunk")
    assert not (env / ".hunk").exists()
    assert not _record(env).exists()

    _fake_hunk(env / ".hunk/bin/hunk", "v9.9.9")
    assert lazy_tools.uninstall_tool("hunk")
    assert (env / ".hunk/bin/hunk").is_file()


def test_install_links_the_bundled_skill_and_uninstall_removes_it(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """The skill link follows the managed hunk and disappears with it."""

    link = env / ".agents/skills/hunk-review"

    assert lazy_tools.install_tool("hunk")
    assert link.is_symlink()
    assert link.resolve() == (env / ".hunk/skills/hunk-review").resolve()

    assert lazy_tools.uninstall_tool("hunk")
    assert not link.is_symlink()


def test_an_existing_skill_directory_is_left_alone(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """A real directory at the link path belongs to the user."""

    link = env / ".agents/skills/hunk-review"
    link.mkdir(parents=True)
    (link / "SKILL.md").write_text("mine")

    assert lazy_tools.install_tool("hunk")
    assert not link.is_symlink()
    assert (link / "SKILL.md").read_text() == "mine"


def test_an_external_hunk_gets_the_skill_link(
    lazy_tools: types.ModuleType, env: pathlib.Path
) -> None:
    """An external hunk is left alone but its skill is still linked."""

    skill = env / ".hunk/skills/hunk-review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text("skill")
    _fake_hunk(env / ".hunk/bin/hunk", "v9.9.9")
    (env / ".hunk/bin/hunk").write_text(
        '#!/bin/sh\n[ "$1" = skill ] && echo "$HOME/.hunk/skills/hunk-review/SKILL.md" '
        '&& exit 0\necho v9.9.9\n'
    )

    assert lazy_tools.install_tool("hunk")
    assert (env / ".agents/skills/hunk-review").is_symlink()


def test_status_reports_missing_managed_and_external(
    lazy_tools: types.ModuleType,
    env: pathlib.Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Status tells the three states apart."""

    _ = lazy_tools.status_tool("hunk")
    assert capsys.readouterr().out.strip() == "hunk: missing"

    _ = lazy_tools.install_tool("hunk")
    _ = capsys.readouterr()
    _ = lazy_tools.status_tool("hunk")
    assert capsys.readouterr().out.strip() == "hunk: managed 0.1.0"

    _ = lazy_tools.uninstall_tool("hunk")
    _fake_hunk(env / ".hunk/bin/hunk", "v9.9.9")
    _ = capsys.readouterr()
    _ = lazy_tools.status_tool("hunk")
    assert capsys.readouterr().out.strip() == "hunk: external 9.9.9"


def test_an_installer_override_that_is_not_a_file_url_is_refused(
    lazy_tools: types.ModuleType,
    env: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The test override must never fetch over the network."""

    monkeypatch.setenv("LAZY_TOOLS_HUNK_INSTALLER_URL", "http://127.0.0.1:1/install.sh")

    assert not lazy_tools.install_tool("hunk")

    assert "must be a file:// URL" in capsys.readouterr().err
    assert not (env / ".hunk").exists()
    assert not _record(env).exists()


def test_cli_exit_codes_and_https_only_download(
    env: pathlib.Path, tmp_path: pathlib.Path
) -> None:
    """The script is executable, maps errors to exit codes and pins curl to https."""

    assert os.access(SCRIPT, os.X_OK)
    assert _cli().returncode == 0
    assert _cli("install", "nope").returncode == 1
    assert _cli("frobnicate").returncode == 2

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    curl = fake_bin / "curl"
    curl.write_text('#!/bin/sh\necho "$@" > "$FAKE_CURL_ARGS"\nexit 22\n')
    curl.chmod(0o755)
    args_file = tmp_path / "curl.args"
    (env / ".hunk/bin/hunk").unlink()

    result = _cli(
        "install",
        LAZY_TOOLS_HUNK_INSTALLER_URL="",
        PATH=f"{fake_bin}:/usr/bin:/bin",
        FAKE_CURL_ARGS=str(args_file),
    )

    assert result.returncode == 1
    args = args_file.read_text()
    assert "--proto =https --tlsv1.2" in args
    assert "https://hunk.dev/install.sh" in args


# Private helpers


def _cli(*args: str, **extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        env={**os.environ, **extra},
        capture_output=True,
        text=True,
    )


def _fake_hunk(path: pathlib.Path, version: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/bin/sh\necho {version}\n")
    path.chmod(0o755)


def _record(home: pathlib.Path) -> pathlib.Path:
    return home / ".local/state/dotfiles/lazy-tools/hunk"


def _installer_calls() -> list[str]:
    log = pathlib.Path(os.environ["FAKE_LOG"])
    return log.read_text().splitlines() if log.exists() else []
