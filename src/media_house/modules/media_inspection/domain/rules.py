"""Findings: technical observations and problems DERIVED from the facts.

Facts are never changed or hidden by a rule; a file with no findings still has all its facts.
Each rule states how sure it is (``Certainty``) and what a consumer should check (``Concern``).
Rules read only the model and the config, never a file, so thresholds can be re-evaluated
without probing anything again.
"""

from collections.abc import Callable, Iterable, Mapping, Sequence

from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.model import (
    AudioStream,
    Finding,
    InspectionStatus,
    Integrity,
    ObservedMedia,
    Synchronization,
    VideoStream,
)
from media_house.modules.media_inspection.domain.values import (
    Category,
    Certainty,
    ChannelClass,
    Concern,
    FrameRateMode,
    JsonValue,
    Rational,
    ScanType,
    Severity,
    Verdict,
)

#: Frame rates of broadcast, cinema and common devices (exact fractions).
STANDARD_FRAME_RATES = frozenset(
    Rational(n, d)
    for n, d in (
        (24000, 1001),
        (24, 1),
        (25, 1),
        (30000, 1001),
        (30, 1),
        (48, 1),
        (50, 1),
        (60000, 1001),
        (60, 1),
        (100, 1),
        (120000, 1001),
        (120, 1),
    )
)
STANDARD_SAMPLE_RATES = frozenset(
    {8000, 11025, 16000, 22050, 24000, 32000, 44100, 48000, 88200, 96000, 176400, 192000}
)
#: Channel counts of the named FFmpeg layouts (the part before any ``(side)`` style suffix).
_LAYOUT_CHANNELS = {
    "mono": 1,
    "stereo": 2,
    "2.1": 3,
    "3.0": 3,
    "quad": 4,
    "4.0": 4,
    "4.1": 5,
    "5.0": 5,
    "5.1": 6,
    "6.0": 6,
    "6.1": 7,
    "7.0": 7,
    "7.1": 8,
}
_COMMON_CHANNEL_COUNTS = frozenset({1, 2, 6, 8})
_FRAME_RATE_MATCH = 0.001
_HDR_TRANSFERS = {"smpte2084": "hdr10", "arib-std-b67": "hlg"}

type Rule = Callable[[ObservedMedia, Synchronization, InspectionConfig], list[Finding]]


def _listed(items: Iterable[JsonValue]) -> list[JsonValue]:
    return list(items)


def _finding(
    code: str,
    severity: Severity,
    category: Category,
    message: str,
    certainty: Certainty,
    *,
    evidence: Mapping[str, JsonValue] | None = None,
    stream: int | None = None,
    concern: Concern | None = None,
) -> Finding:
    return Finding(code, severity, category, message, certainty, evidence or {}, stream, concern)


# ------------------------------------------------------------------------------------------
# container and integrity
# ------------------------------------------------------------------------------------------
def container_rules(
    media: ObservedMedia, _sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    found: list[Finding] = []
    if not media.integrity.readable:
        return [
            _finding(
                "container.unreadable",
                Severity.ERROR,
                Category.INTEGRITY,
                "The file could not be read as media.",
                Certainty.CONFIRMED,
                evidence={"reason": media.integrity.read_error},
                concern=Concern.REPAIR,
            )
        ]
    if not media.video_streams and not media.audio_streams:
        found.append(
            _finding(
                "container.no_streams",
                Severity.ERROR,
                Category.CONTAINER,
                "The container has no audio or video stream.",
                Certainty.CONFIRMED,
            )
        )
    duration = media.container.duration
    if duration is None or duration <= 0:
        found.append(
            _finding(
                "container.no_duration",
                Severity.WARNING,
                Category.CONTAINER,
                "The container declares no usable duration.",
                Certainty.MEASURED,
            )
        )
    return found


def integrity_rules(
    media: ObservedMedia, _sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    if not media.integrity.readable:
        return []
    integrity = media.integrity
    found: list[Finding] = []
    if integrity.read_message_count:
        found.append(
            _finding(
                "integrity.read_errors",
                Severity.ERROR,
                Category.INTEGRITY,
                "The container or its packets could not be read cleanly: the file is damaged.",
                Certainty.CONFIRMED,
                evidence={
                    "messages": integrity.read_message_count,
                    "first": _first_messages(integrity, "read"),
                },
                concern=Concern.REPAIR,
            )
        )
    if integrity.decode_message_count:
        found.append(
            _finding(
                "integrity.decode_errors",
                Severity.ERROR,
                Category.INTEGRITY,
                "Decoding reported errors: the media is damaged or uses unsupported features.",
                Certainty.CONFIRMED,
                evidence={
                    "messages": integrity.decode_message_count,
                    "first": _first_messages(integrity, "decode"),
                },
                concern=Concern.REPAIR,
            )
        )
    if not integrity.decode_checked:
        found.append(
            _finding(
                "integrity.not_decoded",
                Severity.INFO,
                Category.INTEGRITY,
                "The streams were not decoded at this depth, so damage inside them is unchecked.",
                Certainty.MEASURED,
            )
        )
    found.extend(
        _frame_count_rule(video) for video in media.video_streams if _frame_mismatch(video)
    )
    found.extend(_duration_rule(media, config))
    for video in media.video_streams:
        if video.geometry.width <= 0 or video.geometry.height <= 0:
            found.append(
                _finding(
                    "integrity.invalid_dimensions",
                    Severity.ERROR,
                    Category.INTEGRITY,
                    "The video stream declares no usable dimensions.",
                    Certainty.CONFIRMED,
                    stream=video.index,
                    concern=Concern.REPAIR,
                )
            )
    return found


def _first_messages(integrity: Integrity, stage: str) -> list[JsonValue]:
    texts: list[JsonValue] = [m.text for m in integrity.decode_messages if m.stage == stage]
    return texts[:3]


def _frame_mismatch(video: VideoStream) -> bool:
    return (
        video.declared_frame_count is not None
        and video.timing is not None
        and video.timing.packet_count != video.declared_frame_count
    )


def _frame_count_rule(video: VideoStream) -> Finding:
    return _finding(
        "integrity.frame_count_mismatch",
        Severity.WARNING,
        Category.INTEGRITY,
        "The video declares a different number of frames than it contains.",
        Certainty.CONFIRMED,
        evidence={
            "declared": video.declared_frame_count,
            "found": video.timing.packet_count if video.timing else None,
        },
        stream=video.index,
        concern=Concern.REPAIR,
    )


def _duration_rule(media: ObservedMedia, config: InspectionConfig) -> list[Finding]:
    declared = media.container.duration
    streams: list[VideoStream | AudioStream] = [*media.video_streams, *media.audio_streams]
    ends = [
        s.timing.end_pts for s in streams if s.timing is not None and s.timing.end_pts is not None
    ]
    if declared is None or not ends:
        return []
    start = media.container.start_time or 0.0
    measured = max(ends) - start
    if abs(declared - measured) <= config.duration_tolerance_seconds:
        return []
    return [
        _finding(
            "integrity.duration_mismatch",
            Severity.WARNING,
            Category.INTEGRITY,
            "The declared duration differs from the duration of the data actually present "
            "(the file may be incomplete or have a wrong header).",
            Certainty.POSSIBLE,
            evidence={"declared_seconds": declared, "measured_seconds": round(measured, 6)},
            concern=Concern.REPAIR,
        )
    ]


# ------------------------------------------------------------------------------------------
# video
# ------------------------------------------------------------------------------------------
def video_rules(
    media: ObservedMedia, _sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    found: list[Finding] = []
    for video in media.video_streams:
        found.extend(_frame_rate_findings(video, config))
        found.extend(_timestamp_findings(video))
        found.extend(_picture_findings(video, config))
    if len(media.video_streams) > 1:
        found.append(_multiple_video_streams(media.video_streams))
    return found


def _frame_rate_findings(video: VideoStream, config: InspectionConfig) -> list[Finding]:
    found: list[Finding] = []
    timing = video.timing
    rate = video.frame_rate.value
    if timing is not None and timing.mode is FrameRateMode.VARIABLE:
        found.append(
            _finding(
                "video.variable_frame_rate",
                Severity.WARNING,
                Category.TIMING,
                "The frame rate varies: frames are not evenly spaced in time.",
                Certainty.MEASURED,
                evidence={
                    "irregular_intervals": timing.irregular_intervals,
                    "min_interval": timing.min_interval,
                    "max_interval": timing.max_interval,
                    "declared_frame_rate": str(rate) if rate else None,
                },
                stream=video.index,
                concern=Concern.TRANSCODE,
            )
        )
    elif (
        timing is not None
        and rate is not None
        and timing.measured_frame_rate is not None
        and not timing.dropped_frames  # a rate that is off because of holes is explained by them
        and not timing.discontinuity_count
    ):
        measured = timing.measured_frame_rate
        if abs(measured - rate.value) / rate.value > config.frame_rate_tolerance:
            found.append(
                _finding(
                    "video.frame_rate_mismatch",
                    Severity.WARNING,
                    Category.TIMING,
                    "The measured frame rate differs from the declared frame rate.",
                    Certainty.POSSIBLE,
                    evidence={"declared": str(rate), "measured": round(measured, 4)},
                    stream=video.index,
                    concern=Concern.SYNC_CHECK,
                )
            )
    if rate is not None and not _is_standard_rate(rate):
        found.append(
            _finding(
                "video.unusual_frame_rate",
                Severity.INFO,
                Category.TIMING,
                "The frame rate is not one of the usual broadcast or cinema rates.",
                Certainty.MEASURED,
                evidence={"frame_rate": str(rate), "fps": round(rate.value, 4)},
                stream=video.index,
            )
        )
    return found


def _is_standard_rate(rate: Rational) -> bool:
    return any(
        abs(rate.value - s.value) / s.value < _FRAME_RATE_MATCH for s in STANDARD_FRAME_RATES
    )


def _timestamp_findings(video: VideoStream) -> list[Finding]:
    timing = video.timing
    if timing is None:
        return []
    found: list[Finding] = []
    if timing.dropped_frames:
        found.append(
            _finding(
                "video.dropped_frames",
                Severity.WARNING,
                Category.TIMING,
                "Gaps of whole frame intervals in the timestamps suggest dropped frames.",
                Certainty.POSSIBLE,
                evidence={"estimated_frames": timing.dropped_frames},
                stream=video.index,
                concern=Concern.SYNC_CHECK,
            )
        )
    if timing.discontinuity_count:
        found.append(
            _finding(
                "video.timestamp_discontinuity",
                Severity.WARNING,
                Category.TIMING,
                "The video timestamps jump: part of the timeline is missing or was cut.",
                Certainty.MEASURED,
                evidence={
                    "count": timing.discontinuity_count,
                    "at_seconds": list(timing.discontinuity_positions),
                },
                stream=video.index,
                concern=Concern.SYNC_CHECK,
            )
        )
    if timing.duplicate_timestamps:
        found.append(
            _finding(
                "video.duplicate_timestamps",
                Severity.WARNING,
                Category.TIMING,
                "Several frames share one timestamp (duplicated or damaged frames).",
                Certainty.MEASURED,
                evidence={"count": timing.duplicate_timestamps},
                stream=video.index,
                concern=Concern.REPAIR,
            )
        )
    if timing.missing_timestamps:
        found.append(
            _finding(
                "video.missing_timestamps",
                Severity.WARNING,
                Category.TIMING,
                "Some frames carry no presentation timestamp.",
                Certainty.MEASURED,
                evidence={"count": timing.missing_timestamps},
                stream=video.index,
                concern=Concern.REPAIR,
            )
        )
    return found


def _picture_findings(video: VideoStream, config: InspectionConfig) -> list[Finding]:
    found: list[Finding] = []
    geometry = video.geometry
    if video.scan_type.value is ScanType.INTERLACED:
        found.append(
            _finding(
                "video.interlaced",
                Severity.INFO,
                Category.VIDEO,
                "The video is interlaced.",
                Certainty.MEASURED,
                evidence={"field_order": video.field_order},
                stream=video.index,
            )
        )
    if not geometry.square_pixels:
        found.append(
            _finding(
                "video.non_square_pixels",
                Severity.INFO,
                Category.VIDEO,
                "The pixels are not square: the displayed shape differs from the stored one.",
                Certainty.MEASURED,
                evidence={
                    "sample_aspect_ratio": str(geometry.sample_aspect_ratio.value),
                    "stored": [geometry.width, geometry.height],
                    "displayed": list(geometry.display_size),
                },
                stream=video.index,
            )
        )
    if geometry.rotation.value:
        found.append(
            _finding(
                "video.rotated",
                Severity.INFO,
                Category.VIDEO,
                "The video is stored rotated and is turned by the player on display.",
                Certainty.MEASURED,
                evidence={"rotation": geometry.rotation.value},
                stream=video.index,
            )
        )
    subsampling = video.chroma_subsampling.value
    if subsampling in {"420", "422"} and (geometry.width % 2 or geometry.height % 2):
        found.append(
            _finding(
                "video.odd_dimensions",
                Severity.WARNING,
                Category.VIDEO,
                "Odd dimensions with chroma subsampling are rejected by many encoders and tools.",
                Certainty.MEASURED,
                evidence={"width": geometry.width, "height": geometry.height},
                stream=video.index,
                concern=Concern.TRANSCODE,
            )
        )
    found.extend(_bitrate_findings(video, config))
    found.extend(_color_findings(video))
    return found


def _bitrate_findings(video: VideoStream, config: InspectionConfig) -> list[Finding]:
    rate = video.average_frame_rate.value or video.frame_rate.value
    bit_rate = video.bit_rate.value
    pixels = video.geometry.width * video.geometry.height
    if bit_rate is None or rate is None or pixels <= 0:
        return []
    per_pixel = bit_rate / (pixels * rate.value)
    if per_pixel >= config.low_bits_per_pixel:
        return []
    return [
        _finding(
            "video.low_bitrate",
            Severity.WARNING,
            Category.VIDEO,
            "The bit rate is very low for the picture size: visible compression damage is likely.",
            Certainty.POSSIBLE,
            evidence={"bits_per_pixel": round(per_pixel, 4), "bit_rate": bit_rate},
            stream=video.index,
        )
    ]


def _color_findings(video: VideoStream) -> list[Finding]:
    color = video.color
    found: list[Finding] = []
    missing = [
        name
        for name, value in (
            ("primaries", color.primaries),
            ("transfer", color.transfer),
            ("space", color.space),
            ("range", color.range),
        )
        if not value.known
    ]
    if missing:
        found.append(
            _finding(
                "color.undeclared",
                Severity.INFO,
                Category.COLOR,
                "The file does not declare all of its colour properties; none were assumed.",
                Certainty.MEASURED,
                evidence={"missing": _listed(missing)},
                stream=video.index,
                concern=Concern.COLOR_CHECK,
            )
        )
    hdr = color.dynamic_range.value in {"hdr10", "hlg"}
    if hdr and not (color.mastering_display or color.content_light_level):
        found.append(
            _finding(
                "color.hdr_metadata_missing",
                Severity.WARNING,
                Category.COLOR,
                "HDR transfer without mastering-display or light-level metadata.",
                Certainty.MEASURED,
                evidence={"dynamic_range": color.dynamic_range.value},
                stream=video.index,
                concern=Concern.COLOR_CHECK,
            )
        )
    if hdr and (
        (video.bit_depth.value or 10) < 10
        or color.primaries.value in {"bt709", "smpte170m", "bt470bg"}
        or color.space.value in {"bt709", "smpte170m", "bt470bg"}
    ):
        found.append(
            _finding(
                "color.inconsistent",
                Severity.WARNING,
                Category.COLOR,
                "An HDR transfer is declared together with SDR colour properties or low bit depth.",
                Certainty.POSSIBLE,
                evidence={
                    "transfer": color.transfer.value,
                    "primaries": color.primaries.value,
                    "space": color.space.value,
                    "bit_depth": video.bit_depth.value,
                },
                stream=video.index,
                concern=Concern.COLOR_CHECK,
            )
        )
    if color.dolby_vision:
        found.append(
            _finding(
                "color.dolby_vision",
                Severity.INFO,
                Category.COLOR,
                "The stream carries Dolby Vision configuration.",
                Certainty.MEASURED,
                stream=video.index,
                concern=Concern.COLOR_CHECK,
            )
        )
    return found


def _multiple_video_streams(videos: Sequence[VideoStream]) -> Finding:
    shapes = {(v.geometry.width, v.geometry.height) for v in videos}
    rates = {str(v.frame_rate.value) for v in videos}
    consistent = len(shapes) == 1 and len(rates) == 1
    return _finding(
        "video.multiple_streams",
        Severity.INFO if consistent else Severity.WARNING,
        Category.VIDEO,
        "The file has several video streams"
        + ("." if consistent else " that differ in size or frame rate."),
        Certainty.MEASURED,
        evidence={"count": len(videos), "sizes": _listed(sorted(f"{w}x{h}" for w, h in shapes))},
        concern=None if consistent else Concern.TRANSCODE,
    )


# ------------------------------------------------------------------------------------------
# audio
# ------------------------------------------------------------------------------------------
def audio_rules(
    media: ObservedMedia, _sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    found: list[Finding] = []
    audios = media.audio_streams
    if media.video_streams and not audios and media.integrity.readable:
        found.append(
            _finding(
                "audio.missing",
                Severity.INFO,
                Category.AUDIO,
                "The video has no audio stream.",
                Certainty.MEASURED,
            )
        )
    for audio in audios:
        found.extend(_audio_stream_findings(audio))
    rates = {a.sample_rate for a in audios if a.sample_rate}
    if len(rates) > 1:
        found.append(
            _finding(
                "audio.sample_rate_mismatch",
                Severity.WARNING,
                Category.AUDIO,
                "The audio streams use different sample rates.",
                Certainty.MEASURED,
                evidence={"sample_rates": _listed(sorted(rates))},
                concern=Concern.AUDIO_CHECK,
            )
        )
    if len(audios) > 1:
        found.append(
            _finding(
                "audio.multiple_streams",
                Severity.INFO,
                Category.AUDIO,
                "The file has several audio streams.",
                Certainty.MEASURED,
                evidence={"count": len(audios)},
            )
        )
    return found


def _audio_stream_findings(audio: AudioStream) -> list[Finding]:
    found: list[Finding] = []
    if audio.channels is None or audio.sample_rate is None:
        found.append(
            _finding(
                "audio.unsupported",
                Severity.ERROR,
                Category.INTEGRITY,
                "The audio stream has no usable format (unsupported or damaged).",
                Certainty.CONFIRMED,
                evidence={"codec": audio.codec},
                stream=audio.index,
                concern=Concern.REPAIR,
            )
        )
        return found
    if audio.sample_rate not in STANDARD_SAMPLE_RATES:
        found.append(
            _finding(
                "audio.unusual_sample_rate",
                Severity.WARNING,
                Category.AUDIO,
                "The sample rate is not a standard rate.",
                Certainty.MEASURED,
                evidence={"sample_rate": audio.sample_rate},
                stream=audio.index,
                concern=Concern.AUDIO_CHECK,
            )
        )
    implied = _implied_channels(audio.channel_layout.value)
    if implied is not None and implied != audio.channels:
        found.append(
            _finding(
                "audio.channel_layout_mismatch",
                Severity.WARNING,
                Category.AUDIO,
                "The channel layout names a different channel count than the stream has.",
                Certainty.CONFIRMED,
                evidence={"layout": audio.channel_layout.value, "channels": audio.channels},
                stream=audio.index,
                concern=Concern.AUDIO_CHECK,
            )
        )
    elif audio.channels not in _COMMON_CHANNEL_COUNTS or (
        audio.channel_class is ChannelClass.MULTICHANNEL and not audio.channel_layout.known
    ):
        found.append(
            _finding(
                "audio.unusual_channel_layout",
                Severity.INFO,
                Category.AUDIO,
                "The channel count or layout is unusual; check what each channel carries.",
                Certainty.MEASURED,
                evidence={"channels": audio.channels, "layout": audio.channel_layout.value},
                stream=audio.index,
                concern=Concern.AUDIO_CHECK,
            )
        )
    timing = audio.timing
    if timing is not None:
        found.extend(_audio_timing_findings(audio, timing.gap_count, timing.overlap_count))
        if timing.missing_timestamps:
            found.append(
                _finding(
                    "audio.missing_timestamps",
                    Severity.WARNING,
                    Category.TIMING,
                    "Some audio packets carry no timestamp.",
                    Certainty.MEASURED,
                    evidence={"count": timing.missing_timestamps},
                    stream=audio.index,
                    concern=Concern.REPAIR,
                )
            )
    return found


def _audio_timing_findings(audio: AudioStream, gaps: int, overlaps: int) -> list[Finding]:
    if not (gaps or overlaps) or audio.timing is None:
        return []
    return [
        _finding(
            "audio.timestamp_gaps",
            Severity.WARNING,
            Category.TIMING,
            "The audio timestamps have holes or overlaps: samples are missing or repeated.",
            Certainty.MEASURED,
            evidence={
                "gaps": gaps,
                "overlaps": overlaps,
                "total_gap_seconds": round(audio.timing.total_gap_seconds, 6),
            },
            stream=audio.index,
            concern=Concern.SYNC_CHECK,
        )
    ]


def _implied_channels(layout: str | None) -> int | None:
    if layout is None:
        return None
    return _LAYOUT_CHANNELS.get(layout.split("(")[0].strip().lower())


# ------------------------------------------------------------------------------------------
# synchronization, timecode, metadata
# ------------------------------------------------------------------------------------------
def synchronization_rules(
    media: ObservedMedia, sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    found: list[Finding] = []
    tolerance = config.sync_tolerance_seconds
    for offset in sync.offsets:
        if offset.start_offset is not None and abs(offset.start_offset) > tolerance:
            found.append(
                _finding(
                    "sync.audio_offset",
                    Severity.WARNING,
                    Category.SYNC,
                    "The audio starts at a different time than the video.",
                    Certainty.POSSIBLE,
                    evidence={
                        "offset_seconds": round(offset.start_offset, 6),
                        "tolerance_seconds": tolerance,
                    },
                    stream=offset.audio_index,
                    concern=Concern.SYNC_CHECK,
                )
            )
        drift = offset.drift
        allowed = tolerance + _packet_slack(media, offset.audio_index)
        if drift is not None and abs(drift) > allowed:
            found.append(
                _finding(
                    "sync.drift",
                    Severity.WARNING,
                    Category.SYNC,
                    "The audio and video drift apart over the length of the file.",
                    Certainty.POSSIBLE,
                    evidence={
                        "drift_seconds": round(drift, 6),
                        "allowed_seconds": round(allowed, 6),
                    },
                    stream=offset.audio_index,
                    concern=Concern.SYNC_CHECK,
                )
            )
        difference = offset.duration_difference
        if difference is not None and abs(difference) > config.duration_tolerance_seconds:
            found.append(
                _finding(
                    "sync.duration_mismatch",
                    Severity.WARNING,
                    Category.SYNC,
                    "The audio and video have clearly different lengths.",
                    Certainty.POSSIBLE,
                    evidence={"difference_seconds": round(difference, 6)},
                    stream=offset.audio_index,
                    concern=Concern.SYNC_CHECK,
                )
            )
    return found


def _packet_slack(media: ObservedMedia, audio_index: int) -> float:
    """Where the first and last packet fall inside their frames: ends are only known to a packet."""
    audio = next((a for a in media.audio_streams if a.index == audio_index), None)
    video = media.video_streams[0] if media.video_streams else None
    packet = 0.0
    if audio is not None and audio.timing and audio.timing.packet_count:
        duration = audio.timing.measured_duration
        packet = duration / audio.timing.packet_count if duration else 0.0
    frame = video.timing.median_interval if video and video.timing else None
    return packet + (frame or 0.0)


def timecode_rules(
    media: ObservedMedia, _sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    timecode = media.timecode
    video = media.video_streams[0] if media.video_streams else None
    rate = video.frame_rate.value if video else None
    if timecode is None or rate is None:
        return []
    problem = timecode.start.problem_for(rate)
    if problem is None:
        return []
    return [
        _finding(
            "timecode.invalid_for_frame_rate",
            Severity.WARNING,
            Category.TIMECODE,
            f"The start timecode is not valid at this frame rate: {problem}.",
            Certainty.CONFIRMED,
            evidence={"timecode": str(timecode.start), "frame_rate": str(rate)},
            concern=Concern.SYNC_CHECK,
        )
    ]


def metadata_rules(
    media: ObservedMedia, _sync: Synchronization, config: InspectionConfig
) -> list[Finding]:
    production = media.production
    if not media.integrity.readable or (
        production.creation_time or production.camera_make or production.camera_model
    ):
        return []
    return [
        _finding(
            "metadata.production_missing",
            Severity.INFO,
            Category.METADATA,
            "The file carries no creation time or camera information.",
            Certainty.MEASURED,
        )
    ]


RULES: tuple[Rule, ...] = (
    container_rules,
    integrity_rules,
    video_rules,
    audio_rules,
    synchronization_rules,
    timecode_rules,
    metadata_rules,
)


def evaluate(
    media: ObservedMedia,
    sync: Synchronization,
    config: InspectionConfig,
) -> tuple[Finding, ...]:
    """Every finding of every rule, in rule order."""
    return tuple(finding for rule in RULES for finding in rule(media, sync, config))


def derive_status(findings: Sequence[Finding]) -> InspectionStatus:
    """The one-glance answer: usable or not, and what to check first."""
    errors = sum(f.severity is Severity.ERROR for f in findings)
    warnings = sum(f.severity is Severity.WARNING for f in findings)
    concerns = tuple(sorted({f.concern for f in findings if f.concern}, key=lambda c: c.value))
    verdict = Verdict.ERROR if errors else Verdict.WARNING if warnings else Verdict.VALID
    return InspectionStatus(verdict, errors == 0, concerns, warnings, errors)
