from __future__ import annotations

import importlib.util
import re

import pytest
from conftest import REPO_ROOT

RENDER = REPO_ROOT / ".local/libexec/dotfiles/nohang/render.py"

spec = importlib.util.spec_from_file_location("nohang_render", RENDER)
assert spec is not None
assert spec.loader is not None
nohang_render = importlib.util.module_from_spec(spec)
spec.loader.exec_module(nohang_render)

UPSTREAM = "psi_excess_duration = 30\nmin_sleep = 0.1\n"
OVERRIDES = "# comment\n\npsi_excess_duration = 10\n@BADNESS_ADJ_RE_NAME 300 /// ^chrome$\n"


def test_render_replaces_keys_and_appends_badness_rules() -> None:
    rendered = nohang_render.render(UPSTREAM, OVERRIDES)

    assert rendered.splitlines() == [
        "psi_excess_duration = 10",
        "min_sleep = 0.1",
        "@BADNESS_ADJ_RE_NAME 300 /// ^chrome$",
    ]


def test_render_fails_when_an_overridden_key_is_not_upstream() -> None:
    with pytest.raises(SystemExit, match="psi_excess_duration"):
        nohang_render.render("min_sleep = 0.1\n", OVERRIDES)


def test_name_rules_fit_the_kernel_process_name_limit() -> None:
    """Process names are truncated to 15 characters, so longer names never match."""
    overrides = RENDER.with_name("overrides.conf").read_text()
    rules = re.findall(r"^@BADNESS_ADJ_RE_NAME\s+-?\d+\s+///\s+(\S.*)$", overrides, re.M)

    assert rules
    for rule in rules:
        re.compile(rule)
        alternatives = re.sub(r"[\^$()]", "", rule).split("|")
        for name in alternatives:
            assert len(name.replace("\\", "")) <= 15, name
