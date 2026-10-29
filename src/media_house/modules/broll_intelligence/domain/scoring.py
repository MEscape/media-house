"""The independent scores of one candidate (definitions: ``values`` module docstring).

Every score is computed from stated facts by a stated rule, alone: a high semantic relevance never
lifts a poor quality score and a missing measurement is ``None`` with its reason, not ``0``.
``overall`` is the weighted mean of the scores that exist (the profile's weights, renormalised
over them) and names them. The ranking gate (``Support``) is applied BEFORE ``overall`` decides
the order, so ``overall`` never lets an attractive shot stand in for the evidence a need requires.
"""

from media_house.modules.broll_intelligence.domain.facts import MainPicture, ShotFact, TargetFact
from media_house.modules.broll_intelligence.domain.match import Assessment
from media_house.modules.broll_intelligence.domain.model import Score
from media_house.modules.broll_intelligence.domain.need import NeedDecision
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.ranges import Chosen
from media_house.modules.broll_intelligence.domain.values import (
    Association,
    AudioBehavior,
    ClockBasis,
    EvidenceRole,
    Identity,
    NeedKind,
    Precision,
    Purpose,
    ScoreBasis,
    ScoreName,
    TargetOrigin,
    UseKind,
)

#: What each role is worth as evidence (a design choice). ``CONTRADICTORY`` is a real 0: the
#: footage is shown to be something else.
ROLE_STRENGTH: dict[EvidenceRole, float] = {
    EvidenceRole.DIRECT_DEMONSTRATION: 0.95,
    EvidenceRole.DIRECT_EVIDENCE: 0.90,
    EvidenceRole.SPECIFIC_SUBJECT: 0.60,
    EvidenceRole.CONTEXTUAL_ILLUSTRATION: 0.35,
    EvidenceRole.GENERIC_ILLUSTRATION: 0.20,
    EvidenceRole.CONTRADICTORY: 0.0,
}
#: How well a role serves what the story asked the place to do.
_PURPOSE_FIT: dict[str, dict[EvidenceRole, float]] = {
    "demonstration": {
        EvidenceRole.DIRECT_DEMONSTRATION: 1.0,
        EvidenceRole.DIRECT_EVIDENCE: 0.8,
        EvidenceRole.SPECIFIC_SUBJECT: 0.5,
        EvidenceRole.CONTEXTUAL_ILLUSTRATION: 0.3,
        EvidenceRole.GENERIC_ILLUSTRATION: 0.15,
    },
    "evidence": {
        EvidenceRole.DIRECT_EVIDENCE: 1.0,
        EvidenceRole.DIRECT_DEMONSTRATION: 0.85,
        EvidenceRole.SPECIFIC_SUBJECT: 0.5,
        EvidenceRole.CONTEXTUAL_ILLUSTRATION: 0.25,
        EvidenceRole.GENERIC_ILLUSTRATION: 0.1,
    },
    "illustration": {
        EvidenceRole.DIRECT_EVIDENCE: 0.9,
        EvidenceRole.DIRECT_DEMONSTRATION: 0.9,
        EvidenceRole.SPECIFIC_SUBJECT: 0.9,
        EvidenceRole.CONTEXTUAL_ILLUSTRATION: 0.85,
        EvidenceRole.GENERIC_ILLUSTRATION: 0.55,
    },
}
_ASSOCIATION_TRUST: dict[Association, float] = {
    Association.OBSERVED_TEXT: 0.9,
    Association.ENTITY_LABEL: 0.8,
    Association.DESCRIBED: 0.7,
    Association.METADATA: 0.6,
    Association.SEMANTIC: 0.45,
    Association.NONE: 0.3,
}
_IDENTITY_TRUST: dict[Identity, float] = {
    Identity.CONFIRMED: 1.0,
    Identity.NOT_APPLICABLE: 1.0,
    Identity.UNVERIFIED: 0.7,
    Identity.REFERENCE_UNRESOLVED: 0.6,
    Identity.CONFLICT: 0.3,
}
_FRAMING_FIT: dict[Purpose, dict[str, float]] = {
    Purpose.PRODUCT_CLOSEUP: {"extreme_close_up": 1.0, "close_up": 1.0, "medium": 0.6, "wide": 0.3},
    Purpose.SCREEN_CONTENT: {"close_up": 1.0, "medium": 0.8, "extreme_close_up": 0.6, "wide": 0.4},
    Purpose.ESTABLISHING: {"wide": 1.0, "medium": 0.6, "close_up": 0.3, "extreme_close_up": 0.2},
    Purpose.DEMONSTRATION: {"medium": 1.0, "close_up": 0.8, "wide": 0.6, "extreme_close_up": 0.5},
}
_DEFAULT_FRAMING: dict[str, float] = {
    "medium": 0.9,
    "close_up": 0.8,
    "wide": 0.8,
    "extreme_close_up": 0.6,
}


def _s(name: ScoreName, value: float | None, basis: ScoreBasis, reason: str) -> Score:
    return Score(
        name=name,
        value=None if value is None else round(max(0.0, min(1.0, value)), 3),
        basis=basis,
        reason=reason,
    )


def use_for(
    target: TargetFact, need: NeedDecision, shot: ShotFact, assessment: Assessment
) -> UseKind:
    match need.purpose:
        case Purpose.DEMONSTRATION:
            return UseKind.INTERCUT_DEMONSTRATION
        case Purpose.PRODUCT_CLOSEUP:
            return UseKind.PRODUCT_CLOSEUP
        case Purpose.ESTABLISHING:
            return UseKind.ESTABLISHING_SHOT
        case Purpose.COMPARISON:
            return UseKind.COMPARISON_INSERT
        case Purpose.SCREEN_CONTENT:
            return UseKind.SCREEN_RECORDING
        case Purpose.EVIDENCE:
            if shot.screen_kind and assessment.association is Association.OBSERVED_TEXT:
                return UseKind.SCREEN_RECORDING
            return UseKind.CUTAWAY
        case Purpose.VARIETY | Purpose.JUMP_CUT_COVER:
            return UseKind.CUTAWAY
        case Purpose.ILLUSTRATION:
            return UseKind.COVER_OVER_NARRATION


def audio_plan(
    shot: ShotFact, chosen: Chosen, need: NeedDecision
) -> tuple[AudioBehavior, str, Score]:
    """What to do with the footage's own sound, and the score that says how well it suits."""
    name = ScoreName.AUDIO_SUITABILITY
    start, end = chosen.start, chosen.end
    synced = [
        a
        for a in shot.sound_actions
        if start <= a.video_time < end and a.grade in ("corroborated", "indicated")
    ]
    if (
        synced
        and shot.sync_established is not False
        and need.kind
        in (
            NeedKind.DEMONSTRATION,
            NeedKind.REQUIRED_EVIDENCE,
        )
    ):
        kinds = ", ".join(sorted({a.audio_kind for a in synced}))
        return (
            AudioBehavior.RETAIN_SOURCE_AUDIO,
            f"the footage's {kinds} sound is aligned with the action it shows",
            _s(name, 1.0, ScoreBasis.DERIVED, f"alignment pairs a {kinds} sound with the action"),
        )
    if shot.audio is None:
        return (
            AudioBehavior.FLAG_FOR_EDITOR,
            "the footage's own sound was not analysed",
            _s(name, None, ScoreBasis.DERIVED, "the footage's sound was not analysed"),
        )
    speech = [(a, b) for a, b in shot.audio.speech if a < end and b > start]
    if speech:
        seconds = float(sum(min(b, end) - max(a, start) for a, b in speech))
        return (
            AudioBehavior.MUTE_SOURCE,
            f"{seconds:.1f} s of speech in the footage would clash with the narration",
            _s(name, 0.3, ScoreBasis.DERIVED, f"{seconds:.1f} s of speech in the range"),
        )
    events = [e for e in shot.audio.events if e[1] < end and e[2] > start]
    if events and need.kind is NeedKind.DEMONSTRATION:
        return (
            AudioBehavior.FLAG_FOR_EDITOR,
            "the footage has action sounds, but nothing shows they belong to the action",
            _s(
                name,
                0.6,
                ScoreBasis.DERIVED,
                "sound events present, synchronisation not established",
            ),
        )
    return (
        AudioBehavior.RETAIN_VOICEOVER,
        "no speech in the footage; the narration carries on",
        _s(name, 0.9, ScoreBasis.DERIVED, "no speech in the range"),
    )


def score_candidate(
    target: TargetFact,
    need: NeedDecision,
    shot: ShotFact,
    assessment: Assessment,
    chosen: Chosen,
    profile: BrollProfile,
    audio: Score,
    novelty: float | None,
    novelty_reason: str,
) -> tuple[Score, ...]:
    scores = [
        _semantic(assessment),
        _evidence(assessment),
        _narrative(target, assessment),
        _quality(shot),
        _subject(shot),
        _composition(shot, need),
        _activity(shot, need),
        _temporal(chosen),
        _continuity(target.main, shot),
        _duration(chosen),
        _s(ScoreName.NOVELTY, novelty, ScoreBasis.DERIVED, novelty_reason),
        audio,
    ]
    scores.append(_confidence(scores, assessment, chosen, shot))
    return tuple(scores)


def overall(scores: tuple[Score, ...], profile: BrollProfile) -> tuple[float | None, str]:
    weights = dict(profile.weights)
    used = [(s, weights.get(s.name, 0.0)) for s in scores if s.value is not None]
    used = [(s, w) for s, w in used if w > 0]
    if not used:
        return None, "no ranking score could be stated"
    total = sum(w for _, w in used)
    value = sum((s.value or 0.0) * w for s, w in used) / total
    left_out = [s.name.value for s in scores if s.value is None and weights.get(s.name, 0) > 0]
    note = f"weighted mean of {len(used)} scores"
    if left_out:
        note += f"; not stated and left out: {', '.join(left_out)}"
    return round(value, 3), note


# --- the scores ------------------------------------------------------------------------------
def _semantic(a: Assessment) -> Score:
    basis = (
        ScoreBasis.MODEL_ESTIMATED
        if "visual_embedding" in a.reason_codes or "text_embedding" in a.reason_codes
        else ScoreBasis.DERIVED
    )
    return _s(ScoreName.SEMANTIC_RELEVANCE, a.semantic, basis, a.semantic_reason)


def _evidence(a: Assessment) -> Score:
    if a.role is EvidenceRole.UNCERTAIN:
        return _s(
            ScoreName.EVIDENCE_STRENGTH,
            None,
            ScoreBasis.DERIVED,
            "nothing in the footage's observations establishes what it shows",
        )
    base = ROLE_STRENGTH[a.role]
    coverage = a.coverage if a.coverage is not None else 0.5
    value = base * (0.7 + 0.3 * coverage) if a.role is not EvidenceRole.CONTRADICTORY else 0.0
    return _s(
        ScoreName.EVIDENCE_STRENGTH,
        value,
        ScoreBasis.DERIVED,
        f"role {a.role.value} via {a.association.value}, identity {a.identity.value}",
    )


def _narrative(target: TargetFact, a: Assessment) -> Score:
    if target.origin is not TargetOrigin.STORY_VISUAL:
        return _s(
            ScoreName.NARRATIVE_RELEVANCE,
            None,
            ScoreBasis.HEURISTIC,
            "no story plan names this place: narrative fit cannot be judged",
        )
    fit = _PURPOSE_FIT.get(target.purpose or "illustration", _PURPOSE_FIT["illustration"])
    base = fit.get(a.role, 0.0 if a.role is EvidenceRole.CONTRADICTORY else 0.1)
    weight = 0.8 + 0.2 * (target.importance if target.importance is not None else 0.5)
    return _s(
        ScoreName.NARRATIVE_RELEVANCE,
        base * weight,
        ScoreBasis.HEURISTIC,
        f"{a.role.value} for a story place that asks for {target.purpose or 'illustration'}",
    )


def _quality(shot: ShotFact) -> Score:
    parts = [
        v
        for v in (
            shot.score("visual_quality"),
            shot.score("technical_usability"),
            shot.score("stability"),
        )
        if v is not None
    ]
    if not parts:
        return _s(ScoreName.VISUAL_QUALITY, None, ScoreBasis.MEASURED, "quality was not measured")
    return _s(
        ScoreName.VISUAL_QUALITY,
        sum(parts) / len(parts),
        ScoreBasis.MEASURED,
        f"mean of {len(parts)} measured quality scores",
    )


def _subject(shot: ShotFact) -> Score:
    value = shot.score("subject_visibility")
    return _s(
        ScoreName.SUBJECT_VISIBILITY,
        value,
        ScoreBasis.MEASURED,
        "the analysis's subject-visibility score" if value is not None else "not measured",
    )


def _composition(shot: ShotFact, need: NeedDecision) -> Score:
    composition = shot.score("composition")
    fits = _FRAMING_FIT.get(need.purpose, _DEFAULT_FRAMING)
    fit = fits.get(shot.framing) if shot.framing else None
    if composition is None and fit is None:
        return _s(
            ScoreName.COMPOSITION_SUITABILITY, None, ScoreBasis.DERIVED, "framing was not measured"
        )
    parts = [v for v in (composition, fit) if v is not None]
    return _s(
        ScoreName.COMPOSITION_SUITABILITY,
        sum(parts) / len(parts),
        ScoreBasis.DERIVED,
        f"framing {shot.framing or 'unknown'} against what {need.purpose.value} wants"
        + (", with the measured composition" if composition is not None else ""),
    )


def _activity(shot: ShotFact, need: NeedDecision) -> Score:
    levels: list[float] = []
    if shot.static_fraction is not None:
        levels.append(1.0 - shot.static_fraction)
    action = [e for e in shot.events if e.kind != "shot_boundary"]
    if shot.events or shot.static_fraction is not None:
        levels.append(min(1.0, len(action) / 3))
    if not levels:
        return _s(ScoreName.VISUAL_ACTIVITY, None, ScoreBasis.DERIVED, "movement was not measured")
    level = sum(levels) / len(levels)
    wanted = (
        0.7
        if need.kind in (NeedKind.DEMONSTRATION, NeedKind.REQUIRED_EVIDENCE)
        else 0.3
        if need.purpose is Purpose.ESTABLISHING
        else 0.5
    )
    return _s(
        ScoreName.VISUAL_ACTIVITY,
        1.0 - abs(level - wanted),
        ScoreBasis.DERIVED,
        f"activity {level:.2f} against the {wanted:.1f} that {need.purpose.value} wants",
    )


def _temporal(chosen: Chosen) -> Score:
    return _s(
        ScoreName.TEMPORAL_SUITABILITY, chosen.temporal, ScoreBasis.DERIVED, chosen.temporal_reason
    )


def _duration(chosen: Chosen) -> Score:
    wanted = float(chosen.wanted)
    usable = float(chosen.usable)
    value = min(1.0, usable / wanted) if wanted > 0 else 1.0
    return _s(
        ScoreName.DURATION_SUITABILITY,
        value,
        ScoreBasis.DERIVED,
        f"{usable:.1f} s usable for an insert of about {wanted:.1f} s",
    )


def _continuity(main: MainPicture | None, shot: ShotFact) -> Score:
    reason = (
        "basic comparison of environment and light with the picture it interrupts; "
        "not a continuity assessment"
    )
    if main is None:
        return _s(
            ScoreName.CONTINUITY_COMPATIBILITY,
            None,
            ScoreBasis.HEURISTIC,
            "the covered picture is unknown",
        )
    pairs: list[float] = []
    if main.environment and shot.environment:
        pairs.append(1.0 if main.environment == shot.environment else 0.3)
    if main.indoor_outdoor and shot.indoor_outdoor:
        pairs.append(1.0 if main.indoor_outdoor == shot.indoor_outdoor else 0.2)
    if not pairs:
        return _s(
            ScoreName.CONTINUITY_COMPATIBILITY,
            None,
            ScoreBasis.HEURISTIC,
            "neither shot's environment is known",
        )
    related = any(
        r.kind in ("alternate_take_of", "temporally_overlaps_with") and r.certainty != "detected"
        for r in shot.relations
    )
    value = sum(pairs) / len(pairs)
    if related:
        value = min(1.0, value + 0.1)
        reason += "; a person confirmed the recordings belong together"
    return _s(ScoreName.CONTINUITY_COMPATIBILITY, value, ScoreBasis.HEURISTIC, reason)


def _confidence(scores: list[Score], a: Assessment, chosen: Chosen, shot: ShotFact) -> Score:
    measurable = [s for s in scores if s.name not in (ScoreName.NOVELTY, ScoreName.CONFIDENCE)]
    stated = sum(1 for s in measurable if s.value is not None) / len(measurable)
    association = _ASSOCIATION_TRUST[
        a.association
        if a.association is not Association.NONE or a.semantic is None
        else Association.SEMANTIC
    ]
    identity = _IDENTITY_TRUST[a.identity]
    precision = 1.0 if chosen.precision is Precision.SHOT_BOUNDARY else 0.9
    clock = 1.0 if shot.clock is ClockBasis.CONTAINER else 0.95
    value = association * identity * precision * clock * (0.5 + 0.5 * stated)
    return _s(
        ScoreName.CONFIDENCE,
        value,
        ScoreBasis.HEURISTIC,
        f"{stated:.0%} of the scores stated; association {a.association.value}, identity "
        f"{a.identity.value}, range {chosen.precision.value}, clock {shot.clock.value}",
    )
