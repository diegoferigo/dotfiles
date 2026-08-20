# Fast unit tests that call DotfilesRepo and its helpers directly, without going
# through the subprocess/shebang. They cover the backup, manifest, rollback and
# uninstall logic without paying the pixi exec startup cost on every call. The
# integration tests that exercise the full subprocess -> shebang -> clone flow
# live in test_clone.py and test_checkout.py.

from __future__ import annotations

import json
import pathlib
import subprocess
import types

import pytest
from conftest import REPO_ROOT

DOTFILES_DIR_NAME = ".dotfiles"
LOCAL_REPO_URI = f"file://{REPO_ROOT}"


# =======
# Helpers
# =======


def _bootstrap(
    mod: types.ModuleType,
    home: pathlib.Path,
    *,
    overwrite: bool = True,
) -> tuple[list[pathlib.Path], list[pathlib.Path]]:
    """Run the full bootstrap flow using direct module calls (no subprocess)."""

    dotfiles_dir = home / DOTFILES_DIR_NAME
    backup_dir = home / ".dotfiles_backup"

    dotfiles = mod.DotfilesRepo(
        repo_uri=LOCAL_REPO_URI,
        overwrite_git_dir=overwrite,
        home=home,
        dotfiles_dir=dotfiles_dir,
        backup_dir=backup_dir,
    )
    mod.DotfilesRepo.configure_sparse_checkout(repo=dotfiles.repo)
    backed_up, checked_out = mod.DotfilesRepo.checkout_to_home(
        repo=dotfiles.repo,
        home=home,
        backup_dir=backup_dir,
    )
    mod.write_manifest(
        dotfiles_dir=dotfiles_dir,
        backup_dir=backup_dir,
        backed_up=backed_up,
        checked_out=checked_out,
    )
    mod.Bashrc.inject(home, mod.Bashrc.read_block(home))
    return checked_out, backed_up


# ======
# Backup
# ======


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
    block-only file. ~/.bashrc is now untracked entirely — we only inject the
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

    for dev_file in ("AGENTS.md", ".pre-commit-config.yaml", ".shellcheckrc"):
        assert not (fake_home / dev_file).exists(), f"{dev_file} leaked into HOME"
        assert pathlib.Path(dev_file) not in checked_out


# ========
# Manifest
# ========


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


# ========
# Rollback
# ========


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

    # Simulate what main() registers.
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


# =========
# Uninstall
# =========


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


def test_uninstall_removes_bashrc_block(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Uninstall must remove the injected dotfiles block from ~/.bashrc."""

    original = "# original bashrc\n"
    (fake_home / ".bashrc").write_text(original)

    _ = _bootstrap(dotfiles_module, fake_home)
    assert dotfiles_module.Bashrc.BLOCK_BEGIN in (fake_home / ".bashrc").read_text()

    ret = dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert ret == 0
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


# ======
# Update
# ======


def test_update_fails_without_dotfiles_dir(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """update() without a prior bootstrap must return non-zero."""

    ret = dotfiles_module.update(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )
    assert ret != 0


def test_update_reconfigures_sparse_checkout(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """update() must overwrite a corrupted sparse-checkout file with canonical rules."""

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
    _, tracked = dotfiles_module.DotfilesRepo.checkout_to_home(
        repo=dotfiles.repo,
        home=fake_home,
        backup_dir=backup_dir,
    )
    dotfiles_module.write_manifest(
        dotfiles_dir=dotfiles_dir,
        backup_dir=backup_dir,
        backed_up=[],
        checked_out=tracked,
    )

    # Corrupt the sparse-checkout file.
    sparse_file = dotfiles_dir / "info" / "sparse-checkout"
    sparse_file.write_text("# corrupted\n")

    # Patch read_bashrc_block so update() doesn't require .bashrc to be
    # committed in HEAD (unit test concern; integration tests cover the real path).
    minimal_block = (
        f"{dotfiles_module.Bashrc.BLOCK_BEGIN}\n"
        f"[[ -f ~/.bashrc.d/init ]] && source ~/.bashrc.d/init\n"
        f"{dotfiles_module.Bashrc.BLOCK_END}"
    )
    monkeypatch.setattr(dotfiles_module.Bashrc, "read_block", lambda _: minimal_block)

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=backup_dir,
    )
    assert ret == 0
    assert "/*" in sparse_file.read_text()


def test_update_preserves_original_backup(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """--update must not clobber the pristine backup captured at bootstrap.

    Regression test: on update the now-managed file in HOME conflicts again;
    previously it was moved into the backup dir, overwriting the user's original
    copy. The original must be preserved so a later --uninstall restores it.
    """

    original = "# original nanorc\n"
    (fake_home / ".nanorc").write_text(original)

    _ = _bootstrap(dotfiles_module, fake_home)

    backup = fake_home / ".dotfiles_backup" / ".nanorc"
    assert backup.read_text() == original

    ret = dotfiles_module.update(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )
    assert ret == 0

    # The pristine backup must be untouched by the update.
    assert backup.read_text() == original
    # And uninstall must still restore the original.
    dotfiles_module.uninstall(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
    )
    assert (fake_home / ".nanorc").read_text() == original


def test_update_rollback_restores_bashrc_on_failure(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure during --update must roll back and restore ~/.bashrc."""

    (fake_home / ".bashrc").write_text("# user bashrc\nexport KEEP=1\n")

    _ = _bootstrap(dotfiles_module, fake_home)
    bashrc_before_update = (fake_home / ".bashrc").read_text()

    def _boom(_: pathlib.Path) -> str:
        raise RuntimeError("simulated inject failure")

    monkeypatch.setattr(dotfiles_module.Bashrc, "read_block", _boom)

    ret = dotfiles_module.update(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )
    assert ret == 1
    # Rollback must have restored the pre-update ~/.bashrc verbatim.
    assert (fake_home / ".bashrc").read_text() == bashrc_before_update
    assert "export KEEP=1" in (fake_home / ".bashrc").read_text()


# =======================
# inject_bashrc / .bashrc
# =======================


def _make_block(mod: types.ModuleType, content: str = "echo dotfiles") -> str:
    """Build a minimal managed block for inject tests."""
    return f"{mod.Bashrc.BLOCK_BEGIN}\n{content}\n{mod.Bashrc.BLOCK_END}"


def test_inject_bashrc_appends_block(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """inject_bashrc must append the dotfiles block while preserving existing content."""

    original = "# existing content\n"
    (fake_home / ".bashrc").write_text(original)
    block = _make_block(dotfiles_module)
    dotfiles_module.Bashrc.inject(fake_home, block)

    content = (fake_home / ".bashrc").read_text()
    assert dotfiles_module.Bashrc.BLOCK_BEGIN in content
    assert dotfiles_module.Bashrc.BLOCK_END in content
    assert original.strip() in content


def test_inject_bashrc_creates_file_if_missing(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """inject_bashrc must create ~/.bashrc when it does not exist."""

    bashrc = fake_home / ".bashrc"
    if bashrc.exists():
        bashrc.unlink()
    dotfiles_module.Bashrc.inject(fake_home, _make_block(dotfiles_module))

    assert bashrc.exists()
    assert dotfiles_module.Bashrc.BLOCK_BEGIN in bashrc.read_text()


def test_inject_bashrc_updates_existing_block(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """inject_bashrc must replace a stale block without duplicating it."""

    stale_block = _make_block(dotfiles_module, "# old content")
    (fake_home / ".bashrc").write_text(f"# preamble\n{stale_block}\n")

    new_block = _make_block(dotfiles_module, "# new content")
    dotfiles_module.Bashrc.inject(fake_home, new_block)

    content = (fake_home / ".bashrc").read_text()
    assert content.count(dotfiles_module.Bashrc.BLOCK_BEGIN) == 1
    assert "# old content" not in content
    assert "# new content" in content
    assert "# preamble" in content


def test_remove_block_preserves_surrounding_lines(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """remove_block must not merge lines that surround a mid-file block."""

    block = _make_block(dotfiles_module)
    (fake_home / ".bashrc").write_text(f"line_before\n{block}\nline_after\n")

    dotfiles_module.Bashrc.remove_block(fake_home)

    content = (fake_home / ".bashrc").read_text()
    assert dotfiles_module.Bashrc.BLOCK_BEGIN not in content
    # Adjacent content lines must remain on separate lines, not concatenated.
    assert "line_beforeline_after" not in content
    assert "line_before\nline_after" in content


def test_remove_block_appended_at_eof(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """remove_block must restore the original content when the block was at EOF."""

    original = "# original bashrc\n"
    (fake_home / ".bashrc").write_text(original)
    dotfiles_module.Bashrc.inject(fake_home, _make_block(dotfiles_module))

    dotfiles_module.Bashrc.remove_block(fake_home)

    assert (fake_home / ".bashrc").read_text() == original


# ==============
# Sparse-checkout
# ==============


def _sparse_excludes(mod: types.ModuleType) -> list[str]:
    """Return the top-level paths excluded by the sparse-checkout rules."""

    return [
        line[1:].lstrip("/")
        for line in mod.SPARSE_CHECKOUT.splitlines()
        if line.startswith("!")
    ]


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
