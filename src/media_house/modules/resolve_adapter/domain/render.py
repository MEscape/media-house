"""Render settings and the checks of a rendered file.

The frame size, rate and duration always come from the timeline; the profile (``config.py``) only
chooses container and codecs. A render job that reports ``Complete`` is not a valid deliverable
until the file is inspected: that comparison is here, over plain facts the application reads from
the inspection module (the adapter probes nothing itself).
"""

from collections.abc import Mapping
from dataclasses import dataclass
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.resolve_adapter.domain.config import RenderProfile
from media_house.modules.resolve_adapter.domain.report import CheckRecord
from media_house.modules.resolve_adapter.domain.values import Area, Verification

type SettingValue = str | int | float | bool


def render_settings(
    profile: RenderProfile,
    *,
    width: int,
    height: int,
    frame_rate: Rational,
    target_dir: str,
    file_name: str,
) -> Mapping[str, SettingValue]:
    """The values for ``Project.SetRenderSettings`` (the whole timeline, source quality)."""
    settings: dict[str, SettingValue] = {
        "SelectAllFrames": True,
        "TargetDir": target_dir,
        "CustomName": file_name,
        "FormatWidth": width,
        "FormatHeight": height,
        "FrameRate": frame_rate.numerator / frame_rate.denominator,
        "ExportVideo": True,
        "ExportAudio": True,
        "AudioCodec": profile.audio_codec,
    }
    if profile.audio_bitrate_kbps is not None:
        settings["AudioBitRate"] = profile.audio_bitrate_kbps
    settings["VideoQuality"] = 0 if profile.video_quality == "auto" else "Best"
    return settings


@dataclass(frozen=True, slots=True)
class OutputExpectation:
    width: int
    height: int
    frame_rate: Rational
    duration_frames: int
    audio_required: bool


@dataclass(frozen=True, slots=True)
class OutputFacts:
    """What the inspection module says about the rendered file."""

    exists: bool
    size_bytes: int
    readable: bool
    video_codec: str | None
    audio_codec: str | None
    width: int | None
    height: int | None
    frame_rate: Rational | None
    duration: Rational | None
    audio_streams: int


def check_output(
    profile: RenderProfile, expect: OutputExpectation, facts: OutputFacts
) -> tuple[CheckRecord, ...]:
    """Every property of the deliverable that can be compared, one record each."""
    checks: list[CheckRecord] = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append(
            CheckRecord(
                Area.RENDER, name, Verification.VERIFIED if ok else Verification.FAILED, detail
            )
        )

    add("file exists", facts.exists, "" if facts.exists else "the output file is missing")
    if not facts.exists:
        return tuple(checks)
    add("file size", facts.size_bytes > 0, f"{facts.size_bytes} bytes")
    add("file can be inspected", facts.readable, "" if facts.readable else "the inspection failed")
    if not facts.readable:
        return tuple(checks)
    add(
        "video codec",
        (facts.video_codec or "").lower() in profile.expected_video_codecs,
        f"{facts.video_codec} (expected {', '.join(profile.expected_video_codecs)})",
    )
    add(
        "resolution",
        (facts.width, facts.height) == (expect.width, expect.height),
        f"{facts.width}x{facts.height} (expected {expect.width}x{expect.height})",
    )
    add(
        "frame rate",
        facts.frame_rate == expect.frame_rate,
        f"{facts.frame_rate} (expected {expect.frame_rate})",
    )
    if facts.duration is not None and facts.frame_rate is not None:
        wanted = Fraction(expect.duration_frames) / Fraction(
            expect.frame_rate.numerator, expect.frame_rate.denominator
        )
        found = Fraction(facts.duration.numerator, facts.duration.denominator)
        frame = Fraction(expect.frame_rate.denominator, expect.frame_rate.numerator)
        add(
            "duration",
            abs(found - wanted) <= 2 * frame,
            f"{float(found):.3f}s (expected {float(wanted):.3f}s)",
        )
    else:
        checks.append(
            CheckRecord(
                Area.RENDER,
                "duration",
                Verification.NOT_VERIFIABLE,
                "the inspection has no duration",
            )
        )
    if expect.audio_required:
        add("audio stream", facts.audio_streams > 0, f"{facts.audio_streams} audio stream(s)")
        if facts.audio_streams > 0:
            add(
                "audio codec",
                (facts.audio_codec or "").lower() in profile.expected_audio_codecs,
                f"{facts.audio_codec} (expected {', '.join(profile.expected_audio_codecs)})",
            )
    return tuple(checks)
