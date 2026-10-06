import json
import logging
from collections.abc import Iterator

import pytest

from media_house.shared.logging import bind_context, get_logger
from media_house.shared.logging.formatters import (
    REDACTED,
    ConsoleFormatter,
    ContextFilter,
    JsonFormatter,
    redact,
)


class Capture(logging.Handler):
    def __init__(self, formatter: logging.Formatter) -> None:
        super().__init__(logging.DEBUG)
        self.setFormatter(formatter)
        self.addFilter(ContextFilter())
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


@pytest.fixture
def json_capture() -> Iterator[Capture]:
    logger = logging.getLogger("media_house.unit.logging")
    logger.setLevel(logging.DEBUG)
    capture = Capture(JsonFormatter())
    logger.addHandler(capture)
    yield capture
    logger.removeHandler(capture)


def test_json_record_has_standard_fields_and_structured_context(json_capture: Capture) -> None:
    get_logger("media_house.unit.logging").info("hello", asset="a1", count=3)
    entry = json.loads(json_capture.lines[0])
    assert entry["message"] == "hello"
    assert entry["level"] == "INFO"
    assert entry["logger"] == "media_house.unit.logging"
    assert entry["fields"] == {"asset": "a1", "count": 3}
    assert entry["timestamp"].endswith("+00:00")
    assert entry["function"] == "test_json_record_has_standard_fields_and_structured_context"


def test_correlation_and_operation_ids_are_attached_and_scoped(json_capture: Capture) -> None:
    log = get_logger("media_house.unit.logging")
    with bind_context(correlation_id="corr-1"), bind_context(operation_id="op-9"):
        log.info("inside")
    log.info("outside")
    inside, outside = (json.loads(line) for line in json_capture.lines)
    assert (inside["correlation_id"], inside["operation_id"]) == ("corr-1", "op-9")
    assert (outside["correlation_id"], outside["operation_id"]) == (None, None)


def test_sensitive_fields_are_redacted(json_capture: Capture) -> None:
    get_logger("media_house.unit.logging").info(
        "login",
        user="alice",
        password="p4ss",
        nested={"api_key": "k", "ok": 1},
        Authorization="Bearer x",
    )
    fields = json.loads(json_capture.lines[0])["fields"]
    assert fields["user"] == "alice"
    assert fields["password"] == REDACTED
    assert fields["nested"] == {"api_key": REDACTED, "ok": 1}
    assert fields["Authorization"] == REDACTED
    assert "p4ss" not in json_capture.lines[0]


def test_exception_traceback_is_included(json_capture: Capture) -> None:
    log = get_logger("media_house.unit.logging")
    try:
        raise ValueError("boom")
    except ValueError:
        log.exception("failed")
    entry = json.loads(json_capture.lines[0])
    assert "ValueError: boom" in entry["exception"]
    assert "Traceback" in entry["exception"]


def test_non_serialisable_fields_do_not_break_logging(json_capture: Capture) -> None:
    get_logger("media_house.unit.logging").info("x", obj=object())
    assert json.loads(json_capture.lines[0])["fields"]["obj"].startswith("<object")


def test_console_formatter_is_single_line_with_context() -> None:
    logger = logging.getLogger("media_house.unit.console")
    logger.setLevel(logging.DEBUG)
    capture = Capture(ConsoleFormatter())
    logger.addHandler(capture)
    try:
        with bind_context(correlation_id="c1"):
            get_logger("media_house.unit.console").warning("careful", path="/x")
    finally:
        logger.removeHandler(capture)
    line = capture.lines[0]
    assert "WARNING" in line
    assert "careful" in line
    assert "corr=c1" in line
    assert "path=/x" in line
    assert "\n" not in line


def test_redact_leaves_harmless_values_untouched() -> None:
    assert redact({"title": "x"}) == {"title": "x"}
