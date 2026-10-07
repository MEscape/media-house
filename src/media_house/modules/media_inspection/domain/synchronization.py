"""Where each stream sits on the common clock, from measured timestamps.

These are measurements. Whether an offset is an error (or an intentional edit) is not decided
here; ``rules.py`` reports it as a possible problem against a tolerance.
"""

from collections.abc import Sequence

from media_house.modules.media_inspection.domain.model import (
    AudioStream,
    StreamOffset,
    Synchronization,
    VideoStream,
)
from media_house.modules.media_inspection.domain.timing import StreamTiming


def synchronize(
    videos: Sequence[VideoStream],
    audios: Sequence[AudioStream],
) -> Synchronization:
    """Offsets of every audio stream against the first video stream."""
    if not videos:
        return Synchronization(None, None)
    reference = videos[0]
    video_start = _start(reference.start_time, reference.timing)
    if video_start is None:
        return Synchronization(reference.index, None)

    video_end = reference.timing.end_pts if reference.timing else None
    video_duration = _duration(reference.timing, reference.duration)
    offsets = []
    for audio in audios:
        start = _start(audio.start_time, audio.timing)
        end = audio.timing.end_pts if audio.timing else None
        audio_duration = _duration(audio.timing, audio.duration)
        offsets.append(
            StreamOffset(
                audio_index=audio.index,
                start_offset=None if start is None else start - video_start,
                end_offset=None if end is None or video_end is None else end - video_end,
                duration_difference=(
                    None
                    if audio_duration is None or video_duration is None
                    else audio_duration - video_duration
                ),
            )
        )
    return Synchronization(reference.index, video_start, tuple(offsets))


def _start(declared: float | None, timing: StreamTiming | None) -> float | None:
    """Where the stream starts. The header's value already accounts for encoder delay and edit
    lists, which the first packet's timestamp does not (AAC priming makes it negative)."""
    if declared is not None:
        return declared
    return timing.first_pts if timing else None


def _duration(timing: StreamTiming | None, declared: float | None) -> float | None:
    if declared is not None:
        return declared
    return timing.measured_duration if timing else None
