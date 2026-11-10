"""The owner's sound library as rows, read through ``music_and_sound_design``'s contracts.

Whether the planner may select a sound is decided by that module's own default
``LicencePolicy`` (only ``commercial_use: true``); nothing here re-implements the rule.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from media_house.modules.music_and_sound_design.application.contracts import (
    ImportSoundLibraryCommand,
    ImportSoundLibraryReport,
    LicencePolicy,
    LicenceState,
    SoundAsset,
    SoundLibraryCatalog,
    SoundLibraryImporter,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result


class CommercialUse(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


_COMMERCIAL = {
    LicenceState.CLEARED: CommercialUse.YES,
    LicenceState.NOT_COMMERCIAL: CommercialUse.NO,
    LicenceState.UNCONFIRMED: CommercialUse.UNKNOWN,
}


@dataclass(frozen=True, slots=True)
class SoundRow:
    asset_id: str
    kind: str
    title: str
    duration_seconds: float
    commercial_use: CommercialUse
    #: The planner may select it under its default licence policy.
    selectable: bool
    attribution_required: bool
    attribution_text: str
    licence_name: str
    source: str
    author: str
    restrictions: str
    measured: bool
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SoundFilter:
    kind: str = ""  # "" = all
    commercial_use: CommercialUse | None = None
    selectable_only: bool = False
    text: str = ""


@dataclass(frozen=True, slots=True)
class ImportSummary:
    imported: int
    created: int
    failed: tuple[tuple[str, str], ...]
    not_cleared: tuple[str, ...]
    duplicates: int
    checksum_mismatches: int


def to_row(sound: SoundAsset, policy: LicencePolicy) -> SoundRow:
    licence = sound.licence
    return SoundRow(
        asset_id=sound.asset_id,
        kind=sound.kind.value,
        title=sound.title,
        duration_seconds=sound.duration_seconds,
        commercial_use=_COMMERCIAL[licence.state],
        selectable=policy.allows(licence),
        attribution_required=licence.attribution_required,
        attribution_text=licence.attribution_text or "",
        licence_name=licence.name,
        source=licence.source,
        author=licence.author,
        restrictions=licence.restrictions or "",
        measured=sound.measured,
        tags=sound.tags,
    )


def apply_filter(rows: Sequence[SoundRow], spec: SoundFilter) -> list[SoundRow]:
    needle = spec.text.strip().casefold()

    def keep(row: SoundRow) -> bool:
        if spec.kind and row.kind != spec.kind:
            return False
        if spec.commercial_use is not None and row.commercial_use is not spec.commercial_use:
            return False
        if spec.selectable_only and not row.selectable:
            return False
        if needle:
            haystack = " ".join((row.title, row.author, row.source, *row.tags)).casefold()
            return needle in haystack
        return True

    return [row for row in rows if keep(row)]


class SoundLibraryService:
    def __init__(self, catalog: SoundLibraryCatalog, importer: SoundLibraryImporter) -> None:
        self._catalog = catalog
        self._importer = importer
        self._policy = LicencePolicy()

    def rows(self) -> list[SoundRow]:
        """Every imported sound. Blocking (library reads): run it as a job."""
        rows = [to_row(sound, self._policy) for sound in self._catalog.sounds()]
        return sorted(rows, key=lambda r: (r.kind, r.title.casefold()))

    def import_manifest(self, manifest: Path, ctx: JobContext) -> Result[ImportSummary, str]:
        """Import (and measure) the sounds a manifest lists. Blocking: run it as a job."""
        result = self._importer.execute(ImportSoundLibraryCommand(manifest), ctx)
        if isinstance(result, Err):
            return Err(result.error.user_message)
        return Ok(_summary(result.value))


def _summary(report: ImportSoundLibraryReport) -> ImportSummary:
    return ImportSummary(
        imported=len(report.sounds),
        created=sum(1 for s in report.sounds if s.created),
        failed=report.failed,
        not_cleared=report.not_cleared,
        duplicates=len(report.duplicates),
        checksum_mismatches=len(report.checksum_mismatches),
    )
