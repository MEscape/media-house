"""Looking at the preview render: the measurements the render checks work on.

The measuring itself belongs to the shared ``MediaAnalyzer`` port (and the stream facts to the
inspection of the file); this only decides what to ask and keeps a failed measurement from
costing the others: a measurement that could not be made is recorded as skipped, with the reason.
"""

from collections.abc import Callable
from fractions import Fraction
from pathlib import Path

from media_house.core.application.ports import FrameSample, MediaAnalyzer, TimeSpan
from media_house.modules.media_inspection.application.contracts import (
    Depth,
    InspectionConfig,
    InspectMediaCommand,
    MediaInspection,
    MediaInspector,
)
from media_house.modules.quality_control.domain.facts import TimelineFacts
from media_house.modules.quality_control.domain.measurements import (
    ClipLoudness,
    DecodeFacts,
    FrameStats,
    LoudnessFacts,
    RenderMeasurements,
    Span,
    StreamFacts,
)
from media_house.modules.quality_control.domain.profile import QualityProfile
from media_house.modules.quality_control.domain.sampling import plan_samples
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, ExternalSystemError, OperationCancelledError
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_MIN_DIALOGUE_SECONDS = Fraction(3, 2)
_END_WINDOW = Fraction(1, 4)


def _exact(value: float | None) -> Fraction | None:
    return None if value is None else Fraction(f"{value:.6f}")


def _spans(found: tuple[TimeSpan, ...]) -> tuple[Span, ...]:
    return tuple(Span(s.start, s.end) for s in found)


class PreviewMeasurer:
    def __init__(self, analyzer: MediaAnalyzer, inspector: MediaInspector) -> None:
        self._analyzer = analyzer
        self._inspector = inspector

    def identity(self) -> str:
        return self._analyzer.capabilities().identity

    def measure(
        self,
        asset_id: str,
        path: Path,
        timeline: TimelineFacts,
        profile: QualityProfile,
        work_dir: Path,
        ctx: JobContext,
    ) -> tuple[RenderMeasurements, dict[Fraction, Path]]:
        skipped: list[tuple[str, str]] = []
        cancel = ctx.cancellation

        def attempt[T](name: str, step: Callable[[], T]) -> T | None:
            ctx.raise_if_cancelled()
            try:
                return step()
            except OperationCancelledError:
                raise
            except ExternalSystemError as exc:
                skipped.append((name, exc.user_message))
                _log.warning("Preview measurement skipped", measurement=name, reason=str(exc))
                return None

        streams = attempt("streams", lambda: self._streams(asset_id, ctx))
        decode = attempt("decode", lambda: self._decode(path, cancel))
        black = attempt(
            "black frames",
            lambda: _spans(
                self._analyzer.black_spans(
                    path,
                    min_seconds=profile.black_min_seconds,
                    picture_threshold=profile.black_picture_threshold,
                    pixel_threshold=profile.black_pixel_threshold,
                    cancellation=cancel,
                )
            ),
        )
        frozen = attempt(
            "frozen frames",
            lambda: _spans(
                self._analyzer.frozen_spans(
                    path,
                    min_seconds=profile.freeze_min_seconds,
                    noise_db=profile.freeze_noise_db,
                    cancellation=cancel,
                )
            ),
        )
        silent = attempt(
            "silence",
            lambda: _spans(
                self._analyzer.silent_spans(
                    path,
                    min_seconds=profile.silence_min_seconds,
                    noise_db=profile.silence_noise_db,
                    cancellation=cancel,
                )
            ),
        )
        loudness = attempt("loudness", lambda: self._loudness(path, cancel))
        end_peak = attempt("end level", lambda: self._end_peak(path, streams, cancel))
        dialogue = attempt(
            "dialogue levels", lambda: self._dialogue(path, timeline, profile, cancel)
        )
        frames = attempt("frames", lambda: self._frames(path, timeline, profile, work_dir, ctx))
        stats, kept = frames if frames is not None else ((), {})
        return (
            RenderMeasurements(
                asset_id=asset_id,
                analyzer=self.identity(),
                streams=streams,
                decode=decode,
                black=black,
                frozen=frozen,
                silent=silent,
                loudness=loudness,
                end_peak_dbfs=end_peak,
                dialogue_levels=dialogue or (),
                frames=stats,
                skipped=tuple(skipped),
            ),
            kept,
        )

    # --- the measurements -----------------------------------------------------------------------
    def _streams(self, asset_id: str, ctx: JobContext) -> StreamFacts:
        found = self._inspector.execute(
            InspectMediaCommand(asset_id, InspectionConfig(depth=Depth.PROBE)), ctx
        )
        if isinstance(found, Err):
            raise ExternalSystemError(
                f"Inspection failed: {found.error}",
                user_message="The preview could not be inspected.",
            )
        return _stream_facts(found.value.inspection)

    def _decode(self, path: Path, cancel: CancellationToken) -> DecodeFacts:
        report = self._analyzer.decode(path, cancellation=cancel)
        return DecodeFacts(report.complete, report.errors)

    def _loudness(self, path: Path, cancel: CancellationToken) -> LoudnessFacts | None:
        reading = self._analyzer.loudness(path, span=None, cancellation=cancel)
        if not reading.has_audio:
            return None
        return LoudnessFacts(
            reading.integrated_lufs,
            reading.loudness_range_lu,
            reading.true_peak_dbtp,
            reading.sample_peak_dbfs,
        )

    def _end_peak(
        self, path: Path, streams: StreamFacts | None, cancel: CancellationToken
    ) -> float | None:
        if streams is None or not streams.has_audio or streams.duration is None:
            return None
        start = max(Fraction(0), streams.duration - _END_WINDOW)
        reading = self._analyzer.loudness(
            path,
            span=TimeSpan(start, None),
            cancellation=cancel,
        )
        return reading.sample_peak_dbfs

    def _dialogue(
        self,
        path: Path,
        timeline: TimelineFacts,
        profile: QualityProfile,
        cancel: CancellationToken,
    ) -> tuple[ClipLoudness, ...]:
        clips = sorted(
            (
                s
                for s in timeline.sounds
                if s.role == "dialogue"
                and s.enabled
                and not s.unresolved
                and s.end - s.start >= _MIN_DIALOGUE_SECONDS
            ),
            key=lambda s: s.start,
        )
        limit = profile.max_dialogue_measurements
        if len(clips) > limit:
            step = len(clips) / limit
            clips = [clips[int(i * step)] for i in range(limit)]
        found: list[ClipLoudness] = []
        for clip in clips:
            reading = self._analyzer.loudness(
                path,
                span=TimeSpan(clip.start, clip.end),
                cancellation=cancel,
            )
            found.append(ClipLoudness(clip.clip_id, reading.integrated_lufs))
        return tuple(found)

    def _frames(
        self,
        path: Path,
        timeline: TimelineFacts,
        profile: QualityProfile,
        work_dir: Path,
        ctx: JobContext,
    ) -> tuple[tuple[FrameStats, ...], dict[Fraction, Path]]:
        plan = plan_samples(timeline, profile)
        samples = self._analyzer.sample_frames(
            path, [p.at for p in plan], into=work_dir, cancellation=ctx.cancellation
        )
        by_time = {s.requested: s for s in samples}
        stats: list[FrameStats] = []
        kept: dict[Fraction, Path] = {}
        for point in plan:
            sample = by_time.get(point.at)
            if sample is None:
                continue
            stats.append(_frame_stats(point.at, point.tag, point.clip_id, sample))
            if sample.path is not None:
                kept[point.at] = sample.path
        return tuple(stats), kept


def _frame_stats(at: Fraction, tag: str, clip_id: str | None, sample: FrameSample) -> FrameStats:
    return FrameStats(
        at=at,
        tag=tag,
        clip_id=clip_id,
        luma_mean=sample.luma_mean,
        crushed_fraction=sample.crushed_fraction,
        clipped_fraction=sample.clipped_fraction,
        mean_rgb=sample.mean_rgb,
        sharpness=sample.sharpness,
        border_black=sample.border_black,
        black_fraction=sample.black_fraction,
        evidence_key=sample.path.name if sample.path else "",
    )


def _stream_facts(inspection: MediaInspection) -> StreamFacts:
    video = inspection.primary_video
    audio = inspection.audio_stream(0)
    rate = video.frame_rate.value if video else None
    offsets = inspection.synchronization.offsets
    return StreamFacts(
        has_video=video is not None,
        has_audio=audio is not None,
        width=video.geometry.width if video else None,
        height=video.geometry.height if video else None,
        fps=Fraction(rate.numerator, rate.denominator) if rate else None,
        duration=_exact(inspection.observed.container.duration),
        audio_duration=_exact(audio.duration) if audio else None,
        video_codec=video.codec if video else "",
        audio_sample_rate=audio.sample_rate if audio else None,
        av_offset=offsets[0].start_offset if offsets else None,
    )
