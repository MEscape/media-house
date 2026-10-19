"""Use case: stored audio, video (and content) analyses -> one ``AlignmentDocument``.

Nothing is decoded, transcribed or detected here. The result is one derived Media Library
document keyed by the identity of every input actually used, the profile, the clock inputs and
the versions, so any change in any of them misses and nothing else does. A stage that cannot run
is reported in ``stages`` and the others still produce their records.
"""

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import AudioIntelligenceTimeline
from media_house.modules.audio_visual_alignment.application.resolver import (
    Request,
    Resolved,
    UpstreamResolver,
)
from media_house.modules.audio_visual_alignment.domain.config import (
    DEFAULT_PROFILE,
    AlignmentProfile,
    get_profile,
)
from media_house.modules.audio_visual_alignment.domain.engine import align
from media_house.modules.audio_visual_alignment.domain.errors import (
    AlignmentError,
    InvalidAlignmentConfig,
)
from media_house.modules.audio_visual_alignment.domain.inputs import Inputs, ShotFacts
from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument
from media_house.modules.audio_visual_alignment.domain.serialization import (
    alignment_from_json,
    alignment_to_json,
)
from media_house.modules.audio_visual_alignment.domain.validation import validate_document
from media_house.modules.audio_visual_alignment.domain.values import (
    ALIGNMENT_VERSION,
    RESULT_OPERATION,
    SCHEMA_VERSION,
    SCORING_VERSION,
    VOCABULARY_VERSION,
    Acquire,
)
from media_house.modules.content_intelligence.application.contracts import ContentAnalysis
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, MediaHouseError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type AlignError = MediaError | AlignmentError | MediaHouseError


class TextEmbedder(Protocol):
    """An optional engine that turns short texts into vectors (a multilingual sentence model).

    ``unavailable`` says why it cannot run now, else ``None``. Nothing is ever downloaded.
    """

    def identity(self) -> str: ...

    def unavailable(self) -> str | None: ...

    def embed(self, texts: list[str], cancellation: CancellationToken) -> list[list[float]]: ...


@dataclass(frozen=True, slots=True)
class AlignCommand:
    video_asset_id: str
    #: The asset the audio comes from; ``None`` means the same asset as the video.
    audio_asset_id: str | None = None
    profile: str | AlignmentProfile = DEFAULT_PROFILE
    #: ``STORED`` reads stored analyses and fails when one is missing; ``REQUEST`` may compute them.
    acquire: Acquire = Acquire.STORED
    #: Which stored video profile to read (and to compute under ``REQUEST``); ``None`` = newest.
    video_profile: str | None = None
    #: Shift of a separately recorded audio onto the video's clock, in seconds (common = audio + s).
    audio_shift_seconds: float | None = None
    use_content: bool = True
    #: Results the caller already holds (used as given, nothing is looked up for them).
    audio_timeline: AudioIntelligenceTimeline | None = None
    video_analysis: VideoAnalysis | None = None
    content_analysis: ContentAnalysis | None = None


@dataclass(frozen=True, slots=True)
class AlignmentResult:
    document: AlignmentDocument
    #: The stored document (a derived asset of the video).
    asset: MediaAssetDto
    #: ``False`` when the identical result already existed and nothing was computed.
    created: bool


class AlignAudioVisual:
    def __init__(
        self,
        library: MediaLibrary,
        resolver: UpstreamResolver,
        paths: AppPaths,
        clock: Clock,
        embedder: TextEmbedder | None = None,
    ) -> None:
        self._library = library
        self._resolver = resolver
        self._clock = clock
        self._embedder = embedder
        self._documents = DerivedDocuments(library, paths)

    def execute(
        self, command: AlignCommand, ctx: JobContext
    ) -> Result[AlignmentResult, AlignError]:
        try:
            profile = (
                get_profile(command.profile)
                if isinstance(command.profile, str)
                else command.profile
            )
        except AlignmentError as exc:
            return Err(exc)
        if command.audio_shift_seconds is not None and not math.isfinite(
            command.audio_shift_seconds
        ):
            return Err(InvalidAlignmentConfig("audio_shift_seconds must be a finite number"))
        video_id = command.video_asset_id
        audio_id = command.audio_asset_id or video_id
        for asset_id in {video_id, audio_id}:
            found = self._library.get(asset_id)
            if isinstance(found, Err):
                return found
            expected = MediaType.VIDEO if asset_id == video_id else None
            if expected is not None and found.value.media_type is not expected:
                return Err(
                    ValidationError(
                        f"Asset {asset_id} is {found.value.media_type.value}, not video",
                        field="video_asset_id",
                        user_message="Alignment needs a video asset.",
                    )
                )

        ctx.progress.report(1, _STEPS, "Reading the stored analyses")
        resolution = self._resolver.resolve(
            Request(
                video_asset_id=video_id,
                audio_asset_id=audio_id,
                acquire=command.acquire,
                video_profile=command.video_profile,
                use_content=command.use_content and profile.runs("text"),
                audio_timeline=command.audio_timeline,
                video_analysis=command.video_analysis,
                content_analysis=command.content_analysis,
            ),
            ctx,
        )
        if isinstance(resolution, Err):
            return resolution
        resolved = resolution.value

        embedder_id = self._embedder_identity(profile, resolved)
        key = _cache_key(profile, resolved, command.audio_shift_seconds, embedder_id)
        spec = DocumentSpec(RESULT_OPERATION, ALIGNMENT_VERSION, key)
        fingerprint = self._documents.fingerprint(video_id, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(video_id, fingerprint.value, alignment_from_json)
        if cached is not None:
            _log.info("Alignment reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(AlignmentResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(2, _STEPS, "Comparing the embeddings" if embedder_id else "Aligning")
        scored, warnings = self._similarities(profile, resolved, embedder_id, ctx)
        similarities = scored if scored is not None else {}
        if scored is None:
            # The engine failed, so what follows is the words-only result: it is keyed and
            # labelled as that, and never stored under the embedding model's identity (which
            # would serve it as the embedding result after the engine recovers).
            embedder_id = None
            key = _cache_key(profile, resolved, command.audio_shift_seconds, None)
            spec = DocumentSpec(RESULT_OPERATION, ALIGNMENT_VERSION, key)
        document = align(
            Inputs(
                audio=resolved.audio,
                video=resolved.video,
                content=resolved.content,
                clock=resolved.clock,
                audio_shift_seconds=command.audio_shift_seconds,
                similarities=similarities,
            ),
            profile,
            alignment_id="av_" + _digest(key)[:16],
            created_at=self._clock.now(),
            records=resolved.records,
            engines={"text_embedder": embedder_id} if embedder_id else {},
            warnings=(*resolved.warnings, *warnings),
        )
        problems = validate_document(document)
        if problems:
            _log.error("Alignment rejected its own result", problems=problems[:5])
            return Err(AlignmentError("; ".join(problems[:3])))

        ctx.progress.report(_STEPS - 1, _STEPS, "Saving")
        source = self._library.get(video_id)
        if isinstance(source, Err):
            return source
        stored = self._documents.store(
            source.value,
            alignment_to_json(document),
            filename="alignment.json",
            spec=spec,
            display_name=f"{source.value.display_name} (audio-visual alignment)",
            metadata={
                "audio_asset_id": audio_id,
                "profile": profile.name,
                "speech_sources": len(document.speech_sources),
                "text_matches": len(document.text_matches),
                "sound_actions": len(document.sound_actions),
                "flags": len(document.flags),
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Alignment created",
            asset_id=stored.value.id,
            speech_sources=len(document.speech_sources),
            text_matches=len(document.text_matches),
            flags=len(document.flags),
        )
        return Ok(AlignmentResult(document, stored.value, created=True))

    # --- optional embeddings --------------------------------------------------------------------
    def _embedder_identity(self, profile: AlignmentProfile, resolved: Resolved) -> str | None:
        if self._embedder is None or not profile.runs("text") or resolved.content is None:
            return None
        why = self._embedder.unavailable()
        if why is not None:
            _log.info("Embedding engine not available, matching by words only", reason=why)
            return None
        return self._embedder.identity()

    def _similarities(
        self,
        profile: AlignmentProfile,
        resolved: Resolved,
        embedder_id: str | None,
        ctx: JobContext,
    ) -> tuple[dict[tuple[str, str], float] | None, tuple[str, ...]]:
        """``None`` means the engine FAILED; an empty dict means no comparison applies."""
        if embedder_id is None or self._embedder is None or resolved.content is None:
            return {}, ()
        shots = [(s, _visual_text(s)) for s in resolved.video.shots]
        shots = [(s, t) for s, t in shots if t]
        passages = [p for p in resolved.content.passages if p.text.strip()]
        if len(shots) < profile.semantic_min_shots or not passages:
            return {}, ()
        try:
            vectors = self._embedder.embed(
                [*(p.text for p in passages), *(t for _, t in shots)], ctx.cancellation
            )
        except MediaHouseError as exc:
            _log.warning("Embedding failed, matching by words only", reason=str(exc))
            return None, (
                f"the embedding engine failed ({exc.user_message}); matched by words only",
            )
        passage_vectors, shot_vectors = vectors[: len(passages)], vectors[len(passages) :]
        out: dict[tuple[str, str], float] = {}
        for passage, p_vec in zip(passages, passage_vectors, strict=True):
            for (shot, _), s_vec in zip(shots, shot_vectors, strict=True):
                out[(passage.passage_id, shot.shot_id)] = _cosine(p_vec, s_vec)
        return out, ()


_STEPS = 4


def _visual_text(shot: ShotFacts) -> str:
    parts = [*shot.screen_text, *shot.objects]
    if shot.description:
        parts.append(shot.description)
    return ". ".join(p for p in parts if p).strip()


def _cosine(a: Sequence[float], b: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return round(dot / norm, 5) if norm else 0.0


def _digest(key: Mapping[str, JsonValue]) -> str:
    return hashlib.sha256(json.dumps(key, sort_keys=True).encode()).hexdigest()


def _cache_key(
    profile: AlignmentProfile,
    resolved: Resolved,
    audio_shift: float | None,
    embedder_id: str | None,
) -> Mapping[str, JsonValue]:
    """Everything that can change the result and nothing that cannot.

    Not in it: how an input was obtained, the time, the job, or the device. In it: the content
    identity of each upstream document used (not its library id), the clock inputs, the profile,
    the embedding model, and every version.
    """
    inputs: dict[str, JsonValue] = dict(resolved.identities)
    inputs["video_asset"] = resolved.video.asset_id
    inputs["audio_asset"] = resolved.audio.asset_id
    return {
        "inputs": inputs,
        "audio_shift": audio_shift,
        "profile": dict(profile.fingerprint()),  # type: ignore[arg-type]
        "embedder": embedder_id,
        "versions": {
            "alignment": ALIGNMENT_VERSION,
            "scoring": SCORING_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
        },
    }
