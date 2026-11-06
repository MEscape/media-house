"""How a built timeline is stored: one JSON document of the anchor asset, keyed by its inputs.

Both the builder and the edit recorder store through here, so a later build of an unchanged
specification finds the document a recorded edit made (same key) instead of making another.
"""

import json
from collections.abc import Mapping
from typing import cast

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
)
from media_house.modules.timeline_builder.domain.model import BuiltTimeline
from media_house.modules.timeline_builder.domain.serialization import (
    timeline_from_json,
    timeline_to_json,
)
from media_house.modules.timeline_builder.domain.values import BUILDER_VERSION, RESULT_OPERATION
from media_house.shared.errors import Err, Ok, Result


def document_spec(key: Mapping[str, object]) -> DocumentSpec:
    return DocumentSpec(
        RESULT_OPERATION,
        BUILDER_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str))),
    )


def find(
    documents: DerivedDocuments, anchor: str, key: Mapping[str, object]
) -> Result[tuple[BuiltTimeline, MediaAssetDto] | None, MediaError]:
    spec = document_spec(key)
    fingerprint = documents.fingerprint(anchor, spec)
    if isinstance(fingerprint, Err):
        return fingerprint
    return Ok(documents.load(anchor, fingerprint.value, timeline_from_json))


def save(
    documents: DerivedDocuments,
    anchor: MediaAssetDto,
    timeline: BuiltTimeline,
    key: Mapping[str, object],
) -> Result[MediaAssetDto, MediaError]:
    issues = timeline.issues
    return documents.store(
        anchor,
        timeline_to_json(timeline),
        filename="built_timeline.json",
        spec=document_spec(key),
        display_name=f"{anchor.display_name} (built timeline)",
        metadata={
            "timeline_id": timeline.timeline_id,
            "edit_plan_id": timeline.edit.edit_plan_id if timeline.edit else None,
            "build_version": timeline.build.build_version,
            "spec_hash": timeline.build.spec_hash,
            "spec_asset_id": timeline.build.spec_asset_id,
            "build_hash": timeline.build.build_hash,
            "status": timeline.status.value,
            "mode": timeline.build.mode.value,
            "issues": len(issues),
            "unsupported": sum(1 for i in issues if i.severity.value == "unsupported"),
            "reviews": sum(1 for i in issues if i.severity.value == "review"),
        },
    )
