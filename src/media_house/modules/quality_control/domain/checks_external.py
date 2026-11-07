"""What other modules already found, said once: the mix engine's validation, the continuity
analysis, and the story plan compared with what is on the timeline.

Quality control carries these findings with their own certainty; it does not run those analyses
again and it does not overrule them. A finding the owner calls a recommendation, or probably
deliberate style, is reported as an opinion.
"""

from dataclasses import dataclass

from media_house.modules.quality_control.domain.classify import make_issue
from media_house.modules.quality_control.domain.facts import (
    ContinuityFacts,
    MixFacts,
    StoryFacts,
    TimelineFacts,
)
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.values import Basis, Severity

_MIX_SEVERITY = {"critical": Severity.CRITICAL, "error": Severity.HIGH, "warning": Severity.MEDIUM}
_CERTAINTY = {"confirmed": 0.9, "likely": 0.65, "recommendation": 0.4}
_LEVEL = {"high": Severity.HIGH, "medium": Severity.MEDIUM, "low": Severity.LOW}


def carry_mix(timeline: TimelineFacts, mix: MixFacts | None) -> list[QualityIssue]:
    if mix is None:
        return []
    issues: list[QualityIssue] = []
    for found in mix.issues:
        severity = _MIX_SEVERITY.get(found.severity)
        if severity is None or found.certainty == "intentional":
            continue
        start = timeline.frame_at(found.start_seconds) if found.start_seconds is not None else None
        end = timeline.frame_at(found.end_seconds) if found.end_seconds is not None else start
        sound = next((s for s in timeline.sounds if s.clip_id == found.element_id), None)
        issues.append(
            make_issue(
                timeline,
                "audio.mix_finding",
                found.message,
                severity=severity,
                confidence=_CERTAINTY.get(found.certainty, 0.5),
                basis=Basis.SUBJECTIVE if found.certainty == "recommendation" else Basis.OBJECTIVE,
                start_frame=start,
                end_frame=end,
                tracks=(sound.track_id,) if sound else (),
                analysis_refs=(mix.mix_result_id,),
                evidence=(f"{found.certainty}: {found.code}",),
                origin="audio_mix_engine",
                code_name=f"audio.mix.{found.code}",
                key=found.element_id or "",
            )
        )
    return issues


def carry_continuity(
    timeline: TimelineFacts, continuity: ContinuityFacts | None
) -> list[QualityIssue]:
    if continuity is None:
        return []
    issues: list[QualityIssue] = []
    for found in continuity.issues:
        severity = _LEVEL.get(found.level)
        if severity is None or not found.surfaced:
            continue
        deliberate = (found.style_likelihood or 0.0) >= 0.5
        sure = (found.detectability or 0.0) >= 0.6
        frame = timeline.frame_at(found.at_seconds) if found.at_seconds is not None else None
        issues.append(
            make_issue(
                timeline,
                "continuity.finding",
                found.explanation,
                severity=severity,
                confidence=found.detectability if found.detectability is not None else 0.4,
                basis=Basis.SUBJECTIVE if deliberate or not sure else Basis.OBJECTIVE,
                start_frame=frame,
                end_frame=frame,
                sections=found.sections,
                why=found.why_it_matters or None,
                correction=f"Suggested repair: {found.recommended}" if found.recommended else None,
                analysis_refs=(continuity.analysis_id,),
                evidence=(
                    f"style likelihood {found.style_likelihood:.2f}"
                    if found.style_likelihood is not None
                    else "style likelihood unknown",
                ),
                origin="continuity_intelligence",
                code_name=f"continuity.{found.kind}",
                key=found.issue_id,
            )
        )
    return issues


@dataclass(frozen=True, slots=True)
class StoryOutcome:
    issues: tuple[QualityIssue, ...]
    #: ``False``: the timeline's sections do not refer to the story plan's, so nothing was compared.
    comparable: bool
    detail: str


def compare_story(timeline: TimelineFacts, story: StoryFacts | None) -> StoryOutcome:
    if story is None:
        return StoryOutcome((), False, "no stored story plan")
    placed = [s.section_id for s in sorted(timeline.sections, key=lambda s: s.start_frame)]
    planned = [s.section_id for s in sorted(story.sections, key=lambda s: s.order)]
    shared = [s for s in planned if s in placed]
    if not shared:
        return StoryOutcome((), False, "the timeline's sections do not refer to the story plan's")
    issues: list[QualityIssue] = []
    labels = {s.section_id: s.label for s in story.sections}
    for section in planned:
        if section not in placed:
            issues.append(
                make_issue(
                    timeline,
                    "story.section_missing",
                    f"The story plan's section '{labels[section]}' is not on the timeline.",
                    confidence=0.5,
                    analysis_refs=(story.plan_id,),
                    key=section,
                    origin="story_architecture",
                )
            )
    in_timeline_order = [s for s in placed if s in planned]
    if shared != in_timeline_order:
        locked = {s.section_id for s in story.sections if s.order_locked}
        moved = [s for s, t in zip(shared, in_timeline_order, strict=False) if s != t]
        issues.append(
            make_issue(
                timeline,
                "story.order_deviates",
                "The sections run in a different order than the story plan recommends: "
                + " > ".join(labels[s] for s in in_timeline_order[:6])
                + ".",
                severity=Severity.HIGH if locked & set(moved) else None,
                confidence=0.7 if locked & set(moved) else 0.5,
                analysis_refs=(story.plan_id,),
                evidence=("order locked by the plan: " + ", ".join(sorted(locked & set(moved))),)
                if locked & set(moved)
                else (),
                origin="story_architecture",
            )
        )
    for link in story.links:
        if link.payoff_section is None or link.setup_section not in placed:
            continue
        gone = link.payoff_section not in placed
        reversed_ = not gone and placed.index(link.payoff_section) < placed.index(
            link.setup_section
        )
        if gone or reversed_:
            issues.append(
                make_issue(
                    timeline,
                    "story.payoff_missing",
                    f"The setup in '{labels.get(link.setup_section, link.setup_section)}' "
                    + ("has no payoff on the timeline." if gone else "comes after its payoff."),
                    confidence=0.6,
                    analysis_refs=(story.plan_id,),
                    key=link.setup_section + link.payoff_section,
                    origin="story_architecture",
                )
            )
    return StoryOutcome(
        tuple(issues), True, f"{len(shared)} of {len(planned)} planned sections compared"
    )
