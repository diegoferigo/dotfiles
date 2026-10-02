# ruff: noqa: S101

from __future__ import annotations

import dataclasses
import errno
import hashlib
import io
import os
import pathlib
import tarfile
import types

import pytest


def _font_archive_files(
    font_key: str,
    overrides: dict[str, bytes],
) -> dict[str, bytes]:
    """Build a test archive with every pinned member unless overridden."""

    defaults = {
        "jetbrains-mono": {
            "JetBrainsMonoNerdFontMono-Thin.ttf": b"thin-default\n",
            "JetBrainsMonoNerdFontMono-ExtraLight.ttf": b"extra-light-default\n",
            "JetBrainsMonoNerdFontMono-Light.ttf": b"light-default\n",
            "JetBrainsMonoNerdFontMono-Regular.ttf": b"regular-default\n",
            "JetBrainsMonoNerdFontMono-Medium.ttf": b"medium-default\n",
            "JetBrainsMonoNerdFontMono-SemiBold.ttf": b"semibold-default\n",
            "JetBrainsMonoNerdFontMono-Bold.ttf": b"bold-default\n",
            "JetBrainsMonoNerdFontMono-ExtraBold.ttf": b"extra-bold-default\n",
            "OFL.txt": b"license-default\n",
        },
        "fira-code": {
            "FiraCodeNerdFontMono-Light.ttf": b"light-default\n",
            "FiraCodeNerdFontMono-Regular.ttf": b"regular-default\n",
            "FiraCodeNerdFontMono-Retina.ttf": b"retina-default\n",
            "FiraCodeNerdFontMono-Medium.ttf": b"medium-default\n",
            "FiraCodeNerdFontMono-SemiBold.ttf": b"semibold-default\n",
            "FiraCodeNerdFontMono-Bold.ttf": b"bold-default\n",
            "LICENSE": b"license-default\n",
        },
    }
    return {**defaults[font_key], **overrides}


def _installed_archive_files(
    font_key: str,
    overrides: dict[str, bytes],
) -> dict[str, bytes]:
    """Build the expected installed files for one test archive."""

    normalized_overrides = dict(overrides)
    if font_key == "jetbrains-mono" and "LICENSE" in normalized_overrides:
        normalized_overrides["OFL.txt"] = normalized_overrides.pop("LICENSE")

    installed = _font_archive_files(font_key, normalized_overrides)
    if font_key == "jetbrains-mono":
        installed["LICENSE"] = installed.pop("OFL.txt")
    return installed


@pytest.mark.parametrize(
    ("font_key", "archive_name", "files", "expected"),
    [
        (
            "jetbrains-mono",
            "JetBrainsMono.tar.xz",
            _font_archive_files(
                "jetbrains-mono",
                {
                    "JetBrainsMonoNerdFontMono-Thin.ttf": b"thin\n",
                    "JetBrainsMonoNerdFontMono-Regular.ttf": b"regular\n",
                    "JetBrainsMonoNerdFontMono-Bold.ttf": b"bold\n",
                    "JetBrainsMonoNerdFontMono-Italic.ttf": b"italic\n",
                    "JetBrainsMonoNerdFontMono-ExtraBoldItalic.ttf": b"italic too\n",
                    "JetBrainsMonoNerdFont-Bold.ttf": b"wrong family\n",
                    "../../escape.ttf": b"escape\n",
                    "OFL.txt": b"license\n",
                },
            ),
            _installed_archive_files(
                "jetbrains-mono",
                {
                    "JetBrainsMonoNerdFontMono-Thin.ttf": b"thin\n",
                    "JetBrainsMonoNerdFontMono-Regular.ttf": b"regular\n",
                    "JetBrainsMonoNerdFontMono-Bold.ttf": b"bold\n",
                    "LICENSE": b"license\n",
                },
            ),
        ),
        (
            "fira-code",
            "FiraCode.tar.xz",
            _font_archive_files(
                "fira-code",
                {
                    "FiraCodeNerdFontMono-Light.ttf": b"light\n",
                    "FiraCodeNerdFontMono-Regular.ttf": b"regular\n",
                    "FiraCodeNerdFontMono-Bold.ttf": b"bold\n",
                    "FiraCodeNerdFontMono-Italic.ttf": b"italic\n",
                    "FiraCodeNerdFont-Bold.ttf": b"wrong family\n",
                    "README.md": b"readme\n",
                    "LICENSE": b"license\n",
                },
            ),
            _installed_archive_files(
                "fira-code",
                {
                    "FiraCodeNerdFontMono-Light.ttf": b"light\n",
                    "FiraCodeNerdFontMono-Regular.ttf": b"regular\n",
                    "FiraCodeNerdFontMono-Bold.ttf": b"bold\n",
                    "LICENSE": b"license\n",
                },
            ),
        ),
    ],
)
def test_install_nerd_font_installs_allowlisted_files_and_is_idempotent(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    font_key: str,
    archive_name: str,
    files: dict[str, bytes],
    expected: dict[str, bytes],
) -> None:
    """The installer keeps only the allowlisted mono files and the license."""

    archive_path = _write_font_archive(tmp_path / archive_name, files)
    _patch_font_source(dotfiles_module, monkeypatch, font_key, archive_path)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ.get('PATH', '')}")

    spec = dotfiles_module.NERD_FONTS[font_key]
    dotfiles_module.install_nerd_font(home=fake_home, spec=spec)
    first = _installed_font_files(fake_home, dotfiles_module, font_key)
    dotfiles_module.install_nerd_font(home=fake_home, spec=spec)
    second = _installed_font_files(fake_home, dotfiles_module, font_key)

    assert first == second == expected
    assert not list(fake_home.rglob("escape.ttf"))
    assert spec.display_name in capsys.readouterr().out


def test_install_nerd_font_checksum_mismatch_leaves_target_untouched(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A checksum mismatch fails before the installed directory changes."""

    archive_path = _write_font_archive(
        tmp_path / "JetBrainsMono.tar.xz",
        _font_archive_files(
            "jetbrains-mono",
            {
                "JetBrainsMonoNerdFontMono-Regular.ttf": b"regular\n",
                "OFL.txt": b"license\n",
            },
        ),
    )
    _patch_font_source(
        dotfiles_module,
        monkeypatch,
        "jetbrains-mono",
        archive_path,
        sha256="0" * 64,
    )
    target_dir = (
        fake_home / dotfiles_module.NERD_FONTS["jetbrains-mono"].target_dir
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    sentinel = target_dir / "keep.txt"
    sentinel.write_text("keep\n")

    with pytest.raises(RuntimeError, match="checksum mismatch"):
        dotfiles_module.install_nerd_font(
            home=fake_home,
            spec=dotfiles_module.NERD_FONTS["jetbrains-mono"],
        )

    assert target_dir.exists()
    assert {path.name for path in target_dir.iterdir()} == {"keep.txt"}
    assert sentinel.read_text() == "keep\n"


def test_install_nerd_font_missing_allowlisted_member_leaves_target_untouched(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A missing pinned member fails before replacing the installed directory."""

    archive_path = _write_font_archive(
        tmp_path / "FiraCode.tar.xz",
        {
            "FiraCodeNerdFontMono-Regular.ttf": b"regular\n",
        },
    )
    _patch_font_source(dotfiles_module, monkeypatch, "fira-code", archive_path)
    target_dir = fake_home / dotfiles_module.NERD_FONTS["fira-code"].target_dir
    target_dir.mkdir(parents=True, exist_ok=True)
    sentinel = target_dir / "keep.txt"
    sentinel.write_text("keep\n")

    with pytest.raises(RuntimeError, match="Missing archive member"):
        dotfiles_module.install_nerd_font(
            home=fake_home,
            spec=dotfiles_module.NERD_FONTS["fira-code"],
        )

    assert target_dir.exists()
    assert {path.name for path in target_dir.iterdir()} == {"keep.txt"}
    assert sentinel.read_text() == "keep\n"


def test_install_nerd_font_stages_on_target_filesystem(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The final rename avoids cross-filesystem EXDEV failures."""

    archive_path = _write_font_archive(
        tmp_path / "FiraCode.tar.xz",
        _font_archive_files(
            "fira-code",
            {
                "FiraCodeNerdFontMono-Light.ttf": b"light\n",
                "FiraCodeNerdFontMono-Regular.ttf": b"regular\n",
                "FiraCodeNerdFontMono-Retina.ttf": b"retina\n",
                "FiraCodeNerdFontMono-Medium.ttf": b"medium\n",
                "FiraCodeNerdFontMono-SemiBold.ttf": b"semibold\n",
                "FiraCodeNerdFontMono-Bold.ttf": b"bold\n",
                "LICENSE": b"license\n",
            },
        ),
    )
    _patch_font_source(dotfiles_module, monkeypatch, "fira-code", archive_path)

    rename_calls: list[tuple[pathlib.Path, pathlib.Path]] = []
    original_rename = pathlib.Path.rename
    target_dir = fake_home / dotfiles_module.NERD_FONTS["fira-code"].target_dir
    target_parent = target_dir.parent

    def fake_rename(src: pathlib.Path, dst: pathlib.Path) -> pathlib.Path:
        rename_calls.append((src, dst))
        if dst == target_dir and target_parent not in src.parents:
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return original_rename(src, dst)

    monkeypatch.setattr(pathlib.Path, "rename", fake_rename)

    dotfiles_module.install_nerd_font(
        home=fake_home,
        spec=dotfiles_module.NERD_FONTS["fira-code"],
    )

    assert (
        target_dir / "FiraCodeNerdFontMono-Regular.ttf"
    ).read_bytes() == b"regular\n"
    assert any(dst == target_dir for _, dst in rename_calls)
    assert all(
        target_parent in src.parents
        for src, dst in rename_calls
        if dst == target_dir
    )


def test_download_url_to_path_rejects_oversized_body(
    tmp_path: pathlib.Path,
    dotfiles_module: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An oversized download stops before filling the destination path."""

    payload = b"abcdef"
    destination = tmp_path / "archive.tar.xz"

    class FakeResponse:
        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def __init__(self, body: bytes) -> None:
            self._stream = io.BytesIO(body)

        def read(self, size: int = -1) -> bytes:
            return self._stream.read(size)

    monkeypatch.setattr(
        dotfiles_module.urllib.request,
        "urlopen",
        lambda *args, **kwargs: FakeResponse(payload),
    )

    with pytest.raises(RuntimeError, match="Download exceeded expected size"):
        dotfiles_module._download_url_to_path(
            url="https://example.invalid/archive.tar.xz",
            destination=destination,
            expected_size_bytes=5,
        )

    assert not destination.exists()
    assert list(tmp_path.iterdir()) == []


def test_main_fonts_install_installs_all_fonts_by_default(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI installs every known font when no font name is passed."""

    archives = {
        "jetbrains-mono": _write_font_archive(
            tmp_path / "JetBrainsMono.tar.xz",
            _font_archive_files(
                "jetbrains-mono",
                {
                    "JetBrainsMonoNerdFontMono-Regular.ttf": b"regular\n",
                    "OFL.txt": b"license\n",
                },
            ),
        ),
        "fira-code": _write_font_archive(
            tmp_path / "FiraCode.tar.xz",
            _font_archive_files(
                "fira-code",
                {
                    "FiraCodeNerdFontMono-Regular.ttf": b"regular\n",
                    "LICENSE": b"license\n",
                },
            ),
        ),
    }
    for font_key, archive_path in archives.items():
        _patch_font_source(dotfiles_module, monkeypatch, font_key, archive_path)

    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("sys.argv", ["dotfiles", "fonts", "install"])
    original_which = dotfiles_module.shutil.which
    monkeypatch.setattr(
        dotfiles_module.shutil,
        "which",
        lambda name: None if name == "fc-cache" else original_which(name),
    )

    assert dotfiles_module.main() == 0
    output = capsys.readouterr().out

    assert "Verified archive size and sha256" in output
    assert "fc-cache not found" in output
    assert (
        fake_home
        / dotfiles_module.NERD_FONTS["jetbrains-mono"].target_dir
        / "JetBrainsMonoNerdFontMono-Regular.ttf"
    ).exists()
    assert (
        fake_home
        / dotfiles_module.NERD_FONTS["fira-code"].target_dir
        / "FiraCodeNerdFontMono-Regular.ttf"
    ).exists()


def test_main_fonts_install_accepts_one_font_name_and_rejects_unknown_names(
    fake_home: pathlib.Path,
    dotfiles_module: types.ModuleType,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The CLI accepts one known font key and argparse rejects unknown names."""

    archive_path = _write_font_archive(
        tmp_path / "FiraCode.tar.xz",
        _font_archive_files(
            "fira-code",
            {
                "FiraCodeNerdFontMono-Regular.ttf": b"regular\n",
                "LICENSE": b"license\n",
            },
        ),
    )
    _patch_font_source(dotfiles_module, monkeypatch, "fira-code", archive_path)
    monkeypatch.setenv("HOME", str(fake_home))
    monkeypatch.setattr("sys.argv", ["dotfiles", "fonts", "install", "fira-code"])

    assert dotfiles_module.main() == 0
    assert (
        fake_home
        / dotfiles_module.NERD_FONTS["fira-code"].target_dir
        / "FiraCodeNerdFontMono-Regular.ttf"
    ).exists()
    assert not (
        fake_home / dotfiles_module.NERD_FONTS["jetbrains-mono"].target_dir
    ).exists()

    monkeypatch.setattr("sys.argv", ["dotfiles", "fonts", "install", "unknown"])
    with pytest.raises(SystemExit) as exc:
        dotfiles_module.main()
    assert exc.value.code == 2
    assert "invalid choice" in capsys.readouterr().err


# Private helpers


def _patch_font_source(
    mod: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    font_key: str,
    archive_path: pathlib.Path,
    *,
    sha256: str | None = None,
) -> None:
    """Point one font spec at a local test archive."""

    spec = dataclasses.replace(
        mod.NERD_FONTS[font_key],
        archive_url=archive_path.resolve().as_uri(),
        archive_sha256=sha256 or hashlib.sha256(archive_path.read_bytes()).hexdigest(),
        archive_size_bytes=archive_path.stat().st_size,
    )
    monkeypatch.setattr(
        mod,
        "NERD_FONTS",
        {**mod.NERD_FONTS, font_key: spec},
    )


def _write_font_archive(
    archive_path: pathlib.Path,
    files: dict[str, bytes],
) -> pathlib.Path:
    """Create a small tar.xz archive with the requested members."""

    with tarfile.open(archive_path, mode="w:xz") as archive:
        for name, content in files.items():
            info = tarfile.TarInfo(name=name)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
    return archive_path


def _installed_font_files(
    home: pathlib.Path,
    mod: types.ModuleType,
    font_key: str,
) -> dict[str, bytes]:
    """Return the installed files and bytes for one font target directory."""

    target_dir = home / mod.NERD_FONTS[font_key].target_dir
    return {path.name: path.read_bytes() for path in sorted(target_dir.iterdir())}
