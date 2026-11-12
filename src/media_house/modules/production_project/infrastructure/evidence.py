"""Evidence about a file from what the library and the analysis modules already hold.

Nothing is recomputed here: the asset is found by its checksum (or imported once), the probe data
comes from the stored inspection, speech from the stored audio timeline, faces and on-screen text
from the stored video analysis. A missing analysis is simply absent evidence; the classifier then
rests on name, folder and duration and says so through a lower confidence.

Vision tags such as ``night_sky`` or ``product`` need a tagger that is not part of the stored
analyses: pass ``tagger`` to add them, and every rule that reads them starts to fire.
"""

import re
from collections.abc import Callable
from pathlib import Path

from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary, MediaType
from media_house.modules.production_project.application.ports import ScannedFile
from media_house.modules.production_project.domain.classify import FileEvidence
from media_house.modules.production_project.domain.conform import TranscriptLine
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err
from media_house.shared.logging import get_logger

log = get_logger(__name__)
_URL = re.compile(r"https?://|www\.|\.de/|\.com/")
_DOCUMENT = frozenset({".pdf", ".docx", ".doc", ".txt", ".md"})
_MAX_FACES = 12
type Tagger = Callable[[Path], tuple[str, ...]]


class LibraryEvidenceSource:
    """Structurally implements ``EvidenceSource``."""

    def __init__(
        self,
        library: MediaLibrary,
        inspections: InspectionCatalog,
        audio: AudioCatalog,
        video: VideoCatalog,
        tagger: Tagger | None = None,
    ) -> None:
        self._library = library
        self._inspections = inspections
        self._audio = audio
        self._video = video
        self._tagger = tagger
        self._ids: dict[Path, str] = {}

    def asset_id(self, file: ScannedFile) -> str:
        return self._ids.get(file.path, "")

    def evidence(self, file: ScannedFile, sha256: str, ctx: JobContext) -> FileEvidence:
        suffix = file.path.suffix.casefold()
        if suffix in _DOCUMENT:
            return FileEvidence(file.name, "document", sha256, file.size_bytes, file.folder)
        asset_id, kind, width, height, duration = self._asset(file, sha256)
        evidence = FileEvidence(
            file.name,
            kind,
            sha256,
            file.size_bytes,
            file.folder,
            width=width,
            height=height,
            duration=duration,
            is_vector=suffix == ".svg",
        )
        if not asset_id:
            return evidence
        ctx.raise_if_cancelled()
        evidence = self._probe(asset_id, evidence)
        evidence = self._speech(asset_id, evidence)
        evidence = self._vision(asset_id, evidence)
        if self._tagger is not None:
            evidence = _with_tags(evidence, self._tagger(file.path))
        return evidence

    def transcript(self, file: ScannedFile) -> tuple[TranscriptLine, ...]:
        asset_id = self._ids.get(file.path, "")
        if not asset_id:
            return ()
        found = self._audio.latest(asset_id)
        if isinstance(found, Err) or found.value is None:
            return ()
        timeline, _asset = found.value
        return tuple(
            TranscriptLine(file.name, s.start, s.end, s.text.strip())
            for s in timeline.transcript.segments
            if s.text.strip()
        )

    # --- pieces ---------------------------------------------------------------------------------
    def _asset(self, file: ScannedFile, sha256: str) -> tuple[str, str, int, int, float]:
        known = self._library.find_by_checksum(sha256)
        if known is None:
            imported = self._library.import_file(file.path)
            if isinstance(imported, Err):
                log.info("Library refused a file", name=file.name)
                return "", _kind_of(file.path), 0, 0, 0.0
            known = imported.value.asset
        self._ids[file.path] = known.id
        kind = known.media_type.value if known.media_type is not MediaType.OTHER else "other"
        return known.id, kind, known.width or 0, known.height or 0, known.duration_seconds or 0.0

    def _probe(self, asset_id: str, evidence: FileEvidence) -> FileEvidence:
        stored = self._inspections.find(asset_id)
        if stored is None:
            return evidence
        video = stored.inspection.primary_video
        fps = video.frame_rate.value.value if video and video.frame_rate.value else 0.0
        return _replace(
            evidence,
            fps=float(fps),
            has_audio=bool(stored.inspection.audio_streams),
            has_alpha=bool(video.has_alpha) if video else evidence.has_alpha,
        )

    def _speech(self, asset_id: str, evidence: FileEvidence) -> FileEvidence:
        found = self._audio.latest(asset_id)
        if isinstance(found, Err) or found.value is None:
            return evidence
        timeline, _asset = found.value
        words = len(timeline.transcript.words)
        text = " ".join(s.text.strip() for s in timeline.transcript.segments[:3])
        silence = max((p.duration for p in timeline.pauses), default=0.0)
        return _replace(
            evidence,
            speech_words=words,
            transcript_excerpt=text[:240],
            longest_silence=float(silence),
        )

    def _vision(self, asset_id: str, evidence: FileEvidence) -> FileEvidence:
        found = self._video.latest(asset_id)
        if isinstance(found, Err) or found.value is None:
            return evidence
        analysis, _asset = found.value
        faces = len({t.track_id for t in analysis.tracks if t.kind.value == "face"})
        text = " ".join(o.text for o in analysis.overlays if o.text).strip()
        tags = ("browser_ui",) if _URL.search(text) else ()
        return _with_tags(
            _replace(evidence, faces=min(faces, _MAX_FACES), ocr_text=text[:1000]), tags
        )


def _kind_of(path: Path) -> str:
    suffix = path.suffix.casefold()
    if suffix in {".png", ".jpg", ".jpeg", ".webp", ".svg", ".tif", ".tiff", ".bmp", ".gif"}:
        return "image"
    if suffix in {".mp4", ".mov", ".mkv", ".mxf", ".avi", ".m4v"}:
        return "video"
    return "other"


def _replace(e: FileEvidence, **changes: object) -> FileEvidence:
    from dataclasses import replace

    return replace(e, **changes)  # type: ignore[arg-type]


def _with_tags(e: FileEvidence, tags: tuple[str, ...]) -> FileEvidence:
    return _replace(e, tags=tuple(dict.fromkeys((*e.tags, *tags))))
