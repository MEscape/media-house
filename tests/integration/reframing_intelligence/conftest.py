"""The real application for every reframing integration test."""

from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.shared.configuration import Environment


@pytest.fixture
def app(tmp_path: Path):  # type: ignore[no-untyped-def]
    application = Application.start(
        StartupOptions(env={}, home=tmp_path / "home", environment=Environment.TEST)
    )
    yield application
    application.shutdown()
