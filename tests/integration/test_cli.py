from pathlib import Path

import pytest

from media_house import __version__
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
