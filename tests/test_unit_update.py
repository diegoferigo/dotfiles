from __future__ import annotations

import json
import os
import pathlib
import subprocess
import types

import pytest
from helpers import (
    _GIT_IDENTITY_ENV,
    DOTFILES_DIR_NAME,
    LOCAL_REPO_URI,
    _bootstrap,
    _commit_child,
    _commit_test_recipients,
    _fake_pixi,
    _git,
    _git_bare,
    _hermetic_branch_and_remote,
    _install_fake_age,
    _run_main,
)


def test_update_installs_tools_after_a_successful_update(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """--update must install the tools, and only once the update itself worked."""

    status, events, received = _run_main(
        dotfiles_module, monkeypatch, fake_home, "--update", "--with-secrets"
    )
    assert status == 0
    assert events == ["update", "install_tools"]
    assert received["home"] == fake_home
    assert received["with_secrets"] is True


def test_update_skips_tools_with_skip_tools(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, events, _ = _run_main(
        dotfiles_module, monkeypatch, fake_home, "--update", "--skip-tools"
    )
    assert status == 0
    assert events == ["update"]


def test_update_skips_tools_with_skip_tools_env(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, events, _ = _run_main(
        dotfiles_module,
        monkeypatch,
        fake_home,
        "--update",
        env={"DOTFILES_SKIP_TOOLS": "1"},
    )
    assert status == 0
    assert events == ["update"]


def test_failed_update_does_not_install_tools(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    status, events, _ = _run_main(
        dotfiles_module, monkeypatch, fake_home, "--update", update_status=1
    )
    assert status == 1
    assert events == ["update"]


def test_tool_install_failure_does_not_fail_update(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient pixi failure must leave the successful update's exit status alone."""

    monkeypatch.setattr(dotfiles_module, "update", lambda **_: 0)

    def failing_install(pixi: pathlib.Path) -> None:
        raise RuntimeError("pixi is down")

    monkeypatch.setattr(dotfiles_module, "install_tools", failing_install)
    monkeypatch.setattr(dotfiles_module, "find_pixi", lambda: pathlib.Path("pixi"))
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.delenv("DOTFILES_SKIP_TOOLS", raising=False)
    monkeypatch.setattr("sys.argv", ["dotfiles", "--update"])
    assert dotfiles_module.main() == 0


def test_install_tools_only_runs_install_for_every_tool(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    dotfiles_module.install_tools(pixi=_fake_pixi(tmp_path))
    calls = (tmp_path / "pixi.log").read_text().splitlines()
    assert calls == [f"global install {tool}" for tool in dotfiles_module.TOOLS]


def test_install_tools_skips_the_tools_already_installed(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    present = dotfiles_module.TOOLS[:2]
    (tmp_path / "installed.json").write_text(
        json.dumps([{"name": tool} for tool in present])
    )
    dotfiles_module.install_tools(pixi=_fake_pixi(tmp_path))
    calls = (tmp_path / "pixi.log").read_text().splitlines()
    assert calls == [f"global install {tool}" for tool in dotfiles_module.TOOLS[2:]]


def test_install_tools_installs_everything_when_the_list_is_unreadable(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    (tmp_path / "installed.json").write_text("not json")
    dotfiles_module.install_tools(pixi=_fake_pixi(tmp_path))
    calls = (tmp_path / "pixi.log").read_text().splitlines()
    assert calls == [f"global install {tool}" for tool in dotfiles_module.TOOLS]


def test_install_tools_reports_the_install_error_without_a_fallback(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
) -> None:
    failing = dotfiles_module.TOOLS[1]
    with pytest.raises(RuntimeError, match=f"solver error for {failing}"):
        dotfiles_module.install_tools(pixi=_fake_pixi(tmp_path, fail_on=failing))
    calls = (tmp_path / "pixi.log").read_text().splitlines()
    assert calls[-1] == f"global install {failing}"
    assert not any("upgrade" in call for call in calls)


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

    # Corrupt the sparse-checkout file so update() has to rewrite it.
    sparse_file = dotfiles_dir / "info" / "sparse-checkout"
    sparse_file.write_text("# corrupted\n")

    # Patch read_blocks so update() stays focused on sparse-checkout repair.
    environment_block = (
        f"{dotfiles_module.Bashrc.ENVIRONMENT_BLOCK_BEGIN}\n"
        f'export PATH="$HOME/.pixi/bin:$PATH"\n'
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


def test_update_reports_only_new_backups(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """--update must not re-report backups captured by an earlier run.

    Regression test: the warning listed every file with a backup, so each
    update repeated the bootstrap notice even when nothing was backed up.
    """

    (fake_home / ".nanorc").write_text("# original nanorc\n")
    _ = _bootstrap(dotfiles_module, fake_home)
    _ = capsys.readouterr()

    assert (
        dotfiles_module.update(
            dotfiles_dir=fake_home / DOTFILES_DIR_NAME,
            home=fake_home,
            backup_dir=fake_home / ".dotfiles_backup",
        )
        == 0
    )

    assert "have been backed up" not in capsys.readouterr().out
    manifest = json.loads((fake_home / DOTFILES_DIR_NAME / "manifest.json").read_text())
    assert ".nanorc" in manifest["backed_up"]


def test_update_is_silent_for_newly_tracked_identical_file(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A file that becomes tracked while HOME already holds it is not announced.

    Regression test: a newly tracked file is absent from the previous manifest,
    so its backup was reported as a conflict even when the content matched.
    """

    _ = _bootstrap(dotfiles_module, fake_home)
    dotfiles_dir = fake_home / DOTFILES_DIR_NAME
    manifest_path = dotfiles_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["checked_out"].remove(".nanorc")
    manifest_path.write_text(json.dumps(manifest))
    _ = capsys.readouterr()

    assert (
        dotfiles_module.update(
            dotfiles_dir=dotfiles_dir,
            home=fake_home,
            backup_dir=fake_home / ".dotfiles_backup",
        )
        == 0
    )

    out = capsys.readouterr().out
    assert "have been backed up" not in out
    assert "Backing up" not in out
    assert (fake_home / ".dotfiles_backup" / ".nanorc").is_file()
    assert ".nanorc" in json.loads(manifest_path.read_text())["backed_up"]


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

    # Craft a local-only commit that advancing to the remote tip would drop.
    # commit-tree needs an author and committer identity, absent on a fresh CI
    # runner, so the test supplies one through the environment.
    commit_env = {**os.environ, **_GIT_IDENTITY_ENV}
    empty_tree = subprocess.run(
        [
            "git",
            "--git-dir",
            str(dotfiles_dir),
            "hash-object",
            "-t",
            "tree",
            "/dev/null",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dropped = subprocess.run(
        [
            "git",
            "--git-dir",
            str(dotfiles_dir),
            "commit-tree",
            empty_tree,
            "-p",
            kept,
            "-m",
            "local wip",
        ],
        check=True,
        capture_output=True,
        text=True,
        env=commit_env,
    ).stdout.strip()

    discarded = dotfiles_module._discarded_commits(dotfiles_dir, kept, dropped)
    assert any("local wip" in line for line in discarded)
