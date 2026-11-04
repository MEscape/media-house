from pathlib import Path

import pytest

from media_house.shared.errors import ValidationError
from media_house.shared.filesystem import AppPaths, resolve_within


def test_paths_inside_root_resolve(tmp_path: Path) -> None:
    assert resolve_within(tmp_path, "a/b.txt") == (tmp_path / "a" / "b.txt").resolve()


@pytest.mark.parametrize("candidate", ["../outside", "a/../../outside", "/etc/passwd"])
def test_paths_escaping_root_are_rejected(tmp_path: Path, candidate: str) -> None:
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(ValidationError):
        resolve_within(root, candidate)


def test_symlinks_cannot_escape_root(tmp_path: Path) -> None:
    root, outside = tmp_path / "root", tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks not permitted on this platform")
    with pytest.raises(ValidationError):
        resolve_within(root, "link/file.txt")


def test_app_paths_under_root_layout(tmp_path: Path) -> None:
    paths = AppPaths.under_root(tmp_path)
    assert paths.settings_file == tmp_path / "config" / "settings.toml"
    assert paths.temp_dir == tmp_path / "cache" / "tmp"
    paths.ensure_directories()
    assert all(
        p.is_dir() for p in (paths.config_dir, paths.data_dir, paths.cache_dir, paths.log_dir)
    )


def test_temporary_directory_is_removed(tmp_path: Path) -> None:
    paths = AppPaths.under_root(tmp_path)
    with paths.temporary_directory() as scratch:
        (scratch / "f").write_text("x", encoding="utf-8")
        assert scratch.is_dir()
        assert scratch.parent == paths.temp_dir
    assert not scratch.exists()


def test_user_paths_are_namespaced() -> None:
    paths = AppPaths.for_user()
    assert "media-house" in str(paths.data_dir).lower()


def test_a_home_uses_its_own_install_else_the_machine_level_one(tmp_path: Path) -> None:
    # E2E: with --home the fetched models and the HyperFrames runtime (user cache) were missing.
    from dataclasses import replace

    shared = tmp_path / "user-cache"
    paths = replace(AppPaths.under_root(tmp_path / "home"), install_root=shared)

    assert (
        paths.install("content_intelligence", "models")
        == shared / "content_intelligence" / "models"
    )
    own = paths.cache_dir / "content_intelligence" / "models"
    own.mkdir(parents=True)
    assert paths.install("content_intelligence", "models") == own
    portable = AppPaths.under_root(tmp_path / "ci")
    assert (
        portable.install("x") == portable.cache_dir / "x"
    )  # no shared root: never leaves the home
