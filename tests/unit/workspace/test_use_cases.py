import pytest

from media_house.modules.workspace.application.commands import CreateWorkspaceCommand
from media_house.modules.workspace.application.contracts import WorkspaceCatalog
from media_house.modules.workspace.application.create_workspace import CreateWorkspace
from media_house.modules.workspace.application.events import WorkspaceVerificationCompleted
from media_house.modules.workspace.application.list_workspaces import ListWorkspaces
from media_house.modules.workspace.application.verify_workspaces import VerifyWorkspaces
from media_house.modules.workspace.domain.events import WorkspaceCreated
from media_house.modules.workspace.domain.repository import WorkspaceNameTaken
from media_house.modules.workspace.domain.values import WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import (
    ConflictError,
    Err,
    Ok,
    OperationCancelledError,
    ValidationError,
)
from tests.support.fakes import (
    T0,
    FixedClock,
    InMemoryWorkspaceRepository,
    RecordingPublisher,
)


@pytest.fixture
def repository() -> InMemoryWorkspaceRepository:
    return InMemoryWorkspaceRepository()


@pytest.fixture
def events() -> RecordingPublisher:
    return RecordingPublisher()


@pytest.fixture
def create(repository: InMemoryWorkspaceRepository, events: RecordingPublisher) -> CreateWorkspace:
    return CreateWorkspace(repository, FixedClock(), events)


class TestCreateWorkspace:
    def test_creates_persists_and_publishes(
        self,
        create: CreateWorkspace,
        repository: InMemoryWorkspaceRepository,
        events: RecordingPublisher,
    ) -> None:
        result = create.execute(CreateWorkspaceCommand("  Docs "))
        assert isinstance(result, Ok)
        assert (result.value.name, result.value.created_at) == ("Docs", T0)
        assert len(repository.list_all()) == 1
        assert [type(e) for e in events.events] == [WorkspaceCreated]

    def test_invalid_name_is_an_expected_failure_not_an_exception(
        self,
        create: CreateWorkspace,
        events: RecordingPublisher,
    ) -> None:
        result = create.execute(CreateWorkspaceCommand("   "))
        assert isinstance(result, Err)
        assert isinstance(result.error, ValidationError)
        assert result.error.field == "name"
        assert result.error.user_message
        assert events.events == []

    def test_duplicate_names_conflict_case_insensitively(self, create: CreateWorkspace) -> None:
        assert isinstance(create.execute(CreateWorkspaceCommand("Docs")), Ok)
        result = create.execute(CreateWorkspaceCommand("dOCS"))
        assert isinstance(result, Err)
        assert isinstance(result.error, ConflictError)
        assert "dOCS" in result.error.user_message

    def test_losing_a_write_race_is_reported_as_conflict(self, events: RecordingPublisher) -> None:
        class RacyRepository(InMemoryWorkspaceRepository):
            def name_exists(self, name: WorkspaceName) -> bool:
                return False  # the check passed, then someone else won

            def add(self, workspace: Workspace) -> None:
                raise WorkspaceNameTaken(workspace.name)

        result = CreateWorkspace(RacyRepository(), FixedClock(), events).execute(
            CreateWorkspaceCommand("Docs"),
        )
        assert isinstance(result, Err)
        assert isinstance(result.error, ConflictError)
        assert events.events == []

    def test_infrastructure_failures_propagate_as_exceptions(
        self, events: RecordingPublisher
    ) -> None:
        class BrokenRepository(InMemoryWorkspaceRepository):
            def name_exists(self, name: WorkspaceName) -> bool:
                raise OSError("disk gone")

        with pytest.raises(OSError, match="disk gone"):
            CreateWorkspace(BrokenRepository(), FixedClock(), events).execute(
                CreateWorkspaceCommand("Docs"),
            )


def test_list_returns_dtos_and_satisfies_the_public_contract(
    create: CreateWorkspace,
    repository: InMemoryWorkspaceRepository,
) -> None:
    create.execute(CreateWorkspaceCommand("A"))
    create.execute(CreateWorkspaceCommand("B"))
    catalog: WorkspaceCatalog = ListWorkspaces(repository)
    assert sorted(dto.name for dto in catalog.list_workspaces()) == ["A", "B"]


class _RecordingProgress:
    def __init__(self) -> None:
        self.reports: list[tuple[int, int | None, str]] = []

    def report(self, current: int, total: int | None = None, message: str = "") -> None:
        self.reports.append((current, total, message))


class TestVerifyWorkspaces:
    def _seed(self, repository: InMemoryWorkspaceRepository, count: int, *, now=T0) -> None:  # type: ignore[no-untyped-def]
        for index in range(count):
            repository.add(Workspace.create(WorkspaceName.of(f"W{index}"), now=now))

    def test_reports_progress_and_publishes_completion(
        self,
        repository: InMemoryWorkspaceRepository,
        events: RecordingPublisher,
    ) -> None:
        self._seed(repository, 3)
        progress = _RecordingProgress()
        context = JobContext("j", CancellationToken(), progress)

        summary = VerifyWorkspaces(repository, FixedClock(), events).execute(context)

        assert summary.checked == 3
        assert summary.is_healthy
        assert [(c, t) for c, t, _ in progress.reports] == [(1, 3), (2, 3), (3, 3)]
        (event,) = events.events
        assert isinstance(event, WorkspaceVerificationCompleted)
        assert (event.checked, event.problem_count) == (3, 0)

    def test_collects_domain_problems(
        self,
        repository: InMemoryWorkspaceRepository,
        events: RecordingPublisher,
    ) -> None:
        clock = FixedClock()
        self._seed(repository, 1, now=T0.replace(year=2030))
        summary = VerifyWorkspaces(repository, clock, events).execute(JobContext.detached())
        assert not summary.is_healthy
        assert "future" in summary.problems[0]

    def test_honours_cancellation_between_items(
        self,
        repository: InMemoryWorkspaceRepository,
        events: RecordingPublisher,
    ) -> None:
        self._seed(repository, 5)
        token = CancellationToken()

        class CancelAfterFirst(_RecordingProgress):
            def report(self, current: int, total: int | None = None, message: str = "") -> None:
                super().report(current, total, message)
                token.cancel()

        progress = CancelAfterFirst()
        with pytest.raises(OperationCancelledError):
            VerifyWorkspaces(repository, FixedClock(), events).execute(
                JobContext("j", token, progress),
            )
        assert len(progress.reports) == 1
        assert events.events == []  # no completion event for cancelled work
