# Shared helpers for the unit-test modules.

from __future__ import annotations

import os
import pathlib
import shutil
import subprocess
import types

import pytest
from conftest import REPO_ROOT


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


DOTFILES_DIR_NAME = ".dotfiles"

LOCAL_REPO_URI = f"file://{REPO_ROOT}"

_GIT_IDENTITY_ENV = {
    "GIT_AUTHOR_NAME": "Test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "Test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


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
    """Create a commit with parent's tree on top of parent.

    Reuses the parent tree so a re-checkout of the child produces identical
    files. The tests only care about the commit graph, not the content. Returns
    the new commit SHA.
    """

    tree = _git_bare(git_dir, "rev-parse", f"{parent}^{{tree}}").stdout.strip()
    return _git_bare(
        git_dir, "commit-tree", tree, "-p", parent, "-m", message
    ).stdout.strip()


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
    Returns the branch name, the base commit and the remote bare repository
    path. Both the local branch and the remote start at the same base commit.
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
    """Commit fake ciphertext for target into the bootstrapped bare repo."""

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


def _tracked_skill_files() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", ".agents/skills"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [name for name in listing if name.endswith("/SKILL.md")]


def _tracked_skill_scripts() -> list[str]:
    listing = subprocess.run(
        ["git", "ls-files", ".agents/skills"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    return [name for name in listing if "/scripts/" in name]


def _run_main(
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    fake_home: pathlib.Path,
    *args: str,
    update_status: int = 0,
    env: dict[str, str] | None = None,
) -> tuple[int, list[str], dict[str, object]]:
    """Run main() with update() and install_tools() stubbed.

    Returns the exit status, the ordered events and the keyword arguments that
    main() passed to update().
    """

    events: list[str] = []
    received: dict[str, object] = {}

    def fake_update(**kwargs: object) -> int:
        events.append("update")
        received.update(kwargs)
        return update_status

    def fake_install_tools(pixi: pathlib.Path) -> None:
        events.append("install_tools")

    monkeypatch.setattr(dotfiles_module, "update", fake_update)
    monkeypatch.setattr(dotfiles_module, "install_tools", fake_install_tools)
    monkeypatch.setattr(dotfiles_module, "find_pixi", lambda: pathlib.Path("pixi"))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DOTFILES_SKIP_TOOLS", raising=False)
    for key, value in (env or {}).items():
        monkeypatch.setenv(key, value)
    monkeypatch.setattr("sys.argv", ["dotfiles", *args])
    return dotfiles_module.main(), events, received


def _fake_pixi(tmp_path: pathlib.Path, fail_on: str | None = None) -> pathlib.Path:
    """A pixi stand-in that logs its arguments and fails for one tool."""

    log = tmp_path / "pixi.log"
    script = tmp_path / "pixi"
    script.write_text(
        "#!/bin/sh\n"
        f'[ "$2" = list ] && {{ cat {tmp_path}/installed.json 2>/dev/null || echo "[]"; exit 0; }}\n'
        f'echo "$@" >> {log}\n'
        f'[ "$3" = "{fail_on}" ] && {{ echo "solver error for $3" >&2; exit 1; }}\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    return script


def _make_block(
    mod: types.ModuleType,
    content: str,
    *,
    environment: bool,
) -> str:
    """Build one minimal managed block for injection tests."""

    begin = (
        mod.Bashrc.ENVIRONMENT_BLOCK_BEGIN if environment else mod.Bashrc.BLOCK_BEGIN
    )
    end = mod.Bashrc.ENVIRONMENT_BLOCK_END if environment else mod.Bashrc.BLOCK_END
    return f"{begin}\n{content}\n{end}"


def _make_blocks(mod: types.ModuleType) -> tuple[str, str]:
    """Build both managed blocks for injection tests."""

    return (
        _make_block(mod, "export DOTFILES_ENV=1", environment=True),
        _make_block(mod, "echo dotfiles", environment=False),
    )


def _sparse_excludes(mod: types.ModuleType) -> list[str]:
    """Return the top-level paths excluded by the sparse-checkout rules."""

    return [
        line[1:].lstrip("/")
        for line in mod.SPARSE_CHECKOUT.splitlines()
        if line.startswith("!")
    ]
