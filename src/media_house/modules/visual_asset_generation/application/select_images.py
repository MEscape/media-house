"""Use case: find existing library images for a concept before anything is made or generated.

The library is searched by name/file name (its text search) and by tag, word by word; the domain
ranks the hits by source tier (uploads > approved project images > brand assets > earlier
generated images) and by how many of the concept's words they carry. ``for_gaps`` does the same
for every coverage gap of a stored b-roll analysis (read through ``BrollCatalog``; nothing is
analysed here). Picture-to-text similarity is ``not_available``: no contract embeds still
library images.
"""

from dataclasses import dataclass, field

from media_house.core.domain import Rational
from media_house.modules.broll_intelligence.application.contracts import BrollCatalog
from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaQuery,
    MediaType,
    SourceType,
)
from media_house.modules.visual_asset_generation.application.ports import ImageGenerator
from media_house.modules.visual_asset_generation.domain.model import StageReport, StageState
from media_house.modules.visual_asset_generation.domain.profile import (
    SelectionSettings,
    VisualAssetConfig,
)
from media_house.modules.visual_asset_generation.domain.selection import (
    Candidate,
    ImageEntry,
    rank,
    tier_of,
    words,
)
from media_house.shared.errors import Err, Ok, Result

#: Words searched one by one (the library's text search is one LIKE per query).
_MAX_WORDS = 8
_PAGE = 200
SIMILARITY_UNAVAILABLE = (
    "no picture-embedding contract exists for still library images (video_intelligence exposes "
    "only its text tower, VisualTextEncoder): matching uses names, file names and tags"
)


@dataclass(frozen=True, slots=True, kw_only=True)
class SelectImagesCommand:
    concept: str
    #: Library groups of the project: their images count as approved project images.
    project_group_ids: tuple[str, ...] = ()
    config: VisualAssetConfig = field(default_factory=VisualAssetConfig)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageSelection:
    concept: str
    candidates: tuple[Candidate, ...]
    stages: tuple[StageReport, ...]

    @property
    def found(self) -> bool:
        return bool(self.candidates)


@dataclass(frozen=True, slots=True, kw_only=True)
class GapVisuals:
    """Existing images for one b-roll coverage gap, or why a new picture is the only option."""

    gap_id: str
    concept: str
    source_start: Rational
    source_end: Rational
    selection: ImageSelection
    #: ``None`` = generation could run; else why it cannot (it still needs a declared request).
    generation_unavailable: str | None


class SelectImages:
    def __init__(
        self,
        library: MediaLibrary,
        broll: BrollCatalog,
        generator: ImageGenerator,
    ) -> None:
        self._library = library
        self._broll = broll
        self._generator = generator

    def execute(self, command: SelectImagesCommand) -> ImageSelection:
        settings = command.config.resolve().selection
        concept_words = words(command.concept)[:_MAX_WORDS]
        seen: dict[str, MediaAssetDto] = {}
        for word in concept_words:
            for query in (_query(text=word), _query(tags=frozenset({word}))):
                for asset in self._all(query):
                    seen.setdefault(asset.id, asset)
        groups = set(command.project_group_ids)
        entries = [_entry(a, groups, settings) for a in seen.values()]
        return ImageSelection(
            concept=command.concept,
            candidates=rank(command.concept, entries, settings),
            stages=(
                StageReport(
                    stage="name_and_tag_match",
                    state=StageState.OK,
                    detail=f"{len(entries)} images carry a concept word",
                ),
                StageReport(
                    stage="picture_text_similarity",
                    state=StageState.NOT_AVAILABLE,
                    detail=SIMILARITY_UNAVAILABLE,
                ),
            ),
        )

    def for_gaps(
        self,
        asset_id: str,
        *,
        project_group_ids: tuple[str, ...] = (),
        config: VisualAssetConfig | None = None,
    ) -> Result[tuple[GapVisuals, ...], MediaError]:
        """Each coverage gap of the stored b-roll analysis of ``asset_id`` (none stored: ``()``)."""
        stored = self._broll.latest(asset_id)
        if isinstance(stored, Err):
            return stored
        if stored.value is None:
            return Ok(())
        unavailable = self._generator.unavailable()
        chosen = config or VisualAssetConfig()
        return Ok(
            tuple(
                GapVisuals(
                    gap_id=gap.gap_id,
                    concept=gap.concept,
                    source_start=gap.start,
                    source_end=gap.end,
                    selection=self.execute(
                        SelectImagesCommand(
                            concept=gap.concept,
                            project_group_ids=project_group_ids,
                            config=chosen,
                        )
                    ),
                    generation_unavailable=unavailable,
                )
                for gap in stored.value[0].gaps
            )
        )

    def _all(self, query: MediaQuery) -> list[MediaAssetDto]:
        found: list[MediaAssetDto] = []
        page = query
        while True:
            result = self._library.search(page)
            found.extend(result.items)
            if not result.has_next:
                return found
            page = _query(text=query.text, tags=query.tags, page=result.page + 1)


def _query(
    *, text: str | None = None, tags: frozenset[str] = frozenset(), page: int = 1
) -> MediaQuery:
    """Original library images only (derived cutouts are found through their source)."""
    return MediaQuery(
        text=text,
        tags=tags,
        media_types=frozenset({MediaType.IMAGE}),
        roles=frozenset({AssetRole.ORIGINAL}),
        page=page,
        page_size=_PAGE,
    )


def _entry(asset: MediaAssetDto, groups: set[str], settings: SelectionSettings) -> ImageEntry:
    return ImageEntry(
        asset_id=asset.id,
        name=asset.display_name,
        filename=asset.original_filename or "",
        tags=asset.tags,
        tier=tier_of(
            asset.tags,
            generated=asset.source_type is SourceType.AI_GENERATED,
            in_project=bool(groups.intersection(asset.group_ids)),
            settings=settings,
        ),
    )
