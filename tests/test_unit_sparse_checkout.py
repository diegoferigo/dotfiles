from __future__ import annotations

import pathlib
import subprocess
import types

import pytest
from conftest import REPO_ROOT
from helpers import DOTFILES_DIR_NAME, _bootstrap, _sparse_excludes


def test_sparse_excludes_have_no_stale_entries(
    dotfiles_module: types.ModuleType,
) -> None:
    """Every sparse exclude must map to a tracked path or a declared guard.

    This catches the case of a file that stops being tracked (as happened with
    ~/.bashrc) while its exclude lingers, forcing the two to stay in sync.
    """

    tracked = set(
        subprocess.run(
            ["git", "ls-tree", "--name-only", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    )
    guards = dotfiles_module.SPARSE_UNTRACKED_GUARDS

    stale = [
        entry
        for entry in _sparse_excludes(dotfiles_module)
        if entry not in tracked and entry not in guards
    ]
    assert not stale, f"stale sparse-checkout excludes: {stale}"


def test_sparse_guards_are_listed_and_untracked(
    dotfiles_module: types.ModuleType,
) -> None:
    """Each declared guard must be excluded and must not be tracked."""

    tracked = set(
        subprocess.run(
            ["git", "ls-tree", "--name-only", "HEAD"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
    )
    excludes = set(_sparse_excludes(dotfiles_module))

    for guard in dotfiles_module.SPARSE_UNTRACKED_GUARDS:
        assert guard in excludes, f"guard {guard} missing from sparse-checkout"
        assert guard not in tracked, f"guard {guard} is tracked, drop it from guards"


def test_mark_skip_worktree_survives_unmarkable_path(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A path git cannot mark must warn, not raise and roll back the checkout.

    The original _populate_index ran update-index with check=True, so a single
    entry git refused to mark (as reported in a real bootstrap that aborted with
    exit 128) tore down the whole install. Marking is best effort now: the bad
    path is reported and the rest of the run continues.
    """

    _bootstrap(dotfiles_module, fake_home)
    git_dir = fake_home / DOTFILES_DIR_NAME

    # 'does/not/exist' is not in the index, so update-index fails on it. The mix
    # with a real tracked path also proves one bad entry does not sink the batch.
    dotfiles_module.DotfilesRepo._mark_skip_worktree(
        str(git_dir),
        fake_home,
        ["AGENTS.md", "does/not/exist"],
    )

    out = capsys.readouterr().out
    assert "does/not/exist" in out
    assert "AGENTS.md" not in out.split("skip-worktree:")[-1]


def test_populate_index_hides_user_file_collision(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """A sparse-excluded path the user already has in HOME must stay hidden.

    ~/.gitattributes is tracked but sparse-excluded (repo-internal Linguist
    config); the user's own file can live at the same path because the bare-repo
    work-tree is HOME. Modern git will not set skip-worktree on that present,
    differing path, so it would show as modified forever. _populate_index falls
    back to --assume-unchanged and `dotfiles git status` stays clean.
    """

    checked_out, _ = _bootstrap(dotfiles_module, fake_home)
    git_dir = fake_home / DOTFILES_DIR_NAME

    # The user already had their own ~/.gitattributes, differing from the
    # tracked repo-internal one.
    collision = fake_home / ".gitattributes"
    collision.write_text("*.py merge=mergiraf\n")

    dotfiles_module.DotfilesRepo._populate_index(str(git_dir), fake_home, checked_out)

    status = subprocess.run(
        [
            "git",
            "--git-dir",
            str(git_dir),
            "--work-tree",
            str(fake_home),
            "status",
            "--porcelain",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert ".gitattributes" not in status.stdout
    # _populate_index only changes git index flags, never the user's file.
    assert collision.read_text() == "*.py merge=mergiraf\n"

    marks = subprocess.run(
        [
            "git",
            "--git-dir",
            str(git_dir),
            "ls-files",
            "-v",
            "--",
            ".gitattributes",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    # A lowercase flag ('h', assume-unchanged) or 'S' (skip-worktree) both hide
    # the path; a bare 'H' would mean it still shows up in status.
    assert marks.stdout[:1] in ("h", "S")
