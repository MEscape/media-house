"""Builders for music_and_sound_design tests: synthetic library sounds, measurements, timelines.

Nothing here reads the owner's library: every sound is described or synthesised in code (some
library files forbid use as test or model data).
"""

import math
import struct
import wave
from fractions import Fraction
from pathlib import Path

from media_house.core.domain import Rational
from media_house.modules.music_and_sound_design.domain.analysis import (
    EditPoint,
    LoopRegion,
    Loudness,
    MusicAnalysis,
    MusicMeasurement,
    MusicSection,
    SfxMeasurement,
    SfxProfile,
    Tempo,
)
from media_house.modules.music_and_sound_design.domain.library import Licence, SoundAsset
from media_house.modules.music_and_sound_design.domain.timeline import (
    EditRef,
    SoundtrackTimeline,
    SpeechSpan,
    SyncEvent,
    TimelineClip,
    TimelineSection,
)
from media_house.modules.music_and_sound_design.domain.values import (
    ClipAudio,
    EditPointKind,
    EventSource,
    SectionLabel,
    SoundKind,
)
from tests.support.fakes import T0

FPS = Rational(30, 1)
EDIT = EditRef(edit_plan_id="edit-1", plan_version=1)
_RATE = Rational(22050, 256)


def r(value: float | Fraction | int) -> Rational:
    f = Fraction(value).limit_denominator(1_000_000)
    return Rational(f.numerator, f.denominator)


def licence(
    *, commercial: bool | None = True, credit: str | None = None, source: str = "test"
) -> Licence:
    return Licence(
        name="CC0" if credit is None else "CC BY 4.0",
        url="https://example.invalid/licence",
        commercial_use=commercial,
        attribution_required=credit is not None,
        attribution_text=credit,
        source=source,
    )


def _frame(seconds: float) -> int:
    return round(seconds * 22050 / 256)


def music_analysis(
    asset_id: str,
    *,
    bpm: float = 120.0,
    duration: float = 120.0,
    lufs: float = -14.0,
    onset_rate: float = 4.0,
    loop: tuple[float, float] | None = (8.0, 40.0),
    confidence: float = 0.8,
    peak: float = -1.0,
) -> MusicAnalysis:
    period = 60.0 / bpm
    beats = tuple(_frame(i * period) for i in range(int(duration / period)))
    downbeats = beats[::4]
    half = _frame(duration / 2)
    spans = ((0, _frame(8), SectionLabel.INTRO, -24.0),
             (_frame(8), half, SectionLabel.UNLABELED, -18.0),
             (half, _frame(duration), SectionLabel.CLIMAX, -14.0))  # fmt: skip
    sections = tuple(
        MusicSection(start_frame=a, end_frame=b, label=label, energy_db=level, evidence="test")
        for a, b, label, level in spans
    )
    points = [
        EditPoint(kind=EditPointKind.TRACK_START, frame=0, strength=1.0),
        EditPoint(kind=EditPointKind.SECTION_START, frame=_frame(8), strength=0.8),
        EditPoint(kind=EditPointKind.SECTION_START, frame=half, strength=0.9),
        EditPoint(kind=EditPointKind.ENDING, frame=_frame(duration), strength=1.0),
    ]
    loop_region = None
    if loop is not None:
        loop_region = LoopRegion(
            start_frame=_frame(loop[0]), end_frame=_frame(loop[1]), similarity=0.95
        )
    return MusicAnalysis(
        analysis_id=f"mus:{asset_id}",
        asset_id=asset_id,
        checksum="0" * 64,
        created_at=T0,
        analyzer_version=1,
        measurement=MusicMeasurement(
            duration_seconds=duration,
            frame_rate=_RATE,
            loudness=Loudness(integrated_lufs=lufs, loudness_range_lu=4.0, true_peak_dbtp=peak),
            onset_envelope=(0.0, 1.0),
            onset_rate=onset_rate,
            tempo=Tempo(bpm=bpm, confidence=confidence, stability=1.0),
            beat_frames=beats,
            downbeat_frames=downbeats,
            downbeat_confidence=0.5,
            meter_assumed=4,
            energy_rate=Rational(2, 1),
            energy_db=(-20.0,) * int(duration * 2),
            sections=sections,
            loop=loop_region,
            edit_points=tuple(points),
        ),
        schema_version=1,
    )


def music(
    asset_id: str,
    categories: tuple[str, ...] = ("upbeat_positive",),
    *,
    measured: bool = True,
    commercial: bool | None = True,
    credit: str | None = None,
    duration: float = 120.0,
    **analysis: object,
) -> SoundAsset:
    return SoundAsset(
        asset_id=asset_id,
        kind=SoundKind.MUSIC,
        title=f"Track {asset_id}",
        categories=categories,
        tags=(),
        duration_seconds=duration,
        checksum=asset_id.ljust(64, "0"),
        licence=licence(commercial=commercial, credit=credit),
        music=music_analysis(asset_id, duration=duration, **analysis) if measured else None,  # type: ignore[arg-type]
        analysis_asset_id=f"doc-{asset_id}" if measured else None,
    )


def sfx_profile(
    asset_id: str, *, duration: float = 0.5, peak_at: float = 0.01, rms: float = -12.0
) -> SfxProfile:
    rate = 48_000
    return SfxProfile(
        analysis_id=f"sfx:{asset_id}",
        asset_id=asset_id,
        checksum="0" * 64,
        created_at=T0,
        analyzer_version=1,
        measurement=SfxMeasurement(
            duration_seconds=duration,
            sample_rate=rate,
            loudness=Loudness(integrated_lufs=None, loudness_range_lu=None, true_peak_dbtp=-3.0),
            peak_dbfs=-3.0,
            rms_max_dbfs=rms,
            onset_sample=0,
            peak_sample=int(peak_at * rate),
            useful_start_sample=0,
            useful_end_sample=int(duration * rate),
            attack_seconds=peak_at,
            tail_seconds=duration - peak_at,
            spectral_centroid_hz=2000.0,
            brightness=0.2,
        ),
        schema_version=1,
    )


def sfx(
    asset_id: str,
    categories: tuple[str, ...] = ("pop",),
    *,
    duration: float = 0.5,
    commercial: bool | None = True,
    **profile: float,
) -> SoundAsset:
    return SoundAsset(
        asset_id=asset_id,
        kind=SoundKind.SFX,
        title=f"Effect {asset_id}",
        categories=categories,
        tags=(),
        duration_seconds=duration,
        checksum=asset_id.ljust(64, "1"),
        licence=licence(commercial=commercial),
        sfx=sfx_profile(asset_id, duration=duration, **profile),
        analysis_asset_id=f"doc-{asset_id}",
    )


def clip(
    clip_id: str,
    start: float,
    end: float,
    *,
    asset_id: str = "rec",
    audio: ClipAudio = ClipAudio.SOURCE_SYNC,
    section_id: str | None = None,
) -> TimelineClip:
    return TimelineClip(
        clip_id=clip_id,
        asset_id=asset_id,
        source_start=r(start),
        source_end=r(end),
        timeline_start=r(start),
        audio=audio,
        section_id=section_id,
    )


def section(section_id: str, start: float, end: float, *functions: str) -> TimelineSection:
    return TimelineSection(
        section_id=section_id,
        timeline_start=r(start),
        timeline_end=r(end),
        beat_functions=functions,
    )


def speech(start: float, end: float) -> SpeechSpan:
    return SpeechSpan(timeline_start=r(start), timeline_end=r(end), words=3)


def event(
    event_id: str,
    at: float,
    kind: str,
    *,
    source: EventSource = EventSource.GRAPHICS,
    strength: float = 0.9,
) -> SyncEvent:
    return SyncEvent(
        event_id=event_id, source=source, kind=kind, timeline_at=r(at), strength=strength
    )


def timeline(
    *,
    duration: float = 60.0,
    sections: tuple[TimelineSection, ...] = (),
    speech_spans: tuple[SpeechSpan, ...] = (),
    events: tuple[SyncEvent, ...] = (),
    clips: tuple[TimelineClip, ...] | None = None,
    edit: EditRef | None = EDIT,
    **more: object,
) -> SoundtrackTimeline:
    return SoundtrackTimeline(
        edit=edit,
        project_id=None if edit is None else "project-1",
        frame_rate=FPS,
        clips=clips if clips is not None else (clip("c1", 0.0, duration),),
        sections=sections,
        speech=speech_spans,
        events=events,
        **more,  # type: ignore[arg-type]
    )


def write_tone_wav(
    path: Path,
    *,
    seconds: float = 2.0,
    rate: int = 48_000,
    frequency: float = 440.0,
    clicks_bpm: float | None = None,
) -> Path:
    """A 16-bit stereo WAV: a quiet tone, or a click track at ``clicks_bpm`` (synthesised)."""
    frames = []
    period = None if clicks_bpm is None else 60.0 / clicks_bpm
    for i in range(int(seconds * rate)):
        t = i / rate
        if period is None:
            value = 0.25 * math.sin(2 * math.pi * frequency * t)
        else:
            phase = t % period
            accent = 1.0 if int(t / period) % 4 == 0 else 0.6
            value = accent * 0.8 * math.sin(2 * math.pi * 1000 * phase) * math.exp(-phase * 60)
        sample = struct.pack("<h", int(32767 * max(-1.0, min(1.0, value))))
        frames.append(sample * 2)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"".join(frames))
    return path
