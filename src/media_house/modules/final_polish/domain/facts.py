"""What the polish looks at, as its own narrow facts (the domain imports no other module)."""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True, slots=True)
class PolishClip:
    clip_id: str
    #: primary | cover | graphic | caption | vfx | transition_overlay | custom
    kind: str
    name: str
    start_frame: int
    end_frame: int
    enabled: bool
    unresolved: bool
    #: media_house | automated_revision | human_manual | external | unknown
    origin: str
    section_id: str | None


@dataclass(frozen=True, slots=True)
class PolishSound:
    clip_id: str
    role: str
    start: Fraction
    end: Fraction
    fade_out: Fraction
    enabled: bool


@dataclass(frozen=True, slots=True)
class PolishTransition:
    transition_id: str
    mode: str
    cut_frame: int
    start_frame: int
    end_frame: int
    from_clip: str | None
    to_clip: str | None


@dataclass(frozen=True, slots=True)
class PolishMarker:
    marker_id: str
    kind: str
    frame: int
    name: str
    note: str


@dataclass(frozen=True, slots=True)
class PolishCue:
    index: int
    start_frame: int
    end_frame: int
    text: str


@dataclass(frozen=True, slots=True)
class PolishSection:
    section_id: str
    name: str
    start_frame: int
    end_frame: int


@dataclass(frozen=True, slots=True)
class PolishTimeline:
    timeline_id: str
    version: int
    content_hash: str
    anchor_asset_id: str
    project_id: str | None
    fps: Fraction
    duration_frames: int
    clips: tuple[PolishClip, ...]
    sounds: tuple[PolishSound, ...]
    transitions: tuple[PolishTransition, ...]
    markers: tuple[PolishMarker, ...]
    cues: tuple[PolishCue, ...]
    sections: tuple[PolishSection, ...]

    def seconds(self, frame: int) -> float:
        return float(Fraction(frame) / self.fps)


@dataclass(frozen=True, slots=True)
class QualityIssueFacts:
    issue_id: str
    code: str
    severity: str
    basis: str
    description: str
    blocks_export: bool
    start_frame: int | None
    end_frame: int | None
    clips: tuple[str, ...]
    evidence_assets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QualityFacts:
    report_id: str
    issues: tuple[QualityIssueFacts, ...]
    #: Check names that did not run (so what they say is not claimed).
    not_run: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class MixFacts:
    state: str
    delivery: str
    #: ``(code, severity, message)`` of what the engine reported at ``error`` or ``critical``.
    errors: tuple[tuple[str, str, str], ...]
    integrated_lufs: float | None
    target_lufs: float | None
    tolerance_lu: float | None


@dataclass(frozen=True, slots=True)
class FrameFacts:
    """A picture of the final version at one place, measured."""

    label: str
    frame: int
    black_fraction: float
    luma_mean: float
    asset_id: str = ""


@dataclass(frozen=True, slots=True)
class RevisionFacts:
    """What the revisions of this review did."""

    #: Plans carried out (ids), in order.
    executed_plans: tuple[str, ...]
    #: Elements a REJECTED proposal's execution touched (it must be none).
    rejected_but_applied: tuple[str, ...]
    #: Plans approved but not carried out.
    approved_not_executed: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewFacts:
    session_id: str
    review_version: int
    #: The version the review is at (what is analysed must be exactly this).
    session_content_hash: str
    locked_sections: tuple[str, ...]
    #: Locked sections whose content changed after they were locked (a person changed them).
    drifted_locks: tuple[str, ...]
    approved_revisions: tuple[str, ...]
    resolve_project: str
    resolve_timeline: str
