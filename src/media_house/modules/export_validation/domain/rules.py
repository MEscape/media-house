"""The rules: what makes a delivered file acceptable, and what to do when it is not.

Pure functions over ``FileFacts`` (what the file shows), ``Expected`` (what the approved timeline
and the delivery's render profile require), ``Lineage`` (which render and which timeline version
made it) and the plan's declared intentional stretches. Every problem is a ``Finding`` with its
severity, how sure it is, the module that owns the fix and the next action; a check that could not
be made is ``NotChecked`` and keeps the verdict from being a PASS.

The verdict is deterministic:

* FAIL                    a CRITICAL finding, or a HIGH one that is CONFIRMED
* MANUAL_REVIEW_REQUIRED  else, anything that needs a person (a finding that says so, a HIGH one
                          that is not confirmed, a POSSIBLE one of MEDIUM or above, a mandatory
                          check that was not made)
* WARNING                 else, any finding at all (MEDIUM, LOW)
* PASS                    nothing found and everything mandatory was checked
"""

import re
from dataclasses import dataclass

from media_house.modules.export_validation.domain.facts import (
    AudioFacts,
    Expected,
    FileFacts,
    Intentional,
    Lineage,
    Span,
    VideoFacts,
)
from media_house.modules.export_validation.domain.profile import DeliveryProfile
from media_house.modules.export_validation.domain.report import (
    AreaStatus,
    Finding,
    NotChecked,
    ValidationCheck,
)
from media_house.modules.export_validation.domain.values import (
    Category,
    Certainty,
    CheckState,
    NextAction,
    Route,
    Severity,
    Status,
)

C = Category
Sev = Severity
Cert = Certainty
_ORDER = {Sev.CRITICAL: 4, Sev.HIGH: 3, Sev.MEDIUM: 2, Sev.LOW: 1, Sev.INFO: 0}
#: A stretch is "declared" when it lies inside what the plan declared, give or take this.
_DECLARED_SLACK = 0.25
_RATE_TOLERANCE = 0.01
_EDGE_SECONDS = 0.1
_SYNC_TOLERANCE = 0.04


@dataclass(frozen=True, slots=True)
class Evaluation:
    checks: tuple[ValidationCheck, ...]
    findings: tuple[Finding, ...]
    not_checked: tuple[NotChecked, ...]
    status: Status
    severity: Severity
    next_action: NextAction
    area_status: tuple[AreaStatus, ...]


class _Run:
    def __init__(self) -> None:
        self.checks: list[ValidationCheck] = []
        self.findings: list[Finding] = []
        self.unchecked: list[NotChecked] = []

    def passed(self, category: Category, name: str, detail: str = "") -> None:
        self.checks.append(ValidationCheck(category, name, CheckState.PASSED, detail))

    def problem(self, finding: Finding, name: str = "") -> None:
        self.findings.append(finding)
        serious = finding.severity in (Sev.CRITICAL, Sev.HIGH) and not finding.needs_review
        self.checks.append(
            ValidationCheck(
                finding.category,
                name or finding.code,
                CheckState.FAILED if serious else CheckState.WARNING,
                finding.message,
            )
        )

    def skip(self, category: Category, name: str, reason: str, *, mandatory: bool = True) -> None:
        self.unchecked.append(NotChecked(category, name, reason, mandatory))
        self.checks.append(ValidationCheck(category, name, CheckState.NOT_CHECKED, reason))


def _finding(
    code: str,
    category: Category,
    severity: Severity,
    certainty: Certainty,
    message: str,
    route: Route,
    action: NextAction,
    advice: str,
    *,
    evidence: tuple[tuple[str, str], ...] = (),
    span: Span | None = None,
    review: bool = False,
) -> Finding:
    return Finding(
        code, category, severity, certainty, message, route, action, advice, evidence,
        span.start if span else None, (span.end if span else None), review,
    )  # fmt: skip


def _near(a: float, b: float, tolerance: float) -> bool:
    return abs(a - b) <= tolerance


def _rate_matches(a: float, b: float) -> bool:
    return abs(a - b) <= _RATE_TOLERANCE * b


def _declared(span: Span, kind: str, intentional: tuple[Intentional, ...], duration: float) -> bool:
    end = span.end if span.end is not None else duration
    return any(
        i.kind == kind
        and i.start - _DECLARED_SLACK <= span.start
        and end <= i.end + _DECLARED_SLACK
        for i in intentional
    )


def _text(value: float | None, unit: str = "") -> str:
    return "unknown" if value is None else f"{value:.3f}{unit}"


# --- integrity -------------------------------------------------------------------------------


def _integrity(run: _Run, f: FileFacts) -> None:
    if not f.exists:
        run.problem(
            _finding(
                "file.missing", C.INTEGRITY, Sev.CRITICAL, Cert.CONFIRMED,
                "the exported file does not exist", Route.RENDER_MANAGER, NextAction.REEXPORT,
                "export again; nothing can be delivered",
            ),
            "file exists",
        )  # fmt: skip
        return
    run.passed(C.INTEGRITY, "file exists", f"{f.size_bytes} bytes")
    if f.size_bytes < 1024:
        run.problem(
            _finding(
                "file.empty", C.INTEGRITY, Sev.CRITICAL, Cert.CONFIRMED,
                f"the file is only {f.size_bytes} bytes", Route.RENDER_MANAGER,
                NextAction.RERENDER, "render again; the output is not a video",
            ),
            "file size",
        )  # fmt: skip
    if not f.inspected:
        run.skip(
            C.INTEGRITY, "container readable", "the streams of the file could not be inspected"
        )
    elif not f.readable or f.inspection_error:
        run.problem(
            _finding(
                "file.unreadable", C.INTEGRITY, Sev.CRITICAL, Cert.CONFIRMED,
                "the container cannot be read cleanly", Route.RENDER_MANAGER, NextAction.RERENDER,
                "render again; if it repeats, the backend writes a damaged file",
            ),
            "container readable",
        )  # fmt: skip
    else:
        run.passed(C.INTEGRITY, "container readable", "headers and streams were read")
    _decode(run, f)
    if f.duration is None:
        run.skip(C.INTEGRITY, "duration stated", "the file states no duration")


def _decode(run: _Run, f: FileFacts) -> None:
    """A container probe is not a decode: only a decode to the end shows the file plays."""
    if f.decode is None:
        run.skip(C.INTEGRITY, "complete decode", "the file was not decoded to the end")
        return
    if not f.decode.complete:
        run.problem(
            _finding(
                "file.decode_failed", C.INTEGRITY, Sev.CRITICAL, Cert.CONFIRMED,
                "the file does not decode to the end", Route.RENDER_MANAGER, NextAction.RERENDER,
                "render again; the decoder reported errors",
                evidence=tuple((f"error {n + 1}", t) for n, t in enumerate(f.decode.errors[:5])),
            ),
            "complete decode",
        )  # fmt: skip
        return
    until = f.decode.decoded_until
    if until is not None and f.duration and until < f.duration - 0.5:
        run.problem(
            _finding(
                "file.truncated", C.INTEGRITY, Sev.HIGH, Cert.CONFIRMED,
                f"the file states {f.duration:.2f} s but decoding ended at {until:.2f} s",
                Route.RENDER_MANAGER, NextAction.RERENDER, "render again; the file is incomplete",
            ),
            "complete decode",
        )  # fmt: skip
        return
    run.passed(C.INTEGRITY, "complete decode", f"{f.decode.frames_decoded} frames decoded")


# --- video -----------------------------------------------------------------------------------

_RENDER = (Route.RENDER_MANAGER, NextAction.RERENDER)


def _video(
    run: _Run, f: FileFacts, e: Expected, p: DeliveryProfile, intentional: tuple[Intentional, ...]
) -> None:
    v = f.video
    if not f.inspected:
        run.skip(C.VIDEO, "video stream facts", "the streams of the file could not be inspected")
        _black_and_frozen(run, f, e, intentional)
        return
    if v is None:
        run.problem(
            _finding(
                "video.missing", C.VIDEO, Sev.CRITICAL, Cert.CONFIRMED,
                "the file has no video stream", *_RENDER, "render again",
            ),
            "video stream",
        )  # fmt: skip
        return
    run.passed(C.VIDEO, "video stream", f"{v.codec} {v.width}x{v.height}")
    if (v.width, v.height) != (e.width, e.height):
        run.problem(
            _finding(
                "video.resolution", C.VIDEO, Sev.HIGH, Cert.CONFIRMED,
                f"resolution {v.width}x{v.height}, expected {e.width}x{e.height}", *_RENDER,
                "render again with the delivery profile's frame size",
            ),
            "resolution",
        )  # fmt: skip
    else:
        run.passed(C.VIDEO, "resolution", f"{v.width}x{v.height}")
    rate = v.frame_rate.value if v.frame_rate else None
    if rate is not None and not _rate_matches(rate, e.frame_rate.value):
        run.problem(
            _finding(
                "video.frame_rate", C.VIDEO, Sev.HIGH, Cert.CONFIRMED,
                f"frame rate {rate:.3f}, expected {e.frame_rate.value:.3f}", *_RENDER,
                "render again at the timeline's frame rate",
            ),
            "frame rate",
        )  # fmt: skip
    elif rate is not None:
        run.passed(C.VIDEO, "frame rate", f"{rate:.3f}")
    _temporal(run, v, rate, e)
    if v.codec != e.video_codec:
        run.problem(
            _finding(
                "video.codec", C.VIDEO, Sev.HIGH, Cert.CONFIRMED,
                f"video codec {v.codec}, the delivery wants {e.video_codec}", *_RENDER,
                "render again with the delivery profile's codec",
            ),
            "codec",
        )  # fmt: skip
    else:
        run.passed(C.VIDEO, "codec", v.codec)
    if v.pixel_format is not None and v.pixel_format != e.pixel_format:
        run.problem(
            _finding(
                "video.pixel_format", C.VIDEO, Sev.MEDIUM, Cert.CONFIRMED,
                f"pixel format {v.pixel_format}, the delivery wants {e.pixel_format}", *_RENDER,
                "render again with the delivery profile's pixel format",
            ),
            "pixel format",
        )  # fmt: skip
    _aspect(run, v, e)
    if not v.color_tags:
        run.problem(
            _finding(
                "video.color_untagged", C.VIDEO, Sev.LOW, Cert.POSSIBLE,
                "the picture carries no colour space tags", *_RENDER,
                "players may show the colours differently; set the output colour space",
            ),
            "colour metadata",
        )  # fmt: skip
    _black_and_frozen(run, f, e, intentional)


def _temporal(run: _Run, v: VideoFacts, rate: float | None, e: Expected) -> None:
    average = v.average_frame_rate.value if v.average_frame_rate else None
    if rate and average and not _rate_matches(average, rate):
        run.problem(
            _finding(
                "video.variable_rate", C.VIDEO, Sev.MEDIUM, Cert.LIKELY,
                f"the declared rate is {rate:.3f} but the frames average {average:.3f}", *_RENDER,
                "the temporal behaviour is not the declared one; render at a constant rate",
            ),
            "temporal behaviour",
        )  # fmt: skip
    if v.dropped_frames:
        run.problem(
            _finding(
                "video.dropped_frames", C.VIDEO, Sev.HIGH, Cert.CONFIRMED,
                f"{v.dropped_frames} frames are missing from the stream", *_RENDER,
                "render again; frames were dropped while rendering",
            ),
            "dropped frames",
        )  # fmt: skip
    if v.discontinuities:
        run.problem(
            _finding(
                "video.discontinuities", C.VIDEO, Sev.HIGH, Cert.CONFIRMED,
                f"{v.discontinuities} holes in the picture's timestamps", *_RENDER,
                "render again; the picture has gaps in time",
            ),
            "continuous time",
        )  # fmt: skip
    if v.duplicate_timestamps:
        run.problem(
            _finding(
                "video.duplicate_frames", C.VIDEO, Sev.MEDIUM, Cert.LIKELY,
                f"{v.duplicate_timestamps} frames share a timestamp", *_RENDER,
                "render again at a constant frame rate",
            ),
            "duplicate frames",
        )  # fmt: skip
    if v.declared_frames is not None and v.duration:
        wanted = round(v.duration * e.frame_rate.value)
        if abs(v.declared_frames - wanted) > 2:
            run.problem(
                _finding(
                    "video.frame_count", C.VIDEO, Sev.MEDIUM, Cert.LIKELY,
                    f"{v.declared_frames} frames for {v.duration:.2f} s (about {wanted} expected)",
                    *_RENDER, "the frame count does not fit the duration; render again",
                ),
                "frame count",
            )  # fmt: skip


def _aspect(run: _Run, v: VideoFacts, e: Expected) -> None:
    if e.aspect is None:
        return
    wanted = e.aspect[0] / e.aspect[1]
    shown = v.display_aspect.value if v.display_aspect else v.width / v.height
    if abs(shown - wanted) > 0.02 * wanted:
        run.problem(
            _finding(
                "video.aspect", C.VIDEO, Sev.HIGH, Cert.CONFIRMED,
                f"the picture is shown at {shown:.3f}:1, "
                f"the delivery is {e.aspect[0]}:{e.aspect[1]}",
                *_RENDER, "render again with the delivery profile's frame size",
            ),
            "aspect ratio",
        )  # fmt: skip
    else:
        run.passed(C.VIDEO, "aspect ratio", f"{shown:.3f}:1")


def _overlaps(a: Span, spans: tuple[Span, ...] | None, duration: float) -> bool:
    end = a.end if a.end is not None else duration
    return any(
        s.start < end and (s.end if s.end is not None else duration) > a.start for s in spans or ()
    )


def _black_and_frozen(
    run: _Run, f: FileFacts, e: Expected, intentional: tuple[Intentional, ...]
) -> None:
    duration = f.duration or e.duration_seconds
    if f.black is None:
        run.skip(C.VIDEO, "black frames", "black frames were not measured")
    else:
        unexpected = [s for s in f.black if not _declared(s, "black", intentional, duration)]
        for span in unexpected:
            edge = (
                span.start <= _EDGE_SECONDS
                or span.end is None
                or span.end >= duration - _EDGE_SECONDS
            )
            where = "at the start or the end" if edge else "inside the video"
            run.problem(
                _finding(
                    "video.black_edge" if edge else "video.black_frames",
                    C.VIDEO,
                    Sev.MEDIUM if edge else Sev.HIGH,
                    Cert.LIKELY if edge else Cert.CONFIRMED,
                    f"black picture {where}, from {span.start:.2f} s to {_text(span.end, ' s')}",
                    Route.QUALITY_CONTROL,
                    NextAction.INVESTIGATE,
                    "look at the timeline at this time first: a gap or a missing clip is a "
                    "revision, a black render of a good timeline is a new render",
                    span=span,
                ),
                "black frames",
            )
        if not unexpected:
            run.passed(
                C.VIDEO,
                "black frames",
                "none" if not f.black else f"{len(f.black)} stretch(es), all declared by the plan",
            )
    if f.frozen is None:
        run.skip(C.VIDEO, "frozen frames", "frozen frames were not measured", mandatory=False)
        return
    for span in f.frozen:
        if _declared(span, "freeze", intentional, duration) or _overlaps(span, f.black, duration):
            continue  # a plan-declared hold, or black (already judged above)
        run.problem(
            _finding(
                "video.frozen", C.VIDEO, Sev.MEDIUM, Cert.LIKELY,
                f"the picture does not change from {span.start:.2f} s to {_text(span.end, ' s')}",
                Route.QUALITY_CONTROL, NextAction.INVESTIGATE,
                "a held title is fine (declare it); a frozen shot is a problem of the timeline "
                "or of the render",
                span=span,
            ),
            "frozen frames",
        )  # fmt: skip


# --- audio -----------------------------------------------------------------------------------


def _audio(
    run: _Run, f: FileFacts, e: Expected, p: DeliveryProfile, intentional: tuple[Intentional, ...]
) -> None:
    a = f.audio
    if not f.inspected:
        run.skip(C.AUDIO, "audio stream facts", "the streams of the file could not be inspected")
        if e.has_audio and f.loudness is not None and not f.loudness.has_audio:
            _missing_audio(run)
        else:
            _loudness(run, f, p)
            _silence(run, f, e, intentional)
        return
    if a is None:
        if e.has_audio:
            _missing_audio(run)
        else:
            run.passed(C.AUDIO, "audio stream", "the timeline has no sound and the file has none")
        return
    run.passed(C.AUDIO, "audio stream", f"{a.codec} {a.sample_rate} Hz, {a.channels} ch")
    _audio_format(run, a, e)
    _audio_time(run, a, f)
    _loudness(run, f, p)
    _silence(run, f, e, intentional)


def _missing_audio(run: _Run) -> None:
    run.problem(
        _finding(
            "audio.missing", C.AUDIO, Sev.CRITICAL, Cert.CONFIRMED,
            "the timeline has sound but the file has no audio stream", *_RENDER,
            "render again with the audio enabled",
        ),
        "audio stream",
    )  # fmt: skip


def _audio_format(run: _Run, a: AudioFacts, e: Expected) -> None:
    for code, label, got, want in (
        ("audio.codec", "audio codec", a.codec, e.audio_codec),
        ("audio.sample_rate", "sample rate", a.sample_rate, e.sample_rate),
        ("audio.channels", "channel count", a.channels, e.channels),
    ):
        if got is not None and got != want:
            run.problem(
                _finding(
                    code, C.AUDIO, Sev.HIGH, Cert.CONFIRMED,
                    f"{label} is {got}, the delivery wants {want}",
                    *_RENDER, "render again with the delivery profile's audio settings",
                ),
                label,
            )  # fmt: skip
        elif got is not None:
            run.passed(C.AUDIO, label, str(got))


def _audio_time(run: _Run, a: AudioFacts, f: FileFacts) -> None:
    if a.duration is not None and f.duration:
        if abs(a.duration - f.duration) > 0.1:
            shorter = a.duration < f.duration
            run.problem(
                _finding(
                    "audio.duration_mismatch", C.AUDIO, Sev.HIGH, Cert.CONFIRMED,
                    f"audio lasts {a.duration:.2f} s, the picture {f.duration:.2f} s"
                    + (" (the sound ends early: an abrupt cut)" if shorter else ""),
                    *_RENDER, "render again; the sound and the picture are not the same length",
                ),
                "audio duration",
            )  # fmt: skip
        else:
            run.passed(C.AUDIO, "audio duration", f"{a.duration:.2f} s")
    if a.start_offset is not None and abs(a.start_offset) > _SYNC_TOLERANCE:
        run.problem(
            _finding(
                "audio.sync", C.AUDIO, Sev.HIGH, Cert.CONFIRMED,
                f"the sound starts {a.start_offset * 1000:+.0f} ms against the picture", *_RENDER,
                "render again; the sound is out of step with the picture",
            ),
            "audio/video sync",
        )  # fmt: skip
    elif a.start_offset is not None:
        run.passed(C.AUDIO, "audio/video sync", f"{a.start_offset * 1000:+.0f} ms")


def _loudness(run: _Run, f: FileFacts, p: DeliveryProfile) -> None:
    ln = f.loudness
    if ln is None or not ln.has_audio:
        run.skip(C.AUDIO, "loudness", "the loudness of the file was not measured")
        return
    mix = (Route.AUDIO_MIX_ENGINE, NextAction.REMIX_AUDIO)
    if ln.integrated_lufs is None:
        run.problem(
            _finding(
                "audio.silent_programme", C.AUDIO, Sev.HIGH, Cert.LIKELY,
                "the sound never rises above the measurable level", *mix,
                "the mix is silent or nearly so; check the mix before exporting again",
            ),
            "loudness",
        )  # fmt: skip
    elif p.loudness_target_lufs is not None:
        off = ln.integrated_lufs - p.loudness_target_lufs
        evidence = (
            ("integrated", f"{ln.integrated_lufs:.1f} LUFS"),
            ("target", f"{p.loudness_target_lufs:.1f} LUFS"),
        )
        if abs(off) > max(3.0, 3 * p.loudness_tolerance_lu):
            run.problem(
                _finding(
                    "audio.loudness_far", C.AUDIO, Sev.HIGH, Cert.CONFIRMED,
                    f"loudness {ln.integrated_lufs:.1f} LUFS is {off:+.1f} LU from the target",
                    *mix,
                    "remix to the delivery loudness (the mix engine re-renders only the mix)",
                    evidence=evidence,
                ),
                "loudness",
            )  # fmt: skip
        elif abs(off) > p.loudness_tolerance_lu:
            run.problem(
                _finding(
                    "audio.loudness", C.AUDIO, Sev.MEDIUM, Cert.CONFIRMED,
                    f"loudness {ln.integrated_lufs:.1f} LUFS is {off:+.1f} LU from the target",
                    *mix, "the loudness is outside the tolerance; remix if it matters",
                    evidence=evidence,
                ),
                "loudness",
            )  # fmt: skip
        else:
            run.passed(C.AUDIO, "loudness", f"{ln.integrated_lufs:.1f} LUFS")
    if p.true_peak_max_dbtp is not None and ln.true_peak_dbtp is not None:
        if ln.true_peak_dbtp > p.true_peak_max_dbtp:
            run.problem(
                _finding(
                    "audio.true_peak", C.AUDIO, Sev.HIGH, Cert.CONFIRMED,
                    f"true peak {ln.true_peak_dbtp:.1f} dBTP is over {p.true_peak_max_dbtp:.1f}",
                    *mix,
                    "the file may clip when the platform converts it; lower the master level",
                ),
                "true peak",
            )  # fmt: skip
        else:
            run.passed(C.AUDIO, "true peak", f"{ln.true_peak_dbtp:.1f} dBTP")
    if ln.sample_peak_dbfs is not None and ln.sample_peak_dbfs >= -0.1:
        run.problem(
            _finding(
                "audio.clipping", C.AUDIO, Sev.MEDIUM, Cert.LIKELY,
                f"the sample peak is {ln.sample_peak_dbfs:.1f} dBFS: the sound probably clips",
                *mix,
                "lower the master level",
            ),
            "clipping",
        )  # fmt: skip
    if (
        p.loudness_range_max_lu is not None
        and ln.loudness_range_lu is not None
        and ln.loudness_range_lu > p.loudness_range_max_lu
    ):
        run.problem(
            _finding(
                "audio.loudness_range", C.AUDIO, Sev.MEDIUM, Cert.CONFIRMED,
                f"loudness range {ln.loudness_range_lu:.1f} LU "
                f"is over {p.loudness_range_max_lu:.1f}",
                *mix, "the mix is more dynamic than the delivery allows",
            ),
            "loudness range",
        )  # fmt: skip


def _silence(run: _Run, f: FileFacts, e: Expected, intentional: tuple[Intentional, ...]) -> None:
    if f.silent is None:
        run.skip(C.AUDIO, "silence", "silence was not measured", mandatory=False)
        return
    duration = f.duration or e.duration_seconds
    unexpected = [s for s in f.silent if not _declared(s, "silence", intentional, duration)]
    for span in unexpected:
        length = (span.end if span.end is not None else duration) - span.start
        mostly = length > 0.5 * duration
        edge = span.start <= _EDGE_SECONDS or span.end is None
        run.problem(
            _finding(
                "audio.mostly_silent" if mostly else "audio.silence",
                C.AUDIO,
                Sev.HIGH if mostly else Sev.MEDIUM,
                Cert.CONFIRMED if mostly else Cert.LIKELY,
                f"silence from {span.start:.2f} s to {_text(span.end, ' s')}"
                + (" (at the start or the end)" if edge else ""),
                Route.QUALITY_CONTROL,
                NextAction.INVESTIGATE,
                "declare it if it is intended; otherwise look for missing sound in the mix or "
                "the timeline",
                span=span,
            ),
            "silence",
        )
    if not unexpected:
        run.passed(C.AUDIO, "silence", "none unexpected")


# --- the picture compared with the approved review render ------------------------------------


def _visual(run: _Run, f: FileFacts, p: DeliveryProfile) -> None:
    if f.frames is None or not f.reference_used:
        run.skip(
            C.VISUAL,
            "pictures compared with the approved review render",
            "there is no approved review render to compare the exported pictures with",
        )
        return
    run.skip(
        C.VISUAL,
        "text clipping, safe areas, placeholders",
        "these cannot be judged from sampled pictures here; the review in Resolve covers them",
        mandatory=False,
    )
    compared = [s for s in f.frames if s.similarity is not None]
    if not compared:
        run.skip(C.VISUAL, "pictures compared", "no sampled picture could be decoded")
        return
    differing = False
    for sample in compared:
        similarity = sample.similarity or 0.0
        if similarity >= p.similarity_min:
            continue
        differing = True
        kind = {"opening": "opening", "ending": "ending"}.get(sample.kind, "section")
        run.problem(
            _finding(
                f"timeline.{kind}_differs" if kind != "section" else "timeline.section_differs",
                C.TIMELINE,
                Sev.HIGH,
                Cert.LIKELY,
                f"the {sample.label} at {sample.at_seconds:.2f} s does not look like the approved "
                f"review render (similarity {similarity:.2f})",
                Route.RESOLVE_ADAPTER,
                NextAction.INVESTIGATE,
                "compare in Resolve: if the timeline is as approved the render differs "
                "(render again); if the timeline changed, it is a revision",
                evidence=(("similarity", f"{similarity:.3f}"), ("section", sample.section_id)),
                span=Span(sample.at_seconds, sample.at_seconds),
            ),
            f"{sample.label} matches the review render",
        )
    if not differing:
        run.passed(C.VISUAL, "pictures compared with the approved review render",
                   f"{len(compared)} moments match")  # fmt: skip
    if f.unreadable_frames:
        run.problem(
            _finding(
                "visual.frames_unreadable", C.VISUAL, Sev.MEDIUM, Cert.POSSIBLE,
                f"{f.unreadable_frames} sampled moments could not be decoded", *_RENDER,
                "the file may be damaged there; decode check and look",
                review=True,
            ),
            "sampled pictures",
        )  # fmt: skip


# --- delivery --------------------------------------------------------------------------------


def _delivery(run: _Run, f: FileFacts, e: Expected, p: DeliveryProfile) -> None:
    if f.extension.lstrip(".").lower() != e.container:
        run.problem(
            _finding(
                "delivery.container", C.DELIVERY, Sev.HIGH, Cert.CONFIRMED,
                f"the container is .{f.extension.lstrip('.')}, the delivery wants .{e.container}",
                *_RENDER, "render again with the delivery profile's container",
            ),
            "container",
        )  # fmt: skip
    else:
        run.passed(C.DELIVERY, "container", f".{e.container}")
    if p.max_duration_seconds is not None and f.duration is not None:
        if f.duration > p.max_duration_seconds:
            run.problem(
                _finding(
                    "delivery.too_long", C.DELIVERY, Sev.HIGH, Cert.CONFIRMED,
                    f"{f.duration:.1f} s is longer than the {p.max_duration_seconds:.0f} s this "
                    "delivery allows", Route.REVISION_ENGINE, NextAction.REVISE_TIMELINE,
                    "the timeline is too long for this delivery; shorten it or choose another",
                ),
                "maximum duration",
            )  # fmt: skip
        else:
            run.passed(C.DELIVERY, "maximum duration", f"{f.duration:.1f} s")
    if p.max_size_bytes is not None and f.size_bytes > p.max_size_bytes:
        run.problem(
            _finding(
                "delivery.too_large", C.DELIVERY, Sev.HIGH, Cert.CONFIRMED,
                f"{f.size_bytes} bytes is over the {p.max_size_bytes} this delivery allows",
                *_RENDER,
                "render again at a lower quality setting",
            ),
            "maximum size",
        )  # fmt: skip
    tags = {k.lower() for k, _ in f.tags}
    missing = [m for m in p.required_metadata if m.lower() not in tags]
    if missing:
        run.problem(
            _finding(
                "delivery.metadata", C.DELIVERY, Sev.MEDIUM, Cert.CONFIRMED,
                "required metadata is missing: " + ", ".join(missing), *_RENDER,
                "render again with the metadata set",
            ),
            "required metadata",
        )  # fmt: skip
    elif p.required_metadata:
        run.passed(C.DELIVERY, "required metadata", ", ".join(p.required_metadata))
    if p.name_pattern is not None:
        if re.fullmatch(p.name_pattern, f.file_name):
            run.passed(C.DELIVERY, "file name", f.file_name)
        else:
            run.problem(
                _finding(
                    "delivery.name", C.DELIVERY, Sev.MEDIUM, Cert.CONFIRMED,
                    f"the file name '{f.file_name}' does not follow the delivery's naming rule",
                    *_RENDER, "name the output after the delivery's rule",
                ),
                "file name",
            )  # fmt: skip


# --- which timeline the file is of -----------------------------------------------------------


def _timeline(run: _Run, f: FileFacts, e: Expected, lin: Lineage) -> None:
    if not lin.render_found:
        run.problem(
            _finding(
                "lineage.unknown_render", C.TIMELINE, Sev.MEDIUM, Cert.POSSIBLE,
                "no render report is known for this file, so it cannot be tied to the approved "
                "timeline version", Route.RESOLVE_EXPORT, NextAction.MANUAL_REVIEW,
                "confirm in Resolve that the file is of the approved timeline",
                review=True,
            ),
            "made from the approved timeline",
        )  # fmt: skip
    else:
        _lineage(run, lin)
    if f.duration is not None:
        tolerance = max(0.1, 2.0 / e.frame_rate.value)
        if abs(f.duration - e.duration_seconds) > tolerance:
            run.problem(
                _finding(
                    "timeline.duration", C.TIMELINE, Sev.HIGH, Cert.CONFIRMED,
                    f"the file lasts {f.duration:.2f} s, "
                    f"the approved timeline {e.duration_seconds:.2f} s"
                    " (a section is missing, duplicated or cut)", Route.RENDER_MANAGER,
                    NextAction.INVESTIGATE,
                    "compare with the approved timeline: a render that cut it short is a "
                    "new render, "
                    "a timeline of another length is a revision",
                ),
                "duration against the approved timeline",
            )  # fmt: skip
        else:
            run.passed(C.TIMELINE, "duration against the approved timeline", f"{f.duration:.2f} s")


def _lineage(run: _Run, lin: Lineage) -> None:
    wrong = (Route.RESOLVE_EXPORT, NextAction.REEXPORT)
    if lin.render_timeline_id != lin.approved_timeline_id:
        run.problem(
            _finding(
                "lineage.other_timeline", C.TIMELINE, Sev.CRITICAL, Cert.CONFIRMED,
                f"the file was rendered from timeline {lin.render_timeline_id}, not the approved "
                f"{lin.approved_timeline_id}", *wrong, "export the approved timeline",
            ),
            "made from the approved timeline",
        )  # fmt: skip
    elif lin.render_hash != lin.approved_hash:
        run.problem(
            _finding(
                "lineage.wrong_version", C.TIMELINE, Sev.CRITICAL, Cert.CONFIRMED,
                f"the file is of timeline version {lin.render_version}, the approved one is "
                f"{lin.approved_version}", *wrong,
                "export again from the approved version (the latest is not always the approved)",
            ),
            "made from the approved timeline",
        )  # fmt: skip
    else:
        run.passed(C.TIMELINE, "made from the approved timeline", f"version {lin.approved_version}")
    if (
        lin.approved_session_id
        and lin.render_session_id
        and lin.approved_session_id != lin.render_session_id
    ):
        run.problem(
            _finding(
                "lineage.other_review", C.TIMELINE, Sev.HIGH, Cert.CONFIRMED,
                "the render belongs to another review session than the approval", *wrong,
                "export under the review that approved this video",
            ),
            "same review",
        )  # fmt: skip
    if (
        lin.authorized_config_hash
        and lin.render_config_hash
        and (lin.authorized_config_hash != lin.render_config_hash)
    ):
        run.problem(
            _finding(
                "lineage.other_output_settings", C.TIMELINE, Sev.CRITICAL, Cert.CONFIRMED,
                "the file was rendered with other output settings than the ones authorized",
                *wrong, "export again with the authorized output settings",
            ),
            "authorized output settings",
        )  # fmt: skip
    if not lin.render_succeeded:
        run.problem(
            _finding(
                "lineage.render_not_successful", C.TIMELINE, Sev.MEDIUM, Cert.LIKELY,
                "the render report of this file does not say it succeeded", Route.RENDER_MANAGER,
                NextAction.RERENDER, "render again to be sure of the file",
            ),
            "render report",
        )  # fmt: skip


# --- the verdict -----------------------------------------------------------------------------


def _status(findings: tuple[Finding, ...], unchecked: tuple[NotChecked, ...]) -> Status:
    if any(
        f.severity is Sev.CRITICAL or (f.severity is Sev.HIGH and f.certainty is Cert.CONFIRMED)
        for f in findings
    ):
        return Status.FAIL
    if (
        any(f.needs_review or f.severity is Sev.HIGH for f in findings)
        or any(
            f.certainty is Cert.POSSIBLE and _ORDER[f.severity] >= _ORDER[Sev.MEDIUM]
            for f in findings
        )
        or any(n.mandatory for n in unchecked)
    ):
        return Status.MANUAL_REVIEW_REQUIRED
    if any(_ORDER[f.severity] >= _ORDER[Sev.LOW] for f in findings):
        return Status.WARNING
    return Status.PASS


def _next_action(status: Status, findings: tuple[Finding, ...]) -> NextAction:
    if status is Status.PASS or (status is Status.WARNING and not findings):
        return NextAction.DELIVER
    if status is Status.MANUAL_REVIEW_REQUIRED:
        return NextAction.MANUAL_REVIEW
    worst = max(findings, key=lambda x: _ORDER[x.severity], default=None)
    if status is Status.WARNING:
        return NextAction.DELIVER
    return worst.next_action if worst else NextAction.MANUAL_REVIEW


def _areas(checks: tuple[ValidationCheck, ...]) -> tuple[AreaStatus, ...]:
    order = {
        CheckState.FAILED: 3,
        CheckState.WARNING: 2,
        CheckState.NOT_CHECKED: 1,
        CheckState.PASSED: 0,
    }
    result: list[AreaStatus] = []
    for category in Category:
        states = [c.state for c in checks if c.category is category]
        if states:
            result.append(AreaStatus(category, max(states, key=lambda s: order[s])))
    return tuple(result)


def evaluate(
    facts: FileFacts,
    expected: Expected,
    profile: DeliveryProfile,
    lineage: Lineage,
    intentional: tuple[Intentional, ...] = (),
) -> Evaluation:
    """Judge the file. Nothing is changed, nothing is assumed: a measurement that was not made is
    a ``NotChecked``, and a mandatory one keeps the verdict from being a PASS."""
    run = _Run()
    _integrity(run, facts)
    if facts.exists:
        _video(run, facts, expected, profile, intentional)
        _audio(run, facts, expected, profile, intentional)
        _visual(run, facts, profile)
        _delivery(run, facts, expected, profile)
        _timeline(run, facts, expected, lineage)
    findings = tuple(sorted(run.findings, key=lambda x: -_ORDER[x.severity]))
    unchecked = tuple(run.unchecked)
    status = _status(findings, unchecked)
    checks = tuple(run.checks)
    return Evaluation(
        checks=checks,
        findings=findings,
        not_checked=unchecked,
        status=status,
        severity=max((x.severity for x in findings), key=lambda s: _ORDER[s], default=Sev.INFO),
        next_action=_next_action(status, findings),
        area_status=_areas(checks),
    )
