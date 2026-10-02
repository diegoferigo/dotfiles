from __future__ import annotations

import subprocess
import types


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
