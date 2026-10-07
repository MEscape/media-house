"""Verification: did the processing improve the video without breaking what it must preserve?

Two kinds of check, told apart by severity:

* HARD checks protect identity: size, frame rate, duration, frame count, audio streams,
  rotation. A failure means the output is wrong and it is discarded.
* SOFT checks protect quality: clipping, crushed blacks, noise, lost detail, colour tags, and
  whether the real output matches what was predicted. A failure names the stage responsible so
  that stage can be left out (the same plan is rendered again without it).
"""

from dataclasses import dataclass
from enum import StrEnum

from media_house.modules.video_improvement.domain.color import OUTPUT_COLOR
from media_house.modules.video_improvement.domain.measurements import SceneMeasurements
from media_house.modules.video_improvement.domain.planning import ProcessingPlan
from media_house.modules.video_improvement.domain.settings import ProcessingProfile
from media_house.modules.video_improvement.domain.source import VideoFacts
from media_house.modules.video_improvement.domain.values import ProcessingStage

_FRAME_RATE_TOLERANCE = 0.001
#: Duration may differ by one frame plus this allowance (container rounding).
_DURATION_SLACK_SECONDS = 0.05
_NOISE_TOLERANCE = 1.05
#: Noise below this is measurement jitter, not something the grade amplified.
_NOISE_FLOOR = 0.001
_SHARPEN_NOISE_LIMIT = 1.6


class CheckKind(StrEnum):
    HARD = "hard"
    SOFT = "soft"


@dataclass(frozen=True, slots=True)
class Check:
    name: str
    passed: bool
    detail: str
    kind: CheckKind = CheckKind.SOFT
    #: The stage to leave out if this soft check fails.
    stage: ProcessingStage | None = None


def _hard(name: str, passed: bool, detail: str) -> Check:
    return Check(name, passed, detail, CheckKind.HARD)


def verify_output(
    *,
    source: VideoFacts,
    output: VideoFacts,
    source_frames: int | None,
    output_frames: int | None,
    before: SceneMeasurements,
    predicted: SceneMeasurements | None,
    after: SceneMeasurements,
    plan: ProcessingPlan,
    profile: ProcessingProfile,
    color_reference: SceneMeasurements | None = None,
) -> tuple[Check, ...]:
    """Every check; the caller decides from ``kind`` and ``stage`` what a failure means.

    ``color_reference`` is what clipping and crushed blacks are compared with. It defaults to
    the source; for log or flat footage, whose raw signal says nothing about clipping, it is the
    neutral rendering of that footage (the adaptive corrections must not add to it).
    """
    frame = 1.0 / source.frame_rate.value
    rate_error = abs(output.frame_rate.value - source.frame_rate.value) / source.frame_rate.value
    checks = [
        _hard(
            "resolution",
            (output.width, output.height) == (source.width, source.height),
            f"{source.width}x{source.height} -> {output.width}x{output.height}",
        ),
        _hard(
            "frame_rate",
            rate_error <= _FRAME_RATE_TOLERANCE,
            f"{source.frame_rate} -> {output.frame_rate}",
        ),
        _hard(
            "duration",
            abs(output.duration - source.duration) <= frame + _DURATION_SLACK_SECONDS,
            f"{source.duration:.3f}s -> {output.duration:.3f}s",
        ),
        _hard(
            "audio_streams",
            output.audio_stream_count == source.audio_stream_count,
            f"{source.audio_stream_count} -> {output.audio_stream_count}",
        ),
        _hard(
            "rotation",
            output.rotation == source.rotation,
            f"{source.rotation} -> {output.rotation}",
        ),
    ]
    if source_frames is not None and output_frames is not None:
        checks.append(
            _hard(
                "frame_count", source_frames == output_frames, f"{source_frames} -> {output_frames}"
            )
        )
    if source.timecode is not None:
        checks.append(
            Check(
                "timecode",
                output.timecode == source.timecode,
                f"{source.timecode} -> {output.timecode}",
            )
        )
    reference = color_reference or before
    checks.extend(_color_checks(source, output, reference, predicted, after, plan, profile))
    checks.extend(_detail_checks(before, after, plan, profile))
    return tuple(checks)


def _color_checks(
    source: VideoFacts,
    output: VideoFacts,
    before: SceneMeasurements,
    predicted: SceneMeasurements | None,
    after: SceneMeasurements,
    plan: ProcessingPlan,
    profile: ProcessingProfile,
) -> list[Check]:
    if not plan.applied(ProcessingStage.COLOR):
        if source.color.known:
            return [
                Check(
                    "color_tags", output.color == source.color, f"{source.color} -> {output.color}"
                )
            ]
        return []
    c = profile.color
    stage = ProcessingStage.COLOR
    checks = [
        Check(
            "color_tags", output.color == OUTPUT_COLOR, f"{output.color}, expected {OUTPUT_COLOR}"
        ),
        Check(
            "clipping",
            after.highlight_clip - before.highlight_clip <= c.guard_max_clip_increase,
            f"highlight clip {before.highlight_clip:.4f} -> {after.highlight_clip:.4f}",
            stage=stage,
        ),
        Check(
            "crushed_blacks",
            after.shadow_crush - before.shadow_crush <= c.guard_max_crush_increase,
            f"shadow crush {before.shadow_crush:.4f} -> {after.shadow_crush:.4f}",
            stage=stage,
        ),
    ]
    checks.append(
        Check(
            "grade_noise",
            after.noise_sigma <= before.noise_sigma * c.guard_max_noise_gain + _NOISE_FLOOR,
            f"noise {before.noise_sigma:.4f} -> {after.noise_sigma:.4f}",
            stage=stage,
        )
    )
    if predicted is not None:
        error = abs(after.luma_p50 - predicted.luma_p50)
        checks.append(
            Check(
                "prediction",
                error <= c.guard_max_prediction_error,
                f"median luma predicted {predicted.luma_p50:.3f}, measured {after.luma_p50:.3f}",
                stage=stage,
            )
        )
    return checks


def _detail_checks(
    before: SceneMeasurements,
    after: SceneMeasurements,
    plan: ProcessingPlan,
    profile: ProcessingProfile,
) -> list[Check]:
    checks: list[Check] = []
    if plan.applied(ProcessingStage.DENOISE):
        retention = profile.denoise.guard_min_sharpness_retention
        checks.append(
            Check(
                "noise_reduced",
                after.noise_sigma <= before.noise_sigma * _NOISE_TOLERANCE,
                f"noise {before.noise_sigma:.4f} -> {after.noise_sigma:.4f}",
                stage=ProcessingStage.DENOISE,
            )
        )
        checks.append(
            Check(
                "detail_kept",
                after.sharpness >= before.sharpness * retention,
                f"sharpness {before.sharpness:.4f} -> {after.sharpness:.4f}",
                stage=ProcessingStage.DENOISE,
            )
        )
    if plan.applied(ProcessingStage.SHARPEN):
        checks.append(
            Check(
                "noise_not_amplified",
                after.noise_sigma <= before.noise_sigma * _SHARPEN_NOISE_LIMIT,
                f"noise {before.noise_sigma:.4f} -> {after.noise_sigma:.4f}",
                stage=ProcessingStage.SHARPEN,
            )
        )
    return checks


def hard_failures(checks: tuple[Check, ...]) -> tuple[Check, ...]:
    return tuple(c for c in checks if c.kind is CheckKind.HARD and not c.passed)


def regressed_stages(checks: tuple[Check, ...]) -> tuple[ProcessingStage, ...]:
    """The stages whose soft checks failed, in processing order, without repeats."""
    failed = {c.stage for c in checks if c.kind is CheckKind.SOFT and not c.passed and c.stage}
    return tuple(
        s
        for s in (ProcessingStage.DENOISE, ProcessingStage.COLOR, ProcessingStage.SHARPEN)
        if s in failed
    )
