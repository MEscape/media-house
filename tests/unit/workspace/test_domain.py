from datetime import timedelta

import pytest

from media_house.modules.workspace.domain.events import WorkspaceCreated
from media_house.modules.workspace.domain.values import WorkspaceId, WorkspaceName
from media_house.modules.workspace.domain.workspace import Workspace
from media_house.shared.errors import InvariantViolation
from tests.support.fakes import T0


class TestWorkspaceName:
    def test_input_is_trimmed(self) -> None:
        assert WorkspaceName.of("  My Project  ").value == "My Project"

    @pytest.mark.parametrize("raw", ["", "   ", "\t\n"])
    def test_blank_names_are_rejected(self, raw: str) -> None:
        with pytest.raises(InvariantViolation):
            WorkspaceName.of(raw)

    def test_length_limit(self) -> None:
        assert (
            len(WorkspaceName.of("x" * WorkspaceName.MAX_LENGTH).value) == WorkspaceName.MAX_LENGTH
        )
        with pytest.raises(InvariantViolation) as caught:
            WorkspaceName.of("x" * (WorkspaceName.MAX_LENGTH + 1))
        assert "80" in caught.value.user_message

    def test_control_characters_are_rejected(self) -> None:
        with pytest.raises(InvariantViolation):
            WorkspaceName.of("bad\x00name")

    def test_direct_construction_cannot_bypass_the_invariants(self) -> None:
        with pytest.raises(InvariantViolation):
            WorkspaceName(" untrimmed ")

    def test_uniqueness_key_is_case_insensitive(self) -> None:
        assert WorkspaceName.of("Édit").key == WorkspaceName.of("éDIT").key

    def test_value_objects_compare_by_value(self) -> None:
        assert WorkspaceName.of("a") == WorkspaceName.of("a")


def test_workspace_id_rejects_empty_values() -> None:
    with pytest.raises(InvariantViolation):
        WorkspaceId("")
    assert WorkspaceId.new() != WorkspaceId.new()


class TestWorkspace:
    def test_create_records_a_domain_event_once(self) -> None:
        workspace = Workspace.create(WorkspaceName.of("Docs"), now=T0)
        events = workspace.pull_events()
        assert len(events) == 1
        event = events[0]
        assert isinstance(event, WorkspaceCreated)
        assert (event.workspace_id, event.name, event.occurred_at) == (
            workspace.id.value,
            "Docs",
            T0,
        )
        assert workspace.pull_events() == []

    def test_restored_workspaces_raise_no_events(self) -> None:
        workspace = Workspace(id=WorkspaceId.new(), name=WorkspaceName.of("Old"), created_at=T0)
        assert workspace.pull_events() == []

    def test_problems_flags_creation_time_in_the_future(self) -> None:
        workspace = Workspace.create(WorkspaceName.of("Docs"), now=T0 + timedelta(days=1))
        assert workspace.problems(now=T0) == ["'Docs' has a creation time in the future"]
        assert workspace.problems(now=T0 + timedelta(days=2)) == []
