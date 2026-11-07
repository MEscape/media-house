"""The one place that reads other modules' types into the render manager's own facts: an
inspected file becomes ``OutputFacts``, a built timeline becomes its size, rate and sections."""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.media_inspection.application.contracts import (
    InspectionResult,
    Verdict,
)
from media_house.modules.media_library.application.contracts import MediaAssetDto
from media_house.modules.render_manager.domain.verify import OutputFacts
from media_house.modules.timeline_builder.application.contracts import BuiltTimeline


@dataclass(frozen=True, slots=True)
class TimelineView:
    timeline_id: str
    project_id: str | None
    version: int
    content_hash: str
    width: int
    height: int
    frame_rate: Rational
    duration_frames: int
    sections: tuple[tuple[str, int, int], ...]
    has_sound: bool


def timeline_view(timeline: BuiltTimeline) -> TimelineView:
    return TimelineView(
        timeline_id=timeline.timeline_id,
        project_id=timeline.project_id,
        version=timeline.build.build_version,
        content_hash=timeline.build.build_hash,
        width=timeline.settings.width,
        height=timeline.settings.height,
        frame_rate=timeline.settings.frame_rate,
        duration_frames=timeline.settings.duration_frames,
        sections=tuple((s.section_id, s.start_frame, s.end_frame) for s in timeline.sections),
        has_sound=any(t.audio for t in timeline.tracks),
    )


def seconds_of(frames: int, rate: Rational) -> Fraction:
    return Fraction(frames * rate.denominator, rate.numerator)


def output_facts(asset: MediaAssetDto, result: InspectionResult | None) -> OutputFacts:
    """What the finished file shows. No inspection means it could not be read."""
    if result is None:
        return OutputFacts(
            exists=True, readable=False, size_bytes=asset.file_size,
            extension=asset.extension, duration=None, has_video=False, has_audio=False,
            width=None, height=None, frame_rate=None, video_codec=None, pixel_format=None,
            audio_codec=None, audio_duration=None, sample_rate=None, channels=None,
            inspected=False,
        )  # fmt: skip
    inspection = result.inspection
    video = inspection.primary_video
    audio = inspection.audio_stream(0)
    return OutputFacts(
        exists=True,
        readable=inspection.observed.integrity.readable,
        size_bytes=asset.file_size,
        extension=asset.extension,
        duration=inspection.container.duration,
        has_video=video is not None,
        has_audio=audio is not None,
        width=video.geometry.width if video else None,
        height=video.geometry.height if video else None,
        frame_rate=(video.average_frame_rate.value or video.frame_rate.value) if video else None,
        video_codec=video.codec if video else None,
        pixel_format=video.pixel_format if video else None,
        audio_codec=audio.codec if audio else None,
        audio_duration=audio.duration if audio else None,
        sample_rate=audio.sample_rate if audio else None,
        channels=audio.channels if audio else None,
        inspection_error=inspection.status.verdict is Verdict.ERROR,
    )
