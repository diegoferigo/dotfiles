# Fast unit tests that call DotfilesRepo and its helpers directly, without going
# through the subprocess/shebang. They cover the backup, manifest, rollback and
# uninstall logic without paying the pixi exec startup cost on every call. The
# integration tests that exercise the full subprocess -> shebang -> clone flow
# live in test_clone.py and test_checkout.py.

from __future__ import annotations

import json
import os
import pathlib
import shutil
import stat
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
    _strip_repository_secrets(dotfiles_dir)
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
    mod.Bashrc.inject(home, *mod.Bashrc.read_blocks(home))
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


def _strip_repository_secrets(dotfiles_dir: pathlib.Path) -> None:
    """Remove production secret fixtures from one isolated bare test clone."""

    paths = [
        path
        for path in _git_bare(
            dotfiles_dir,
            "ls-tree",
            "-r",
            "--name-only",
            "HEAD",
        ).stdout.splitlines()
        if path.startswith("secrets/home/")
        or path
        in {
            ".config/dotfiles/age/identity.txt.age",
            ".config/dotfiles/age/recipients.txt",
        }
    ]
    if not paths:
        return

    parent = _git_bare(dotfiles_dir, "rev-parse", "HEAD").stdout.strip()
    _git_bare(dotfiles_dir, "read-tree", parent)
    subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "update-index", "--index-info"],
        input="".join(f"0 {'0' * 40}\t{path}\n" for path in paths),
        capture_output=True,
        text=True,
        env={**os.environ, **_GIT_IDENTITY_ENV},
        check=True,
    )
    tree = _git_bare(dotfiles_dir, "write-tree").stdout.strip()
    commit = _git_bare(
        dotfiles_dir,
        "commit-tree",
        tree,
        "-p",
        parent,
        "-m",
        "Remove production secret fixtures",
    ).stdout.strip()
    _git_bare(dotfiles_dir, "update-ref", "HEAD", commit)
    _git_bare(dotfiles_dir, "remote", "set-url", "origin", str(dotfiles_dir))


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


def _install_fake_age(
    tmp_path: pathlib.Path,
) -> pathlib.Path:
    """Create a deterministic age stand-in for encryption/decryption tests."""

    age = tmp_path / "age"
    age.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if "--decrypt" in args:
    if "--output" in args:
        output = pathlib.Path(args[args.index("--output") + 1])
        payload = pathlib.Path(args[-1]).read_bytes()
        for prefix in (b"AGE-IDENTITY-OLD\\n", b"AGE-IDENTITY-NEW\\n"):
            if payload.startswith(prefix):
                output.write_bytes(payload.removeprefix(prefix))
                break
        else:
            print("invalid test identity", file=sys.stderr)
            raise SystemExit(1)
    else:
        payload = sys.stdin.buffer.read()
        if not payload.startswith(b"AGE-TEST\\n"):
            print("invalid test ciphertext", file=sys.stderr)
            raise SystemExit(1)
        sys.stdout.buffer.write(payload.removeprefix(b"AGE-TEST\\n"))
else:
    output = pathlib.Path(args[args.index("--output") + 1])
    source = pathlib.Path(args[-1])
    prefix = b"AGE-IDENTITY-NEW\\n" if "--passphrase" in args else b"AGE-TEST\\n"
    output.write_bytes(prefix + source.read_bytes())
"""
    )
    age.chmod(0o755)
    return age


def _install_fake_age_keygen(tmp_path: pathlib.Path) -> pathlib.Path:
    """Create a deterministic age-keygen stand-in."""

    age_keygen = tmp_path / "age-keygen"
    age_keygen.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if "-y" in args:
    print("age1testrecipient")
else:
    output = pathlib.Path(args[args.index("--output") + 1])
    output.write_text("AGE-SECRET-KEY-TEST\\n")
    output.chmod(0o600)
"""
    )
    age_keygen.chmod(0o755)
    return age_keygen


def _install_test_identity(home: pathlib.Path, mod: types.ModuleType) -> pathlib.Path:
    """Install a non-secret test identity with the required permissions."""

    identity = home / mod.AGE_IDENTITY
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text("AGE-SECRET-KEY-TEST\n")
    identity.chmod(0o600)
    return identity


def _install_test_recipients(home: pathlib.Path, mod: types.ModuleType) -> pathlib.Path:
    """Install the public recipient fixture."""

    recipients = home / mod.AGE_RECIPIENTS
    recipients.parent.mkdir(parents=True, exist_ok=True)
    recipients.write_text("age1testrecipient\n")
    return recipients


def _commit_test_recipients(
    dotfiles_dir: pathlib.Path,
    home: pathlib.Path,
    mod: types.ModuleType,
) -> pathlib.Path:
    """Install and commit the public recipient fixture."""

    recipients = _install_test_recipients(home, mod)
    _git(dotfiles_dir, home, "add", "--", str(mod.AGE_RECIPIENTS))
    _git(dotfiles_dir, home, "commit", "-m", "add test recipient")
    return recipients


def _commit_test_secret(
    dotfiles_dir: pathlib.Path,
    home: pathlib.Path,
    target: pathlib.Path,
    plaintext: bytes,
) -> pathlib.PurePosixPath:
    """Commit fake ciphertext for *target* into the bootstrapped bare repo."""

    relative = target.relative_to(home)
    source = pathlib.PurePosixPath("secrets/home") / f"{relative}.age"
    source_path = home / pathlib.Path(*source.parts)
    source_path.parent.mkdir(parents=True, exist_ok=True)
    source_path.write_bytes(b"AGE-TEST\n" + plaintext)
    _git(dotfiles_dir, home, "add", "--sparse", "-f", "--", str(source))
    _git(dotfiles_dir, home, "commit", "-m", f"add test secret {relative}")
    shutil.rmtree(home / "secrets")
    return source


def _remove_test_secret(
    dotfiles_dir: pathlib.Path,
    home: pathlib.Path,
    source: pathlib.PurePosixPath,
) -> None:
    """Commit removal of a sparse-excluded encrypted test source."""

    _git(dotfiles_dir, home, "rm", "--cached", "--sparse", "-f", "--", str(source))
    _git(dotfiles_dir, home, "commit", "-m", f"remove test secret {source}")


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


def test_update_refuses_and_preserves_staged_ciphertext(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Update cannot reset ciphertext staged by the authoring command."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    _hermetic_branch_and_remote(dotfiles_dir, fake_home, tmp_path)
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"staged\n")
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.encrypt_secret(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
        plaintext,
    )
    source = "secrets/home/.config/private.conf.age"
    before = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        check=True,
        capture_output=True,
    ).stdout

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )

    assert ret == 1
    after = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        check=True,
        capture_output=True,
    ).stdout
    assert after == before


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

    # Patch read_blocks so update() does not depend on tracked Bash payloads.
    environment_block = (
        f"{dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN}\n"
        f"export PATH=\"$HOME/.pixi/bin:$PATH\"\n"
        f"{dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_END}"
    )
    interactive_block = (
        f"{dotfiles_module.Bashrc.BLOCK_BEGIN}\n"
        f"[[ -f ~/.bashrc.d/init ]] && source ~/.bashrc.d/init\n"
        f"{dotfiles_module.Bashrc.BLOCK_END}"
    )
    monkeypatch.setattr(
        dotfiles_module.Bashrc,
        "read_blocks",
        lambda _: (environment_block, interactive_block),
    )

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=backup_dir,
    )
    assert ret == 0
    assert "/*" in sparse_file.read_text()


def test_update_preserves_manifest_backup_directory(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Update keeps using the backup directory selected during bootstrap."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    manifest_path = dotfiles_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    custom_backup = fake_home / "custom-backup"
    manifest["backup_dir"] = str(custom_backup)
    manifest_path.write_text(json.dumps(manifest))

    assert (
        dotfiles_module.update(
            dotfiles_dir=dotfiles_dir,
            home=fake_home,
            backup_dir=fake_home / ".dotfiles_backup",
        )
        == 0
    )

    updated = json.loads(manifest_path.read_text())
    assert updated["backup_dir"] == str(custom_backup)


def test_update_rejects_invalid_secret_manifest(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Update must not silently discard malformed secret deployment metadata."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    manifest_path = dotfiles_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["secrets"] = ["invalid"]
    manifest_path.write_text(json.dumps(manifest))

    assert (
        dotfiles_module.update(
            dotfiles_dir=dotfiles_dir,
            home=fake_home,
            backup_dir=fake_home / ".dotfiles_backup",
        )
        == 1
    )
    assert json.loads(manifest_path.read_text())["secrets"] == ["invalid"]


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

    def _boom(_: pathlib.Path) -> tuple[str, str]:
        raise RuntimeError("simulated inject failure")

    monkeypatch.setattr(dotfiles_module.Bashrc, "read_blocks", _boom)

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


def test_reapply_stashed_ignores_converged_edit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """An incoming file equal to the local edit must not create a conflict backup."""

    rel = pathlib.Path(".nanorc")
    converged = b"# same local and upstream edit\n"
    (fake_home / rel).write_bytes(converged)
    backup_dir = fake_home / ".dotfiles_backup"

    preserved, conflicts = dotfiles_module._reapply_stashed(
        {rel: converged},
        {rel},
        fake_home,
        backup_dir,
    )

    assert preserved == []
    assert conflicts == []
    assert (fake_home / rel).read_bytes() == converged
    assert not backup_dir.exists()


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


def test_update_does_not_back_up_converged_edit(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    """A local edit already present in the remote update must converge cleanly."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    branch, base, _remote_dir = _hermetic_branch_and_remote(
        dotfiles_dir, fake_home, tmp_path
    )
    rel = pathlib.Path(".nanorc")
    converged = b"# same local and upstream edit\n"
    (fake_home / rel).write_bytes(converged)
    _git(dotfiles_dir, fake_home, "add", "--", str(rel))
    _git(dotfiles_dir, fake_home, "commit", "-m", "remote file update")
    remote_sha = dotfiles_module._git_head_sha(dotfiles_dir)
    _git(
        dotfiles_dir,
        fake_home,
        "push",
        "origin",
        f"{remote_sha}:refs/heads/{branch}",
    )
    _git(dotfiles_dir, fake_home, "update-ref", f"refs/heads/{branch}", base)
    _git(dotfiles_dir, fake_home, "read-tree", "--reset", base)

    ret = dotfiles_module.update(
        dotfiles_dir=dotfiles_dir,
        home=fake_home,
        backup_dir=fake_home / ".dotfiles_backup",
    )

    assert ret == 0
    assert dotfiles_module._git_head_sha(dotfiles_dir) == remote_sha
    assert (fake_home / rel).read_bytes() == converged
    assert not (fake_home / ".dotfiles_backup" / ".nanorc.local").exists()


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


def _make_block(
    mod: types.ModuleType,
    content: str,
    *,
    environment: bool,
) -> str:
    """Build one minimal managed block for injection tests."""

    begin = (
        mod.Bashrc.ENVIRONMENT_BLOCK_BEGIN
        if environment
        else mod.Bashrc.BLOCK_BEGIN
    )
    end = (
        mod.Bashrc.ENVIRONMENT_BLOCK_END
        if environment
        else mod.Bashrc.BLOCK_END
    )
    return f"{begin}\n{content}\n{end}"


def _make_blocks(mod: types.ModuleType) -> tuple[str, str]:
    """Build both managed blocks for injection tests."""

    return (
        _make_block(mod, "export DOTFILES_ENV=1", environment=True),
        _make_block(mod, "echo dotfiles", environment=False),
    )


def test_read_blocks_sources_environment_and_embeds_interactive_payload(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """The top block stays compact while the interactive payload remains embedded."""

    (fake_home / dotfiles_module.Bashrc.ENVIRONMENT_FILE).write_text(
        "export SHOULD_NOT_BE_EMBEDDED=1\n"
    )
    (fake_home / dotfiles_module.Bashrc.DOTFILES_FILE).write_text(
        "echo embedded-interactive\n"
    )

    environment_block, interactive_block = dotfiles_module.Bashrc.read_blocks(
        fake_home
    )

    assert (
        f"source ~/{dotfiles_module.Bashrc.ENVIRONMENT_FILE}"
        in environment_block
    )
    assert "SHOULD_NOT_BE_EMBEDDED" not in environment_block
    assert "echo embedded-interactive" in interactive_block


def test_inject_bashrc_places_blocks_around_user_content(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """The environment block is prepended and the interactive block appended."""

    original = "# existing content\n"
    (fake_home / ".bashrc").write_text(original)
    dotfiles_module.Bashrc.inject(fake_home, *_make_blocks(dotfiles_module))

    content = (fake_home / ".bashrc").read_text()
    assert content.startswith(dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN)
    assert content.endswith(f"{dotfiles_module.Bashrc.BLOCK_END}\n")
    assert (
        content.index(dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_END)
        < content.index(original.strip())
        < content.index(dotfiles_module.Bashrc.BLOCK_BEGIN)
    )


def test_inject_bashrc_creates_file_if_missing(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """inject_bashrc must create ~/.bashrc when it does not exist."""

    bashrc = fake_home / ".bashrc"
    if bashrc.exists():
        bashrc.unlink()
    dotfiles_module.Bashrc.inject(fake_home, *_make_blocks(dotfiles_module))

    content = bashrc.read_text()
    assert content.startswith(dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN)
    assert content.endswith(f"{dotfiles_module.Bashrc.BLOCK_END}\n")


def test_inject_bashrc_updates_existing_blocks_without_duplicates(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Repeated injection replaces both managed blocks without duplication."""

    stale_environment = _make_block(
        dotfiles_module,
        "export DOTFILES_ENV=old",
        environment=True,
    )
    stale_interactive = _make_block(
        dotfiles_module,
        "# old content",
        environment=False,
    )
    (fake_home / ".bashrc").write_text(
        f"{stale_environment}\n# user content\n{stale_interactive}\n"
    )

    environment_block, interactive_block = _make_blocks(dotfiles_module)
    dotfiles_module.Bashrc.inject(
        fake_home,
        environment_block,
        interactive_block,
    )

    content = (fake_home / ".bashrc").read_text()
    assert content.count(dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN) == 1
    assert content.count(dotfiles_module.Bashrc.BLOCK_BEGIN) == 1
    assert "DOTFILES_ENV=old" not in content
    assert "# old content" not in content
    assert "# user content" in content


def test_remove_blocks_preserves_user_content(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Removing both managed blocks preserves the user's Bash configuration."""

    original = "line_before\nline_after\n"
    (fake_home / ".bashrc").write_text(original)
    dotfiles_module.Bashrc.inject(fake_home, *_make_blocks(dotfiles_module))

    dotfiles_module.Bashrc.remove_blocks(fake_home)

    content = (fake_home / ".bashrc").read_text()
    assert dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN not in content
    assert dotfiles_module.Bashrc.BLOCK_BEGIN not in content
    assert content == original


# ==================
# Encrypted dotfiles
# ==================


def test_secret_target_maps_below_home_and_rejects_unsafe_paths(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Encrypted sources cannot escape HOME or overlap manager-owned state."""

    source = pathlib.PurePosixPath("secrets/home/.ssh/config.d/rai.conf.age")
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    backup_dir = fake_home / ".dotfiles_backup"
    assert dotfiles_module._secret_target(
        source, fake_home, dotfiles_dir, backup_dir
    ) == (fake_home / ".ssh/config.d/rai.conf")

    unsafe = (
        "secrets/home/../outside.age",
        "secrets/home/.dotfiles/HEAD.age",
        "secrets/home/.dotfiles_backup/private.age",
        "secrets/home/.config/dotfiles/age/identity.txt.age",
        "secrets/home/.config/dotfiles/age/identity.txt.age.age",
        "secrets/home/.config/dotfiles/age/recipients.txt.age",
    )
    for path in unsafe:
        with pytest.raises(ValueError):
            dotfiles_module._secret_target(
                pathlib.PurePosixPath(path),
                fake_home,
                dotfiles_dir,
                backup_dir,
            )

    (fake_home / "alias").symlink_to(fake_home)
    with pytest.raises(ValueError, match="symlink"):
        dotfiles_module._secret_target(
            pathlib.PurePosixPath("secrets/home/alias/.nanorc.age"),
            fake_home,
            dotfiles_dir,
            backup_dir,
        )


def test_apply_secrets_missing_identity_fails_without_touching_target(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """Explicit apply fails clearly when the identity is missing."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".ssh/config.d/rai.conf"
    _commit_test_secret(dotfiles_dir, fake_home, target, b"Host robot\n")

    with pytest.raises(RuntimeError, match="pending"):
        dotfiles_module.apply_secrets(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
        )

    assert not target.exists()


def test_identity_symlink_is_rejected(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
) -> None:
    """The private identity must be a regular file, not an indirect symlink."""

    real_identity = tmp_path / "identity.txt"
    real_identity.write_text("AGE-SECRET-KEY-TEST\n")
    real_identity.chmod(0o600)
    identity = fake_home / dotfiles_module.AGE_IDENTITY
    identity.parent.mkdir(parents=True)
    identity.symlink_to(real_identity)

    path, error = dotfiles_module._identity_status(fake_home)

    assert path == identity
    assert error is not None
    assert "symlink" in error


def test_apply_secrets_unlocks_repository_managed_identity(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply prompts through age once and never persists the unlocked identity."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    encrypted = fake_home / dotfiles_module.AGE_ENCRYPTED_IDENTITY
    encrypted.parent.mkdir(parents=True, exist_ok=True)
    encrypted.write_bytes(b"AGE-IDENTITY-OLD\nAGE-SECRET-KEY-TEST\n")
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )

    assert target.read_bytes() == b"managed\n"
    assert not (fake_home / dotfiles_module.AGE_IDENTITY).exists()


def test_init_secret_identity_generates_and_stages_metadata(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Init creates no persistent plaintext identity and stages public metadata."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    age = _install_fake_age(tmp_path)
    age_keygen = _install_fake_age_keygen(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    monkeypatch.setattr(dotfiles_module, "find_age_keygen", lambda _age: age_keygen)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    dotfiles_module.init_secret_identity(dotfiles_dir, fake_home)

    encrypted = fake_home / dotfiles_module.AGE_ENCRYPTED_IDENTITY
    recipients = fake_home / dotfiles_module.AGE_RECIPIENTS
    assert encrypted.read_bytes().startswith(b"AGE-IDENTITY-NEW\n")
    assert recipients.read_text() == "age1testrecipient\n"
    assert not (fake_home / dotfiles_module.AGE_IDENTITY).exists()
    staged = _git(
        dotfiles_dir,
        fake_home,
        "diff",
        "--cached",
        "--name-only",
    ).stdout.splitlines()
    assert str(dotfiles_module.AGE_ENCRYPTED_IDENTITY) in staged
    assert str(dotfiles_module.AGE_RECIPIENTS) in staged


def test_change_secret_passphrase_preserves_and_stages_identity(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Passphrase rotation changes only the encrypted identity wrapper."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    encrypted = fake_home / dotfiles_module.AGE_ENCRYPTED_IDENTITY
    encrypted.parent.mkdir(parents=True, exist_ok=True)
    encrypted.write_bytes(b"AGE-IDENTITY-OLD\nAGE-SECRET-KEY-TEST\n")
    _git(
        dotfiles_dir,
        fake_home,
        "add",
        "--",
        str(dotfiles_module.AGE_ENCRYPTED_IDENTITY),
    )
    _git(dotfiles_dir, fake_home, "commit", "-m", "Add encrypted test identity")
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    dotfiles_module.change_secret_passphrase(dotfiles_dir, fake_home)

    assert encrypted.read_bytes() == (
        b"AGE-IDENTITY-NEW\nAGE-SECRET-KEY-TEST\n"
    )
    staged = _git(
        dotfiles_dir,
        fake_home,
        "diff",
        "--cached",
        "--name-only",
    ).stdout.splitlines()
    assert staged == [str(dotfiles_module.AGE_ENCRYPTED_IDENTITY)]


def test_encrypt_secret_stages_sparse_ciphertext_without_home_copy(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authoring writes one index blob and never checks ciphertext into HOME."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".ssh/config.d/robot.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"Host robot\n")
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    dotfiles_module.encrypt_secret(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
        plaintext,
    )

    source = "secrets/home/.ssh/config.d/robot.conf.age"
    ciphertext = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        check=True,
        capture_output=True,
    ).stdout
    assert ciphertext == b"AGE-TEST\nHost robot\n"
    assert not (fake_home / "secrets").exists()
    status = _git(
        dotfiles_dir,
        fake_home,
        "status",
        "--short",
        "--untracked-files=no",
    ).stdout.splitlines()
    assert f"A  {source}" in status


def test_encrypt_secret_requires_committed_recipient(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An untracked recipient cannot produce undecryptable committed ciphertext."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"managed\n")
    _install_test_recipients(fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    with pytest.raises(RuntimeError, match="recipients file is not committed"):
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
        )

    source = "secrets/home/.config/private.conf.age"
    staged = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        capture_output=True,
    )
    assert staged.returncode != 0


def test_encrypt_secret_rejects_public_dotfile_target(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authoring cannot create a secret that apply will reject as public."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    public = fake_home / ".bashrc.dotfiles.sh"

    with pytest.raises(RuntimeError, match="already a public dotfile"):
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            public,
        )

    source = "secrets/home/.bashrc.dotfiles.sh.age"
    staged = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        capture_output=True,
    )
    assert staged.returncode != 0


def test_encrypt_secret_rejects_modified_recipient(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Encryption cannot use recipient bytes that differ from the repository."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"managed\n")
    recipients = _commit_test_recipients(
        dotfiles_dir, fake_home, dotfiles_module
    )
    recipients.write_text("age1differentrecipient\n")
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    with pytest.raises(RuntimeError, match="recipients file has unstaged changes"):
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
        )


def test_encrypt_secret_refuses_existing_staged_ciphertext(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second authoring attempt cannot overwrite an uncommitted ciphertext."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"first\n")
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.encrypt_secret(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
        plaintext,
    )
    plaintext.write_bytes(b"second\n")

    with pytest.raises(RuntimeError, match="already has staged changes"):
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
        )

    source = "secrets/home/.config/private.conf.age"
    ciphertext = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        check=True,
        capture_output=True,
    ).stdout
    assert ciphertext == b"AGE-TEST\nfirst\n"


def test_encrypt_secret_reports_unresolved_repository_conflicts(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Authoring stops before encryption while the shared index is unmerged."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"managed\n")
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    blobs = [
        subprocess.run(
            ["git", "--git-dir", str(dotfiles_dir), "hash-object", "-w", "--stdin"],
            input=value,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        for value in (b"base\n", b"ours\n", b"theirs\n")
    ]
    index_info = "".join(
        f"100644 {blob} {stage}\tconflicted.txt\n"
        for stage, blob in enumerate(blobs, start=1)
    )
    subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "update-index", "--index-info"],
        input=index_info,
        text=True,
        check=True,
        capture_output=True,
    )

    with pytest.raises(RuntimeError, match="unresolved merge conflicts") as exc_info:
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
        )

    assert "conflicted.txt" in str(exc_info.value)
    assert "Resolve them before encrypting" in str(exc_info.value)


def test_encrypt_secret_explains_other_conflicts_before_resolution(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A ciphertext conflict names other paths that must be resolved first."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"managed\n")
    source = "secrets/home/.config/private.conf.age"
    monkeypatch.setattr(
        dotfiles_module,
        "_unmerged_paths",
        lambda _dotfiles_dir, **_kwargs: [".bashrc.dotfiles.sh", source],
    )

    with pytest.raises(RuntimeError, match="Other unresolved paths") as exc_info:
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
            resolve=True,
        )

    message = str(exc_info.value)
    assert ".bashrc.dotfiles.sh" in message
    assert "then re-run this command with '--resolve'" in message


def test_encrypt_secret_resolves_its_ciphertext_conflict(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--resolve replaces only the matching unmerged ciphertext from plaintext."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"resolved\n")
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    source = "secrets/home/.config/private.conf.age"
    blobs = [
        subprocess.run(
            ["git", "--git-dir", str(dotfiles_dir), "hash-object", "-w", "--stdin"],
            input=value,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        for value in (b"base\n", b"ours\n", b"theirs\n")
    ]
    index_info = "".join(
        f"100644 {blob} {stage}\t{source}\n"
        for stage, blob in enumerate(blobs, start=1)
    )
    subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "update-index", "--index-info"],
        input=index_info,
        text=True,
        check=True,
        capture_output=True,
    )
    materialized = fake_home / source
    materialized.parent.mkdir(parents=True, exist_ok=True)
    materialized.write_text("<<<<<<< HEAD\nciphertext\n>>>>>>> theirs\n")

    with pytest.raises(RuntimeError, match="same command with '--resolve'"):
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
        )

    dotfiles_module.encrypt_secret(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
        plaintext,
        resolve=True,
    )

    assert dotfiles_module._unmerged_paths(dotfiles_dir) == []
    assert not (fake_home / "secrets").exists()
    ciphertext = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "show", f":{source}"],
        check=True,
        capture_output=True,
    ).stdout
    assert ciphertext == b"AGE-TEST\nresolved\n"


def test_encrypt_secret_restores_conflict_index_when_staging_fails(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-cacheinfo failure leaves every unmerged index stage intact."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    plaintext = fake_home / ".config/private.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"resolved\n")
    _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
    age = _install_fake_age(tmp_path)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    source = "secrets/home/.config/private.conf.age"
    blobs = [
        subprocess.run(
            ["git", "--git-dir", str(dotfiles_dir), "hash-object", "-w", "--stdin"],
            input=value,
            check=True,
            capture_output=True,
        ).stdout.decode().strip()
        for value in (b"base\n", b"ours\n", b"theirs\n")
    ]
    index_info = "".join(
        f"100644 {blob} {stage}\t{source}\n"
        for stage, blob in enumerate(blobs, start=1)
    )
    subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "update-index", "--index-info"],
        input=index_info,
        text=True,
        check=True,
        capture_output=True,
    )
    before = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "ls-files", "--unmerged"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    original_run = dotfiles_module.subprocess.run

    def _fail_skip_worktree(
        args: list[str],
        *run_args: object,
        **run_kwargs: object,
    ) -> subprocess.CompletedProcess[bytes]:
        if "update-index" in args and "--skip-worktree" in args:
            raise subprocess.CalledProcessError(
                returncode=128,
                cmd=args,
                stderr=b"simulated skip-worktree failure",
            )
        return original_run(args, *run_args, **run_kwargs)

    monkeypatch.setattr(dotfiles_module.subprocess, "run", _fail_skip_worktree)

    with pytest.raises(
        subprocess.CalledProcessError,
        match="returned non-zero exit status",
    ):
        dotfiles_module.encrypt_secret(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
            plaintext,
            resolve=True,
        )

    after = subprocess.run(
        ["git", "--git-dir", str(dotfiles_dir), "ls-files", "--unmerged"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    assert after == before
    assert not (dotfiles_dir / "index.lock").exists()


def test_apply_secrets_deploys_from_git_and_updates_manifest(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Apply reads sparse-excluded ciphertext from Git and writes mode 0600."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".ssh/config.d/rai.conf"
    _commit_test_secret(
        dotfiles_dir,
        fake_home,
        target,
        b"Host robot\n    HostName 192.0.2.10\n",
    )
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )

    assert target.read_bytes() == b"Host robot\n    HostName 192.0.2.10\n"
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert not (fake_home / "secrets").exists()
    manifest = json.loads((dotfiles_dir / "manifest.json").read_text())
    entry = manifest["secrets"][".ssh/config.d/rai.conf"]
    assert entry["source_blob"]
    assert entry["plaintext_sha256"] == dotfiles_module._sha256(target.read_bytes())
    assert "source" not in entry
    assert "backed_up" not in entry
    assert "created_dirs" not in entry


def test_apply_secrets_backs_up_existing_target_and_uninstall_restores_it(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A first apply preserves the user's file and uninstall restores it."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".ssh/config.d/rai.conf"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"Host original\n")
    _commit_test_secret(dotfiles_dir, fake_home, target, b"Host managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )
    assert target.read_bytes() == b"Host managed\n"

    assert dotfiles_module.uninstall(dotfiles_dir, fake_home, force=True) == 0
    assert target.read_bytes() == b"Host original\n"
    assert (fake_home / dotfiles_module.AGE_IDENTITY).exists()


def test_apply_secrets_backs_up_identical_unmanaged_target(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An identical pre-existing file remains user-owned across uninstall."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"same\n")
    _commit_test_secret(dotfiles_dir, fake_home, target, b"same\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )

    assert (fake_home / ".dotfiles_backup/.config/private.conf").is_file()
    assert dotfiles_module.uninstall(dotfiles_dir, fake_home, force=True) == 0
    assert target.read_bytes() == b"same\n"


def test_apply_secrets_guards_local_edit_and_force_replaces_it(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A deployed secret edited locally requires --force before replacement."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )
    target.write_bytes(b"local edit\n")

    with pytest.raises(RuntimeError, match="locally modified"):
        dotfiles_module.apply_secrets(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
        )
    assert target.read_bytes() == b"local edit\n"

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
        force=True,
    )
    assert target.read_bytes() == b"managed\n"


def test_apply_secrets_records_completed_targets_before_later_failure(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A partial apply leaves completed targets recorded and resumable."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    first = fake_home / ".config/first.secret"
    second = fake_home / ".config/second.secret"
    _commit_test_secret(dotfiles_dir, fake_home, first, b"first\n")
    _commit_test_secret(dotfiles_dir, fake_home, second, b"second\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    original_replace = dotfiles_module.os.replace
    deployed = 0

    def _fail_second_deployment(src: str, dst: str | pathlib.Path) -> None:
        nonlocal deployed
        if pathlib.Path(dst) in (first, second):
            deployed += 1
            if deployed == 2:
                raise OSError("simulated replacement failure")
        original_replace(src, dst)

    monkeypatch.setattr(dotfiles_module.os, "replace", _fail_second_deployment)

    with pytest.raises(OSError, match="simulated replacement failure"):
        dotfiles_module.apply_secrets(
            dotfiles_dir,
            fake_home,
            fake_home / ".dotfiles_backup",
        )

    assert first.read_bytes() == b"first\n"
    assert not second.exists()
    manifest = json.loads((dotfiles_dir / "manifest.json").read_text())
    assert ".config/first.secret" in manifest["secrets"]
    assert ".config/second.secret" in manifest["secrets"]

    monkeypatch.setattr(dotfiles_module.os, "replace", original_replace)
    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )

    assert second.read_bytes() == b"second\n"


def test_apply_secrets_removes_orphaned_target(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Deleting a ciphertext removes its unchanged deployed plaintext."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    source = _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )
    _remove_test_secret(dotfiles_dir, fake_home, source)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )

    assert not target.exists()
    manifest = json.loads((dotfiles_dir / "manifest.json").read_text())
    assert ".config/private.conf" not in manifest["secrets"]


def test_apply_secrets_restores_backup_when_source_is_removed(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Removing a ciphertext restores the file that predated deployment."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original\n")
    source = _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )
    _remove_test_secret(dotfiles_dir, fake_home, source)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )

    assert target.read_bytes() == b"original\n"


def test_orphan_restoration_resumes_after_manifest_failure(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed orphan manifest update keeps the pristine backup recoverable."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    backup_dir = fake_home / ".dotfiles_backup"
    target = fake_home / ".config/private.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original\n")
    source = _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.apply_secrets(dotfiles_dir, fake_home, backup_dir)
    _remove_test_secret(dotfiles_dir, fake_home, source)

    original_write = dotfiles_module._write_manifest_data
    monkeypatch.setattr(
        dotfiles_module,
        "_write_manifest_data",
        lambda *_args: (_ for _ in ()).throw(OSError("manifest write failed")),
    )
    with pytest.raises(OSError, match="manifest write failed"):
        dotfiles_module.apply_secrets(dotfiles_dir, fake_home, backup_dir)

    backup = backup_dir / ".config/private.conf"
    assert target.read_bytes() == b"original\n"
    assert backup.read_bytes() == b"original\n"

    monkeypatch.setattr(dotfiles_module, "_write_manifest_data", original_write)
    dotfiles_module.apply_secrets(dotfiles_dir, fake_home, backup_dir)

    assert target.read_bytes() == b"original\n"
    assert not backup.exists()
    manifest = json.loads((dotfiles_dir / "manifest.json").read_text())
    assert ".config/private.conf" not in manifest["secrets"]


def test_apply_secrets_uses_manifest_backup_directory(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Explicit apply keeps using the backup directory chosen at bootstrap."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"original\n")
    _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)

    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / "wrong-backup",
    )

    assert (fake_home / ".dotfiles_backup/.config/private.conf").is_file()
    assert not (fake_home / "wrong-backup").exists()


def test_uninstall_refuses_secret_target_replaced_by_directory(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even --force does not recursively delete a directory at a secret target."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    target = fake_home / ".config/private.conf"
    _commit_test_secret(dotfiles_dir, fake_home, target, b"managed\n")
    age = _install_fake_age(tmp_path)
    _install_test_identity(fake_home, dotfiles_module)
    monkeypatch.setattr(dotfiles_module, "find_age", lambda: age)
    dotfiles_module.apply_secrets(
        dotfiles_dir,
        fake_home,
        fake_home / ".dotfiles_backup",
    )
    target.unlink()
    target.mkdir()

    assert dotfiles_module.uninstall(dotfiles_dir, fake_home, force=True) == 1
    assert target.is_dir()
    assert dotfiles_dir.exists()


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
