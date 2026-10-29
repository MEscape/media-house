"""Does this shot genuinely support this passage? The evidence assessment.

A shot is judged on what its OWN observations say, in this order of trust:

1. text read from the picture, a detected object, or the asset's own name/tags naming the thing;
2. a scene description (a model's words: useful, never proof of identity);
3. the embedding models' similarity (a related scene: meaning, not evidence).

From that the shot gets an ``EvidenceRole`` (see ``values``). The rules that keep it honest:

* a passage naming a specific product (or pointing at one: "this model") needs the shot to name
  the SAME one; a shot naming another model of the same line is ``CONTRADICTORY``, a shot that
  says nothing about which one it is cannot exceed ``SPECIFIC_SUBJECT``;
* a demonstration needs the action itself in the shot (events or measured movement); the right
  subject standing still is a subject, not a demonstration;
* an association found only by the embedding is a ``CONTEXTUAL``/``GENERIC`` illustration;
* the description's words never confirm identity.

Everything is derived from stated facts by stated thresholds; the thresholds are design choices
(``CORE_COVERAGE`` ...), not calibrated values.
"""

import math
from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.broll_intelligence.domain.facts import (
    Match,
    NameFact,
    ShotFact,
    TargetFact,
)
from media_house.modules.broll_intelligence.domain.model import EvidenceRef
from media_house.modules.broll_intelligence.domain.need import NeedDecision
from media_house.modules.broll_intelligence.domain.profile import BrollProfile
from media_house.modules.broll_intelligence.domain.text import query_terms, reduce, shot_words
from media_house.modules.broll_intelligence.domain.values import (
    Association,
    EvidenceRole,
    Identity,
    NeedKind,
    Purpose,
)

#: Share of the concept's (weighted) words a shot must show to count as showing the subject: two
#: of three, so one shared noun ("board") never turns a different job into the demonstration.
CORE_COVERAGE = 0.67
#: Semantic relevance from which a related scene counts as a contextual illustration.
CONTEXTUAL_MIN = 0.55
#: A standard-score of 2 (unusually close for this passage) maps to a relevance of 0.5.
VISUAL_Z_CENTER = 2.0
VISUAL_Z_SCALE = 0.8
#: CLIP ViT-B/32 text-image cosine: about 0.18 for unrelated, 0.34 for a clear match.
VISUAL_COS_LOW, VISUAL_COS_HIGH = 0.18, 0.34
#: Paraphrase-MiniLM description/passage cosine: about 0.25 unrelated, 0.75 a clear match.
TEXT_COS_LOW, TEXT_COS_HIGH = 0.25, 0.75
#: How far each signal alone can carry the semantic relevance (a design choice).
LEXICAL_WEIGHT, VISUAL_WEIGHT, TEXT_WEIGHT = 0.9, 0.8, 0.6
#: A shot with at most this share of static frames shows movement.
MOVEMENT_STATIC_MAX = 0.5
#: A visible face that speaks in at least this share of the shot is a talking shot.
SPEAKING_FACE_MIN = 0.3

_ACTION_EVENTS = frozenset(
    {
        "gesture",
        "motion_peak",
        "scrolling",
        "screen_content_change",
        "track_entered",
        "track_exited",
    }
)
_PLACES = (
    Association.OBSERVED_TEXT,
    Association.ENTITY_LABEL,
    Association.DESCRIBED,
    Association.METADATA,
)


@dataclass(frozen=True, slots=True)
class Assessment:
    association: Association
    #: Share of the concept-level words found (``None`` = the shot has no word observations).
    coverage: float | None
    hits: tuple[str, ...]
    identity: Identity
    #: ``observed`` (events), ``movement`` (measured, no event), ``none`` (static), ``unknown``.
    activity: str
    role: EvidenceRole
    semantic: float | None
    semantic_reason: str
    reason_codes: tuple[str, ...]
    warnings: tuple[str, ...]
    #: Hard reasons the shot cannot be used (each a stable code and a sentence).
    refusals: tuple[tuple[str, str], ...]
    evidence: tuple[EvidenceRef, ...]


def assess(
    target: TargetFact,
    need: NeedDecision,
    shot: ShotFact,
    match: Match | None,
    profile: BrollProfile,
) -> Assessment:
    codes: list[str] = []
    warnings: list[str] = []
    evidence: list[EvidenceRef] = [
        EvidenceRef(kind="shot", ref=shot.shot_id, asset_id=shot.asset_id)
    ]

    core, overall, association, hits = _words(target, shot, evidence)
    if hits:
        codes.append(f"words_found:{association.value}")
    visual = _visual(match)
    text = _text(match)
    if match is not None and match.visual_similarity is not None:
        evidence.append(
            EvidenceRef(
                kind="visual_embedding",
                ref=shot.shot_id,
                asset_id=shot.asset_id,
                detail=_visual_detail(match),
            )
        )
        codes.append("visual_embedding")
    if text is not None:
        evidence.append(
            EvidenceRef(kind="text_embedding", ref=shot.shot_id, asset_id=shot.asset_id)
        )
        codes.append("text_embedding")
    semantic, why = _semantic(overall, visual, text)

    identity, identity_note = _identity(target, shot, evidence)
    codes.append(f"identity:{identity.value}")
    if identity_note:
        warnings.append(identity_note)

    activity = _activity(shot)
    if activity in ("observed", "movement"):
        codes.append(f"action:{activity}")
    wants_action = _wants_action(target, need)

    role = _role(
        target, need, profile, core, association, identity, activity, semantic, wants_action,
        codes, warnings,
    )  # fmt: skip
    if target.negated:
        warnings.append(
            "the passage negates, doubts or imagines what it names; footage could imply "
            "something the narration does not say"
        )
        codes.append("passage_negates")
    for ref in target.references:
        if ref.state != "resolved" and not ref.about_activity:
            codes.append("reference_unresolved")
            break
    return Assessment(
        association=association,
        coverage=core,
        hits=hits,
        identity=identity,
        activity=activity,
        role=role,
        semantic=semantic,
        semantic_reason=why,
        reason_codes=tuple(dict.fromkeys(codes)),
        warnings=tuple(dict.fromkeys(warnings)),
        refusals=_refusals(target, need, shot, profile, role, semantic, association),
        evidence=tuple(evidence[:10]),
    )


# --- words -----------------------------------------------------------------------------------
def _words(
    target: TargetFact, shot: ShotFact, evidence: list[EvidenceRef]
) -> tuple[float | None, float | None, Association, tuple[str, ...]]:
    terms = query_terms(target)
    words = shot_words(shot)
    if not words or not terms:
        return None, None, Association.NONE, ()
    found = [(t, w) for t, w in terms if t in words]
    total = sum(w for _, w in terms)
    overall = sum(w for _, w in found) / total
    # Names are judged by identity, not by words: a passage comparing two models would otherwise
    # make every shot of one of them look half unrelated.
    named = {w for n in target.names for w in (*reduce(n.family), n.value.replace(" ", ""))}
    core_terms = (
        [(t, w) for t, w in terms if w >= 1.0 and t not in named]
        or [(t, w) for t, w in terms if w >= 1.0]
        or list(terms)
    )
    core_total = sum(w for _, w in core_terms)
    core = sum(w for t, w in core_terms if t in words) / core_total
    association = Association.NONE
    for place in _PLACES:
        if any(words[t] == place.value for t, _ in found):
            association = place
            break
    if found:
        _words_evidence(shot, [t for t, _ in found], words, evidence)
    return core, overall, association, tuple(t for t, _ in found)


def _words_evidence(
    shot: ShotFact, hits: list[str], words: dict[str, str], evidence: list[EvidenceRef]
) -> None:
    places = {words[t] for t in hits}
    if Association.OBSERVED_TEXT.value in places and shot.texts:
        evidence.append(
            EvidenceRef(
                kind="ocr_text",
                ref=shot.shot_id,
                asset_id=shot.asset_id,
                detail="; ".join(shot.texts[:3])[:120],
            )
        )
    if Association.ENTITY_LABEL.value in places and shot.objects:
        evidence.append(
            EvidenceRef(
                kind="object_label",
                ref=shot.shot_id,
                asset_id=shot.asset_id,
                detail=", ".join(shot.objects[:5]),
            )
        )
    if Association.DESCRIBED.value in places and shot.description:
        evidence.append(
            EvidenceRef(
                kind="description",
                ref=shot.shot_id,
                asset_id=shot.asset_id,
                detail=shot.description[:120],
            )
        )
    if Association.METADATA.value in places:
        evidence.append(EvidenceRef(kind="metadata", ref=shot.asset_name, asset_id=shot.asset_id))


# --- the embedding signals -------------------------------------------------------------------
def _visual(match: Match | None) -> float | None:
    if match is None:
        return None
    if match.visual_z is not None:
        return 1.0 / (1.0 + math.exp(-(match.visual_z - VISUAL_Z_CENTER) / VISUAL_Z_SCALE))
    if match.visual_similarity is not None:
        return _clip(
            (match.visual_similarity - VISUAL_COS_LOW) / (VISUAL_COS_HIGH - VISUAL_COS_LOW)
        )
    return None


def _visual_detail(match: Match) -> str:
    parts = []
    if match.visual_similarity is not None:
        parts.append(f"cosine {match.visual_similarity:.3f}")
    if match.visual_z is not None:
        parts.append(f"z {match.visual_z:.2f}")
    return ", ".join(parts)


def _text(match: Match | None) -> float | None:
    if match is None or match.text_similarity is None:
        return None
    return _clip((match.text_similarity - TEXT_COS_LOW) / (TEXT_COS_HIGH - TEXT_COS_LOW))


def _clip(value: float) -> float:
    return max(0.0, min(1.0, value))


def _semantic(
    lexical: float | None, visual: float | None, text: float | None
) -> tuple[float | None, str]:
    """Relevance supported by any of the independent signals (a noisy-or, not an average).

    A signal that found nothing is weak evidence of irrelevance (descriptions are incomplete and an
    unlabelled shot is not an unrelated one), so it adds nothing rather than pulling a strong
    signal down; two signals that agree raise the value above either.
    """
    parts = [
        ("words", lexical, LEXICAL_WEIGHT),
        ("picture embedding", visual, VISUAL_WEIGHT),
        ("description embedding", text, TEXT_WEIGHT),
    ]
    present = [(n, v, w) for n, v, w in parts if v is not None]
    if not present:
        return None, "no word observation and no embedding for this shot"
    miss = 1.0
    for _, value, weight in present:
        miss *= 1.0 - value * weight
    label = ", ".join(f"{n} {v:.2f}" for n, v, _ in present)
    return round(_clip(1.0 - miss), 3), f"supported by {label}"


# --- identity --------------------------------------------------------------------------------
def same(a: NameFact, b: NameFact) -> bool:
    return a.value == b.value and (a.family == b.family or not a.family or not b.family)


def conflicts(a: NameFact, b: NameFact) -> bool:
    if a.value == b.value:
        return False
    if a.family and b.family:
        return a.family == b.family
    return bool(a.stem) and a.stem == b.stem


def _identity(
    target: TargetFact, shot: ShotFact, evidence: list[EvidenceRef]
) -> tuple[Identity, str]:
    unresolved_product = any(
        r.state != "resolved" and not r.about_activity for r in target.references
    )
    if not target.names:
        if unresolved_product:
            return (
                Identity.REFERENCE_UNRESOLVED,
                "the passage points at something it does not name; the footage cannot be "
                "shown to be the right one",
            )
        return Identity.NOT_APPLICABLE, ""
    strong = [n for n, place in shot.names if place in ("text", "metadata")]
    weak = [n for n, place in shot.names if place == "description"]
    if any(same(t, s) for t in target.names for s in strong):
        evidence.append(
            EvidenceRef(
                kind="metadata" if all(p == "metadata" for _, p in shot.names) else "ocr_text",
                ref=shot.shot_id,
                asset_id=shot.asset_id,
                detail="names " + ", ".join(n.text for n in strong[:3]),
            )
        )
        return Identity.CONFIRMED, ""
    clash = next((s for t in target.names for s in strong if conflicts(t, s)), None)
    if clash is not None:
        evidence.append(
            EvidenceRef(
                kind="ocr_text", ref=shot.shot_id, asset_id=shot.asset_id, detail=clash.text
            )
        )
        return (
            Identity.CONFLICT,
            f"the footage names {clash.text}, the passage names "
            + ", ".join(n.text for n in target.names),
        )
    note = "nothing in the footage says which one it is"
    if any(conflicts(t, s) for t in target.names for s in weak):
        note += " (its description names another, which is not proof)"
    return Identity.UNVERIFIED, note


# --- action ----------------------------------------------------------------------------------
def _activity(shot: ShotFact) -> str:
    if any(e.kind in _ACTION_EVENTS for e in shot.events):
        return "observed"
    if shot.static_fraction is not None:
        return "movement" if shot.static_fraction <= MOVEMENT_STATIC_MAX else "none"
    return "unknown"


def _wants_action(target: TargetFact, need: NeedDecision) -> bool:
    return need.kind is NeedKind.DEMONSTRATION or need.purpose is Purpose.DEMONSTRATION


# --- the role --------------------------------------------------------------------------------
def _role(
    target: TargetFact,
    need: NeedDecision,
    profile: BrollProfile,
    core: float | None,
    association: Association,
    identity: Identity,
    activity: str,
    semantic: float | None,
    wants_action: bool,
    codes: list[str],
    warnings: list[str],
) -> EvidenceRole:
    if identity is Identity.CONFLICT:
        return EvidenceRole.CONTRADICTORY
    shows_subject = (
        association in (Association.OBSERVED_TEXT, Association.ENTITY_LABEL, Association.DESCRIBED)
        and core is not None
        and core >= CORE_COVERAGE
    )
    named = identity in (Identity.CONFIRMED, Identity.UNVERIFIED, Identity.REFERENCE_UNRESOLVED)
    if shows_subject:
        if identity is Identity.CONFIRMED or (
            not named and association is Association.OBSERVED_TEXT
        ):
            if wants_action and activity in ("observed", "movement"):
                return EvidenceRole.DIRECT_DEMONSTRATION
            if wants_action:
                warnings.append("the subject is shown, but no action is observed in the footage")
                codes.append("action_not_observed")
                return EvidenceRole.SPECIFIC_SUBJECT
            return EvidenceRole.DIRECT_EVIDENCE
        if not named and wants_action and activity in ("observed", "movement"):
            return EvidenceRole.DIRECT_DEMONSTRATION
        if wants_action and not named:
            warnings.append("the subject is shown, but no action is observed in the footage")
            codes.append("action_not_observed")
        return EvidenceRole.SPECIFIC_SUBJECT
    if semantic is not None and semantic >= CONTEXTUAL_MIN:
        return EvidenceRole.CONTEXTUAL_ILLUSTRATION
    if semantic is not None and semantic >= profile.min_semantic:
        return EvidenceRole.GENERIC_ILLUSTRATION
    return EvidenceRole.UNCERTAIN


# --- refusals --------------------------------------------------------------------------------
def _refusals(
    target: TargetFact,
    need: NeedDecision,
    shot: ShotFact,
    profile: BrollProfile,
    role: EvidenceRole,
    semantic: float | None,
    association: Association,
) -> tuple[tuple[str, str], ...]:
    out: list[tuple[str, str]] = []
    if shot.asset_id == target.asset_id and _overlaps(shot, target):
        out.append(("main_shot", "it is the picture already on screen during the passage"))
    if shot.end <= shot.start:
        out.append(("empty_shot", "the shot has no length"))
    if shot.asset_duration is not None and shot.start >= shot.asset_duration:
        out.append(("past_end", "the shot lies beyond the end of the recording"))
    usability = shot.score("technical_usability")
    if usability is not None and usability < profile.min_usability:
        out.append(
            (
                "low_usability",
                f"technical usability {usability:.2f} is below {profile.min_usability:.2f}",
            )
        )
    if shot.indicators:
        out.append(("flagged", "flagged as " + ", ".join(shot.indicators)))
    if (
        profile.refuse_speaking_faces
        and shot.visual_speaking is not None
        and shot.visual_speaking >= SPEAKING_FACE_MIN
    ):
        out.append(
            ("speaking_face", "a visible face speaks in it; its lips would not match the narration")
        )
    if role is EvidenceRole.CONTRADICTORY:
        out.append(("contradicts", "the footage shows something other than what the passage names"))
    if association is Association.NONE and (semantic is None or semantic < profile.min_semantic):
        out.append(("unrelated", "nothing in it relates to the passage"))
    return tuple(out)


def _overlaps(shot: ShotFact, target: TargetFact) -> bool:
    start = Fraction(target.start).limit_denominator(1_000_000)
    end = Fraction(target.end).limit_denominator(1_000_000)
    return shot.start < end and shot.end > start
