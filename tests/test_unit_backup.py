from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
import types

import pytest
from conftest import REPO_ROOT
from helpers import DOTFILES_DIR_NAME, LOCAL_REPO_URI, _bootstrap, _tracked_skill_files


def test_backup_existing_file(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """.nanorc already present must be backed up, not overwritten."""

    original = "# original nanorc\n"
    (fake_home / ".nanorc").write_text(original)

    _ = _bootstrap(dotfiles_module, fake_home)

    backed_up = fake_home / ".dotfiles_backup" / ".nanorc"
    assert backed_up.exists(), "backed-up .nanorc not found"
    assert backed_up.read_text() == original
    assert (fake_home / ".nanorc").read_text() != original


def test_identical_existing_file_is_backed_up_silently(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An identical pre-existing file keeps its backup, but nothing is announced.

    The backup lets uninstall restore the file with its original mode.
    """

    _ = _bootstrap(dotfiles_module, fake_home)
    tracked = (fake_home / ".nanorc").read_text()
    (fake_home / ".nanorc").chmod(0o600)
    shutil.rmtree(fake_home / ".dotfiles_backup", ignore_errors=True)
    _ = capsys.readouterr()

    _, backed_up = _bootstrap(dotfiles_module, fake_home)
    dotfiles_module.notify_backups(
        backed_up, home=fake_home, backup_dir=fake_home / ".dotfiles_backup"
    )

    out = capsys.readouterr().out
    assert pathlib.Path(".nanorc") in backed_up
    assert "have been backed up" not in out
    assert "Backing up" not in out
    backup = fake_home / ".dotfiles_backup" / ".nanorc"
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600

    dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert (fake_home / ".nanorc").read_text() == tracked
    assert stat.S_IMODE((fake_home / ".nanorc").stat().st_mode) == 0o600


@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores file modes")
def test_unreadable_existing_file_is_backed_up_and_reported(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An unreadable conflict cannot be compared, so it is reported, not fatal."""

    _ = _bootstrap(dotfiles_module, fake_home)
    (fake_home / ".nanorc").chmod(0o000)
    shutil.rmtree(fake_home / ".dotfiles_backup", ignore_errors=True)
    _ = capsys.readouterr()

    try:
        _, backed_up = _bootstrap(dotfiles_module, fake_home)
        dotfiles_module.notify_backups(
            backed_up, home=fake_home, backup_dir=fake_home / ".dotfiles_backup"
        )
    finally:
        (fake_home / ".dotfiles_backup" / ".nanorc").chmod(0o644)

    assert pathlib.Path(".nanorc") in backed_up
    assert ".nanorc" in capsys.readouterr().out


def test_notice_lists_only_divergent_backups(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """With one identical and one divergent file, only the divergent one is reported."""

    checked_out, _ = _bootstrap(dotfiles_module, fake_home)
    other = next(
        rel
        for rel in checked_out
        if rel != pathlib.Path(".nanorc") and (fake_home / rel).is_file()
    )
    (fake_home / other).write_text("# local edit\n")
    shutil.rmtree(fake_home / ".dotfiles_backup", ignore_errors=True)
    _ = capsys.readouterr()

    _, backed_up = _bootstrap(dotfiles_module, fake_home)
    dotfiles_module.notify_backups(
        backed_up, home=fake_home, backup_dir=fake_home / ".dotfiles_backup"
    )

    out = capsys.readouterr().out
    assert {pathlib.Path(".nanorc"), other} <= set(backed_up)
    assert "have been backed up" in out
    assert ".nanorc" not in out
    assert (fake_home / ".dotfiles_backup" / other).read_text() == "# local edit\n"


def test_symlink_with_identical_content_is_still_backed_up(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """A symlink is not a regular copy: it is backed up and replaced by a file."""

    _ = _bootstrap(dotfiles_module, fake_home)
    tracked = (fake_home / ".nanorc").read_text()
    real = fake_home / "nanorc-elsewhere"
    real.write_text(tracked)
    (fake_home / ".nanorc").unlink()
    (fake_home / ".nanorc").symlink_to(real)
    shutil.rmtree(fake_home / ".dotfiles_backup", ignore_errors=True)

    _, backed_up = _bootstrap(dotfiles_module, fake_home)

    assert pathlib.Path(".nanorc") in backed_up
    assert (fake_home / ".dotfiles_backup" / ".nanorc").is_symlink()
    assert not (fake_home / ".nanorc").is_symlink()
    assert real.read_text() == tracked


def test_no_backup_dir_when_no_conflicts(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """backup_dir must not be created when there are no conflicting files."""

    for f in [".bashrc", ".bash_logout", ".profile"]:
        p = fake_home / f
        if p.exists():
            p.unlink()

    _ = _bootstrap(dotfiles_module, fake_home)

    assert not (fake_home / ".dotfiles_backup").exists()


def test_bootstrap_preserves_existing_bashrc(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """A pre-existing ~/.bashrc (not tracked by the repo) must be preserved.

    Regression test: previously ~/.bashrc was a tracked-but-sparse-excluded
    file, so the full tracked list drove the backup step and the user's existing
    .bashrc was moved into the backup dir and never restored, then replaced by a
    block-only file. ~/.bashrc is now untracked entirely, so we only inject the
    managed block into whatever the user already has.
    """

    original = "# my custom bashrc\nexport FOO=bar\nalias ll='ls -la'\n"
    (fake_home / ".bashrc").write_text(original)

    _ = _bootstrap(dotfiles_module, fake_home)

    content = (fake_home / ".bashrc").read_text()
    # Original content must survive.
    assert "export FOO=bar" in content
    assert "alias ll='ls -la'" in content
    # The managed block must be appended.
    assert dotfiles_module.Bashrc.BLOCK_BEGIN in content
    # .bashrc must never be moved into the backup dir.
    assert not (fake_home / ".dotfiles_backup" / ".bashrc").exists()


def test_bootstrap_excludes_dev_files_from_home(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Development-only tracked files must never be checked out into HOME."""

    checked_out, _ = _bootstrap(dotfiles_module, fake_home)

    for dev_file in (
        "AGENTS.md",
        ".pre-commit-config.yaml",
        "pyproject.toml",
        ".shellcheckrc",
    ):
        assert not (fake_home / dev_file).exists(), f"{dev_file} leaked into HOME"
        assert pathlib.Path(dev_file) not in checked_out


def test_manifest_written(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """manifest.json must be written inside dotfiles_dir."""

    _ = _bootstrap(dotfiles_module, fake_home)

    manifest_path = fake_home / DOTFILES_DIR_NAME / "manifest.json"
    assert manifest_path.exists()

    manifest = json.loads(manifest_path.read_text())
    assert "timestamp" in manifest
    assert "backup_dir" in manifest
    assert "checked_out" in manifest
    assert "backed_up" in manifest
    assert isinstance(manifest["checked_out"], list)
    assert len(manifest["checked_out"]) > 0


def test_manifest_records_backed_up_files(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Backed-up files must be listed in manifest.json."""

    (fake_home / ".nanorc").write_text("# original\n")

    _ = _bootstrap(dotfiles_module, fake_home)

    manifest = json.loads((fake_home / DOTFILES_DIR_NAME / "manifest.json").read_text())
    assert ".nanorc" in manifest["backed_up"]


def test_rollback_undoes_checkout(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """RollbackStack must remove checked-out files and restore backups."""

    original = "# original\n"
    (fake_home / ".nanorc").write_text(original)

    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    backup_dir = fake_home / ".dotfiles_backup"

    dotfiles = dotfiles_module.DotfilesRepo(
        repo_uri=LOCAL_REPO_URI,
        overwrite_git_dir=True,
        home=fake_home,
        dotfiles_dir=dotfiles_dir,
        backup_dir=backup_dir,
    )

    dotfiles_module.DotfilesRepo.configure_sparse_checkout(repo=dotfiles.repo)
    backed_up, tracked = dotfiles_module.DotfilesRepo.checkout_to_home(
        repo=dotfiles.repo,
        home=fake_home,
        backup_dir=backup_dir,
    )

    # Match the rollback order that main() registers during bootstrap.
    rollback = dotfiles_module.RollbackStack()
    rollback.push(
        "remove dotfiles dir",
        lambda: __import__("shutil").rmtree(dotfiles_dir, ignore_errors=True),
    )

    _home, _bdir, _backed_up = fake_home, backup_dir, backed_up

    def _undo() -> None:
        import shutil

        for rel in tracked:
            f = _home / rel
            if f.is_file() and not f.is_symlink():
                f.unlink()
        for rel in _backed_up:
            src, dst = _bdir / rel, _home / rel
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(src), str(dst))

    rollback.push("restore backed-up files and remove checked-out dotfiles", _undo)
    rollback.rollback()

    assert not dotfiles_dir.exists()
    assert (fake_home / ".nanorc").read_text() == original


def test_uninstall_removes_dotfiles(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """After uninstall, tracked dotfiles must be gone and dotfiles_dir removed."""

    _ = _bootstrap(dotfiles_module, fake_home)
    assert (fake_home / ".bashrc").exists()

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret == 0

    assert not (fake_home / DOTFILES_DIR_NAME).exists()
    assert not (fake_home / ".nanorc").exists()


def test_uninstall_restores_backed_up_files(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Files backed up during bootstrap must be restored to HOME after uninstall."""

    original = "# original nanorc\n"
    (fake_home / ".nanorc").write_text(original)

    _ = _bootstrap(dotfiles_module, fake_home)

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret == 0
    assert (fake_home / ".nanorc").read_text() == original


def test_tracked_skills_exist() -> None:
    assert _tracked_skill_files()


@pytest.mark.parametrize("skill", _tracked_skill_files())
def test_tracked_skill_is_deployed_to_home(
    skill: str,
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    _ = _bootstrap(dotfiles_module, fake_home)

    deployed = fake_home / skill
    assert deployed.is_file()
    assert deployed.read_text() == (REPO_ROOT / skill).read_text()


@pytest.mark.parametrize("skill", _tracked_skill_files())
def test_tracked_skill_replaces_a_local_file_and_uninstall_restores_it(
    skill: str,
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    local = fake_home / skill
    local.parent.mkdir(parents=True)
    local.write_text("# local skill\n")

    _ = _bootstrap(dotfiles_module, fake_home)
    assert local.read_text() == (REPO_ROOT / skill).read_text()

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret == 0
    assert local.read_text() == "# local skill\n"


def test_uninstall_removes_bashrc_block(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Uninstall must remove the injected dotfiles block from ~/.bashrc."""

    original = "# original bashrc\n"
    (fake_home / ".bashrc").write_text(original)

    _ = _bootstrap(dotfiles_module, fake_home)
    assert (
        dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN
        in (fake_home / ".bashrc").read_text()
    )
    assert dotfiles_module.Bashrc.BLOCK_BEGIN in (fake_home / ".bashrc").read_text()

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret == 0
    assert (
        dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN
        not in (fake_home / ".bashrc").read_text()
    )
    assert dotfiles_module.Bashrc.BLOCK_BEGIN not in (fake_home / ".bashrc").read_text()
    assert (fake_home / ".bashrc").read_text() == original


def test_uninstall_fails_without_manifest(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Uninstall without a prior bootstrap (no manifest) must return non-zero."""

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret != 0


def test_overwrite_refuses_non_bare_dir(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """--overwrite-git-dir must refuse to delete a dir that is not a bare repo."""

    ddir = fake_home / DOTFILES_DIR_NAME
    ddir.mkdir()
    (ddir / "important_user_data").write_text("do not delete me\n")

    with pytest.raises(RuntimeError):
        dotfiles_module.DotfilesRepo(
            repo_uri=LOCAL_REPO_URI,
            overwrite_git_dir=True,
            home=fake_home,
            dotfiles_dir=ddir,
            backup_dir=fake_home / ".dotfiles_backup",
        )

    # The directory and its content must be untouched.
    assert (ddir / "important_user_data").read_text() == "do not delete me\n"


def test_uninstall_aborts_on_local_modifications(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """--uninstall must not silently drop uncommitted edits to a tracked file."""

    _ = _bootstrap(dotfiles_module, fake_home)

    edited = "# my local edit that must survive\n"
    (fake_home / ".nanorc").write_text(edited)

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret == 1
    # The abort must leave everything in place.
    assert (fake_home / ".nanorc").read_text() == edited
    assert (fake_home / DOTFILES_DIR_NAME).exists()


def test_uninstall_force_overrides_local_modifications(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """--uninstall --force must proceed despite local edits."""

    _ = _bootstrap(dotfiles_module, fake_home)
    (fake_home / ".nanorc").write_text("# my local edit\n")

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
        force=True,
    )
    assert ret == 0
    assert not (fake_home / DOTFILES_DIR_NAME).exists()


def test_retire_untracked_rolls_back(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Rolling back a retirement puts the file and its original back."""

    home, backup = tmp_path / "home", tmp_path / "backup"
    rel = pathlib.Path(".config/tool.conf")
    (home / rel).parent.mkdir(parents=True)
    (home / rel).write_bytes(b"ours\n")
    (backup / rel).parent.mkdir(parents=True)
    (backup / rel).write_bytes(b"original\n")
    rollback = dotfiles_module.RollbackStack()

    removed, kept = dotfiles_module._retire_untracked(
        [rel],
        {rel: dotfiles_module._sha256(b"ours\n")},
        home,
        backup,
        rollback,
    )

    assert (removed, kept) == ([rel], [])
    assert (home / rel).read_bytes() == b"original\n"
    rollback.rollback()
    assert (home / rel).read_bytes() == b"ours\n"
    assert (backup / rel).read_bytes() == b"original\n"


def test_retire_untracked_keeps_an_unverifiable_file(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """A file without a deployed digest is never removed."""

    rel = pathlib.Path(".config/tool.conf")
    (tmp_path / rel).parent.mkdir(parents=True)
    (tmp_path / rel).write_bytes(b"ours\n")

    removed, kept = dotfiles_module._retire_untracked(
        [rel], {}, tmp_path, tmp_path / "backup", dotfiles_module.RollbackStack()
    )

    assert (removed, kept) == ([], [rel])
    assert (tmp_path / rel).read_bytes() == b"ours\n"
