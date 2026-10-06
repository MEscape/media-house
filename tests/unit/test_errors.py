import logging

import pytest

from media_house.shared.errors import (
    ApplicationError,
    DomainError,
    Err,
    ErrorCategory,
    ExternalSystemError,
    InfrastructureError,
    InvariantViolation,
    MediaHouseError,
    Ok,
    ProcessFailedError,
    Result,
    ToolNotFoundError,
    UnexpectedError,
    ValidationError,
)
from media_house.shared.errors.handler import ErrorHandler


def test_every_error_has_a_category_and_a_unique_id() -> None:
    first, second = DomainError("a"), DomainError("a")
    assert first.category is ErrorCategory.DOMAIN
    assert first.error_id.startswith("ERR-")
    assert first.error_id != second.error_id


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (InvariantViolation("x"), ErrorCategory.DOMAIN),
        (ValidationError("x"), ErrorCategory.VALIDATION),
        (ApplicationError("x"), ErrorCategory.APPLICATION),
        (InfrastructureError("x"), ErrorCategory.INFRASTRUCTURE),
        (ToolNotFoundError("ffmpeg"), ErrorCategory.EXTERNAL_SYSTEM),
        (UnexpectedError("x"), ErrorCategory.UNEXPECTED),
    ],
)
def test_categories(error: MediaHouseError, category: ErrorCategory) -> None:
    assert error.category is category


def test_external_errors_are_infrastructure_errors_but_keep_their_own_category() -> None:
    assert issubclass(ExternalSystemError, InfrastructureError)
    assert ExternalSystemError("x").category is ErrorCategory.EXTERNAL_SYSTEM


def test_tool_not_found_has_actionable_user_message() -> None:
    error = ToolNotFoundError("ffmpeg")
    assert "ffmpeg" in error.user_message
    assert error.details == {"executable": "ffmpeg"}


def test_process_failed_keeps_stderr_in_details_not_in_user_message() -> None:
    error = ProcessFailedError("ffmpeg", 1, "/secret/path: bad codec")
    assert error.details["stderr"] == "/secret/path: bad codec"
    assert "/secret/path" not in error.user_message


def test_result_pattern_matching() -> None:
    def describe(result: Result[int, str]) -> str:
        match result:
            case Ok(value):
                return f"ok {value}"
            case Err(error):
                return f"err {error}"

    assert describe(Ok(1)) == "ok 1"
    assert describe(Err("nope")) == "err nope"


class TestErrorHandler:
    def test_unexpected_exception_is_wrapped_logged_with_traceback_and_hidden_from_user(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        handler = ErrorHandler()
        with caplog.at_level(logging.DEBUG, logger="media_house"):
            try:
                raise RuntimeError("internal detail /home/alice/secret")
            except RuntimeError as exc:
                report = handler.handle(exc, operation="demo")

        assert report.category is ErrorCategory.UNEXPECTED
        assert "internal detail" not in report.user_message
        assert report.error_id.startswith("ERR-")
        assert "RuntimeError" in report.technical_summary
        record = caplog.records[-1]
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None  # traceback preserved for developers
        assert record.mh_fields["error_id"] == report.error_id  # type: ignore[attr-defined]

    def test_expected_errors_are_logged_without_traceback(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.DEBUG, logger="media_house"):
            report = ErrorHandler().handle(ValidationError("bad name", field="name"))
        assert report.user_message == "bad name"
        assert caplog.records[-1].levelno == logging.INFO
        assert caplog.records[-1].exc_info is None

    def test_media_house_errors_keep_their_identity(self) -> None:
        error = ToolNotFoundError("ffprobe")
        report = ErrorHandler().handle(error)
        assert report.error_id == error.error_id
        assert report.code == "external.tool_not_found"
        assert report.recoverable is True
