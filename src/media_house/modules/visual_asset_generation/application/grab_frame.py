"""Use case: take one exact picture out of a library video and store it as a derived IMAGE.

The stored inspection (``InspectionCatalog``, never re-probed here) supplies the frame rate, the
rate mode and the stream offset, so the picture is addressed by frame, not by guesswork. Without
an inspection the frame is still taken (first picture at or after the time) and the result says
it is not frame-exact. The picture is a derived asset of the video: cut it out like any image.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.media_inspection.application.contracts import (
    FrameRateMode,
    InspectionCatalog,
    MediaInspection,
)
from media_house.modules.media_library.application.contracts import (
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.modules.visual_asset_generation.application.derive import Derivation, Deriver
from media_house.modules.visual_asset_generation.application.ports import (
    ExtractedFrame,
    FrameExtractor,
)
from media_house.modules.visual_asset_generation.domain.errors import (
    AdjustmentFailed,
    FrameGrabFailed,
    NoVideoStream,
)
from media_house.modules.visual_asset_generation.domain.frames import (
    FramePosition,
    StreamClock,
    decimal_seconds,
    frame_position,
)
from media_house.modules.visual_asset_generation.domain.model import (
    FRAME_GRAB_OPERATION,
    FRAME_GRAB_VERSION,
    InputHow,
    InputRecord,
)
from media_house.modules.visual_asset_generation.domain.qc import QcIssue, check_file
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, Result, ValidationError

type FrameError = MediaError | AdjustmentFailed | ExternalSystemError


@dataclass(frozen=True, slots=True, kw_only=True)
class GrabFrameCommand:
    video_asset_id: str
    #: Exact seconds on the recording's common clock (zero = the container start).
    source_time: Rational


@dataclass(frozen=True, slots=True, kw_only=True)
class FrameGrabResult:
    asset: MediaAssetDto
    #: ``False`` when the identical picture was already stored (nothing decoded).
    created: bool
    position: FramePosition
    inspection: InputRecord
    checks: tuple[QcIssue, ...]
    #: ``(name, version)`` of the decoder; ``None`` on a reused picture.
    engine: tuple[str, str] | None


class GrabFrame:
    def __init__(
        self,
        library: MediaLibrary,
        inspections: InspectionCatalog,
        extractor: FrameExtractor,
        deriver: Deriver,
    ) -> None:
        self._library = library
        self._inspections = inspections
        self._extractor = extractor
        self._deriver = deriver

    def execute(
        self, command: GrabFrameCommand, ctx: JobContext
    ) -> Result[FrameGrabResult, FrameError]:
        video = self._library.get(command.video_asset_id)
        if isinstance(video, Err):
            return video
        source = video.value
        if source.media_type is MediaType.AUDIO:
            return Err(NoVideoStream(source.id))
        if source.media_type is not MediaType.VIDEO:
            return Err(
                ValidationError(
                    f"Asset {source.id} is {source.media_type.value}, not a video",
                    field="video_asset_id",
                    user_message="Frames can only be taken from videos.",
                )
            )
        stored = self._inspections.find(source.id)
        clock: StreamClock | None = None
        if stored is None:
            record = InputRecord(
                kind="media_inspection",
                how=InputHow.NOT_AVAILABLE,
                asset_id=source.id,
                detail="no stored inspection: the frame is not addressed exactly",
            )
        else:
            inspection = stored.inspection
            if inspection.primary_video is None:
                return Err(NoVideoStream(source.id))
            ended = _past_end(inspection, command.source_time)
            if ended is not None:
                return Err(FrameGrabFailed(ended))
            clock = _clock(inspection)
            record = InputRecord(
                kind="media_inspection",
                how=InputHow.REUSED,
                asset_id=source.id,
                identity=stored.asset.id,
                detail="constant frame rate" if clock else "variable or unknown frame rate",
            )
        position = frame_position(command.source_time, clock)
        source_path = self._library.local_path(source.id)
        if isinstance(source_path, Err):
            return source_path
        seek = decimal_seconds(position.seek_time)
        config: dict[str, JsonValue] = {
            "source_time": str(position.source_time),
            "seek": seek,
            "frame_index": position.frame_index,
            "pixel_format": "rgb48be",
        }
        item = f"frame:{source.id}:{position.source_time}"
        derived = self._deriver.derive(
            Derivation(
                source=source,
                operation=FRAME_GRAB_OPERATION,
                version=FRAME_GRAB_VERSION,
                config=config,
                filename="frame.png",
                display_name=f"{source.display_name} (frame at {position.source_time.value:.3f} s)",
            ),
            lambda out: self._extractor.extract(source_path.value, seek, out, ctx.cancellation),
            lambda frame: _metadata(position, frame),
            lambda facts, _report, _hit: check_file(facts, item, expected_format="PNG"),
            ctx,
        )
        if isinstance(derived, Err):
            return derived
        done = derived.value
        if done.result is None:
            return Err(FrameGrabFailed("; ".join(i.message for i in done.issues)))
        return Ok(
            FrameGrabResult(
                asset=done.result.asset,
                created=done.result.created,
                position=position,
                inspection=record,
                checks=done.issues,
                engine=None if done.report is None else done.report.engine,
            )
        )


def _fraction(seconds: float) -> Fraction:
    return Fraction(seconds).limit_denominator(1_000_000)


def _clock(inspection: MediaInspection) -> StreamClock | None:
    """A frame clock only for a picture stream known (or declared) to run at a constant rate."""
    video = inspection.primary_video
    if video is None:
        return None
    rate, average = video.frame_rate.value, video.average_frame_rate.value
    mode = video.timing.mode if video.timing is not None else FrameRateMode.UNKNOWN
    constant = mode is FrameRateMode.CONSTANT or (
        mode is FrameRateMode.UNKNOWN and rate is not None and rate == average
    )
    if rate is None or rate.numerator == 0 or not constant:
        return None
    container_start = inspection.observed.container.start_time or 0.0
    offset = max(Fraction(0), _fraction((video.start_time or container_start) - container_start))
    return StreamClock(
        frame_rate=rate,
        video_offset=Rational(offset.numerator, offset.denominator),
    )


def _past_end(inspection: MediaInspection, source_time: Rational) -> str | None:
    duration = inspection.observed.container.duration
    if duration is None:
        return None
    if Fraction(source_time.numerator, source_time.denominator) >= _fraction(duration):
        return f"{source_time.value:.3f} s is past the end of the video ({duration:.3f} s)"
    return None


def _metadata(position: FramePosition, frame: ExtractedFrame) -> dict[str, JsonValue]:
    return {
        "source_time": str(position.source_time),
        "frame_index": position.frame_index,
        "frame_time": None if position.frame_time is None else str(position.frame_time),
        "frame_exact": position.exact,
        "pixel_format": frame.pixel_format,
        "engine": f"{frame.engine[0]} {frame.engine[1]}",
    }
