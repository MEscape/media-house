"""Timeline validation: findings are REPORTED (``metadata.warnings``), never repaired."""

import math
from collections.abc import Iterable
from typing import Protocol

from media_house.modules.audio_intelligence.domain.analysis.acoustic import SILENCE_FLOOR_DB
from media_house.modules.audio_intelligence.domain.analysis.scoring import EDITING_KINDS
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_intelligence.domain.validation import (
    DEFAULT_TOLERANCE,
    MAX_REPORTED,
    Severity,
    ValidationIssue,
    validate_transcript,
)

_MAX_F0 = 2_000.0
_MAX_DBFS = 6.0
_SYNC_TOLERANCE = 1e-3
_TRACK_SLACK_SECONDS = 0.25


class _Add(Protocol):
    def __call__(
        self,
        code: str,
        severity: Severity,
        message: str,
        word: int | None = None,
    ) -> None: ...


def validate_timeline(
    timeline: AudioIntelligenceTimeline,
    tolerance: float = DEFAULT_TOLERANCE,
) -> tuple[ValidationIssue, ...]:
    issues: list[ValidationIssue] = list(validate_transcript(timeline.transcript, tolerance))
    meta, transcript = timeline.metadata, timeline.transcript

    def add(code: str, severity: Severity, message: str, word: int | None = None) -> None:
        issues.append(ValidationIssue(code, severity, message, word))

    if meta.source_asset_id != transcript.metadata.source_asset_id:
        add("source_mismatch", Severity.ERROR, "timeline and transcript name different sources")
    if meta.audio_asset_id != transcript.metadata.audio_asset_id:
        add("audio_mismatch", Severity.ERROR, "timeline and transcript name different audio")
    if not math.isclose(meta.audio_offset, timeline.track.origin, abs_tol=_SYNC_TOLERANCE):
        add(
            "frames_out_of_sync",
            Severity.ERROR,
            f"frames start at {timeline.track.origin:.4f}s but the audio offset is "
            f"{meta.audio_offset:.4f}s",
        )
    if timeline.track.end > meta.duration + _TRACK_SLACK_SECONDS:
        add(
            "frames_past_duration",
            Severity.WARNING,
            f"frames end at {timeline.track.end:.2f}s, media is {meta.duration:.2f}s",
        )
    _check_frames(timeline, add)
    _check_events(timeline, tolerance, add)
    _check_structure(timeline, add)
    _check_scores(timeline, add)
    return tuple(issues)


def _check_frames(timeline: AudioIntelligenceTimeline, add: _Add) -> None:
    track = timeline.track
    bad: dict[str, int] = {}

    def flag(code: str, index: int) -> None:
        bad.setdefault(code, index)

    for i in range(len(track)):
        f0, confidence = track.f0[i], track.pitch_confidence[i]
        if math.isfinite(f0) and not 0 < f0 <= _MAX_F0:
            flag("f0_out_of_range", i)
        if math.isinf(f0) or math.isinf(confidence):
            flag("infinite_value", i)
        if math.isfinite(confidence) and not 0.0 <= confidence <= 1.0:
            flag("confidence_out_of_range", i)
        db = track.rms_db[i]
        if not math.isfinite(db) or not SILENCE_FLOOR_DB - 1 <= db <= _MAX_DBFS:
            flag("rms_out_of_range", i)
        if math.isinf(track.loudness[i]):
            flag("infinite_value", i)
        if track.speech[i] not in {0.0, 1.0}:
            flag("speech_not_binary", i)
    for code, first in bad.items():
        add(code, Severity.ERROR, f"invalid acoustic frames, first at index {first}")


def _check_events(timeline: AudioIntelligenceTimeline, tolerance: float, add: _Add) -> None:
    previous = -math.inf
    for n, event in enumerate(timeline.events):
        where = f"event {n} ({event.kind})"
        if not (math.isfinite(event.start) and math.isfinite(event.end)):
            add("event_non_finite", Severity.ERROR, f"{where} has a non-finite time")
            continue
        if event.start > event.end or event.start < -tolerance:
            add("event_time_invalid", Severity.ERROR, f"{where} has an impossible time span")
        if event.end > timeline.duration + _TRACK_SLACK_SECONDS:
            add("event_past_duration", Severity.WARNING, f"{where} ends after the media")
        if event.start < previous - tolerance:
            add("events_out_of_order", Severity.ERROR, f"{where} starts before its predecessor")
        previous = max(previous, event.start)
        if not 0.0 <= event.strength <= 1.0:
            add("event_strength_range", Severity.ERROR, f"{where} strength {event.strength}")
        if event.confidence is not None and not 0.0 <= event.confidence <= 1.0:
            add("event_confidence_range", Severity.ERROR, f"{where} confidence {event.confidence}")


def _check_structure(timeline: AudioIntelligenceTimeline, add: _Add) -> None:
    words = timeline.transcript.words
    if len(timeline.words) != len(words) or any(
        tw.index != w.index for tw, w in zip(timeline.words, words, strict=False)
    ):
        add("word_mismatch", Severity.ERROR, "timeline words do not match the transcript")
    if len(timeline.segments) != len(timeline.transcript.segments):
        add("segment_mismatch", Severity.ERROR, "timeline segments do not match the transcript")
    previous = -math.inf
    for n, pause in enumerate(timeline.pauses):
        if pause.start > pause.end or pause.start < previous - DEFAULT_TOLERANCE:
            add("pause_invalid", Severity.ERROR, f"pause {n} is out of order or inverted")
        previous = pause.start
        for ref in (pause.before_word, pause.after_word):
            if ref is not None and not 0 <= ref < len(words):
                add("pause_reference", Severity.ERROR, f"pause {n} references word {ref}")


def _in_unit_interval(values: Iterable[float]) -> bool:
    return all(math.isfinite(v) and 0.0 <= v <= 1.0 for v in values)


def _check_scores(timeline: AudioIntelligenceTimeline, add: _Add) -> None:
    for word in timeline.words:
        for name, signal in (
            ("emphasis", word.signals.emphasis),
            ("expressiveness", word.signals.expressiveness),
            ("arousal", word.signals.arousal),
            ("local_contrast", word.signals.local_contrast),
            ("moment", word.signals.moment),
        ):
            if signal is not None and not _in_unit_interval(
                [signal.score, *signal.contributors.values()]
            ):
                add("score_out_of_range", Severity.ERROR, f"word {word.index} {name}", word.index)
    for segment in timeline.segments:
        for signal in (segment.acoustics.expressiveness, segment.acoustics.monotony):
            if signal is not None and not _in_unit_interval(
                [signal.score, *signal.contributors.values()]
            ):
                add("score_out_of_range", Severity.ERROR, f"segment {segment.id}")
    for n, editing in enumerate(timeline.editing_signals):
        if editing.kind not in EDITING_KINDS:
            add(
                "signal_kind",
                Severity.ERROR,
                f"editing signal {n} has unknown kind {editing.kind!r}",
            )
        if editing.start > editing.end or not _in_unit_interval(
            [editing.score, *editing.contributors.values()]
        ):
            add("signal_invalid", Severity.ERROR, f"editing signal {n} ({editing.kind})")


def summarize(issues: tuple[ValidationIssue, ...]) -> tuple[str, ...]:
    lines = [str(i) for i in issues[:MAX_REPORTED]]
    if len(issues) > MAX_REPORTED:
        lines.append(f"... and {len(issues) - MAX_REPORTED} more findings")
    return tuple(lines)
