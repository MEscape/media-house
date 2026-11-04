from pathlib import Path

import pytest

from media_house import __version__
from media_house.bootstrap import cli
from media_house.bootstrap.application import StartupOptions
from media_house.bootstrap.cli import main

pytestmark = pytest.mark.integration


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip() == f"media-house {__version__}"


def test_check_starts_headless_and_exits_zero(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(["--check", "--home", str(tmp_path), "--environment", "test"]) == 0
    output = capsys.readouterr().out
    assert "OK" in output
    assert "workspace" in output


def test_check_reports_startup_failures_without_a_traceback(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MEDIA_HOUSE_LOGGING__LEVEL", "LOUD")
    assert main(["--check", "--home", str(tmp_path), "--environment", "test"]) == 2
    err = capsys.readouterr().err
    assert "Reference: ERR-" in err
    assert "phase: configuration" in err
    assert "Traceback" not in err


def test_a_portable_home_still_finds_the_machine_level_installs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Models and HyperFrames are installed once per machine (user cache); --home must not hide them.
    seen: list[StartupOptions] = []

    def check(options: StartupOptions) -> int:
        seen.append(options)
        return 0

    monkeypatch.setattr(cli, "run_check", check)

    assert main(["--check", "--home", str(tmp_path)]) == 0
    assert seen[0].home == tmp_path
    assert seen[0].shared_installs
