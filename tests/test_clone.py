from __future__ import annotations

import pathlib
import subprocess

from conftest import run_bootstrap

DOTFILES_DIR_NAME = ".dotfiles"


# =======
# Helpers
# =======


def dotfiles_git(
    home: pathlib.Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Run a git command against the dotfiles bare repo in the given home."""

    dotfiles_dir = home / DOTFILES_DIR_NAME

    return subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), *args],
        capture_output=True,
        text=True,
    )


# =====
# Tests
# =====


def test_clone_creates_bare_repo(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """The dotfiles bare repo must exist and be recognised by git."""

    result = run_bootstrap(fake_home, repo_uri, "--overwrite-git-dir")
    assert result.returncode == 0, result.stderr

    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    assert dotfiles_dir.is_dir()

    check = dotfiles_git(fake_home, "rev-parse", "--git-dir")
    assert check.returncode == 0, check.stderr


def test_clone_sparse_checkout_configured(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """The sparse-checkout file must exist and contain the expected rules."""

    _ = run_bootstrap(fake_home, repo_uri, "--overwrite-git-dir")

    sparse_file = fake_home / DOTFILES_DIR_NAME / "info" / "sparse-checkout"
    assert sparse_file.exists(), "sparse-checkout file not found"

    content = sparse_file.read_text()
    assert "/*" in content
    assert "!README.md" in content
    assert "!bootstrap" in content
    assert "!AGENTS.md" in content
    assert "!.pre-commit-config.yaml" in content
    assert "!.shellcheckrc" in content


def test_clone_untracked_files_hidden(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """Git must be configured to hide untracked files in the work-tree."""

    _ = run_bootstrap(fake_home, repo_uri, "--overwrite-git-dir")

    result = dotfiles_git(fake_home, "config", "status.showUntrackedFiles")
    assert result.returncode == 0
    assert result.stdout.strip() == "no"


def test_clone_fails_if_dotfiles_dir_exists(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """A second run without --overwrite-git-dir must exit non-zero."""

    _ = run_bootstrap(fake_home, repo_uri, "--overwrite-git-dir")
    result = run_bootstrap(fake_home, repo_uri)  # no --overwrite-git-dir
    assert result.returncode != 0


def test_clone_overwrite_replaces_existing(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """Running twice with --overwrite-git-dir must succeed."""

    _ = run_bootstrap(fake_home, repo_uri, "--overwrite-git-dir")
    result = run_bootstrap(fake_home, repo_uri, "--overwrite-git-dir")
    assert result.returncode == 0, result.stderr


# ==============
# Bootstrap shim
# ==============


def test_bootstrap_pipe_has_no_unbound_variable() -> None:
    """`curl ... | bash` must not trip over BASH_SOURCE under 'set -u'.

    When the shim is piped from stdin BASH_SOURCE[0] is unset, so the guard has
    to tolerate it and fall through to the download branch. PATH is stripped so
    the download fails fast (no network) right after announcing itself, which is
    enough to prove the guard did not fire.
    """

    import os
    import shutil

    from conftest import REPO_ROOT

    bash = shutil.which("bash")
    assert bash is not None
    bootstrap = (REPO_ROOT / "bootstrap").read_text(encoding="utf-8")

    proc = subprocess.run(
        [bash, "-s", "--", "--help"],
        input=bootstrap,
        capture_output=True,
        text=True,
        env={**os.environ, "PATH": "/nonexistent"},
    )

    combined = proc.stdout + proc.stderr
    assert "unbound variable" not in combined
    assert "Downloading dotfiles script from GitHub" in proc.stdout
