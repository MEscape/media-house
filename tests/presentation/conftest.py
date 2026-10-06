from collections.abc import Iterator

import pytest

from media_house.presentation.qt.dispatcher import UiDispatcher
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.shared.concurrency import ThreadPoolJobScheduler
from media_house.shared.errors.handler import ErrorHandler, ErrorReport

pytestmark = pytest.mark.presentation


@pytest.fixture
def dispatcher(qapp: object) -> UiDispatcher:
    return UiDispatcher()


@pytest.fixture
def unhandled_errors() -> list[ErrorReport]:
    return []


@pytest.fixture
def runner(
    dispatcher: UiDispatcher,
    unhandled_errors: list[ErrorReport],
) -> Iterator[UiJobRunner]:
    scheduler = ThreadPoolJobScheduler(max_workers=2, error_handler=ErrorHandler())
    yield UiJobRunner(scheduler, dispatcher, on_unhandled_error=unhandled_errors.append)
    scheduler.shutdown(timeout=5)
