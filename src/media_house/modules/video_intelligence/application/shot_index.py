"""Find-or-build of the searchable shot index of an asset's newest stored analysis.

The index is a small derived document (see ``domain.shot_index``). It is keyed by the library
identity of the analysis it projects (the stored analysis asset and its checksum) plus the index
version, so:

* a lookup never parses the (large) analysis: it asks the library whether the index exists;
* a new analysis of the asset, or a new index version, makes a different key and a new index;
* a damaged index is removed by ``DerivedDocuments`` and rebuilt from the analysis.

Nothing is analysed here: if the asset has no stored analysis the answer is ``None``.
"""

from dataclasses import dataclass

from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.video_intelligence.domain.serialization import (
    analysis_from_json,
    document_from_json,
    document_to_json,
)
from media_house.modules.video_intelligence.domain.shot_index import (
    INDEX_DOCUMENT,
    INDEX_OPERATION,
    INDEX_VERSION,
    ShotIndex,
    build_shot_index,
)
from media_house.modules.video_intelligence.domain.values import RESULT_OPERATION
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class IndexedAsset:
    index: ShotIndex
    #: The stored index document.
    asset: MediaAssetDto
    #: ``False`` when the index already existed.
    built: bool


class ShotIndexes:
    def __init__(self, library: MediaLibrary, paths: AppPaths) -> None:
        self._library = library
        self._documents = DerivedDocuments(library, paths)

    def shot_index(self, asset_id: str) -> Result[IndexedAsset | None, MediaError]:
        """The index of the newest readable stored analysis of ``asset_id``, else ``Ok(None)``."""
        stored = sorted(
            (
                a
                for a in self._library.list_derived(asset_id)
                if a.metadata.get("processing_type") == RESULT_OPERATION
            ),
            key=lambda a: (a.created_at, a.id),
            reverse=True,
        )
        source = self._library.get(asset_id)
        if isinstance(source, Err):
            return source
        for analysis_asset in stored:
            spec = DocumentSpec(
                INDEX_OPERATION,
                INDEX_VERSION,
                {"analysis": analysis_asset.id, "checksum": analysis_asset.checksum},
            )
            fingerprint = self._documents.fingerprint(asset_id, spec)
            if isinstance(fingerprint, Err):
                return fingerprint
            found = self._documents.load(asset_id, fingerprint.value, _index_from_json)
            if found is not None:
                return Ok(IndexedAsset(found[0], found[1], built=False))
            built = self._build(source.value, analysis_asset, spec)
            if built is not None:
                return built
        return Ok(None)

    def _build(
        self, source: MediaAssetDto, analysis_asset: MediaAssetDto, spec: DocumentSpec
    ) -> Result[IndexedAsset, MediaError] | None:
        path = self._library.local_path(analysis_asset.id)
        if isinstance(path, Err):
            return path
        try:
            index = build_shot_index(analysis_from_json(path.value.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            _log.warning(
                "Stored video analysis unusable, trying an older one",
                asset_id=analysis_asset.id,
                reason=str(exc),
            )
            return None
        stored = self._documents.store(
            source,
            document_to_json(INDEX_DOCUMENT, index),
            filename="shot_index.json",
            spec=spec,
            display_name=f"{source.display_name} (shot index)",
            metadata={"analysis_asset_id": analysis_asset.id, "shots": len(index.shots)},
        )
        if isinstance(stored, Err):
            return stored
        _log.info("Shot index built", asset_id=stored.value.id, shots=len(index.shots))
        return Ok(IndexedAsset(index, stored.value, built=True))


def _index_from_json(text: str) -> ShotIndex:
    return document_from_json(INDEX_DOCUMENT, ShotIndex, text)
