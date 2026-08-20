# Integration tests that run the full subprocess -> shebang -> clone -> checkout
# flow. The logic-only tests (backup, manifest, rollback, uninstall) live in
# test_unit.py and use direct module calls for speed. The tests here verify the
# end-to-end path including pixi exec, git transport (local and git-daemon) and
# subprocess passthrough behaviour.

from __future__ import annotations

import pathlib
import subprocess

from conftest import run_bootstrap, run_dotfiles

DOTFILES_DIR_NAME = ".dotfiles"


# =======
# Helpers
# =======


def bootstrap(home: pathlib.Path, uri: str) -> subprocess.CompletedProcess[str]:
    """Run bootstrap with --overwrite-git-dir and assert success."""

    result = run_bootstrap(home, uri, "--overwrite-git-dir")
    assert result.returncode == 0, result.stderr
    return result


# =====================
# Checkout — end-to-end
# =====================


def test_checkout_places_dotfiles_in_home(
    fake_home: pathlib.Path, repo_uri: str
) -> None:
    """Tracked dotfiles must be present in HOME and .bashrc must have the inject block."""

    _ = bootstrap(fake_home, repo_uri)
    bashrc = fake_home / ".bashrc"
    assert bashrc.exists()
    assert "# >>> dotfiles >>>" in bashrc.read_text()
    assert (fake_home / ".bashrc.dotfiles.sh").exists()
    assert (fake_home / ".nanorc").exists()


def test_checkout_respects_sparse_checkout(
    fake_home: pathlib.Path, repo_uri: str
) -> None:
    """Files excluded by sparse-checkout must NOT appear in HOME."""

    _ = bootstrap(fake_home, repo_uri)
    assert not (fake_home / "README.md").exists()
    assert not (fake_home / "bootstrap").exists()
    assert not (fake_home / "pixi.toml").exists()
    # Development-only files must never land in HOME.
    assert not (fake_home / "AGENTS.md").exists()
    assert not (fake_home / ".pre-commit-config.yaml").exists()
    assert not (fake_home / ".shellcheckrc").exists()
    # The script itself must be checked out into ~/.local/bin/
    assert (fake_home / ".local" / "bin" / "dotfiles").exists()


# ========
# Rollback
# ========


def test_rollback_removes_dotfiles_dir_on_clone_failure(
    fake_home: pathlib.Path,
) -> None:
    """If the URI is invalid the dotfiles_dir must not be left behind."""

    result = run_bootstrap(
        fake_home,
        "git://127.0.0.1:1/nonexistent",
        "--overwrite-git-dir",
    )
    assert result.returncode != 0
    assert not (fake_home / DOTFILES_DIR_NAME).exists()


# ===================
# CLI argument errors
# ===================


def test_missing_repo_uri_exits_nonzero(fake_home: pathlib.Path) -> None:
    """Running without --repo-uri and no DOTFILES_REPO env must exit non-zero."""

    result = run_dotfiles(fake_home, unset_env=("DOTFILES_REPO",))
    assert result.returncode != 0


# ===============
# Git passthrough
# ===============


def test_git_passthrough_log(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """'dotfiles git log' must exit zero and show at least one commit."""

    _ = bootstrap(fake_home, repo_uri)

    result = run_dotfiles(fake_home, "git", "log", "--oneline", "-1")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() != ""


def test_git_passthrough_status(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """'dotfiles git status' must exit zero after a clean bootstrap."""

    _ = bootstrap(fake_home, repo_uri)

    result = run_dotfiles(fake_home, "git", "status")
    assert result.returncode == 0, result.stderr


def test_git_status_hides_sparse_excluded_files(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """Sparse-excluded tracked files must not show up as deleted.

    They live in the repo but are intentionally not checked out to HOME, so
    'dotfiles git status' must not report them as deleted and 'git add -u' must
    not stage their (spurious) deletion.
    """

    _ = bootstrap(fake_home, repo_uri)

    status = run_dotfiles(fake_home, "git", "status", "--short")
    assert status.returncode == 0, status.stderr
    for excluded in ("README.md", "pixi.toml", "tests/conftest.py", "AGENTS.md"):
        assert (
            f"D {excluded}" not in status.stdout
        ), f"{excluded} wrongly reported as deleted:\n{status.stdout}"

    # ~/.bashrc is not tracked (we only inject a managed block into it), so it
    # must never appear in the status either.
    assert ".bashrc" not in status.stdout, f"unexpected .bashrc entry:\n{status.stdout}"

    # After a clean bootstrap the work-tree must be pristine: no tracked file
    # differs from HEAD, so the status is entirely empty.
    assert status.stdout.strip() == "", f"status not clean:\n{status.stdout}"

    # A blanket 'add -u' must not stage any deletion of excluded files.
    add = run_dotfiles(fake_home, "git", "add", "-u")
    assert add.returncode == 0, add.stderr
    staged = run_dotfiles(fake_home, "git", "diff", "--cached", "--name-status")
    assert staged.returncode == 0, staged.stderr
    assert staged.stdout.strip() == "", f"unexpected staged changes:\n{staged.stdout}"


# ======
# Update
# ======


def test_update_after_bootstrap(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """--update after bootstrap must exit zero and leave dotfiles in place."""

    _ = bootstrap(fake_home, repo_uri)

    result = run_dotfiles(fake_home, "--update")
    assert result.returncode == 0, result.stderr
    assert (fake_home / ".bashrc").exists()
    assert (fake_home / ".nanorc").exists()
    # HEAD does not move on a same-repo update, so it must be reported as a no-op.
    assert "up to date" in result.stdout.lower()


def test_update_preserves_existing_bashrc(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """A user's pre-existing ~/.bashrc content must survive bootstrap and update."""

    original_marker = "export MY_CUSTOM_VAR=42"
    (fake_home / ".bashrc").write_text(f"# user bashrc\n{original_marker}\n")

    _ = bootstrap(fake_home, repo_uri)
    assert original_marker in (fake_home / ".bashrc").read_text()

    result = run_dotfiles(fake_home, "--update")
    assert result.returncode == 0, result.stderr
    content = (fake_home / ".bashrc").read_text()
    assert original_marker in content
    assert "# >>> dotfiles >>>" in content
