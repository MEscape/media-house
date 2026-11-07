"""The one place that reads other modules' types into the validator's own facts: an inspection of
the file, the analyzer's measurements, the approved timeline and the render report."""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.application.ports import (
    DecodeReport,
    LoudnessReading,
    TimeSpan,
)
from media_house.modules.export_validation.domain.facts import (
    AudioFacts,
    DecodeFacts,
    LoudnessFacts,
    Span,
    VideoFacts,
)
from media_house.modules.media_inspection.application.contracts import (
    FrameRateMode,
    InspectionResult,
    Verdict,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline


@dataclass(frozen=True, slots=True)
class TimelineFacts:
    timeline_id: str
    project_id: str | None
    version: int
    content_hash: str
    width: int
    height: int
    frame_rate_num: int
    frame_rate_den: int
    duration_frames: int
    has_sound: bool
    sections: tuple[tuple[str, int, int], ...]

    @property
    def duration_seconds(self) -> float:
        return self.duration_frames * self.frame_rate_den / self.frame_rate_num

    def moment(self, frame: float) -> Fraction:
        return Fraction(round(frame * 2) * self.frame_rate_den, 2 * self.frame_rate_num)


def timeline_facts(timeline: BuiltTimeline) -> TimelineFacts:
    rate = timeline.settings.frame_rate
    return TimelineFacts(
        timeline_id=timeline.timeline_id,
        project_id=timeline.project_id,
        version=timeline.build.build_version,
        content_hash=timeline.build.build_hash,
        width=timeline.settings.width,
        height=timeline.settings.height,
        frame_rate_num=rate.numerator,
        frame_rate_den=rate.denominator,
        duration_frames=timeline.settings.duration_frames,
        has_sound=any(t.audio for t in timeline.tracks),
        sections=tuple(
            (s.section_id, s.start_frame, s.end_frame)
            for s in sorted(timeline.sections, key=lambda s: s.order)
        ),
    )


def spans(found: tuple[TimeSpan, ...]) -> tuple[Span, ...]:
    return tuple(Span(float(s.start), None if s.end is None else float(s.end)) for s in found)


def decode_facts(report: DecodeReport) -> DecodeFacts:
    return DecodeFacts(
        report.complete,
        report.frames_decoded,
        None if report.decoded_until is None else float(report.decoded_until),
        report.errors,
    )


def loudness_facts(reading: LoudnessReading) -> LoudnessFacts:
    return LoudnessFacts(
        reading.integrated_lufs,
        reading.loudness_range_lu,
        reading.true_peak_dbtp,
        reading.sample_peak_dbfs,
        reading.has_audio,
    )


def video_facts(result: InspectionResult) -> VideoFacts | None:
    inspection = result.inspection
    video = inspection.primary_video
    if video is None:
        return None
    timing = video.timing
    color = video.color
    tags = tuple(
        name
        for name, sourced in (
            ("space", color.space),
            ("primaries", color.primaries),
            ("transfer", color.transfer),
            ("range", color.range),
        )
        if sourced.value
    )
    return VideoFacts(
        codec=video.codec,
        width=video.geometry.width,
        height=video.geometry.height,
        frame_rate=video.frame_rate.value,
        average_frame_rate=video.average_frame_rate.value,
        pixel_format=video.pixel_format,
        display_aspect=video.geometry.display_aspect_ratio.value,
        duration=video.duration if video.duration is not None else inspection.container.duration,
        declared_frames=video.declared_frame_count,
        dropped_frames=timing.dropped_frames if timing else None,
        duplicate_timestamps=timing.duplicate_timestamps if timing else None,
        discontinuities=timing.discontinuity_count if timing else None,
        variable_rate=(timing.mode is FrameRateMode.VARIABLE) if timing else None,
        color_tags=tags,
    )


def audio_facts(result: InspectionResult) -> AudioFacts | None:
    inspection = result.inspection
    audio = inspection.audio_stream(0)
    if audio is None:
        return None
    offset = next(
        (o for o in inspection.synchronization.offsets if o.audio_index == audio.index), None
    )
    return AudioFacts(
        codec=audio.codec,
        sample_rate=audio.sample_rate,
        channels=audio.channels,
        duration=audio.duration,
        start_offset=offset.start_offset if offset else None,
        duration_difference=offset.duration_difference if offset else None,
    )


def inspection_failed(result: InspectionResult) -> bool:
    return result.inspection.status.verdict is Verdict.ERROR
