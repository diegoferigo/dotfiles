# Integration tests that run the full subprocess -> shebang -> clone -> checkout
# flow. The logic-only tests (backup, manifest, rollback, uninstall) live in
# test_unit.py and use direct module calls for speed. The tests here verify the
# end-to-end path including pixi exec, git transport (local and git-daemon) and
# subprocess passthrough behaviour.

from __future__ import annotations

import pathlib
import stat
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


def _secret_repo(
    tmp_path: pathlib.Path,
    relative: pathlib.Path,
    plaintext: bytes,
) -> pathlib.Path:
    """Clone HEAD and add deterministic test ciphertext to a private test origin."""

    from conftest import REPO_ROOT

    repo = tmp_path / "origin"
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(repo)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "fetch", "--quiet", str(REPO_ROOT), "HEAD"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "checkout", "--quiet", "-B", "test-main", "FETCH_HEAD"],
        check=True,
        capture_output=True,
    )
    source = repo / "secrets/home" / pathlib.Path(f"{relative}.age")
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"AGE-TEST\n" + plaintext)
    subprocess.run(
        ["git", "-C", str(repo), "add", str(source.relative_to(repo))],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "Add test ciphertext",
        ],
        check=True,
        capture_output=True,
    )
    return repo


def _install_fake_age(home: pathlib.Path) -> None:
    """Install a deterministic age stand-in where the subprocess finds it."""

    age = home / ".pixi/bin/age"
    age.write_text(
        """#!/usr/bin/env python3
import pathlib
import sys

args = sys.argv[1:]
if "--encrypt" in args:
    output = pathlib.Path(args[args.index("--output") + 1])
    source = pathlib.Path(args[-1])
    output.write_bytes(b"AGE-TEST\\n" + source.read_bytes())
elif "--output" in args:
    output = pathlib.Path(args[args.index("--output") + 1])
    payload = pathlib.Path(args[-1]).read_bytes()
    if not payload.startswith(b"AGE-IDENTITY-OLD\\n"):
        print("invalid test identity", file=sys.stderr)
        raise SystemExit(1)
    output.write_bytes(payload.removeprefix(b"AGE-IDENTITY-OLD\\n"))
else:
    payload = sys.stdin.buffer.read()
    if not payload.startswith(b"AGE-TEST\\n"):
        print("invalid test ciphertext", file=sys.stderr)
        raise SystemExit(1)
    sys.stdout.buffer.write(payload.removeprefix(b"AGE-TEST\\n"))
"""
    )
    age.chmod(0o755)


def _install_test_identity(home: pathlib.Path) -> pathlib.Path:
    """Install a non-secret identity fixture with the required permissions."""

    identity = home / ".config/dotfiles/age/identity.txt"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_text("AGE-SECRET-KEY-TEST\n")
    identity.chmod(0o600)
    return identity


def _commit_encrypted_identity(repo: pathlib.Path) -> None:
    """Commit the repository-managed identity fixture."""

    identity = repo / ".config/dotfiles/age/identity.txt.age"
    identity.parent.mkdir(parents=True, exist_ok=True)
    identity.write_bytes(b"AGE-IDENTITY-OLD\nAGE-SECRET-KEY-TEST\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", str(identity.relative_to(repo))],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "Add encrypted test identity",
        ],
        check=True,
        capture_output=True,
    )


def _commit_repo_file(repo: pathlib.Path, relative: pathlib.Path, content: bytes) -> None:
    """Commit one file to the controlled integration-test origin."""

    target = repo / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    subprocess.run(
        ["git", "-C", str(repo), "add", str(relative)],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            f"Update {relative}",
        ],
        check=True,
        capture_output=True,
    )


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


def test_encrypted_file_can_be_applied_after_pending_bootstrap(
    fake_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """Bootstrap stays usable without an identity, then explicit apply deploys."""

    relative = pathlib.Path(".ssh/config.d/robot.conf")
    plaintext = b"Host robot\n    HostName 192.0.2.10\n"
    repo = _secret_repo(tmp_path, relative, plaintext)
    _install_fake_age(fake_home)

    result = bootstrap(fake_home, f"file://{repo}")

    target = fake_home / relative
    assert not target.exists()
    assert not (fake_home / "secrets").exists()
    assert "1 encrypted file(s) available" in result.stdout
    assert "dotfiles secrets apply" in result.stdout

    pending = run_dotfiles(fake_home, "secrets", "status")
    assert pending.returncode == 0, pending.stderr
    assert "missing" in pending.stdout
    assert str(target) in pending.stdout

    missing = run_dotfiles(fake_home, "secrets", "apply")
    assert missing.returncode != 0
    assert "identity.txt: missing" in missing.stderr

    _install_test_identity(fake_home)
    applied = run_dotfiles(fake_home, "secrets", "apply")
    assert applied.returncode == 0, applied.stderr
    assert target.read_bytes() == plaintext
    assert stat.S_IMODE(target.stat().st_mode) == 0o600

    current = run_dotfiles(fake_home, "secrets", "status")
    assert current.returncode == 0, current.stderr
    assert "current" in current.stdout
    status = run_dotfiles(fake_home, "git", "status", "--short")
    assert status.returncode == 0, status.stderr
    assert status.stdout.strip() == ""


def test_bootstrap_with_secrets_unlocks_repository_managed_identity(
    fake_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """--with-secrets unlocks the tracked identity after public bootstrap."""

    relative = pathlib.Path(".config/private.conf")
    plaintext = b"managed\n"
    repo = _secret_repo(tmp_path, relative, plaintext)
    _commit_encrypted_identity(repo)
    _install_fake_age(fake_home)

    result = run_bootstrap(
        fake_home,
        f"file://{repo}",
        "--overwrite-git-dir",
        "--with-secrets",
    )

    assert result.returncode == 0, result.stderr
    assert (fake_home / relative).read_bytes() == plaintext
    assert not (fake_home / ".config/dotfiles/age/identity.txt").exists()


def test_update_with_secrets_refreshes_plaintext(
    fake_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """--update --with-secrets refreshes stale plaintext in one command."""

    relative = pathlib.Path(".config/private.conf")
    repo = _secret_repo(tmp_path, relative, b"version one\n")
    _commit_encrypted_identity(repo)
    _install_fake_age(fake_home)
    initial = run_bootstrap(
        fake_home,
        f"file://{repo}",
        "--overwrite-git-dir",
        "--with-secrets",
    )
    assert initial.returncode == 0, initial.stderr

    source = repo / "secrets/home" / pathlib.Path(f"{relative}.age")
    source.write_bytes(b"AGE-TEST\nversion two\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", str(source.relative_to(repo))],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "Update test ciphertext",
        ],
        check=True,
        capture_output=True,
    )

    updated = run_dotfiles(fake_home, "--update", "--with-secrets")

    assert updated.returncode == 0, updated.stderr
    assert (fake_home / relative).read_bytes() == b"version two\n"


def test_encrypt_command_stages_ciphertext_in_bare_repository(
    fake_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """The CLI authors a secret without a development clone or HOME ciphertext."""

    from conftest import REPO_ROOT

    repo = tmp_path / "origin"
    subprocess.run(
        ["git", "clone", "--quiet", str(REPO_ROOT), str(repo)],
        check=True,
        capture_output=True,
    )
    _commit_repo_file(
        repo,
        pathlib.Path(".config/dotfiles/age/recipients.txt"),
        b"age1testrecipient\n",
    )
    _install_fake_age(fake_home)
    _ = bootstrap(fake_home, f"file://{repo}")
    plaintext = fake_home / ".ssh/config.d/robot.conf"
    plaintext.parent.mkdir(parents=True, exist_ok=True)
    plaintext.write_bytes(b"Host robot\n")

    encrypted = run_dotfiles(fake_home, "secrets", "encrypt", str(plaintext))

    assert encrypted.returncode == 0, encrypted.stderr
    source = "secrets/home/.ssh/config.d/robot.conf.age"
    staged = run_dotfiles(fake_home, "git", "show", f":{source}")
    assert staged.returncode == 0, staged.stderr
    assert staged.stdout == "AGE-TEST\nHost robot\n"
    assert not (fake_home / "secrets").exists()


def test_update_without_identity_preserves_stale_plaintext(
    fake_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A ciphertext update without the identity leaves deployed bytes untouched."""

    relative = pathlib.Path(".config/private.conf")
    repo = _secret_repo(tmp_path, relative, b"version one\n")
    _install_fake_age(fake_home)
    identity = _install_test_identity(fake_home)
    _ = bootstrap(fake_home, f"file://{repo}")
    assert not (fake_home / relative).exists()
    applied = run_dotfiles(fake_home, "secrets", "apply")
    assert applied.returncode == 0, applied.stderr
    target = fake_home / relative
    assert target.read_bytes() == b"version one\n"

    source = repo / "secrets/home" / pathlib.Path(f"{relative}.age")
    source.write_bytes(b"AGE-TEST\nversion two\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", str(source.relative_to(repo))],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        [
            "git",
            "-C",
            str(repo),
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.com",
            "commit",
            "--quiet",
            "-m",
            "Update test ciphertext",
        ],
        check=True,
        capture_output=True,
    )
    identity.unlink()

    updated = run_dotfiles(fake_home, "--update")

    assert updated.returncode == 0, updated.stderr
    assert "encrypted file(s) available" in updated.stdout
    assert target.read_bytes() == b"version one\n"
    status = run_dotfiles(fake_home, "secrets", "status")
    assert status.returncode == 0, status.stderr
    assert "stale" in status.stdout


def test_update_rejects_public_file_at_deployed_secret_target(
    fake_home: pathlib.Path,
    tmp_path: pathlib.Path,
) -> None:
    """A public update cannot overwrite a target still managed as a secret."""

    relative = pathlib.Path(".config/private.conf")
    repo = _secret_repo(tmp_path, relative, b"secret\n")
    _install_fake_age(fake_home)
    _install_test_identity(fake_home)
    _ = bootstrap(fake_home, f"file://{repo}")
    applied = run_dotfiles(fake_home, "secrets", "apply")
    assert applied.returncode == 0, applied.stderr
    _commit_repo_file(repo, relative, b"public\n")

    updated = run_dotfiles(fake_home, "--update")

    assert updated.returncode != 0
    assert "overlap deployed secret targets" in updated.stderr
    assert (fake_home / relative).read_bytes() == b"secret\n"


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


def test_git_status_hides_preexisting_user_gitattributes(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """A user's own ~/.gitattributes must not show up as modified.

    .gitattributes is tracked but sparse-excluded (repo-internal Linguist
    config). The user can already have their own ~/.gitattributes at the same
    path because the bare-repo work-tree is HOME. Modern git will not set
    skip-worktree on that present, differing path, so without the
    --assume-unchanged fallback it would show as modified forever.
    """

    user_gitattributes = "*.py merge=mergiraf\n"
    (fake_home / ".gitattributes").write_text(user_gitattributes)

    _ = bootstrap(fake_home, repo_uri)

    status = run_dotfiles(fake_home, "git", "status", "--short")
    assert status.returncode == 0, status.stderr
    assert ".gitattributes" not in status.stdout, (
        f".gitattributes wrongly reported:\n{status.stdout}"
    )
    # The user's own content must be left untouched.
    assert (fake_home / ".gitattributes").read_text() == user_gitattributes


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


def test_update_preserves_local_modifications(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """--update must keep an uncommitted edit to a tracked file (autostash).

    A same-repo update does not move HEAD, so the pull never touches .nanorc and
    the edit is restored verbatim, with no prompt and no --force.
    """

    _ = bootstrap(fake_home, repo_uri)

    edited = "# my local edit that must survive\n"
    (fake_home / ".nanorc").write_text(edited)

    result = run_dotfiles(fake_home, "--update")
    assert result.returncode == 0, result.stderr
    assert (fake_home / ".nanorc").read_text() == edited


def test_update_keeps_edit_visible_in_status(
    fake_home: pathlib.Path,
    repo_uri: str,
) -> None:
    """After --update the preserved edit is still an ordinary uncommitted change,
    so `dotfiles git status` reports it exactly as before the update."""

    _ = bootstrap(fake_home, repo_uri)

    (fake_home / ".nanorc").write_text("# my local edit\n")

    result = run_dotfiles(fake_home, "--update")
    assert result.returncode == 0, result.stderr

    status = run_dotfiles(fake_home, "git", "status", "--short")
    assert status.returncode == 0, status.stderr
    assert ".nanorc" in status.stdout
