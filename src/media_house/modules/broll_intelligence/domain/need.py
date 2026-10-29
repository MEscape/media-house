"""Does this passage need supplementary footage, and of what kind?

The answer comes from what the story, the content analysis, the pacing analysis and the picture
already decided, never from a long unbroken shot alone. A need says what a candidate must PROVE
to satisfy it (``requires``): required evidence and demonstrations ask for footage that shows the
thing or the action; an illustration asks only for related footage. A passage that should keep its
picture (an emphatic face, something already covered, a graphic) is a ``NoChange`` with the
reason, not a need nobody can meet.
"""

from dataclasses import dataclass

from media_house.modules.broll_intelligence.domain.facts import TargetFact
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.values import (
    NeedKind,
    Priority,
    Purpose,
    TargetOrigin,
)

_GRAPHIC_PURPOSES = frozenset({"explainer_graphic", "text_on_screen"})
_GRAPHIC_CATEGORIES = frozenset({"diagram"})
_EVIDENCE_REQUIRES = "footage shown to be the specific thing the passage names"
_DEMONSTRATION_REQUIRES = "footage in which the action itself is seen happening"

_CATEGORY: dict[str, tuple[NeedKind, Purpose]] = {
    "object_closeup": (NeedKind.ILLUSTRATIVE, Purpose.PRODUCT_CLOSEUP),
    "process_footage": (NeedKind.DEMONSTRATION, Purpose.DEMONSTRATION),
    "physical_action": (NeedKind.DEMONSTRATION, Purpose.DEMONSTRATION),
    "location": (NeedKind.ILLUSTRATIVE, Purpose.ESTABLISHING),
    "archive_or_historical": (NeedKind.REQUIRED_EVIDENCE, Purpose.EVIDENCE),
    "comparison_visual": (NeedKind.REQUIRED_EVIDENCE, Purpose.COMPARISON),
}
_STORY_PURPOSE: dict[str, tuple[NeedKind, Purpose]] = {
    "demonstration": (NeedKind.DEMONSTRATION, Purpose.DEMONSTRATION),
    "evidence": (NeedKind.REQUIRED_EVIDENCE, Purpose.EVIDENCE),
    "comparison": (NeedKind.REQUIRED_EVIDENCE, Purpose.COMPARISON),
    "illustration": (NeedKind.ILLUSTRATIVE, Purpose.ILLUSTRATION),
}


@dataclass(frozen=True, slots=True)
class NeedDecision:
    kind: NeedKind
    purpose: Purpose
    priority: Priority
    requires: str
    reasons: tuple[str, ...]
    #: For ``NO_VISUAL_NEEDED``: a stable code and the reason in words.
    no_change_code: str = ""


def decide(target: TargetFact, profile: BrollProfile) -> NeedDecision:
    skip = _keep_picture(target, profile)
    if skip is not None:
        return skip
    match target.origin:
        case TargetOrigin.STORY_VISUAL:
            return _from_story(target)
        case TargetOrigin.CONTENT_OPPORTUNITY:
            return _from_content(target, profile)
        case TargetOrigin.PACING:
            return _variety(target, profile)
        case TargetOrigin.JUMP_CUT:
            return _jump_cut(target, profile)


def _keep_picture(target: TargetFact, profile: BrollProfile) -> NeedDecision | None:
    if target.purpose in _GRAPHIC_PURPOSES or target.category in _GRAPHIC_CATEGORIES:
        return _none(
            "graphic_not_footage",
            f"'{target.concept or target.text[:40]}' calls for a graphic, not footage "
            "(requested from the graphics stage, not searched here)",
        )
    if target.already_covered:
        return _none(
            "already_covered",
            "the content analysis found that footage already shows this",
        )
    main = target.main
    if (
        profile.protect_speaker
        and main is not None
        and main.speaking_face is True
        and target.emphatic is True
        and target.necessity != "essential"
    ):
        return _none(
            "protect_speaker",
            "the speaker's face carries emphatic delivery here; covering it would lose the "
            "expression",
        )
    if (
        target.origin is not TargetOrigin.JUMP_CUT
        and target.length < profile.min_insert_seconds * 0.8
    ):
        return _none(
            "passage_too_short",
            f"the passage lasts {target.length:.1f} s, less than an insert of "
            f"{profile.min_insert_seconds:.1f} s needs",
        )
    return None


def _none(code: str, reason: str) -> NeedDecision:
    return NeedDecision(
        kind=NeedKind.NO_VISUAL_NEEDED,
        purpose=Purpose.ILLUSTRATION,
        priority=Priority.LOW,
        requires="",
        reasons=(reason,),
        no_change_code=code,
    )


def _priority(strength: float | None, essential: bool) -> Priority:
    if essential or (strength is not None and strength >= 0.7):
        return Priority.HIGH
    if strength is not None and strength >= 0.4:
        return Priority.MEDIUM
    return Priority.LOW


def _from_story(target: TargetFact) -> NeedDecision:
    essential = target.necessity == "essential"
    kind, purpose = _STORY_PURPOSE.get(
        target.purpose, (NeedKind.ILLUSTRATIVE, Purpose.ILLUSTRATION)
    )
    reasons = [f"the story asks for {target.purpose or 'illustration'}"]
    if essential:
        reasons.append("the passage cannot work without the picture (essential)")
        if kind is NeedKind.ILLUSTRATIVE:
            kind = NeedKind.REQUIRED_EVIDENCE
    return NeedDecision(
        kind=kind,
        purpose=purpose,
        priority=_priority(target.strength, essential),
        requires=_requires(kind, target),
        reasons=tuple(reasons),
    )


def _from_content(target: TargetFact, profile: BrollProfile) -> NeedDecision:
    kind, purpose = _CATEGORY.get(target.category, (NeedKind.ILLUSTRATIVE, Purpose.ILLUSTRATION))
    reasons = [f"the content analysis suggests {target.category or 'supplementary'} footage"]
    if kind is NeedKind.ILLUSTRATIVE and target.names and profile.strict_identity:
        kind, purpose = NeedKind.REQUIRED_EVIDENCE, Purpose.PRODUCT_CLOSEUP
        reasons.append(
            "the passage names a specific product, so footage must be shown to be that product"
        )
    return NeedDecision(
        kind=kind,
        purpose=purpose,
        priority=_priority(target.strength, False),
        requires=_requires(kind, target),
        reasons=tuple(reasons),
    )


def _variety(target: TargetFact, profile: BrollProfile) -> NeedDecision:
    if profile.variety_appetite < 0.15:
        return _none(
            "profile_adds_no_variety",
            f"the {profile.name} profile does not offer a change of picture for variety alone",
        )
    return NeedDecision(
        kind=NeedKind.VARIETY,
        purpose=Purpose.VARIETY,
        priority=Priority.MEDIUM if profile.variety_appetite >= 0.6 else Priority.LOW,
        requires=_requires(NeedKind.VARIETY, target),
        reasons=(target.reason or "the picture does not change for a long time",),
    )


def _jump_cut(target: TargetFact, profile: BrollProfile) -> NeedDecision:
    if not profile.cover_jump_cuts:
        return _none(
            "profile_keeps_jump_cuts",
            f"the {profile.name} profile leaves jump cuts uncovered",
        )
    likelihood = target.main.jump_cut_likelihood if target.main else None
    return NeedDecision(
        kind=NeedKind.JUMP_CUT_COVER,
        purpose=Purpose.JUMP_CUT_COVER,
        priority=Priority.HIGH if likelihood is not None and likelihood >= 0.7 else Priority.MEDIUM,
        requires=_requires(NeedKind.JUMP_CUT_COVER, target),
        reasons=(target.reason or "a cut in the picture is easy to notice",),
    )


def _requires(kind: NeedKind, target: TargetFact) -> str:
    match kind:
        case NeedKind.REQUIRED_EVIDENCE:
            return (
                _EVIDENCE_REQUIRES
                if target.names or any(not r.about_activity for r in target.references)
                else "footage that shows what the passage states (the named object or text)"
            )
        case NeedKind.DEMONSTRATION:
            return _DEMONSTRATION_REQUIRES
        case NeedKind.ILLUSTRATIVE:
            return "footage whose content relates to the passage"
        case NeedKind.VARIETY:
            return "related footage that changes the picture"
        case NeedKind.JUMP_CUT_COVER:
            return "related footage that can hide the cut"
        case NeedKind.NO_VISUAL_NEEDED:
            return ""
