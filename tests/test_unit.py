# Fast unit tests that call DotfilesRepo and its helpers directly, without going
# through the subprocess/shebang. They cover the backup, manifest, rollback and
# uninstall logic without paying the pixi exec startup cost on every call. The
# integration tests that exercise the full subprocess -> shebang -> clone flow
# live in test_clone.py and test_checkout.py.

from __future__ import annotations

import json
import os
import pathlib
import subprocess
import types

import pytest
from conftest import REPO_ROOT

DOTFILES_DIR_NAME = ".dotfiles"
LOCAL_REPO_URI = f"file://{REPO_ROOT}"

# A fixed git identity for tests that create commits: a fresh CI runner has no
# user.name/user.email configured, so commit and commit-tree would fail.
_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


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


def _git(
    dotfiles_dir: pathlib.Path,
    home: pathlib.Path,
    *args: str,
) -> subprocess.CompletedProcess[str]:
    """Run git against the bare dotfiles repo with a work-tree and a fixed
    identity, so commits in the tests never depend on the host git config."""

    env = {**os.environ, **_GIT_IDENTITY_ENV}
    return subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "--work-tree", str(home), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _git_bare(git_dir: pathlib.Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run git against a bare repo (no work-tree) with a fixed identity."""

    env = {**os.environ, **_GIT_IDENTITY_ENV}
    return subprocess.run(
        ["git", "--git-dir", str(git_dir), *args],
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )


def _commit_child(git_dir: pathlib.Path, parent: str, message: str) -> str:
    """Create a commit with *parent*'s tree on top of *parent*, return its sha.

    Reuses the parent tree so a re-checkout of the child produces identical
    files: the tests only care about the commit graph, not the content.
    """

    tree = _git_bare(git_dir, "rev-parse", f"{parent}^{{tree}}").stdout.strip()
    return _git_bare(git_dir, "commit-tree", tree, "-p", parent, "-m", message).stdout.strip()


def _hermetic_branch_and_remote(
    dotfiles_dir: pathlib.Path,
    home: pathlib.Path,
    tmp_path: pathlib.Path,
    branch: str = "ci-main",
) -> tuple[str, str, pathlib.Path]:
    """Give the bootstrapped bare repo a named branch and a controlled origin.

    The CI checkout of a PR is a detached, shallow clone, so the bootstrapped
    bare repo would otherwise inherit an empty branch name (no fast-forward) and
    a single-commit history. These update tests need a real branch and an origin
    that actually serves it, independent of how REPO_ROOT was checked out.
    Returns ``(branch, base_sha, remote_dir)``; both the local branch and the
    remote start at ``base_sha``.
    """

    base = _git(dotfiles_dir, home, "rev-parse", "HEAD").stdout.strip()
    _git(dotfiles_dir, home, "branch", "-f", branch, base)
    _git(dotfiles_dir, home, "symbolic-ref", "HEAD", f"refs/heads/{branch}")

    remote_dir = tmp_path / "remote.git"
    subprocess.run(
        ["git", "clone", "--bare", str(dotfiles_dir), str(remote_dir)],
        capture_output=True,
        text=True,
        check=True,
    )
    _git(dotfiles_dir, home, "remote", "set-url", "origin", str(remote_dir))
    return branch, base, remote_dir


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


# ==================
# Local-change guard
# ==================


def test_update_preserves_local_modifications(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """--update must keep uncommitted edits to a tracked file (autostash).

    A same-repo update is a no-op on HEAD, so the pull never touches .nanorc;
    the user's edit must survive the re-checkout without any prompt or --force.
    """

    _ = _bootstrap(dotfiles_module, fake_home)

    edited = "# my local edit that must survive\n"
    (fake_home / ".nanorc").write_text(edited)

    ret = dotfiles_module.update(
        dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )
    assert ret == 0
    assert (fake_home / ".nanorc").read_text() == edited


def test_update_guards_local_commit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    """A non-interactive --update must refuse to drop a local commit.

    pytest captures stdin, so _confirm_override sees a non-interactive shell and
    declines. HEAD has not moved yet, so the local commit stays reachable.
    """

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    _, _base, _ = _hermetic_branch_and_remote(dotfiles_dir, fake_home, tmp_path)

    # A commit only on the local branch: advancing to the remote tip would drop it.
    (fake_home / ".nanorc").write_text("# committed locally\n")
    _git(dotfiles_dir, fake_home, "add", "--", ".nanorc")
    _git(dotfiles_dir, fake_home, "commit", "-m", "local only commit")
    local_sha = dotfiles_module._git_head_sha(dotfiles_dir)

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )
    assert ret == 1
    assert dotfiles_module._git_head_sha(dotfiles_dir) == local_sha


def test_update_force_drops_local_commit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    """--update --force must drop the local commit and take the remote tip."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    _, base, _ = _hermetic_branch_and_remote(dotfiles_dir, fake_home, tmp_path)

    (fake_home / ".nanorc").write_text("# committed locally\n")
    _git(dotfiles_dir, fake_home, "add", "--", ".nanorc")
    _git(dotfiles_dir, fake_home, "commit", "-m", "local only commit")

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
        force=True,
    )
    assert ret == 0
    assert dotfiles_module._git_head_sha(dotfiles_dir) == base


def test_update_fast_forwards_to_remote_tip(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    """--update must advance HEAD to the remote tip.

    A bare clone sets no fetch refspec, so a plain fetch never moves
    refs/heads/*. Put the remote one commit ahead of the local branch and check
    that --update fetches the remote tip and fast-forwards HEAD to it.
    """

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    branch, base, remote_dir = _hermetic_branch_and_remote(
        dotfiles_dir, fake_home, tmp_path
    )

    # Advance the remote one commit past the local branch.
    ahead = _commit_child(remote_dir, base, "remote advance")
    _git_bare(remote_dir, "update-ref", f"refs/heads/{branch}", ahead)

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )
    assert ret == 0
    assert dotfiles_module._git_head_sha(dotfiles_dir) == ahead


# =========
# Autostash
# =========


def test_reapply_stashed_restores_untouched_edit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """An edit to a file the pull did not change must be written straight back."""

    rel = pathlib.Path(".nanorc")
    (fake_home / rel).write_text("# fresh checkout\n")
    backup_dir = fake_home / ".dotfiles_backup"

    preserved, conflicts = dotfiles_module._reapply_stashed(
        {rel: b"# my edit\n"},
        set(),
        fake_home,
        backup_dir,
    )

    assert preserved == [rel]
    assert conflicts == []
    assert (fake_home / rel).read_bytes() == b"# my edit\n"


def test_reapply_stashed_backs_up_conflicting_edit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """When the pull also changed the file, keep the incoming version and park
    the user's edit in the backup dir instead of merging or losing it."""

    rel = pathlib.Path(".nanorc")
    incoming = b"# updated upstream\n"
    (fake_home / rel).write_bytes(incoming)
    backup_dir = fake_home / ".dotfiles_backup"

    preserved, conflicts = dotfiles_module._reapply_stashed(
        {rel: b"# my edit\n"},
        {rel},
        fake_home,
        backup_dir,
    )

    assert preserved == []
    assert len(conflicts) == 1
    conflict_rel, dst = conflicts[0]
    assert conflict_rel == rel
    # The incoming version stays in HOME, the user's edit is parked, no merge.
    assert (fake_home / rel).read_bytes() == incoming
    assert dst.read_bytes() == b"# my edit\n"


def test_unique_local_backup_never_clobbers_pristine(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """The collision backup must not overwrite the pristine bootstrap backup at
    backup_dir/rel."""

    rel = pathlib.Path(".nanorc")
    backup_dir = fake_home / ".dotfiles_backup"
    (backup_dir / rel).parent.mkdir(parents=True, exist_ok=True)
    (backup_dir / rel).write_text("# pristine original\n")

    dst = dotfiles_module._unique_local_backup(backup_dir, rel)

    assert dst != backup_dir / rel
    assert not dst.exists()
    assert (backup_dir / rel).read_text() == "# pristine original\n"


def test_update_preserves_edit_and_drops_commit_together(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    """With --force, a local commit is dropped while an uncommitted edit to a
    different file is preserved by the autostash."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    _, base, _ = _hermetic_branch_and_remote(dotfiles_dir, fake_home, tmp_path)

    # A local-only commit touching .nanorc.
    (fake_home / ".nanorc").write_text("# committed locally\n")
    _git(dotfiles_dir, fake_home, "add", "--", ".nanorc")
    _git(dotfiles_dir, fake_home, "commit", "-m", "local only commit")

    # An uncommitted edit to a different tracked file.
    edited = "# uncommitted starship edit\n"
    (fake_home / ".config" / "starship.toml").write_text(edited)

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
        force=True,
    )
    assert ret == 0
    assert dotfiles_module._git_head_sha(dotfiles_dir) == base
    assert (fake_home / ".config" / "starship.toml").read_text() == edited


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


def test_confirm_override_force_short_circuits(
    dotfiles_module: types.ModuleType,
) -> None:
    """force=True must answer yes without touching stdin."""

    assert dotfiles_module._confirm_override(force=True) is True


def test_confirm_override_non_interactive_declines(
    dotfiles_module: types.ModuleType,
) -> None:
    """A non-interactive stdin must default to no."""

    assert dotfiles_module._confirm_override(force=False) is False


def test_discarded_commits_lists_dropped_local_commit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """A local commit reachable only from the dropped sha must be reported."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME

    kept = dotfiles_module._git_head_sha(dotfiles_dir)

    # Craft a commit on top of HEAD to simulate an unpushed local commit that a
    # advancing to the remote tip would drop. commit-tree needs an author and committer identity,
    # absent on a fresh CI runner, so it is supplied through the environment.
    commit_env = {**os.environ, **_GIT_IDENTITY_ENV}
    empty_tree = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "hash-object", "-t", "tree", "/dev/null"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dropped = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "commit-tree", empty_tree, "-p", kept, "-m", "local wip"],
        check=True,
        capture_output=True,
        text=True,
        env=commit_env,
    ).stdout.strip()

    discarded = dotfiles_module._discarded_commits(dotfiles_dir, kept, dropped)
    assert any("local wip" in line for line in discarded)


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


# =======================
# Skip-worktree hardening
# =======================


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

    # 'does/not/exist' is not in the index, so update-index fails on it; the mix
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

    dotfiles_module.DotfilesRepo._populate_index(
        str(git_dir), fake_home, checked_out
    )

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
    # The user's own content is untouched.
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


# ==============
# Error messages
# ==============


def test_describe_error_unpacks_called_process_error(
    dotfiles_module: types.ModuleType,
) -> None:
    """A CalledProcessError must surface git's stderr, not just the exit code.

    str(CalledProcessError) drops the captured output, so the raw exception only
    says 'returned non-zero exit status 128'. describe_error has to include the
    command and the real message.
    """

    exc = subprocess.CalledProcessError(
        returncode=128,
        cmd=["git", "update-index", "--skip-worktree", "--", "does/not/exist"],
        output="",
        stderr="fatal: Unable to mark file does/not/exist\n",
    )

    message = dotfiles_module.describe_error(exc)

    assert "exit 128" in message
    assert "git update-index --skip-worktree" in message
    assert "fatal: Unable to mark file does/not/exist" in message


def test_describe_error_passes_through_plain_exception(
    dotfiles_module: types.ModuleType,
) -> None:
    """Non-subprocess errors must stay untouched."""

    assert dotfiles_module.describe_error(RuntimeError("boom")) == "boom"
