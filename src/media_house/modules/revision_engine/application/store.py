"""How revision plans are stored: JSON documents of the anchor asset in the Media Library (no
storage of its own). A plan moves (approved, carried out, validated); each state is a new document
and the earlier ones stay, so the history of a revision can be read back."""

import json
from typing import TYPE_CHECKING, cast

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.revision_engine.domain.errors import InvalidRevisionDocument
from media_house.modules.revision_engine.domain.model import RevisionPlan
from media_house.modules.revision_engine.domain.serialization import plan_from_json, plan_to_json
from media_house.modules.revision_engine.domain.values import RESULT_OPERATION, REVISION_VERSION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

if TYPE_CHECKING:
    from collections.abc import Mapping

_log = get_logger(__name__)


def _spec(plan: RevisionPlan) -> DocumentSpec:
    key: Mapping[str, object] = {
        "revision_plan_id": plan.revision_plan_id,
        "content_hash": plan.content_hash,
    }
    return DocumentSpec(
        RESULT_OPERATION,
        REVISION_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key))),
    )


class RevisionStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def save(self, anchor: MediaAssetDto, plan: RevisionPlan) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            plan_to_json(plan),
            filename="revision_plan.json",
            spec=_spec(plan),
            display_name=f"{anchor.display_name} (revision plan {plan.plan_version})",
            metadata={
                "revision_plan_id": plan.revision_plan_id,
                "revision_id": plan.revision_id,
                "review_session_id": plan.review_session_id,
                "timeline_id": plan.timeline_id,
                "source_timeline_version": plan.source_timeline_version,
                "execution_status": plan.execution_status.value,
                "approval_requirement": plan.approval_requirement.value,
            },
        )

    def history(self, anchor_id: str, *, session_id: str | None = None) -> list[MediaAssetDto]:
        found = [
            a
            for a in self._library.list_derived(anchor_id)
            if a.metadata.get("processing_type") == RESULT_OPERATION
            and (session_id is None or a.metadata.get("review_session_id") == session_id)
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)

    def load(self, asset_id: str) -> Result[RevisionPlan, MediaError | InvalidRevisionDocument]:
        path = self._library.local_path(asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(plan_from_json(path.value.read_text(encoding="utf-8")))
        except InvalidRevisionDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, MediaHouseError) as exc:
            _log.warning("Stored revision plan unusable", asset_id=asset_id, reason=str(exc))
            return Err(InvalidRevisionDocument(str(exc)))

    def plans(
        self, anchor_id: str, *, session_id: str | None = None
    ) -> Result[list[tuple[RevisionPlan, MediaAssetDto]], MediaError]:
        """The newest document of every plan, newest plan first."""
        newest: dict[str, tuple[RevisionPlan, MediaAssetDto]] = {}
        for asset in self.history(anchor_id, session_id=session_id):
            loaded = self.load(asset.id)
            if isinstance(loaded, Err):
                if isinstance(loaded.error, InvalidRevisionDocument):
                    continue
                return Err(loaded.error)
            newest.setdefault(loaded.value.revision_plan_id, (loaded.value, asset))
        return Ok(list(newest.values()))

    def find(
        self, anchor_id: str, plan_id: str, *, session_id: str | None = None
    ) -> Result[tuple[RevisionPlan, MediaAssetDto] | None, MediaError]:
        listed = self.plans(anchor_id, session_id=session_id)
        if isinstance(listed, Err):
            return listed
        return Ok(next((p for p in listed.value if p[0].revision_plan_id == plan_id), None))
