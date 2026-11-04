"""Quality control of produced images (Layer 4 QC shape): judges MEASURED facts of the file.

The infrastructure measures the written file (``ImageFacts``); these pure rules turn the facts
into issues. CRITICAL = the set is invalid and never stored. ERROR = this output is unusable: it
is not registered (or not handed out) and its safe fallback is used. WARNING / INFO are recorded.
"""

from dataclasses import dataclass
from enum import StrEnum

from media_house.core.domain import Rational
from media_house.modules.visual_asset_generation.domain.profile import QcThresholds


class Severity(StrEnum):
    CRITICAL = "critical"
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QcCategory(StrEnum):
    TECHNICAL = "technical"
    CREATIVE = "creative"


@dataclass(frozen=True, slots=True, kw_only=True)
class QcIssue:
    code: str
    severity: Severity
    category: QcCategory
    message: str
    item_id: str | None = None
    timeline_start: Rational | None = None
    timeline_end: Rational | None = None
    evidence: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageFacts:
    """What a produced file really is (measured after writing, never taken from the intent)."""

    #: ``None`` when the file could not be decoded; then nothing else is meaningful.
    error: str | None = None
    format: str = ""
    mode: str = ""
    width: int = 0
    height: int = 0
    has_alpha: bool = False
    bits_per_channel: int = 8
    #: Shares of fully transparent / fully opaque pixels (alpha 0 / max).
    transparent_ratio: float = 0.0
    opaque_ratio: float = 1.0
    #: Highest alpha on the outermost pixel ring, 0-1 (0 = nothing touches the canvas edge).
    border_alpha: float = 0.0
    #: Mean colour distance (0-1) between soft edge pixels and the solid subject next to them.
    rim_deviation: float | None = None
    #: Share of faint pixels that are not attached to the subject (leftover background).
    residue_ratio: float = 0.0


@dataclass(frozen=True, slots=True, kw_only=True)
class CutoutFacts:
    """What the segmentation and fitting did (from the renderer's report or stored metadata)."""

    #: ``(left, top, right, bottom)`` of the mask in source pixels, if known.
    mask_box: tuple[int, int, int, int] | None
    source_size: tuple[int, int] | None
    #: Output pixels per source pixel.
    scale: float | None


def _issue(
    code: str, severity: Severity, message: str, item_id: str, evidence: str = ""
) -> QcIssue:
    return QcIssue(
        code=code,
        severity=severity,
        category=QcCategory.TECHNICAL,
        message=message,
        item_id=item_id,
        evidence=evidence,
    )


def check_file(facts: ImageFacts, item_id: str, *, expected_format: str) -> list[QcIssue]:
    """File integrity and format: every produced image passes this first."""
    if facts.error is not None:
        return [
            _issue(
                "file_unreadable", Severity.ERROR, "output cannot be decoded", item_id, facts.error
            )
        ]
    issues: list[QcIssue] = []
    if facts.format.upper() != expected_format.upper():
        issues.append(
            _issue(
                "wrong_format",
                Severity.ERROR,
                f"output is {facts.format or 'unknown'}, not {expected_format}",
                item_id,
            )
        )
    if facts.width <= 0 or facts.height <= 0:
        issues.append(_issue("empty_image", Severity.ERROR, "output has no pixels", item_id))
    return issues


def check_transparency(facts: ImageFacts, item_id: str, *, clipped_is_error: bool) -> list[QcIssue]:
    """Alpha correctness for a cutout or an enhanced variant (PNG RGBA expected)."""
    if facts.error is not None:
        return []
    issues: list[QcIssue] = []
    if not facts.has_alpha:
        issues.append(_issue("no_alpha", Severity.ERROR, "output has no alpha channel", item_id))
        return issues
    if facts.transparent_ratio <= 0.0:
        issues.append(_issue("no_transparency", Severity.ERROR, "no pixel is transparent", item_id))
    if facts.opaque_ratio <= 0.0:
        issues.append(
            _issue("no_solid_subject", Severity.ERROR, "no pixel of the subject is opaque", item_id)
        )
    if facts.border_alpha > 0.0:
        issues.append(
            _issue(
                "touches_canvas_edge",
                Severity.ERROR if clipped_is_error else Severity.WARNING,
                "visible pixels reach the canvas edge"
                + (": an effect is clipped" if clipped_is_error else ": the subject may be cut"),
                item_id,
                f"border alpha {facts.border_alpha:.3f}",
            )
        )
    return issues


def check_cutout(
    facts: ImageFacts,
    cutout: CutoutFacts,
    thresholds: QcThresholds,
    item_id: str,
) -> list[QcIssue]:
    """Edge quality and subject integrity of a clean cutout."""
    if facts.error is not None:
        return []
    issues: list[QcIssue] = []
    if facts.rim_deviation is not None and facts.rim_deviation > thresholds.max_rim_deviation:
        issues.append(
            _issue(
                "edge_halo",
                Severity.WARNING,
                "the soft edge differs in colour from the subject (background halo)",
                item_id,
                f"rim deviation {facts.rim_deviation:.3f} > {thresholds.max_rim_deviation:g}",
            )
        )
    if facts.residue_ratio > thresholds.max_residue_ratio:
        issues.append(
            _issue(
                "background_residue",
                Severity.WARNING,
                "faint pixels detached from the subject remain (leftover background)",
                item_id,
                f"residue {facts.residue_ratio:.4f} > {thresholds.max_residue_ratio:g}",
            )
        )
    if cutout.scale is not None and cutout.scale > thresholds.upscale_warning:
        issues.append(
            _issue(
                "upscaled",
                Severity.WARNING,
                f"the subject was enlarged {cutout.scale:.2f}x and will look soft",
                item_id,
            )
        )
    if cutout.source_size is not None and min(cutout.source_size) < thresholds.min_source_edge:
        issues.append(
            _issue(
                "small_source",
                Severity.WARNING,
                f"the source is only {cutout.source_size[0]}x{cutout.source_size[1]} pixels",
                item_id,
            )
        )
    if cutout.mask_box is not None and cutout.source_size is not None:
        left, top, right, bottom = cutout.mask_box
        width, height = cutout.source_size
        if left <= 0 or top <= 0 or right >= width or bottom >= height:
            issues.append(
                _issue(
                    "subject_at_source_edge",
                    Severity.WARNING,
                    "the subject touches the edge of the source picture and may be cut off there",
                    item_id,
                    f"mask box {cutout.mask_box} in {width}x{height}",
                )
            )
    elif cutout.mask_box is None:
        issues.append(
            _issue(
                "subject_box_unknown",
                Severity.INFO,
                "the source mask box is not recorded (cutout made before it was stored)",
                item_id,
            )
        )
    return issues


def blocking(issues: list[QcIssue]) -> bool:
    return any(i.severity in {Severity.CRITICAL, Severity.ERROR} for i in issues)
