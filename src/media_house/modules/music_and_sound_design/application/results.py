"""Read stored documents and the sound library without running anything."""

from collections.abc import Callable
from dataclasses import replace

from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.modules.music_and_sound_design.domain.analysis import MusicAnalysis, SfxProfile
from media_house.modules.music_and_sound_design.domain.errors import InvalidSoundDocument
from media_house.modules.music_and_sound_design.domain.library import (
    SoundAsset,
    SoundIndex,
    SoundMatch,
    SoundQuery,
    sound_from_metadata,
)
from media_house.modules.music_and_sound_design.domain.model import SoundDesignPlan
from media_house.modules.music_and_sound_design.domain.serialization import (
    music_analysis_from_json,
    plan_from_json,
    sfx_profile_from_json,
)
from media_house.modules.music_and_sound_design.domain.values import (
    ANALYZER_VERSION,
    LIBRARY_TAG,
    METADATA_KEY,
    MUSIC_ANALYSIS,
    SFX_PROFILE,
    SOUND_DESIGN_PLAN,
    SoundKind,
)
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_PAGE = 200


class _Documents[T]:
    def __init__(self, library: MediaLibrary, operation: str, parse: Callable[[str], T]) -> None:
        self._library = library
        self._operation = operation
        self._parse = parse

    def stored(self, asset_id: str, **match: object) -> list[MediaAssetDto]:
        found = [
            a
            for a in self._library.list_derived(asset_id)
            if a.metadata.get("processing_type") == self._operation
            and all(v is None or a.metadata.get(k) == v for k, v in match.items())
        ]
        return sorted(found, key=lambda a: (a.created_at, a.id), reverse=True)

    def latest(
        self, asset_id: str, **match: object
    ) -> Result[tuple[T, MediaAssetDto] | None, MediaError]:
        for asset in self.stored(asset_id, **match):
            loaded = self.load(asset.id)
            if isinstance(loaded, Ok):
                return Ok((loaded.value, asset))
            if not isinstance(loaded.error, InvalidSoundDocument):
                return Err(loaded.error)
            _log.warning(
                "Stored sound document unusable, trying an older one",
                asset_id=asset.id,
                reason=str(loaded.error),
            )
        return Ok(None)

    def load(self, document_asset_id: str) -> Result[T, MediaError | InvalidSoundDocument]:
        path = self._library.local_path(document_asset_id)
        if isinstance(path, Err):
            return path
        try:
            return Ok(self._parse(path.value.read_text(encoding="utf-8")))
        except InvalidSoundDocument as exc:
            return Err(exc)
        except (OSError, UnicodeDecodeError, ValueError, MediaHouseError) as exc:
            return Err(InvalidSoundDocument(str(exc)))


class MusicAnalysisResults:
    """Stored ``music_analysis`` documents (one per music asset and analyser version)."""

    def __init__(self, library: MediaLibrary) -> None:
        self._docs = _Documents(library, MUSIC_ANALYSIS, music_analysis_from_json)

    def latest(
        self, asset_id: str
    ) -> Result[tuple[MusicAnalysis, MediaAssetDto] | None, MediaError]:
        return self._docs.latest(asset_id)

    def load(
        self, document_asset_id: str
    ) -> Result[MusicAnalysis, MediaError | InvalidSoundDocument]:
        return self._docs.load(document_asset_id)


class SfxProfileResults:
    """Stored ``sfx_profile`` documents (one per effect asset and analyser version)."""

    def __init__(self, library: MediaLibrary) -> None:
        self._docs = _Documents(library, SFX_PROFILE, sfx_profile_from_json)

    def latest(self, asset_id: str) -> Result[tuple[SfxProfile, MediaAssetDto] | None, MediaError]:
        return self._docs.latest(asset_id)

    def load(self, document_asset_id: str) -> Result[SfxProfile, MediaError | InvalidSoundDocument]:
        return self._docs.load(document_asset_id)


class SoundDesignResults:
    """Stored ``sound_design_plan`` documents (derived documents of the edit's anchor asset)."""

    def __init__(self, library: MediaLibrary) -> None:
        self._docs = _Documents(library, SOUND_DESIGN_PLAN, plan_from_json)

    def latest(
        self,
        asset_id: str,
        *,
        edit_plan_id: str | None = None,
        profile: str | None = None,
    ) -> Result[tuple[SoundDesignPlan, MediaAssetDto] | None, MediaError]:
        return self._docs.latest(asset_id, edit_plan_id=edit_plan_id, profile=profile)

    def history(self, asset_id: str) -> list[MediaAssetDto]:
        return self._docs.stored(asset_id)

    def load(
        self, plan_asset_id: str
    ) -> Result[SoundDesignPlan, MediaError | InvalidSoundDocument]:
        return self._docs.load(plan_asset_id)


class SoundLibraryResults:
    """The imported sound library as a searchable index (manifest facts + measurements).

    The Media Library filters by tags only, so every ``sound_library`` asset is paged in and its
    metadata read in memory. A measurement counts only if it was made by the current analyser
    (an older one is stale and treated as missing).
    """

    def __init__(
        self,
        library: MediaLibrary,
        music: MusicAnalysisResults,
        sfx: SfxProfileResults,
    ) -> None:
        self._library = library
        self._music = music
        self._sfx = sfx

    def assets(self, kind: SoundKind | None = None) -> list[MediaAssetDto]:
        tags = {LIBRARY_TAG} | ({f"sound:{kind.value}"} if kind else set())
        out: list[MediaAssetDto] = []
        page = 1
        while True:
            found = self._library.search(
                MediaQuery(
                    media_types=frozenset({MediaType.AUDIO}),
                    tags=frozenset(tags),
                    page=page,
                    page_size=_PAGE,
                )
            )
            out += list(found.items)
            if page * _PAGE >= found.total or not found.items:
                return out
            page += 1

    def sounds(self, kind: SoundKind | None = None) -> list[SoundAsset]:
        out = []
        for asset in self.assets(kind):
            sound = self.sound_of(asset)
            if sound is not None:
                out.append(sound)
        return out

    def sound_of(self, asset: MediaAssetDto) -> SoundAsset | None:
        meta = asset.metadata.get(METADATA_KEY)
        if not isinstance(meta, dict):
            return None
        sound = sound_from_metadata(
            asset_id=asset.id,
            checksum=asset.checksum,
            duration_seconds=asset.duration_seconds,
            favorite=asset.is_favorite,
            metadata=meta,
        )
        if sound is None:
            return None
        if sound.kind is SoundKind.MUSIC:
            music = self._music.latest(asset.id)
            if isinstance(music, Ok) and music.value is not None:
                analysis, doc = music.value
                if analysis.analyzer_version == ANALYZER_VERSION:
                    return _with(sound, music=analysis, doc=doc.id)
        else:
            sfx = self._sfx.latest(asset.id)
            if isinstance(sfx, Ok) and sfx.value is not None:
                profile, doc = sfx.value
                if profile.analyzer_version == ANALYZER_VERSION:
                    return _with(sound, sfx=profile, doc=doc.id)
        return sound

    def get(self, asset_id: str) -> SoundAsset | None:
        found = self._library.get(asset_id)
        return self.sound_of(found.value) if isinstance(found, Ok) else None

    def index(self) -> SoundIndex:
        return SoundIndex(self.sounds())

    def search(self, query: SoundQuery) -> list[SoundMatch]:
        return self.index().search(query)


def _with(
    sound: SoundAsset,
    *,
    doc: str,
    music: MusicAnalysis | None = None,
    sfx: SfxProfile | None = None,
) -> SoundAsset:
    return replace(sound, music=music, sfx=sfx, analysis_asset_id=doc)
