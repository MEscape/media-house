"""The HyperFrames runtime lookup explains a path Windows cannot start (instead of ENOENT)."""

import json
from pathlib import Path

import pytest

from media_house.modules.motion_graphics_engine.infrastructure import hyperframes
from media_house.modules.motion_graphics_engine.infrastructure.hyperframes import locate, pinned


def _install(root: Path) -> Path:
    for package, version in pinned().items():
        folder = root / "node_modules" / package
        folder.mkdir(parents=True)
        (folder / "package.json").write_text(json.dumps({"version": version}), encoding="utf-8")
    (root / "node_modules" / "hyperframes" / "bin").mkdir()
    (root / "node_modules" / "hyperframes" / "bin" / "hyperframes.mjs").write_text("")
    (root / "node_modules" / "gsap" / "dist").mkdir()
    (root / "node_modules" / "gsap" / "dist" / "gsap.min.js").write_text("")
    chrome = root / "home" / ".cache" / "hyperframes" / "chrome" / "win64"
    chrome.mkdir(parents=True)
    (chrome / "chrome-headless-shell.exe").write_text("")
    return root


def test_a_browser_path_over_the_windows_limit_is_reported_clearly(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # E2E regression: a deep --home put Chrome at 290 characters and every render failed ENOENT.
    install = _install(tmp_path / "hf")
    monkeypatch.setattr("os.name", "nt")
    monkeypatch.setattr("shutil.which", lambda _name: "node")

    monkeypatch.setattr(hyperframes, "_MAX_PATH", 10_000)
    assert not isinstance(locate(install), str)
    monkeypatch.setattr(hyperframes, "_MAX_PATH", 20)
    found = locate(install)
    assert isinstance(found, str) and "over the Windows limit" in found
