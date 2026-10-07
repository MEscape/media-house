"""Measured timing of a stream, from its packet timestamps (no decoding).

This is detection, not declaration: a container can claim a constant frame rate while its
timestamps say otherwise. The results are plain measurements; turning them into findings is
``rules.py``'s job, so thresholds can change without re-reading any media.
"""

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import pairwise

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.values import FrameRateMode

#: Positions of at most this many discontinuities are kept (the count is always exact).
MAX_POSITIONS = 10
_TICKS_OF_JITTER = 1.5
#: Irregular or missing frames needed before a stream counts as variable-rate.
_MIN_EVENTS_FOR_VARIABLE = 2
#: Audio packets may overlap or leave a hole of this many seconds before it is worth reporting.
_AUDIO_GAP_FLOOR_SECONDS = 0.0005


@dataclass(frozen=True, slots=True)
class Packet:
    """One compressed packet as the container stores it (times in seconds)."""

    pts: float | None
    dts: float | None
    duration: float | None
    size: int
    keyframe: bool


@dataclass(frozen=True, slots=True)
class StreamTiming:
    packet_count: int
    first_pts: float | None
    #: End of the last packet. Together with ``first_pts`` this is the MEASURED stream duration.
    end_pts: float | None
    missing_timestamps: int
    duplicate_timestamps: int
    #: Holes at least ``discontinuity_seconds`` long, and where the first few start.
    discontinuity_count: int
    discontinuity_positions: tuple[float, ...]
    #: Compressed size over measured duration (bits per second).
    measured_bit_rate: int | None
    # --- video: frame rhythm ----------------------------------------------------------------
    mode: FrameRateMode = FrameRateMode.UNKNOWN
    median_interval: float | None = None
    min_interval: float | None = None
    max_interval: float | None = None
    irregular_intervals: int = 0
    #: Frames missing inside holes that are whole multiples of the normal interval.
    dropped_frames: int = 0
    #: Decode order differs from presentation order (B-frames, or reordering muxers).
    reordered: bool = False
    keyframe_count: int = 0
    max_keyframe_interval: int | None = None
    # --- audio: continuity ------------------------------------------------------------------
    gap_count: int = 0
    overlap_count: int = 0
    total_gap_seconds: float = 0.0

    @property
    def measured_duration(self) -> float | None:
        if self.first_pts is None or self.end_pts is None:
            return None
        return self.end_pts - self.first_pts

    @property
    def measured_frame_rate(self) -> float | None:
        """Frames per second over the whole stream (average, whatever the mode)."""
        duration = self.measured_duration
        return self.packet_count / duration if duration and duration > 0 else None


def _basics(packets: Sequence[Packet]) -> tuple[list[float], int, int, bool]:
    """Sorted unique timestamps, then the missing and duplicate counts and the reorder flag."""
    stamped = [p.pts for p in packets if p.pts is not None]
    missing = len(packets) - len(stamped)
    reordered = any(a > b for a, b in pairwise(stamped))
    ordered = sorted(stamped)
    unique = [t for i, t in enumerate(ordered) if i == 0 or t != ordered[i - 1]]
    return unique, missing, len(ordered) - len(unique), reordered


def _bit_rate(packets: Sequence[Packet], duration: float | None) -> int | None:
    if not duration or duration <= 0:
        return None
    return round(sum(p.size for p in packets) * 8 / duration)


def analyze_video_packets(
    packets: Sequence[Packet],
    tick: float,
    config: InspectionConfig,
) -> StreamTiming:
    """Frame rhythm of a video stream; ``tick`` is one time-base unit in seconds."""
    times, missing, duplicates, reordered = _basics(packets)
    keyframes = [i for i, p in enumerate(sorted(packets, key=_by_pts)) if p.keyframe]
    intervals = [b - a for a, b in pairwise(times)]
    normal = statistics.median(intervals) if intervals else None

    irregular = dropped = events = 0
    holes: list[float] = []
    mode = FrameRateMode.UNKNOWN
    if normal is not None:
        tolerance = max(_TICKS_OF_JITTER * tick, config.interval_tolerance * normal)
        for start, interval in zip(times, intervals, strict=False):
            if abs(interval - normal) <= tolerance:
                continue
            multiple = round(interval / normal)
            if interval >= config.discontinuity_seconds and multiple >= 2:
                holes.append(start)
            elif multiple >= 2 and abs(interval - multiple * normal) <= tolerance * multiple:
                dropped += multiple - 1
                events += 1
            else:
                irregular += 1
                events += 1
        # one hiccup is damage, not a variable rate: that needs a recurring pattern
        variable = (
            events >= _MIN_EVENTS_FOR_VARIABLE
            and events / len(intervals) > config.variable_rate_fraction
        )
        mode = FrameRateMode.VARIABLE if variable else FrameRateMode.CONSTANT

    first = times[0] if times else None
    last_duration = packets[-1].duration or normal or 0.0 if packets else 0.0
    end = times[-1] + last_duration if times else None
    return StreamTiming(
        packet_count=len(packets),
        first_pts=first,
        end_pts=end,
        missing_timestamps=missing,
        duplicate_timestamps=duplicates,
        discontinuity_count=len(holes),
        discontinuity_positions=tuple(holes[:MAX_POSITIONS]),
        measured_bit_rate=_bit_rate(packets, end - first if first is not None and end else None),
        mode=mode,
        median_interval=normal,
        min_interval=min(intervals, default=None),
        max_interval=max(intervals, default=None),
        irregular_intervals=irregular,
        dropped_frames=dropped,
        reordered=reordered,
        keyframe_count=len(keyframes),
        max_keyframe_interval=max((b - a for a, b in pairwise(keyframes)), default=None),
    )


def analyze_audio_packets(
    packets: Sequence[Packet],
    tick: float,
    config: InspectionConfig,
) -> StreamTiming:
    """Continuity of an audio stream: each packet should start where the previous one ended."""
    stamped = sorted(((p.pts, p.duration) for p in packets if p.pts is not None), key=_first)
    tolerance = max(_TICKS_OF_JITTER * tick, _AUDIO_GAP_FLOOR_SECONDS)
    gaps = overlaps = duplicates = 0
    total_gap = 0.0
    holes: list[float] = []
    for (pts, duration), (next_pts, _) in pairwise(stamped):
        step = next_pts - pts
        if step == 0:
            duplicates += 1
            continue
        expected = duration if duration and duration > 0 else step
        difference = step - expected
        if difference > tolerance:
            gaps += 1
            total_gap += difference
            if difference >= config.discontinuity_seconds:
                holes.append(pts + expected)
        elif difference < -tolerance:
            overlaps += 1

    first = stamped[0][0] if stamped else None
    end = stamped[-1][0] + (stamped[-1][1] or 0.0) if stamped else None
    return StreamTiming(
        packet_count=len(packets),
        first_pts=first,
        end_pts=end,
        missing_timestamps=len(packets) - len(stamped),
        duplicate_timestamps=duplicates,
        discontinuity_count=len(holes),
        discontinuity_positions=tuple(holes[:MAX_POSITIONS]),
        measured_bit_rate=_bit_rate(packets, end - first if first is not None and end else None),
        gap_count=gaps,
        overlap_count=overlaps,
        total_gap_seconds=total_gap,
    )


def _by_pts(packet: Packet) -> float:
    return packet.pts if packet.pts is not None else float("inf")


def _first(item: tuple[float, float | None]) -> float:
    return item[0]
