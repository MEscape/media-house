"""Repair options for a finding: what could change, who would do it, what was looked at.

Continuity executes nothing. A repair is listed only when it makes sense for the kind of problem,
and says honestly how far it was examined (``Availability``):

* ``checked``: something concrete backs it (a cleaner cut point found in the sound, a cutaway the
  B-roll analysis has for this place, a measured correction size);
* ``unchecked``: a direction; no candidate was examined (an alternate take exists but its range
  was not compared);
* ``unavailable``: the module that would do it does not exist, or looked and found nothing.

Repairs are ranked checked before unchecked before unavailable, then by difficulty; the best is
not always right, so ``KEEP_INTENTIONAL`` is offered whenever the style estimate is high.
"""

from dataclasses import replace
from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.continuity_intelligence.domain.detect import Finding
from media_house.modules.continuity_intelligence.domain.facts import TransitionFact
from media_house.modules.continuity_intelligence.domain.model import Repair
from media_house.modules.continuity_intelligence.domain.values import (
    Availability,
    IssueType,
    Owner,
    RepairKind,
    TransitionKind,
)

_ORDER = {Availability.CHECKED: 0, Availability.UNCHECKED: 1, Availability.UNAVAILABLE: 2}
_STYLE_KEEP = 0.5


def _rational(value: Fraction) -> Rational:
    value = max(Fraction(0), value)
    return Rational(value.numerator, value.denominator)


def repairs_for(finding: Finding, t: TransitionFact, style: float) -> tuple[Repair, ...]:
    options: list[Repair] = []
    n = 0

    def add(
        kind: RepairKind,
        owner: Owner,
        availability: Availability,
        description: str,
        difficulty: float | None,
        *tradeoffs: str,
        at: Fraction | None = None,
        conflicts: tuple[str, ...] = (),
    ) -> None:
        nonlocal n
        n += 1
        options.append(
            Repair(
                repair_id=f"rp{n}",
                kind=kind,
                owner=owner,
                availability=availability,
                description=description,
                difficulty=difficulty,
                tradeoffs=tuple(tradeoffs),
                at_source_seconds=None if at is None else _rational(at),
                conflicts=conflicts,
            )
        )

    kind = finding.type
    if kind is IssueType.JUMP_CUT:
        _cut_point(t, add)
        _cover(t, add)
        _bridge(t, add)
        add(
            RepairKind.REFRAME,
            Owner.REFRAMING,
            Availability.UNCHECKED,
            "change the crop on one side so the subject's size differs enough to read as a cut",
            0.3,
            "reframing_intelligence recommends crops per shot and judges the size step between "
            "neighbours; the crops of this cut were not compared here",
        )
    elif kind in (IssueType.VISUAL_DISCONTINUITY, IssueType.NARRATIVE_CONFUSION):
        _cover(t, add)
        _alternate(t, add, "a more compatible shot")
    elif kind is IssueType.LIGHTING_MISMATCH:
        step = finding.peak
        hard = step >= 0.7
        add(
            RepairKind.EXPOSURE_CORRECTION,
            Owner.VIDEO_IMPROVEMENT,
            Availability.UNCHECKED,
            "match the exposure of one side to the other in the grade",
            0.6 if hard else 0.3,
            "only if the difference is not a deliberate change of place or time",
        )
        add(
            RepairKind.COLOR_CORRECTION,
            Owner.VIDEO_IMPROVEMENT,
            Availability.UNCHECKED,
            "match the colour balance of one side to the other in the grade",
            0.6 if hard else 0.3,
            "a large step may not be fully correctable",
        )
        _alternate(t, add, "a better matching shot")
    elif kind in (IssueType.SCREEN_DIRECTION, IssueType.EYELINE):
        _cover(t, add)
        _alternate(t, add, "a shot that keeps the direction or eyeline")
    elif kind in (IssueType.ACTION_CONTINUITY, IssueType.OBJECT_STATE):
        if t.gap is not None and t.gap > 0:
            add(
                RepairKind.ADJUST_CUT_POINT,
                Owner.EDITING,
                Availability.CHECKED,
                f"keep the {float(t.gap):.1f} s the cut skips, or start the later clip earlier",
                0.3,
                "makes the edit longer",
                at=t.incoming.moment - t.gap,
            )
        _alternate(t, add, "a take where the action runs through")
        _cover(t, add)
    elif kind is IssueType.IDENTITY_MISMATCH:
        add(
            RepairKind.CONFIRM_IDENTITY,
            Owner.PERSON,
            Availability.CHECKED,
            "decide whether both sides show the same person or product",
            0.2,
        )
        _alternate(t, add, "a shot of the right person or model")
    elif kind is IssueType.CLIPPED_SPEECH:
        _cut_point(t, add)
        add(
            RepairKind.REVIEW_SPEECH,
            Owner.EDITING,
            Availability.UNCHECKED,
            "listen to the cut and restore the cut-off syllable",
            0.2,
        )
    elif kind is IssueType.AUDIO_DISCONTINUITY:
        add(
            RepairKind.AUDIO_CROSSFADE,
            Owner.AUDIO_IMPROVEMENT,
            Availability.UNCHECKED,
            "crossfade the sound across the cut",
            0.15,
            "can smear a word if it is too long",
        )
        add(
            RepairKind.ROOM_TONE,
            Owner.AUDIO_IMPROVEMENT,
            Availability.UNCHECKED,
            "lay room tone under the cut",
            0.35,
            "needs a clean sample of the room",
        )
        add(
            RepairKind.AMBIENCE_BRIDGE,
            Owner.AUDIO_IMPROVEMENT,
            Availability.UNCHECKED,
            "bridge the step with the ambience of one side",
            0.4,
        )
        _bridge(t, add)
        _cut_point(t, add)
    elif kind is IssueType.SYNC_UNCERTAIN:
        add(
            RepairKind.VERIFY_SYNC,
            Owner.PERSON,
            Availability.CHECKED,
            "check lip sync and action sounds at this cut against the picture",
            0.2,
        )
    elif kind is IssueType.TEMPORAL_INCONSISTENCY:
        add(
            RepairKind.REVIEW_ORDER,
            Owner.EDITING,
            Availability.CHECKED,
            "put the clips in recording order, or mark the reorder as deliberate",
            0.25,
            "changing the order changes the story",
            conflicts=(
                ("the cut is a story section boundary: the story fixes this order",)
                if t.section_change
                else ()
            ),
        )
    if style >= _STYLE_KEEP:
        add(
            RepairKind.KEEP_INTENTIONAL,
            Owner.EDITING,
            Availability.CHECKED,
            "keep the cut: it is likely a deliberate choice of this format",
            0.0,
            "the style estimate is an estimate, not a fact",
        )
    ranked = sorted(
        options,
        key=lambda r: (
            _ORDER[r.availability],
            r.difficulty if r.difficulty is not None else 2.0,
            r.kind.value,
        ),
    )
    return tuple(replace(r, rank=i) for i, r in enumerate(ranked, start=1))


def _cut_point(t: TransitionFact, add) -> None:  # type: ignore[no-untyped-def]
    x, y = t.outgoing.audio, t.incoming.audio
    if x is not None and x.pause_before is not None:
        add(
            RepairKind.ADJUST_CUT_POINT,
            Owner.EDITING,
            Availability.CHECKED,
            f"end the earlier clip in the pause at {float(x.pause_before):.2f} s",
            0.15,
            "changes the length of the pause",
            at=x.pause_before,
        )
    elif y is not None and y.pause_after is not None:
        add(
            RepairKind.ADJUST_CUT_POINT,
            Owner.EDITING,
            Availability.CHECKED,
            f"start the later clip in the pause at {float(y.pause_after):.2f} s",
            0.15,
            "changes the length of the pause",
            at=y.pause_after,
        )


def _cover(t: TransitionFact, add) -> None:  # type: ignore[no-untyped-def]
    if t.covered:
        return
    if t.cover_available is True:
        add(
            RepairKind.BROLL_COVER,
            Owner.BROLL,
            Availability.CHECKED,
            "cover the cut with a cutaway the B-roll analysis found for this place",
            0.25,
            "hides the speaker for the length of the cutaway",
        )
    elif t.cover_available is False:
        add(
            RepairKind.BROLL_COVER,
            Owner.BROLL,
            Availability.UNAVAILABLE,
            "cover the cut with a cutaway",
            0.5,
            "the B-roll analysis found no suitable footage for this place",
        )
    else:
        add(
            RepairKind.BROLL_COVER,
            Owner.BROLL,
            Availability.UNCHECKED,
            "cover the cut with a cutaway",
            0.4,
            "no B-roll analysis was available to say whether footage exists",
        )


def _bridge(t: TransitionFact, add) -> None:  # type: ignore[no-untyped-def]
    if t.bridged:
        return
    x, y = t.outgoing.audio, t.incoming.audio
    if x is not None and y is not None and x.speech_near and y.speech_near:
        add(
            RepairKind.L_CUT,
            Owner.EDITING,
            Availability.CHECKED,
            "let the earlier clip's sound run over the start of the later picture (L-cut)",
            0.3,
            "needs picture handles on the later clip",
        )
        add(
            RepairKind.J_CUT,
            Owner.EDITING,
            Availability.CHECKED,
            "start the later clip's sound before its picture (J-cut)",
            0.3,
            "needs picture handles on the earlier clip",
        )


def _alternate(t: TransitionFact, add, what: str) -> None:  # type: ignore[no-untyped-def]
    if t.related_assets:
        add(
            RepairKind.ALTERNATE_TAKE,
            Owner.EDITING,
            Availability.UNCHECKED,
            f"use {what} from a related take ({', '.join(t.related_assets[:3])})",
            0.5,
            "the matching range of the other take was not compared",
        )
    elif t.kind is not TransitionKind.SAME_SHOT:
        add(
            RepairKind.ALTERNATE_SHOT,
            Owner.EDITING,
            Availability.UNAVAILABLE,
            f"use {what}",
            0.6,
            "no related recording is known; new footage would be needed",
        )
