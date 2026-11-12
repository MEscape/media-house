"""The Thumbnail page's facade: library pictures, the reaction collection, design and export.

Plain rows for the page (ids, names, local files to preview); every rule stays in thumbnail
design. Expected failures come back as ``Err(user message)``.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from media_house.modules.media_library.application.contracts import (
    AssetRole,
    MediaAssetDto,
    MediaLibrary,
    MediaQuery,
    MediaType,
)
from media_house.modules.thumbnail_design.application.contracts import (
    DesignThumbnailsCommand,
    DeviceCard,
    Headline,
    ReactionCollection,
    ReactionSuggester,
    StoryBadge,
    ThumbnailBrief,
    ThumbnailDesigner,
    ThumbnailExporter,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, InvariantViolation, Ok, Result


@dataclass(frozen=True, slots=True)
class AssetRow:
    asset_id: str
    name: str
    #: A local file to preview (pictures only); ``None`` when it is not available.
    path: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class StudioCatalogue:
    videos: tuple[AssetRow, ...]
    pictures: tuple[AssetRow, ...]
    reactions: tuple[AssetRow, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ThumbnailForm:
    """What the page collects. Empty strings mean "not used"."""

    video_id: str = ""
    line1: str = ""
    line2: str = ""
    accent_second: bool = True
    alt_line1: str = ""
    alt_line2: str = ""
    reaction_id: str = ""
    flip_reaction: bool = False
    product_ids: tuple[str, ...] = ()
    logo_id: str = ""
    device: bool = False
    device_product_id: str = ""
    device_name: str = ""
    device_price: str = ""
    device_button: str = "In den Warenkorb"
    device_screen_id: str = ""
    badge: str = ""
    plate_id: str = ""


@dataclass(frozen=True, slots=True)
class VariantRow:
    name: str
    label: str
    asset_id: str | None
    path: str | None
    issues: tuple[str, ...] = field(default_factory=tuple)
    reused: bool = False


def _headline(first: str, second: str, accent_second: bool) -> Headline | None:
    lines = tuple(line for line in (first, second) if line.strip())
    if not lines:
        return None
    return Headline(lines, accent_line=(len(lines) - 1) if accent_second else 0)


def brief_of(form: ThumbnailForm) -> ThumbnailBrief:
    """The brief of a filled form; raises ``InvariantViolation`` with the reason to show."""
    headline = _headline(form.line1, form.line2, form.accent_second)
    if headline is None:
        raise InvariantViolation(
            "No headline", user_message="Write the headline: one or two short lines."
        )
    device = None
    if form.device:
        device = DeviceCard(
            product_asset_id=form.device_product_id,
            product_name=form.device_name.strip(),
            price=form.device_price.strip(),
            button=form.device_button.strip() or "In den Warenkorb",
            screen_asset_id=form.device_screen_id or None,
        )
    return ThumbnailBrief(
        headline=headline,
        alternate_headline=_headline(form.alt_line1, form.alt_line2, form.accent_second),
        reaction_asset_id=form.reaction_id,
        flip_reaction=form.flip_reaction,
        products=form.product_ids,
        logo_asset_id=form.logo_id or None,
        device=device,
        badge=StoryBadge(form.badge) if form.badge.strip() else None,
        plate_asset_id=form.plate_id or None,
    )


class ThumbnailStudio:
    def __init__(
        self,
        *,
        library: MediaLibrary,
        designer: ThumbnailDesigner,
        suggester: ReactionSuggester,
        reactions: ReactionCollection,
        exporter: ThumbnailExporter,
    ) -> None:
        self._library = library
        self._designer = designer
        self._suggester = suggester
        self._reactions = reactions
        self._exporter = exporter

    def catalogue(self) -> StudioCatalogue:
        videos = self._library.search(
            MediaQuery(
                media_types=frozenset({MediaType.VIDEO}),
                roles=frozenset({AssetRole.ORIGINAL}),
                page_size=MediaQuery.MAX_PAGE_SIZE,
            )
        ).items
        pictures = [
            a
            for a in self._library.search(
                MediaQuery(
                    media_types=frozenset({MediaType.IMAGE}), page_size=MediaQuery.MAX_PAGE_SIZE
                )
            ).items
            # derived helpers (cut-outs, finished thumbnails) are not inputs to pick
            if a.role is AssetRole.ORIGINAL or a.metadata.get("processing_type") == "frame_grab"
        ]
        return StudioCatalogue(
            videos=tuple(self._row(v, preview=False) for v in videos),
            pictures=tuple(self._row(p) for p in pictures),
            reactions=tuple(self._row(r) for r in self._reactions.list()),
        )

    def _row(self, asset: MediaAssetDto, *, preview: bool = True, note: str = "") -> AssetRow:
        path = self._library.local_path(asset.id) if preview else None
        return AssetRow(
            asset.id,
            asset.display_name,
            str(path.value) if isinstance(path, Ok) else None,
            note,
        )

    def add_reaction(self, path: Path) -> Result[AssetRow, str]:
        added = self._reactions.add(path)
        if isinstance(added, Err):
            return Err(added.error.user_message)
        return Ok(self._row(added.value))

    def import_picture(self, path: Path) -> Result[AssetRow, str]:
        imported = self._library.import_file(path)
        if isinstance(imported, Err):
            return Err(imported.error.user_message)
        return Ok(self._row(imported.value.asset))

    def suggest(
        self, video_ids: Sequence[str], ctx: JobContext
    ) -> Result[tuple[AssetRow, ...], str]:
        found = self._suggester.execute(video_ids, ctx)
        if isinstance(found, Err):
            return Err(found.error.user_message)
        rows = []
        for c in found.value:
            marked = self._reactions.mark(c.picture.id)  # a pick from the footage joins the set
            picture = marked.value if isinstance(marked, Ok) else c.picture
            rows.append(self._row(picture, note=", ".join(c.moment.reasons)))
        return Ok(tuple(rows))

    def design(self, form: ThumbnailForm, ctx: JobContext) -> Result[tuple[VariantRow, ...], str]:
        try:
            brief = brief_of(form)
        except InvariantViolation as exc:
            return Err(exc.user_message)
        designed = self._designer.execute(
            DesignThumbnailsCommand(brief=brief, anchor_asset_id=form.video_id or None), ctx
        )
        if isinstance(designed, Err):
            return Err(designed.error.user_message)
        rows = []
        for v in designed.value.variants:
            path = None
            if v.asset is not None:
                local = self._library.local_path(v.asset.id)
                path = str(local.value) if isinstance(local, Ok) else None
            rows.append(
                VariantRow(
                    v.name,
                    v.label,
                    v.asset.id if v.asset else None,
                    path,
                    tuple(i.message for i in v.issues) + designed.value.warnings,
                    reused=not v.created,
                )
            )
        return Ok(tuple(rows))

    def export(self, asset_id: str, destination: Path) -> Result[Path, str]:
        written = self._exporter.execute(asset_id, destination)
        if isinstance(written, Err):
            return Err(written.error.user_message)
        return written
