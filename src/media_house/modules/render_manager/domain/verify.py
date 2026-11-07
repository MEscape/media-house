"""Looking at a finished render: does the file hold what the request promised?

Pure rules over facts the application read (from the Media Library's inspection of the file). A
render job that says "complete" proves nothing; these rules say what the file shows. They do not
replace ``export_validation``, which independently decodes and measures the delivered file.
"""

from dataclasses import dataclass

from media_house.core.domain import Rational
from media_house.modules.render_manager.domain.values import CheckState, Verification

#: A file smaller than this is not a render of anything.
MIN_BYTES = 1024
#: Below this many bytes per second of programme a video file is suspiciously empty.
MIN_BYTES_PER_SECOND = 2000
FRAME_RATE_TOLERANCE = 0.01
#: The check that stands for "the file was looked at"; without it nothing is verified.
INSPECTED = "output inspected"


@dataclass(frozen=True, slots=True)
class OutputFacts:
    exists: bool
    readable: bool
    size_bytes: int
    extension: str
    duration: float | None
    has_video: bool
    has_audio: bool
    width: int | None
    height: int | None
    frame_rate: Rational | None
    video_codec: str | None
    pixel_format: str | None
    audio_codec: str | None
    audio_duration: float | None
    sample_rate: int | None
    channels: int | None
    #: The inspection's own verdict was an error (decode problems, damaged container).
    inspection_error: bool = False
    #: The file could be inspected at all (``False``: the inspection was unavailable).
    inspected: bool = True


@dataclass(frozen=True, slots=True)
class Expectation:
    width: int
    height: int
    frame_rate: Rational
    #: The programme length in seconds (the timeline, or the requested range with its padding).
    duration: float
    video_codec: str
    container: str
    pixel_format: str
    audio_required: bool
    audio_codec: str
    sample_rate: int
    channels: int
    #: Settings the backend could not apply (so the check is not held against the file).
    not_applied: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    state: CheckState
    detail: str = ""


def _check(name: str, ok: bool, detail: str, *, warn: bool = False) -> Check:
    if ok:
        return Check(name, CheckState.PASSED, detail)
    return Check(name, CheckState.WARNING if warn else CheckState.FAILED, detail)


def _same_rate(a: Rational, b: Rational) -> bool:
    return abs(a.value - b.value) <= FRAME_RATE_TOLERANCE * b.value


def verify_output(facts: OutputFacts, exp: Expectation) -> tuple[Check, ...]:
    """Every check, with what was seen; a check that cannot be made says so."""
    if not facts.exists:
        return (Check("output exists", CheckState.FAILED, "there is no output file"),)
    if not facts.inspected:
        return (
            Check("output exists", CheckState.PASSED, f"{facts.size_bytes} bytes"),
            Check(
                INSPECTED,
                CheckState.NOT_CHECKED,
                "the file could not be inspected, so nothing about it is confirmed",
            ),
        )
    readable = facts.readable and not facts.inspection_error
    checks = [
        _check(
            "output readable",
            readable,
            "the file was read and inspected" if readable else "the file could not be read cleanly",
        ),
        _size_check(facts, exp),
        _check(
            "video stream",
            facts.has_video,
            "a video stream is present" if facts.has_video else "no video stream",
        ),
    ]
    if exp.audio_required:
        checks.append(
            _check(
                "audio stream",
                facts.has_audio,
                "an audio stream is present"
                if facts.has_audio
                else "the file has no sound but the timeline does",
            )
        )
    tolerance = max(0.1, 2.0 / exp.frame_rate.value)
    if facts.duration is None:
        checks.append(Check("duration", CheckState.NOT_CHECKED, "the file states no duration"))
    else:
        checks.append(
            _check(
                "duration",
                abs(facts.duration - exp.duration) <= tolerance,
                f"{facts.duration:.3f} s, expected {exp.duration:.3f} s (±{tolerance:.3f})",
            )
        )
    if (
        exp.audio_required
        and facts.has_audio
        and facts.audio_duration is not None
        and facts.duration
    ):
        checks.append(
            _check(
                "audio duration",
                abs(facts.audio_duration - facts.duration) <= tolerance,
                f"audio {facts.audio_duration:.3f} s, picture {facts.duration:.3f} s",
            )
        )
    if facts.width is not None:
        ok = (facts.width, facts.height) == (exp.width, exp.height)
        checks.append(_resolution(ok, facts, exp))
    if facts.frame_rate is not None:
        checks.append(
            _check(
                "frame rate",
                _same_rate(facts.frame_rate, exp.frame_rate),
                f"{facts.frame_rate} (expected {exp.frame_rate})",
            )
        )
    checks.append(_codec(facts, exp))
    if facts.pixel_format is not None and "pixel_format" not in exp.not_applied:
        checks.append(
            _check(
                "pixel format",
                facts.pixel_format == exp.pixel_format,
                f"{facts.pixel_format} (profile: {exp.pixel_format})",
                warn=True,
            )
        )
    extension = facts.extension.lstrip(".").lower()
    checks.append(
        _check("container", extension == exp.container, f".{extension} (profile: .{exp.container})")
    )
    if (
        exp.audio_required
        and facts.audio_codec is not None
        and "audio_codec" not in exp.not_applied
    ):
        checks.append(
            _check(
                "audio codec",
                facts.audio_codec == exp.audio_codec,
                f"{facts.audio_codec} (profile: {exp.audio_codec})",
                warn=True,
            )
        )
    return tuple(checks)


def _size_check(facts: OutputFacts, exp: Expectation) -> Check:
    if facts.size_bytes < MIN_BYTES:
        return Check("size plausible", CheckState.FAILED, f"only {facts.size_bytes} bytes")
    per_second = facts.size_bytes / max(exp.duration, 0.001)
    if per_second < MIN_BYTES_PER_SECOND:
        return Check(
            "size plausible",
            CheckState.WARNING,
            f"{facts.size_bytes} bytes for {exp.duration:.2f} s is very little",
        )
    return Check("size plausible", CheckState.PASSED, f"{facts.size_bytes} bytes")


def _resolution(ok: bool, facts: OutputFacts, exp: Expectation) -> Check:
    return _check(
        "resolution", ok, f"{facts.width}x{facts.height} (expected {exp.width}x{exp.height})"
    )


def _codec(facts: OutputFacts, exp: Expectation) -> Check:
    if facts.video_codec is None:
        return Check("video codec", CheckState.NOT_CHECKED, "the codec is unknown")
    # A backend that cannot choose the codec says so; then a different one is a warning only.
    return _check(
        "video codec",
        facts.video_codec == exp.video_codec,
        f"{facts.video_codec} (profile: {exp.video_codec})",
        warn="codec" in exp.not_applied,
    )


def overall(checks: tuple[Check, ...]) -> Verification:
    if any(c.state is CheckState.FAILED for c in checks):
        return Verification.FAILED
    if any(c.name == INSPECTED and c.state is CheckState.NOT_CHECKED for c in checks):
        return Verification.NOT_VERIFIED
    if any(c.state in (CheckState.WARNING, CheckState.NOT_CHECKED) for c in checks):
        return Verification.WITH_WARNINGS
    return Verification.VERIFIED
