"""Profiles: what kind of supplementary footage a format wants, and how strictly.

A profile is DATA. It changes which evidence a need requires, how ranking weighs the independent
scores, how long an insert may be and whether a protected moment may be covered; it never changes
what a score MEANS (see ``values``), so two profiles always rank the same facts differently, never
different facts. Every number is a design choice that has not been calibrated on real footage.

* ``weights`` rank candidates that already pass the evidence gate; a score that could not be
  stated is left out and the rest are re-weighted (missing is not zero).
* ``min_overall`` is the quality threshold: a candidate below it is "not suitable", and a passage
  with no suitable candidate is reported as a coverage gap, never filled with the best of a bad lot.
* ``illustrative_for_*`` say whether a related-but-unproven clip may satisfy a need that normally
  asks for evidence. Both are off wherever a viewer could be misled (documentary, review).
* ``strict_identity`` makes a passage that names a specific product require footage that is shown
  to be that product.
"""

from dataclasses import dataclass, field

from media_house.modules.broll_intelligence.domain.errors import InvalidBrollConfig
from media_house.modules.broll_intelligence.domain.values import PROFILE_VERSION, ScoreName

DEFAULT_PROFILE = "generic"

_BASE_WEIGHTS: dict[ScoreName, float] = {
    ScoreName.SEMANTIC_RELEVANCE: 0.22,
    ScoreName.EVIDENCE_STRENGTH: 0.22,
    ScoreName.NARRATIVE_RELEVANCE: 0.08,
    ScoreName.VISUAL_QUALITY: 0.10,
    ScoreName.SUBJECT_VISIBILITY: 0.06,
    ScoreName.COMPOSITION_SUITABILITY: 0.04,
    ScoreName.VISUAL_ACTIVITY: 0.06,
    ScoreName.TEMPORAL_SUITABILITY: 0.06,
    ScoreName.CONTINUITY_COMPATIBILITY: 0.04,
    ScoreName.DURATION_SUITABILITY: 0.06,
    ScoreName.NOVELTY: 0.04,
    ScoreName.AUDIO_SUITABILITY: 0.02,
}


@dataclass(frozen=True, slots=True, kw_only=True)
class BrollProfile:
    name: str
    description: str
    version: int = PROFILE_VERSION
    weights: tuple[tuple[ScoreName, float], ...] = tuple(_BASE_WEIGHTS.items())
    #: Below this weighted mean a candidate is not suitable.
    min_overall: float = 0.45
    #: Below this semantic relevance (when stated) a shot is not a candidate at all.
    min_semantic: float = 0.30
    #: A measured technical usability below this refuses the shot.
    min_usability: float = 0.40
    #: Seconds an insert should last, at least and at most.
    min_insert_seconds: float = 1.5
    max_insert_seconds: float = 8.0
    #: May a related clip whose action/identity is not shown satisfy a DEMONSTRATION need?
    illustrative_for_demonstration: bool = False
    #: May a related clip satisfy a REQUIRED_EVIDENCE need?
    illustrative_for_evidence: bool = False
    #: A passage naming a specific product needs footage shown to be that product.
    strict_identity: bool = True
    #: Never cover a speaking face during emphatic delivery (reactions, credibility moments).
    protect_speaker: bool = True
    #: 0..1: how readily a long unchanged picture is offered a change of image.
    variety_appetite: float = 0.4
    #: Offer cover for a jump cut in the speech.
    cover_jump_cuts: bool = True
    #: Report a gap for an optional (illustrative) passage with nothing suitable.
    report_optional_gaps: bool = False
    #: Seconds of the main picture to keep before and after an insert.
    context_before_seconds: float = 1.0
    context_after_seconds: float = 0.5
    #: Most alternatives listed per passage (after the best).
    max_alternatives: int = 3
    #: Most candidates a passage may carry into ranking.
    max_candidates: int = 40
    #: A shot whose own face speaks is refused unless the use is a reaction.
    refuse_speaking_faces: bool = True

    def weight(self, name: ScoreName) -> float:
        return dict(self.weights).get(name, 0.0)

    def problems(self) -> list[str]:
        out: list[str] = []
        weights = dict(self.weights)
        if any(w < 0 for w in weights.values()) or sum(weights.values()) <= 0:
            out.append("weights must be non-negative with a positive sum")
        for label, value in (
            ("min_overall", self.min_overall),
            ("min_semantic", self.min_semantic),
            ("min_usability", self.min_usability),
            ("variety_appetite", self.variety_appetite),
        ):
            if not 0.0 <= value <= 1.0:
                out.append(f"{label} must lie within [0, 1]")
        if not 0 < self.min_insert_seconds <= self.max_insert_seconds:
            out.append("insert length needs 0 < min <= max")
        if self.max_alternatives < 0 or self.max_candidates < 1:
            out.append("candidate counts must be positive")
        return out


def _profile(name: str, description: str, **changes: object) -> BrollProfile:
    return BrollProfile(name=name, description=description, **changes)  # type: ignore[arg-type]


def _weights(**changes: float) -> tuple[tuple[ScoreName, float], ...]:
    merged = dict(_BASE_WEIGHTS)
    for key, value in changes.items():
        merged[ScoreName(key)] = value
    return tuple(merged.items())


PROFILES: dict[str, BrollProfile] = {
    p.name: p
    for p in (
        _profile("generic", "Neutral defaults: evidence is required where the story says so."),
        _profile(
            "tutorial",
            "Show the actual step and its result; a lookalike is not a demonstration.",
            weights=_weights(visual_activity=0.10, temporal_suitability=0.10, novelty=0.02),
            min_insert_seconds=2.5,
            max_insert_seconds=14.0,
            variety_appetite=0.2,
            context_before_seconds=1.5,
        ),
        _profile(
            "product_review",
            "Separate this product's footage from generic product imagery.",
            weights=_weights(evidence_strength=0.28, subject_visibility=0.08, novelty=0.03),
            min_insert_seconds=1.5,
            max_insert_seconds=7.0,
            variety_appetite=0.5,
        ),
        _profile(
            "unboxing",
            "Close, specific shots of the item; the reaction to it stays on screen.",
            weights=_weights(subject_visibility=0.10, composition_suitability=0.07),
            max_insert_seconds=6.0,
            variety_appetite=0.5,
        ),
        _profile(
            "talking_head",
            "Few, short inserts; the speaker's face is the content.",
            min_insert_seconds=1.2,
            max_insert_seconds=5.0,
            variety_appetite=0.3,
            context_before_seconds=2.0,
            context_after_seconds=1.0,
        ),
        _profile(
            "interview_podcast",
            "Do not cover reactions or delivery; inserts only where the picture adds fact.",
            min_overall=0.55,
            min_insert_seconds=2.0,
            max_insert_seconds=6.0,
            variety_appetite=0.1,
            cover_jump_cuts=True,
            context_before_seconds=3.0,
            context_after_seconds=1.5,
        ),
        _profile(
            "documentary",
            "Provenance and factual relevance first; illustration never stands in for evidence.",
            weights=_weights(evidence_strength=0.30, semantic_relevance=0.20, novelty=0.03),
            min_overall=0.55,
            min_semantic=0.40,
            min_insert_seconds=2.5,
            max_insert_seconds=12.0,
            variety_appetite=0.3,
            report_optional_gaps=True,
        ),
        _profile(
            "top_10_ranking",
            "Each ranked item gets its own footage; repeats are avoided.",
            weights=_weights(novelty=0.10, narrative_relevance=0.10),
            max_insert_seconds=7.0,
            variety_appetite=0.6,
        ),
        _profile(
            "shorts_reels",
            "Concise, relevant visuals; clarity over variety.",
            weights=_weights(duration_suitability=0.10, visual_activity=0.09),
            min_insert_seconds=1.0,
            max_insert_seconds=4.0,
            variety_appetite=0.7,
            context_before_seconds=0.5,
            context_after_seconds=0.3,
        ),
        _profile(
            "cinematic_montage",
            "Atmosphere and rhythm may carry the picture, but never a false association.",
            weights=_weights(
                visual_quality=0.16, composition_suitability=0.10, evidence_strength=0.12,
                continuity_compatibility=0.08,
            ),
            min_overall=0.40,
            min_semantic=0.25,
            illustrative_for_demonstration=True,
            illustrative_for_evidence=False,
            min_insert_seconds=2.0,
            max_insert_seconds=10.0,
            variety_appetite=0.7,
            protect_speaker=False,
        ),
    )
}  # fmt: skip

#: Story formats that name a B-roll profile. Anything else uses ``generic``.
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
class BrollConfig:
    """What the caller chooses: a named profile (else the story format's, else generic)."""

    profile: str | None = None
    #: A profile of the caller's own for one run (used instead of ``profile``).
    custom: BrollProfile | None = field(default=None)
    #: Most passages to find footage for (best first); ``None`` = all.
    max_targets: int | None = None

    def resolve(self, story_format: str | None = None) -> BrollProfile:
        if self.custom is not None:
            profile = self.custom
        else:
            name = self.profile or STORY_FORMAT_PROFILE.get(story_format or "", DEFAULT_PROFILE)
            found = PROFILES.get(name)
            if found is None:
                raise InvalidBrollConfig(
                    f"unknown profile {name!r}; known: {', '.join(sorted(PROFILES))}"
                )
            profile = found
        problems = profile.problems()
        if self.max_targets is not None and self.max_targets < 1:
            problems.append("max_targets must be at least 1")
        if problems:
            raise InvalidBrollConfig("; ".join(problems))
        return profile

    def fingerprint(self, profile: BrollProfile) -> dict[str, object]:
        """Everything of the configuration that can change an analysis, as JSON-safe data."""
        return {
            "profile": {
                "name": profile.name,
                "version": profile.version,
                "weights": [[n.value, w] for n, w in profile.weights],
                "min_overall": profile.min_overall,
                "min_semantic": profile.min_semantic,
                "min_usability": profile.min_usability,
                "insert": [profile.min_insert_seconds, profile.max_insert_seconds],
                "illustrative": [
                    profile.illustrative_for_demonstration,
                    profile.illustrative_for_evidence,
                ],
                "strict_identity": profile.strict_identity,
                "protect_speaker": profile.protect_speaker,
                "variety": profile.variety_appetite,
                "jump_cuts": profile.cover_jump_cuts,
                "optional_gaps": profile.report_optional_gaps,
                "context": [profile.context_before_seconds, profile.context_after_seconds],
                "alternatives": profile.max_alternatives,
                "candidates": profile.max_candidates,
                "speaking_faces": profile.refuse_speaking_faces,
            },
            "max_targets": self.max_targets,
        }
