from __future__ import annotations

import json
import pathlib
import stat
import subprocess
import types

import pytest
from helpers import (
    DOTFILES_DIR_NAME,
    _bootstrap,
    _commit_test_recipients,
    _commit_test_secret,
    _git,
    _install_fake_age,
    _install_fake_age_keygen,
    _install_test_identity,
    _install_test_recipients,
    _remove_test_secret,
)


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


def test_secret_source_rejects_a_symlinked_file_inside_home(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    """A symlinked plaintext source inside HOME must be rejected."""

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    backup_dir = fake_home / ".dotfiles_backup"
    outside = fake_home / "outside-secret.txt"
    outside.write_text("token\n")
    source = fake_home / ".config/dotfiles/secrets.sh"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.symlink_to(outside)

    with pytest.raises(RuntimeError, match="symlink"):
        dotfiles_module._secret_source_for_target(
            source,
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

    assert encrypted.read_bytes() == (b"AGE-IDENTITY-NEW\nAGE-SECRET-KEY-TEST\n")
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
    recipients = _commit_test_recipients(dotfiles_dir, fake_home, dotfiles_module)
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
        )
        .stdout.decode()
        .strip()
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
        )
        .stdout.decode()
        .strip()
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
        )
        .stdout.decode()
        .strip()
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
