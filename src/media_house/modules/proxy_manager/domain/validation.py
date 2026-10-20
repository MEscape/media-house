"""Validation policy: does the finished file keep every promise of its spec?

Pure rules over typed facts; reading the file is an adapter's job. Evidence is explicit:

* ``PACKETS``: every packet of every stream was listed and measured. The file is complete and
  consistent with the spec. This does NOT prove the streams decode.
* ``DECODED``: the file was also decoded to the end without a single decoder complaint.

A verdict never claims more than its evidence, and a check that could not be evaluated makes the
verdict ``UNVERIFIABLE``, never ``VALID``. Tolerances come from the media's own timing (frame
intervals, audio codec frame length), not from fixed seconds.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.proxy_manager.domain.source import SourceFacts, VideoFacts
from media_house.modules.proxy_manager.domain.spec import ResolvedSpec
from media_house.modules.proxy_manager.domain.values import (
    VALIDATOR_VERSION,
    Codec,
    Evidence,
    Verdict,
)

_CODEC_NAMES = {Codec.H264: "h264", Codec.PRORES: "prores"}
#: Two audio codec frames: AAC encoder priming plus one frame of rounding (1024 samples each).
_AUDIO_FRAMES = 2048
_MIN_FILE_BYTES = 1024


@dataclass(frozen=True, slots=True)
class OutputVideo:
    codec: str
    width: int
    height: int
    sample_aspect_ratio: Fraction
    rotation: int
    pixel_format: str | None
    frames: int
    first_pts: float
    end_pts: float
    frame_interval: float


@dataclass(frozen=True, slots=True)
class OutputAudio:
    codec: str
    channels: int
    sample_rate: int
    first_pts: float
    end_pts: float
    language: str | None = None


@dataclass(frozen=True, slots=True)
class OutputFacts:
    size_bytes: int
    video: OutputVideo | None
    audio: tuple[OutputAudio, ...]
    #: Streams that are neither the video nor a mapped audio stream (the spec allows none).
    other_streams: int
    timecode: str | None
    #: Decoder complaints when the file was decoded to the end; ``None`` if it was not decoded.
    decode_errors: int | None


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    #: ``None`` means the check could not be evaluated (missing information).
    passed: bool | None
    detail: str


@dataclass(frozen=True, slots=True)
class ValidationReport:
    verdict: Verdict
    evidence: Evidence
    checks: tuple[Check, ...]
    transformations: tuple[str, ...]
    #: Departures that do not break the proxy's promises but that a user should know about,
    #: e.g. a language tag the container could not carry.
    warnings: tuple[str, ...] = ()
    validator_version: int = VALIDATOR_VERSION

    @property
    def failures(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.passed is False)

    @property
    def unevaluated(self) -> tuple[Check, ...]:
        return tuple(c for c in self.checks if c.passed is None)

    def summary(self) -> str:
        bad = self.failures or self.unevaluated
        return "; ".join(f"{c.name}: {c.detail}" for c in bad)


def _span(first: float, end: float) -> float:
    return end - first


def validate(
    spec: ResolvedSpec,
    source: SourceFacts,
    output: OutputFacts,
    *,
    reserved_bytes: int,
    require_decode: bool,
) -> ValidationReport:
    """Judge ``output`` against ``spec`` and the ``source`` it was made from."""
    src = source.require_video()
    checks: list[Check] = [
        Check(
            "size",
            _MIN_FILE_BYTES <= output.size_bytes <= reserved_bytes,
            f"{output.size_bytes} bytes (allowed {_MIN_FILE_BYTES}-{reserved_bytes})",
        ),
        Check(
            "streams",
            output.video is not None
            and len(output.audio) == len(spec.audio)
            and output.other_streams == 0,
            f"video={'yes' if output.video else 'no'}, audio={len(output.audio)} "
            f"(expected {len(spec.audio)}), other={output.other_streams} (expected 0)",
        ),
    ]
    if output.video is not None:
        checks.extend(_video_checks(spec, src, output.video))
    if output.video is not None and len(output.audio) == len(spec.audio):
        checks.extend(_audio_checks(spec, source, output))
    if source.timecode is not None:
        checks.append(
            Check(
                "timecode",
                output.timecode == source.timecode,
                f"{source.timecode} -> {output.timecode}",
            )
        )
    decoded = output.decode_errors == 0
    if require_decode:
        checks.append(
            Check(
                "decodable",
                None if output.decode_errors is None else decoded,
                "not decoded" if output.decode_errors is None else f"{output.decode_errors} errors",
            )
        )
    elif output.decode_errors:
        checks.append(Check("decodable", False, f"{output.decode_errors} decoder errors"))
    return ValidationReport(
        verdict=_verdict(checks, spec),
        evidence=Evidence.DECODED if decoded else Evidence.PACKETS,
        checks=tuple(checks),
        transformations=spec.transformations,
        warnings=_language_warnings(spec, source, output),
    )


def _language_warnings(
    spec: ResolvedSpec, source: SourceFacts, output: OutputFacts
) -> tuple[str, ...]:
    """Language tags the source declared and the output lost (a muxer limit, not a bad proxy)."""
    by_index = {a.index: a for a in source.audio}
    return tuple(
        f"audio track {position}: language '{origin.language}' was not preserved "
        f"({out.language or 'unset'})"
        for position, (wanted, out) in enumerate(zip(spec.audio, output.audio, strict=False))
        if (origin := by_index[wanted.source_index]).language and out.language != origin.language
    )


def _verdict(checks: list[Check], spec: ResolvedSpec) -> Verdict:
    if any(c.passed is False for c in checks):
        return Verdict.INVALID
    if any(c.passed is None for c in checks):
        return Verdict.UNVERIFIABLE
    return Verdict.VALID_WITH_TRANSFORMS if spec.transformations else Verdict.VALID


def _video_checks(spec: ResolvedSpec, src: VideoFacts, out: OutputVideo) -> list[Check]:
    interval_tolerance = src.frame_interval + out.frame_interval
    rebased = bool(spec.transformations)
    start_limit = 2 * out.frame_interval
    start_ok = (
        out.first_pts <= start_limit
        if rebased
        else abs(out.first_pts - src.first_pts) <= start_limit
    )
    return [
        Check(
            "codec",
            out.codec == _CODEC_NAMES[spec.codec] and out.pixel_format == spec.pixel_format,
            f"{out.codec}/{out.pixel_format}, "
            f"expected {_CODEC_NAMES[spec.codec]}/{spec.pixel_format}",
        ),
        Check(
            "geometry",
            (out.width, out.height) == (spec.width, spec.height)
            and out.sample_aspect_ratio == Fraction(1),
            f"{out.width}x{out.height} sar {out.sample_aspect_ratio}, "
            f"expected {spec.width}x{spec.height} sar 1",
        ),
        Check(
            "rotation", out.rotation == spec.rotation, f"{out.rotation}, expected {spec.rotation}"
        ),
        Check(
            "frame_count",
            out.frames == src.visible_frames,
            f"{src.visible_frames} -> {out.frames}",
        ),
        Check(
            "video_duration",
            abs(_span(out.first_pts, out.end_pts) - _span(src.first_pts, src.end_pts))
            <= interval_tolerance,
            f"{_span(src.first_pts, src.end_pts):.3f}s -> {_span(out.first_pts, out.end_pts):.3f}s "
            f"(tolerance {interval_tolerance:.3f}s)",
        ),
        Check(
            "start_time",
            start_ok,
            f"{src.first_pts:.3f}s -> {out.first_pts:.3f}s"
            + (" (rebased to zero by design)" if rebased else ""),
        ),
    ]


def _audio_checks(spec: ResolvedSpec, source: SourceFacts, output: OutputFacts) -> list[Check]:
    src_video = source.require_video()
    assert output.video is not None  # noqa: S101 - callers check
    by_index = {a.index: a for a in source.audio}
    checks: list[Check] = []
    for position, (wanted, out) in enumerate(zip(spec.audio, output.audio, strict=True)):
        origin = by_index[wanted.source_index]
        tolerance = _AUDIO_FRAMES / wanted.sample_rate + output.video.frame_interval
        offset_in = origin.first_pts - src_video.first_pts
        offset_out = out.first_pts - output.video.first_pts
        checks.append(
            Check(
                f"audio_{position}",
                out.channels == wanted.channels and out.sample_rate == wanted.sample_rate,
                f"{out.channels}ch {out.sample_rate}Hz, expected {wanted.channels}ch "
                f"{wanted.sample_rate}Hz",
            )
        )
        checks.append(
            Check(
                f"audio_{position}_duration",
                abs(_span(out.first_pts, out.end_pts) - _span(origin.first_pts, origin.end_pts))
                <= tolerance,
                f"{_span(origin.first_pts, origin.end_pts):.3f}s -> "
                f"{_span(out.first_pts, out.end_pts):.3f}s (tolerance {tolerance:.3f}s)",
            )
        )
        checks.append(
            Check(
                f"audio_{position}_sync",
                abs(offset_out - offset_in) <= tolerance,
                f"offset to video {offset_in:.3f}s -> {offset_out:.3f}s "
                f"(tolerance {tolerance:.3f}s)",
            )
        )
    return checks
