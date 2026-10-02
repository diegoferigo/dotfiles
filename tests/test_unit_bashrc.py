from __future__ import annotations

import pathlib
import types

from helpers import _make_block, _make_blocks


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

    environment_block, interactive_block = dotfiles_module.Bashrc.read_blocks(fake_home)

    assert f"source ~/{dotfiles_module.Bashrc.ENVIRONMENT_FILE}" in environment_block
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
