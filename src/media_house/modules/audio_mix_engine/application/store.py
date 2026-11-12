"""How specifications and results are stored: JSON documents of the anchor asset in the Media
Library (no storage of its own). Rendered audio (stems, the mix) are derived assets of it."""

import json
from collections.abc import Mapping
from typing import cast

from media_house.modules.audio_mix_engine.domain.errors import InvalidMixDocument
from media_house.modules.audio_mix_engine.domain.result import (
    MixResult,
    result_from_json,
    result_to_json,
)
from media_house.modules.audio_mix_engine.domain.spec import (
    AudioMixSpecification,
    spec_from_json,
    spec_to_json,
)
from media_house.modules.audio_mix_engine.domain.values import (
    ENGINE_VERSION,
    LEVELS_OPERATION,
    RESULT_OPERATION,
    SPEC_OPERATION,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


def _spec(operation: str, key: Mapping[str, object]) -> DocumentSpec:
    return DocumentSpec(
        operation,
        ENGINE_VERSION,
        cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str))),
    )


class MixStore:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    # --- specification ------------------------------------------------------------------------
    def find_spec(
        self, anchor_id: str, content_hash: str
    ) -> Result[tuple[AudioMixSpecification, MediaAssetDto] | None, MediaError]:
        spec = _spec(SPEC_OPERATION, {"content_hash": content_hash})
        fingerprint = self._documents.fingerprint(anchor_id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        return Ok(self._documents.load(anchor_id, fingerprint.value, spec_from_json))

    def save_spec(
        self, anchor: MediaAssetDto, spec: AudioMixSpecification
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            spec_to_json(spec),
            filename="audio_mix_specification.json",
            spec=_spec(SPEC_OPERATION, {"content_hash": spec.content_hash}),
            display_name=f"{anchor.display_name} (mix specification)",
            metadata={
                "timeline_id": spec.timeline_id,
                "timeline_version": spec.timeline_version,
                "content_hash": spec.content_hash,
                "specification_id": spec.specification_id,
                "profile": spec.provenance.profile,
                "delivery": spec.provenance.delivery,
            },
        )

    def latest_spec(
        self, anchor_id: str, timeline_id: str | None
    ) -> Result[tuple[AudioMixSpecification, MediaAssetDto] | None, MediaError]:
        for asset in self._history(anchor_id, SPEC_OPERATION, timeline_id):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                return path
            try:
                return Ok((spec_from_json(path.value.read_text(encoding="utf-8")), asset))
            except (InvalidMixDocument, OSError, UnicodeDecodeError, MediaHouseError) as exc:
                _log.warning("Stored mix specification unusable", reason=str(exc))
        return Ok(None)

    # --- measured levels ------------------------------------------------------------------------
    def load_levels(self, anchor_id: str, keys: list[str]) -> dict[str, float | None] | None:
        """Loudness measured before for exactly these parts of files (``method|checksum|in|out``),
        or ``None``: nothing stored (the files are measured again)."""
        spec = _spec(LEVELS_OPERATION, {"keys": keys})
        fingerprint = self._documents.fingerprint(anchor_id, spec)
        if isinstance(fingerprint, Err):
            return None
        found = self._documents.load(anchor_id, fingerprint.value, _levels_from_json)
        return None if found is None or set(found[0]) != set(keys) else found[0]

    def save_levels(
        self, anchor: MediaAssetDto, values: Mapping[str, float | None]
    ) -> Result[MediaAssetDto, MediaError]:
        keys = sorted(values)
        return self._documents.store(
            anchor,
            json.dumps({k: values[k] for k in keys}, sort_keys=True),
            filename="audio_mix_levels.json",
            spec=_spec(LEVELS_OPERATION, {"keys": keys}),
            display_name=f"{anchor.display_name} (measured sound levels)",
            metadata={"parts": len(keys)},
        )

    # --- result -------------------------------------------------------------------------------
    def save_result(
        self, anchor: MediaAssetDto, result: MixResult
    ) -> Result[MediaAssetDto, MediaError]:
        return self._documents.store(
            anchor,
            result_to_json(result),
            filename="audio_mix_result.json",
            spec=_spec(RESULT_OPERATION, {"mix_job_id": result.mix_job_id}),
            display_name=f"{anchor.display_name} (mix result)",
            metadata={
                "timeline_id": result.timeline_id,
                "mix_job_id": result.mix_job_id,
                "state": result.state.value,
                "specification_hash": result.specification_hash,
                "mix_asset_id": result.mix.asset_id if result.mix else None,
            },
        )

    def latest_result(
        self, anchor_id: str, timeline_id: str | None
    ) -> Result[tuple[MixResult, MediaAssetDto] | None, MediaError]:
        for asset in self._history(anchor_id, RESULT_OPERATION, timeline_id):
            path = self._library.local_path(asset.id)
            if isinstance(path, Err):
                return path
            try:
                return Ok((result_from_json(path.value.read_text(encoding="utf-8")), asset))
            except (InvalidMixDocument, OSError, UnicodeDecodeError, MediaHouseError) as exc:
                _log.warning("Stored mix result unusable", reason=str(exc))
        return Ok(None)

    def _history(
        self, anchor_id: str, operation: str, timeline_id: str | None
    ) -> list[MediaAssetDto]:
        found = [
            a
            for a in self._library.list_derived(anchor_id)
            if a.metadata.get("processing_type") == operation
            and (timeline_id is None or a.metadata.get("timeline_id") == timeline_id)
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)


def _levels_from_json(text: str) -> dict[str, float | None]:
    raw = json.loads(text)
    if not isinstance(raw, dict) or not all(
        v is None or isinstance(v, int | float) for v in raw.values()
    ):
        raise InvalidMixDocument("not a set of measured levels")
    return {str(k): None if v is None else float(v) for k, v in raw.items()}
