from __future__ import annotations

import subprocess

from conftest import REPO_ROOT

SCRIPT = REPO_ROOT / ".local/libexec/dotfiles/nohang/install.sh"

UPSTREAM = "psi_excess_duration = 30\nmin_sleep = 0.1\n"


def _render(tmp_path, upstream: str) -> subprocess.CompletedProcess[str]:
    base = tmp_path / "nohang-desktop.conf"
    base.write_text(upstream)
    return subprocess.run(
        [str(SCRIPT), "render", str(base)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )


def test_render_replaces_keys_and_appends_badness_rules(tmp_path) -> None:
    result = _render(tmp_path, UPSTREAM)

    assert result.returncode == 0, result.stderr
    lines = result.stdout.splitlines()
    assert "psi_excess_duration = 10" in lines
    assert "psi_excess_duration = 30" not in lines
    assert "min_sleep = 0.1" in lines
    assert lines[-1].startswith("@BADNESS_ADJ_RE_NAME")


def test_render_fails_when_an_overridden_key_is_not_upstream(tmp_path) -> None:
    result = _render(tmp_path, "min_sleep = 0.1\n")

    assert result.returncode != 0
    assert "psi_excess_duration" in result.stderr
