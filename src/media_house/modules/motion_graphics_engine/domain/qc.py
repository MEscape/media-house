"""QC issue helpers and the render QC rules (judged on ffprobe/FFmpeg facts, not on intent)."""

from dataclasses import dataclass

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.domain.values import QcCategory, QcIssue, Severity


def issue(
    code: str,
    severity: Severity,
    message: str,
    item_id: str | None = None,
    *,
    category: QcCategory = QcCategory.TECHNICAL,
    prop: str = "",
    expected: str = "",
    actual: str = "",
    fix: str = "",
    evidence: str = "",
    timeline_start: Rational | None = None,
    timeline_end: Rational | None = None,
) -> QcIssue:
    if category is QcCategory.CREATIVE and severity in (Severity.CRITICAL, Severity.ERROR):
        severity = Severity.WARNING  # a creative finding is never above WARNING
    return QcIssue(
        code=code,
        severity=severity,
        category=category,
        message=message,
        item_id=item_id,
        property=prop,
        expected=expected,
        actual=actual,
        fix=fix,
        evidence=evidence,
        timeline_start=timeline_start,
        timeline_end=timeline_end,
    )


def blocking(issues: tuple[QcIssue, ...] | list[QcIssue]) -> bool:
    return any(i.severity in (Severity.CRITICAL, Severity.ERROR) for i in issues)


@dataclass(frozen=True, slots=True, kw_only=True)
class AlphaSample:
    frame: int
    alpha_min: int
    alpha_max: int
    #: Share of pixels with alpha > 0.
    covered: float
    #: Share of pixels fully opaque.
    opaque: float


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderFacts:
    """What ffprobe/FFmpeg say about a rendered file."""

    codec: str
    profile: str
    pix_fmt: str
    width: int
    height: int
    fps: Rational
    frames: int
    alpha: tuple[AlphaSample, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class RenderExpectation:
    width: int
    height: int
    fps: Rational
    frames: int
    #: A full-frame graphic (end screen with a plate) may be opaque everywhere.
    may_be_opaque: bool = False
    still: bool = False


def render_qc(facts: RenderFacts, want: RenderExpectation, item: str) -> tuple[QcIssue, ...]:
    """Dims, fps, codec/profile, pixel format with alpha, exact frame count, alpha present and
    neither fully transparent nor (unless allowed) fully opaque."""
    found: list[QcIssue] = []

    def bad(code: str, prop: str, expected: str, actual: str, fix: str) -> None:
        found.append(
            issue(
                code,
                Severity.ERROR,
                f"{prop} is {actual}, expected {expected}",
                item,
                prop=prop,
                expected=expected,
                actual=actual,
                fix=fix,
            )
        )

    if (facts.width, facts.height) != (want.width, want.height):
        bad(
            "render.size",
            "size",
            f"{want.width}x{want.height}",
            f"{facts.width}x{facts.height}",
            "render at the style's output size",
        )
    if not want.still:
        if facts.fps != want.fps:
            bad("render.fps", "fps", str(want.fps), str(facts.fps), "render at the edit's fps")
        if facts.codec != "prores" or "4444" not in facts.profile:
            bad(
                "render.codec",
                "codec",
                "prores 4444",
                f"{facts.codec} {facts.profile}",
                "render MOV (ProRes 4444)",
            )
        if facts.frames != want.frames:
            bad(
                "render.frames",
                "frames",
                str(want.frames),
                str(facts.frames),
                "write the composition duration from the frame span",
            )
    if not facts.pix_fmt.startswith(("yuva", "rgba", "bgra", "gbrap", "ya")):
        bad("render.no_alpha", "pix_fmt", "a format with alpha", facts.pix_fmt, "keep alpha")
    if not facts.alpha:
        bad("render.alpha_unchecked", "alpha", "decoded samples", "none", "decode frames")
    elif all(s.alpha_max == 0 for s in facts.alpha):
        bad(
            "render.transparent",
            "alpha",
            "visible pixels",
            "fully transparent",
            "check the composition (fonts, assets, timing)",
        )
    elif not want.may_be_opaque and all(s.alpha_min == 255 for s in facts.alpha):
        bad(
            "render.opaque",
            "alpha",
            "transparent background",
            "fully opaque",
            "leave html/body/root unpainted",
        )
    return tuple(found)
