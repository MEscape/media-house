import json
import logging
from pathlib import Path

import pytest

from media_house.shared.configuration import LoggingSettings, LogLevel
from media_house.shared.errors import InfrastructureError
from media_house.shared.logging import bind_context, configure_logging, get_logger
from media_house.shared.logging.setup import LOG_FILE_NAME

pytestmark = pytest.mark.integration


def read_entries(log_dir: Path) -> list[dict[str, object]]:
    lines = (log_dir / LOG_FILE_NAME).read_text(encoding="utf-8").splitlines()
    return [json.loads(line) for line in lines]


def test_file_logging_writes_json_lines(tmp_path: Path) -> None:
    handle = configure_logging(LoggingSettings(level=LogLevel.INFO), log_dir=tmp_path)
    with bind_context(correlation_id="c-1"):
        get_logger("media_house.test").info("hello", answer=42)
    get_logger("media_house.test").debug("filtered out by level")
    handle.close()

    (entry,) = read_entries(tmp_path)
    assert entry["message"] == "hello"
    assert entry["correlation_id"] == "c-1"
    assert entry["fields"] == {"answer": 42}


def test_reconfiguring_replaces_instead_of_duplicating(tmp_path: Path) -> None:
    configure_logging(LoggingSettings(), log_dir=tmp_path)
    handle = configure_logging(LoggingSettings(), log_dir=tmp_path)
    get_logger("media_house.test").info("once")
    handle.close()
    assert len(read_entries(tmp_path)) == 1


def test_console_logging_goes_to_stderr_and_file_can_be_disabled(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    handle = configure_logging(
        LoggingSettings(console=True, file=False, level=LogLevel.DEBUG),
        log_dir=tmp_path,
    )
    get_logger("media_house.test").debug("visible")
    handle.close()
    assert "visible" in capsys.readouterr().err
    assert not (tmp_path / LOG_FILE_NAME).exists()


def test_logging_is_isolated_from_the_root_logger(tmp_path: Path) -> None:
    handle = configure_logging(LoggingSettings(), log_dir=tmp_path)
    assert logging.getLogger("media_house").propagate is False
    handle.close()


def test_unwritable_log_directory_is_an_infrastructure_error(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("not a directory", encoding="utf-8")
    with pytest.raises(InfrastructureError) as caught:
        configure_logging(LoggingSettings(), log_dir=blocker / "logs")
    assert caught.value.__cause__ is not None
