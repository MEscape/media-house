"""Use cases: import the owner's sound library into the Media Library, and measure each sound.

The manifest is the only source: every file it lists is imported from the owner's folder (never
downloaded), tagged with its kind, categories and source, and its licence and stated facts are
stored in the asset metadata. Identical content is stored once (the library is content-addressed),
so a re-run imports nothing new; a manifest entry whose metadata changed is updated in place.

Measurements are derived JSON documents of each sound (``music_analysis`` / ``sfx_profile``),
cached by the analyser version and the engine versions: an unchanged sound is never measured
twice, a new analyser version measures again.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    SourceType,
)
from media_house.modules.music_and_sound_design.application.ports import (
    ManifestReader,
    SoundMeasurer,
)
from media_house.modules.music_and_sound_design.domain.analysis import MusicAnalysis, SfxProfile
from media_house.modules.music_and_sound_design.domain.errors import (
    InvalidManifest,
    NotASound,
    SoundDesignError,
)
from media_house.modules.music_and_sound_design.domain.library import (
    ManifestEntry,
    alternatives_by_title,
    entry_metadata,
    library_tags,
)
from media_house.modules.music_and_sound_design.domain.serialization import (
    music_analysis_from_json,
    music_analysis_to_json,
    sfx_profile_from_json,
    sfx_profile_to_json,
)
from media_house.modules.music_and_sound_design.domain.values import (
    ANALYZER_VERSION,
    METADATA_KEY,
    MUSIC_ANALYSIS,
    SCHEMA_VERSION,
    SFX_PROFILE,
    LicenceState,
    SoundKind,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type ImportError_ = MediaError | SoundDesignError


@dataclass(frozen=True, slots=True)
class AnalyzeSoundsCommand:
    asset_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SoundAnalysisReport:
    #: Asset ids measured now.
    analysed: tuple[str, ...] = ()
    #: Asset ids whose current measurement was already stored (nothing computed).
    reused: tuple[str, ...] = ()
    #: ``(asset_id, reason)`` of sounds that could not be measured.
    failed: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class ImportSoundLibraryCommand:
    manifest_path: Path
    kinds: frozenset[SoundKind] = frozenset(SoundKind)
    #: Manifest paths to import (empty = every file).
    only: tuple[str, ...] = ()
    #: Measure every imported sound (cached: unchanged sounds are not measured again).
    analyze: bool = True


@dataclass(frozen=True, slots=True)
class ImportedSound:
    path: str
    asset_id: str
    kind: SoundKind
    #: ``False`` when the identical file was already in the library.
    created: bool
    #: ``True`` when stored manifest facts (tags, licence, metadata) were refreshed.
    updated: bool
    licence_state: LicenceState


@dataclass(frozen=True, slots=True)
class ImportSoundLibraryReport:
    sounds: tuple[ImportedSound, ...]
    #: ``(path, reason)`` of files that could not be imported.
    failed: tuple[tuple[str, str], ...] = ()
    #: ``(path, first path)``: the manifest lists the same content twice.
    duplicates: tuple[tuple[str, str], ...] = ()
    #: Files whose content does not match the manifest's sha256 (imported by real content).
    checksum_mismatches: tuple[str, ...] = ()
    #: ``(path, path)`` whose titles differ only by a version suffix.
    alternatives: tuple[tuple[str, str], ...] = ()
    #: Paths whose ``commercial_use`` is not ``true`` (excluded from selection by default).
    not_cleared: tuple[str, ...] = ()
    #: How many files forbid use as model or test data (``ai_training_allowed: false``).
    no_training: int = 0
    analysis: SoundAnalysisReport = field(default_factory=SoundAnalysisReport)


class AnalyzeSounds:
    def __init__(
        self,
        library: MediaLibrary,
        measurer: SoundMeasurer,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._measurer = measurer
        self._documents = DerivedDocuments(library, paths)
        self._clock = clock

    def execute(
        self, command: AnalyzeSoundsCommand, ctx: JobContext
    ) -> Result[SoundAnalysisReport, MediaError]:
        analysed: list[str] = []
        reused: list[str] = []
        failed: list[tuple[str, str]] = []
        total = len(command.asset_ids)
        for i, asset_id in enumerate(command.asset_ids, start=1):
            ctx.raise_if_cancelled()
            ctx.progress.report(i, total, "Measuring sounds")
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            outcome = self.measure(found.value, ctx)
            if isinstance(outcome, Err):
                failed.append((asset_id, outcome.error.user_message))
            elif outcome.value:
                analysed.append(asset_id)
            else:
                reused.append(asset_id)
        return Ok(SoundAnalysisReport(tuple(analysed), tuple(reused), tuple(failed)))

    def measure(
        self, asset: MediaAssetDto, ctx: JobContext
    ) -> Result[bool, MediaError | MediaHouseError]:
        """``Ok(True)`` = measured now, ``Ok(False)`` = the current measurement was stored."""
        meta = asset.metadata.get(METADATA_KEY)
        kind = meta.get("kind") if isinstance(meta, dict) else None
        if kind not in (SoundKind.MUSIC.value, SoundKind.SFX.value):
            return Err(NotASound(asset.id))
        music = kind == SoundKind.MUSIC.value
        try:
            identity = self._measurer.identity
        except MediaHouseError as exc:
            return Err(exc)
        spec = DocumentSpec(
            MUSIC_ANALYSIS if music else SFX_PROFILE,
            ANALYZER_VERSION,
            {"analyzer": ANALYZER_VERSION, "schema": SCHEMA_VERSION, "engines": identity},
        )
        fingerprint = self._documents.fingerprint(asset.id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        parse = music_analysis_from_json if music else sfx_profile_from_json
        if self._documents.load(asset.id, fingerprint.value, parse) is not None:
            return Ok(False)
        path = self._library.local_path(asset.id)
        if isinstance(path, Err):
            return path
        try:
            if music:
                measured = self._measurer.measure_music(path.value, ctx.cancellation)
                document = MusicAnalysis(
                    analysis_id=f"music:{asset.checksum[:16]}:v{ANALYZER_VERSION}",
                    asset_id=asset.id,
                    checksum=asset.checksum,
                    created_at=self._clock.now(),
                    analyzer_version=ANALYZER_VERSION,
                    measurement=measured,
                    schema_version=SCHEMA_VERSION,
                )
                text = music_analysis_to_json(document)
                tempo = measured.tempo
                summary: dict[str, JsonValue] = {
                    "bpm": None if tempo is None else tempo.bpm,
                    "tempo_confidence": None if tempo is None else tempo.confidence,
                    "loopable": measured.loop is not None,
                    "integrated_lufs": measured.loudness.integrated_lufs,
                    "sections": len(measured.sections),
                }
            else:
                effect = self._measurer.measure_sfx(path.value, ctx.cancellation)
                text = sfx_profile_to_json(
                    SfxProfile(
                        analysis_id=f"sfx:{asset.checksum[:16]}:v{ANALYZER_VERSION}",
                        asset_id=asset.id,
                        checksum=asset.checksum,
                        created_at=self._clock.now(),
                        analyzer_version=ANALYZER_VERSION,
                        measurement=effect,
                        schema_version=SCHEMA_VERSION,
                    )
                )
                summary = {
                    "attack_seconds": effect.attack_seconds,
                    "tail_seconds": effect.tail_seconds,
                    "rms_max_dbfs": effect.rms_max_dbfs,
                }
        except MediaHouseError as exc:
            _log.warning("Sound could not be measured", asset_id=asset.id, reason=str(exc))
            return Err(exc)
        stored = self._documents.store(
            asset,
            text,
            filename=f"{spec.operation}.json",
            spec=spec,
            display_name=f"{asset.display_name} ({spec.operation})",
            metadata={"kind": kind, "analyzer_version": ANALYZER_VERSION, **summary},
        )
        if isinstance(stored, Err):
            return stored
        return Ok(True)


class ImportSoundLibrary:
    def __init__(
        self,
        library: MediaLibrary,
        reader: ManifestReader,
        analyzer: AnalyzeSounds,
    ) -> None:
        self._library = library
        self._reader = reader
        self._analyzer = analyzer

    def execute(
        self, command: ImportSoundLibraryCommand, ctx: JobContext
    ) -> Result[ImportSoundLibraryReport, ImportError_]:
        try:
            manifest = self._reader.read(command.manifest_path)
        except InvalidManifest as exc:
            return Err(exc)
        root = command.manifest_path.parent
        wanted = set(command.only)
        entries = [
            e
            for e in manifest.entries
            if e.kind in command.kinds and (not wanted or e.path in wanted)
        ]
        sounds: list[ImportedSound] = []
        failed: list[tuple[str, str]] = []
        duplicates: list[tuple[str, str]] = []
        mismatches: list[str] = []
        first_by_sha: dict[str, str] = {}
        for i, entry in enumerate(entries, start=1):
            ctx.raise_if_cancelled()
            ctx.progress.report(i, len(entries), "Importing the sound library")
            if entry.sha256 in first_by_sha:
                duplicates.append((entry.path, first_by_sha[entry.sha256]))
                continue
            first_by_sha[entry.sha256] = entry.path
            imported = self._import(root, entry)
            if isinstance(imported, Err):
                failed.append((entry.path, imported.error.user_message))
                continue
            sound, mismatch = imported.value
            sounds.append(sound)
            if mismatch:
                mismatches.append(entry.path)
        analysis = SoundAnalysisReport()
        if command.analyze and sounds:
            measured = self._analyzer.execute(
                AnalyzeSoundsCommand(tuple(dict.fromkeys(s.asset_id for s in sounds))), ctx
            )
            if isinstance(measured, Err):
                return measured
            analysis = measured.value
        report = ImportSoundLibraryReport(
            sounds=tuple(sounds),
            failed=tuple(failed),
            duplicates=tuple(duplicates),
            checksum_mismatches=tuple(mismatches),
            alternatives=tuple(alternatives_by_title(entries)),
            not_cleared=tuple(
                e.path for e in entries if e.licence.state is not LicenceState.CLEARED
            ),
            no_training=sum(1 for e in entries if e.licence.ai_training_allowed is False),
            analysis=analysis,
        )
        _log.info(
            "Sound library imported",
            files=len(entries),
            created=sum(1 for s in sounds if s.created),
            updated=sum(1 for s in sounds if s.updated),
            failed=len(failed),
            analysed=len(analysis.analysed),
            reused=len(analysis.reused),
        )
        return Ok(report)

    def _import(
        self, root: Path, entry: ManifestEntry
    ) -> Result[tuple[ImportedSound, bool], MediaError | SoundDesignError]:
        metadata = _json({METADATA_KEY: entry_metadata(entry, entry.path)})
        tags = library_tags(entry)
        existing = self._library.find_by_checksum(entry.sha256)
        created = False
        if existing is None:
            path = root / entry.path
            if not path.is_file():
                return Err(InvalidManifest(f"{entry.path} is listed but missing on disk"))
            result = self._library.import_file(
                path,
                display_name=entry.title,
                tags=tags,
                metadata=metadata,
                source_type=SourceType.WEB_DOWNLOAD,
                source_url=entry.licence.source_url or None,
                source_provider=entry.licence.source or None,
            )
            if isinstance(result, Err):
                return result
            asset, created = result.value.asset, result.value.created
        else:
            asset = existing
        updated = False
        if not created and asset.metadata.get(METADATA_KEY) != metadata[METADATA_KEY]:
            patched = self._library.update_metadata(asset.id, metadata)
            if isinstance(patched, Err):
                return patched
            asset, updated = patched.value, True
        missing_tags = [t for t in tags if t not in asset.tags]
        if missing_tags:
            tagged = self._library.add_tags(asset.id, missing_tags)
            if isinstance(tagged, Err):
                return tagged
            updated = True
        return Ok(
            (
                ImportedSound(
                    path=entry.path,
                    asset_id=asset.id,
                    kind=entry.kind,
                    created=created,
                    updated=updated,
                    licence_state=entry.licence.state,
                ),
                asset.checksum != entry.sha256,
            )
        )


def _json(value: Mapping[str, object]) -> dict[str, JsonValue]:
    return cast("dict[str, JsonValue]", json.loads(json.dumps(value)))
