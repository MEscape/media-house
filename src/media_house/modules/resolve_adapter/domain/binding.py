"""The stable link between Media House and the Resolve project: what was sent and what it looked
like when it was last in sync.

Stable internal identities are the keys (the timeline's element ids, the library's asset ids).
Resolve's own ids are stored as EXTERNAL references next to them and never replace them, because
a project, a timeline or a clip can be renamed, moved or re-imported in Resolve.

``fields`` of an element are the comparable state the adapter wrote and then read back. Comparing
them with the timeline's new state (what Media House wants now) and with what Resolve holds now is
the three-way merge that finds a person's work.
"""

import json
from dataclasses import dataclass

from media_house.core.domain import json_codec
from media_house.modules.resolve_adapter.domain.errors import InvalidBindingDocument
from media_house.modules.resolve_adapter.domain.values import (
    ADAPTER_VERSION,
    BINDING_DOCUMENT_TYPE,
    SCHEMA_VERSION,
    ChangeOrigin,
    Resolution,
)


@dataclass(frozen=True, slots=True)
class ProjectBinding:
    anchor_asset_id: str
    timeline_id: str
    project_name: str
    project_id: str | None
    database: str | None
    timeline_name: str
    timeline_resolve_id: str | None


@dataclass(frozen=True, slots=True)
class MediaBinding:
    asset_id: str
    pool_item_id: str
    path: str
    folder: tuple[str, ...]
    checksum: str | None
    proxy_path: str | None = None


@dataclass(frozen=True, slots=True)
class ElementBinding:
    element_id: str
    kind: str  # video | audio
    item_id: str
    #: The comparable state last written and read back (name, value).
    fields: tuple[tuple[str, str], ...]
    digest: str


@dataclass(frozen=True, slots=True)
class MarkerBinding:
    custom: str
    digest: str


@dataclass(frozen=True, slots=True)
class FieldConflict:
    name: str
    base: str | None
    media_house: str | None
    resolve: str | None


@dataclass(frozen=True, slots=True)
class ConflictRecord:
    element_id: str
    fields: tuple[FieldConflict, ...]
    origin: ChangeOrigin
    resolution: Resolution
    note: str = ""


@dataclass(frozen=True, slots=True)
class SyncState:
    project: ProjectBinding
    media: tuple[MediaBinding, ...]
    elements: tuple[ElementBinding, ...]
    markers: tuple[MarkerBinding, ...]
    #: Conflicts still waiting for a decision (blocks a final render).
    conflicts: tuple[ConflictRecord, ...]
    build_hash: str
    build_version: int
    sequence: int
    #: Which Resolve ``observed`` was confirmed on, and the operations a read-back confirmed there.
    environment_key: str
    observed: tuple[str, ...]
    last_execution_id: str
    adapter_version: int = ADAPTER_VERSION
    schema_version: int = SCHEMA_VERSION

    def element(self, element_id: str) -> ElementBinding | None:
        return next((e for e in self.elements if e.element_id == element_id), None)

    def media_of(self, asset_id: str) -> MediaBinding | None:
        return next((m for m in self.media if m.asset_id == asset_id), None)

    def marker(self, custom: str) -> MarkerBinding | None:
        return next((m for m in self.markers if m.custom == custom), None)


def state_to_json(state: SyncState) -> str:
    return json_codec.dumps(BINDING_DOCUMENT_TYPE, state)


def state_from_json(text: str) -> SyncState:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidBindingDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != BINDING_DOCUMENT_TYPE:
        raise InvalidBindingDocument("not a Resolve binding document")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidBindingDocument(
            f"schema version {raw.get('schema_version')!r} is not readable"
        )
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(SyncState, body, "binding")
    except (TypeError, ValueError) as exc:
        raise InvalidBindingDocument(str(exc)) from exc
    if not isinstance(decoded, SyncState):
        raise InvalidBindingDocument("not a Resolve binding document")
    return decoded
