"""Profiles: how much each kind of break matters in a format, and how likely it is deliberate.

A profile is DATA. It scales how much an issue counts (``weights``), says how likely a kind of
difference is a deliberate choice of the format (``style_priors``) and where the severity levels
begin. It never changes the evidence: two profiles judge the same measurements and report them
alike; only the level and the style estimate differ. Every number is an uncalibrated design choice.

``strictness`` (0.5 lenient .. 1.5 strict) scales the level thresholds for one run; the weights and
priors stay the profile's.
"""

from dataclasses import dataclass

from media_house.modules.continuity_intelligence.domain.errors import InvalidContinuityConfig
from media_house.modules.continuity_intelligence.domain.values import PROFILE_VERSION, IssueType

DEFAULT_PROFILE = "generic"

_NEUTRAL = dict.fromkeys(IssueType, 1.0)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContinuityProfile:
    name: str
    description: str
    version: int = PROFILE_VERSION
    #: How much each kind of break counts here (1 neutral, 0 ignored, above 1 matters more).
    weights: tuple[tuple[IssueType, float], ...] = tuple(_NEUTRAL.items())
    #: Prior likelihood that a difference of this kind is deliberate in this format.
    style_priors: tuple[tuple[IssueType, float], ...] = ()
    #: Effective score from which an issue is HIGH / MEDIUM / LOW (below LOW it is not an issue).
    high: float = 0.65
    medium: float = 0.40
    low: float = 0.22
    #: The format cuts between recordings or looks freely (a montage): cross-recording looks differ.
    cross_recording_prior: float = 0.1

    def weight(self, kind: IssueType) -> float:
        return dict(self.weights).get(kind, 1.0)

    def style_prior(self, kind: IssueType) -> float:
        return dict(self.style_priors).get(kind, 0.0)

    def problems(self) -> list[str]:
        out = []
        if not 0 < self.low < self.medium < self.high <= 1:
            out.append("thresholds need 0 < low < medium < high <= 1")
        if any(w < 0 for _, w in self.weights):
            out.append("weights must not be negative")
        if (
            any(not 0 <= p <= 1 for _, p in self.style_priors)
            or not 0 <= self.cross_recording_prior <= 1
        ):
            out.append("style priors must lie within [0, 1]")
        return out


def _w(**changes: float) -> tuple[tuple[IssueType, float], ...]:
    merged = dict(_NEUTRAL)
    for key, value in changes.items():
        merged[IssueType(key)] = value
    return tuple(merged.items())


def _p(**changes: float) -> tuple[tuple[IssueType, float], ...]:
    return tuple((IssueType(k), v) for k, v in changes.items())


def _profile(name: str, description: str, **changes: object) -> ContinuityProfile:
    return ContinuityProfile(name=name, description=description, **changes)  # type: ignore[arg-type]


PROFILES: dict[str, ContinuityProfile] = {
    p.name: p
    for p in (
        _profile("generic", "Neutral defaults."),
        _profile(
            "talking_head",
            "Jump cuts are the style; identity and audio defects still matter.",
            weights=_w(jump_cut=0.45, visual_discontinuity=0.8, clipped_speech=1.3),
            style_priors=_p(jump_cut=0.8),
        ),
        _profile(
            "shorts_reels",
            "Fast cuts and visual contrast are expected.",
            weights=_w(jump_cut=0.3, visual_discontinuity=0.5, lighting_mismatch=0.7, eyeline=0.6),
            style_priors=_p(jump_cut=0.85, visual_discontinuity=0.5, lighting_mismatch=0.3),
            cross_recording_prior=0.4,
        ),
        _profile(
            "interview_podcast",
            "Eyelines and the sound carry the format; jump cuts are tolerated less.",
            weights=_w(
                jump_cut=0.9, eyeline=1.4, screen_direction=1.2, clipped_speech=1.4,
                audio_discontinuity=1.3, sync_uncertain=1.2,
            ),
            style_priors=_p(jump_cut=0.35),
            medium=0.36,
        ),
        _profile(
            "product_review",
            "The product must stay the same product and in the same state.",
            weights=_w(
                identity_mismatch=1.4, object_state=1.3, jump_cut=0.6, action_continuity=1.1
            ),
            style_priors=_p(jump_cut=0.6),
        ),
        _profile(
            "unboxing",
            "Object state and action continuity first.",
            weights=_w(
                object_state=1.4, action_continuity=1.4, identity_mismatch=1.3, jump_cut=0.7
            ),
            style_priors=_p(jump_cut=0.5),
        ),
        _profile(
            "tutorial",
            "A step must not be skipped or reversed; clean sound.",
            weights=_w(
                action_continuity=1.4, object_state=1.3, temporal_inconsistency=1.4,
                jump_cut=0.7, clipped_speech=1.2,
            ),
            style_priors=_p(jump_cut=0.55),
        ),
        _profile(
            "documentary",
            "Provenance and chronology; looks may change with place and time.",
            weights=_w(
                temporal_inconsistency=1.3, identity_mismatch=1.2, narrative_confusion=1.2,
                lighting_mismatch=0.8,
            ),
            style_priors=_p(lighting_mismatch=0.4, visual_discontinuity=0.35),
            cross_recording_prior=0.3,
        ),
        _profile(
            "cinematic_dialogue",
            "The classical rules: screen direction, eyelines, matching action and light.",
            weights=_w(
                screen_direction=1.5, eyeline=1.5, jump_cut=1.3, action_continuity=1.3,
                lighting_mismatch=1.2, visual_discontinuity=1.1,
            ),
            style_priors=_p(jump_cut=0.1),
            high=0.6,
            medium=0.36,
            low=0.2,
        ),
        _profile(
            "fast_montage",
            "Contrast and rhythm are the point; only defects are flagged.",
            weights=_w(
                jump_cut=0.2, visual_discontinuity=0.3, lighting_mismatch=0.4,
                screen_direction=0.3, eyeline=0.3, action_continuity=0.3, object_state=0.4,
                identity_mismatch=0.7,
            ),
            style_priors=_p(
                jump_cut=0.9, visual_discontinuity=0.8, lighting_mismatch=0.6, screen_direction=0.6,
            ),
            cross_recording_prior=0.6,
        ),
        _profile(
            "top_10_ranking",
            "Items are separate scenes: contrast between them is expected.",
            weights=_w(visual_discontinuity=0.4, lighting_mismatch=0.5, jump_cut=0.6),
            style_priors=_p(visual_discontinuity=0.7, lighting_mismatch=0.5),
            cross_recording_prior=0.5,
        ),
    )
}  # fmt: skip

STORY_FORMAT_PROFILE: dict[str, str] = {
    "top_n": "top_10_ranking",
    "review": "product_review",
    "product_introduction": "product_review",
    "unboxing": "unboxing",
    "talking_head": "talking_head",
    "tutorial": "tutorial",
    "interview": "interview_podcast",
    "podcast": "interview_podcast",
    "documentary": "documentary",
    "short": "shorts_reels",
}


@dataclass(frozen=True, slots=True)
class ContinuityConfig:
    profile: str | None = None
    custom: ContinuityProfile | None = None
    #: 0.5 lenient .. 1.5 strict: scales the level thresholds.
    strictness: float = 1.0

    def resolve(self, story_format: str | None = None) -> ContinuityProfile:
        if self.custom is not None:
            profile = self.custom
        else:
            name = self.profile or STORY_FORMAT_PROFILE.get(story_format or "", DEFAULT_PROFILE)
            found = PROFILES.get(name)
            if found is None:
                raise InvalidContinuityConfig(
                    f"unknown profile {name!r}; known: {', '.join(sorted(PROFILES))}"
                )
            profile = found
        problems = profile.problems()
        if not 0.5 <= self.strictness <= 1.5:
            problems.append("strictness must lie within [0.5, 1.5]")
        if problems:
            raise InvalidContinuityConfig("; ".join(problems))
        return profile

    def fingerprint(self, profile: ContinuityProfile) -> dict[str, object]:
        return {
            "profile": {
                "name": profile.name,
                "version": profile.version,
                "weights": [[k.value, w] for k, w in profile.weights],
                "priors": [[k.value, p] for k, p in profile.style_priors],
                "levels": [profile.high, profile.medium, profile.low],
                "cross": profile.cross_recording_prior,
            },
            "strictness": self.strictness,
        }
