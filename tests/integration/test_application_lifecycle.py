from pathlib import Path

import pytest

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.lifecycle import StartupError, StartupPhase
from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.workspace.application.commands import CreateWorkspaceCommand
from media_house.modules.workspace.application.contracts import WorkspaceCatalog
from media_house.modules.workspace.application.create_workspace import CreateWorkspace
from media_house.shared.concurrency import JobScheduler
from media_house.shared.configuration import AppSettings, Environment
from media_house.shared.errors import ApplicationError, Ok
from media_house.shared.errors.handler import ErrorHandler
from media_house.shared.events import EventBus, EventPublisher
from media_house.shared.filesystem import AppPaths

pytestmark = pytest.mark.integration


def options(home: Path, **kwargs: object) -> StartupOptions:
    return StartupOptions(env={}, home=home, environment=Environment.TEST, **kwargs)  # type: ignore[arg-type]


def test_starts_with_default_modules_and_wires_everything(tmp_path: Path) -> None:
    application = Application.start(options(tmp_path))
    try:
        container = application.container
        for service in (
            AppSettings,
            AppPaths,
            ErrorHandler,
            EventBus,
            JobScheduler,
            ProcessRunner,
            Clock,
        ):
            assert container.resolve(service) is not None
        assert container.resolve(EventPublisher) is container.resolve(EventBus)
        assert {m.name for m in application.modules} == {
            "workspace",
            "media_library",
            "image_adjustment",
            "audio_intelligence",
        }
        assert (tmp_path / "data").is_dir()
    finally:
        application.shutdown()


def test_the_example_module_works_end_to_end_against_a_real_database(tmp_path: Path) -> None:
    application = Application.start(options(tmp_path))
    try:
        create = application.container.resolve(CreateWorkspace)
        assert isinstance(create.execute(CreateWorkspaceCommand("Real")), Ok)
        catalog = application.container.resolve(WorkspaceCatalog)
        assert [w.name for w in catalog.list_workspaces()] == ["Real"]
        assert (tmp_path / "data" / "workspaces.db").is_file()
    finally:
        application.shutdown()


def test_shutdown_is_idempotent_and_stops_background_work(tmp_path: Path) -> None:
    application = Application.start(options(tmp_path))
    scheduler = application.container.resolve(JobScheduler)
    application.shutdown()
    application.shutdown()
    with pytest.raises(ApplicationError):
        scheduler.submit("late", lambda _ctx: None)


def test_invalid_configuration_fails_in_the_configuration_phase(tmp_path: Path) -> None:
    bad = StartupOptions(
        env={"MEDIA_HOUSE_LOGGING__LEVEL": "LOUD"},
        home=tmp_path,
        environment=Environment.TEST,
    )
    with pytest.raises(StartupError) as caught:
        Application.start(bad)
    assert caught.value.phase is StartupPhase.CONFIGURATION
    assert "logging.level" in caught.value.user_message
    assert caught.value.error_id == caught.value.__cause__.error_id  # type: ignore[union-attr]


def test_unwritable_home_fails_in_the_environment_phase(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x", encoding="utf-8")
    with pytest.raises(StartupError) as caught:
        Application.start(options(blocker / "home"))
    assert caught.value.phase is StartupPhase.ENVIRONMENT


def test_a_module_that_fails_to_register_aborts_startup_cleanly(tmp_path: Path) -> None:
    class Broken:
        name = "broken"

        def register(self, container: Container) -> None:
            raise RuntimeError("module bug")

    with pytest.raises(StartupError) as caught:
        Application.start(options(tmp_path, modules=[Broken()]))
    assert caught.value.phase is StartupPhase.SERVICES
    assert isinstance(caught.value.__cause__, RuntimeError)


def test_home_directory_can_come_from_the_environment(tmp_path: Path) -> None:
    application = Application.start(
        StartupOptions(
            env={"MEDIA_HOUSE_HOME": str(tmp_path / "portable")}, environment=Environment.TEST
        ),
    )
    try:
        assert application.paths.data_dir == tmp_path / "portable" / "data"
    finally:
        application.shutdown()
