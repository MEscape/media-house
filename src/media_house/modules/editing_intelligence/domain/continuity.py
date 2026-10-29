"""Continuity at the cuts the edit makes, from what video analysis measured and nothing else.

``continuity_risk`` is how likely a cut is to read as a visible jump. It is assessed only where
there is evidence, and says so where there is not:

* a cut inside ONE continuous shot leaves the frame unchanged on both sides, so a visible subject
  jumps (heuristic; lower where the profile makes jump cuts part of the style);
* between NEIGHBOURING shots, video analysis measured the jump-cut likelihood and the luma and
  colour differences (measured);
* a cut covered by cutaway footage is hidden (low);
* between shots that are not neighbours, or recordings that differ, nothing was measured: the
  risk is ``None``, never 0;
* subject identity, eyelines and pose are not exposed by the analysis, so they are not claimed.
"""

from collections.abc import Callable, Mapping, Sequence
from dataclasses import replace
from fractions import Fraction
from itertools import pairwise

from media_house.modules.editing_intelligence.domain.facts import AssetEvidence, ContinuityFacts
from media_house.modules.editing_intelligence.domain.hooks import CutQuery, Hooks
from media_house.modules.editing_intelligence.domain.model import Issue, Placement, Signal
from media_house.modules.editing_intelligence.domain.profile import EditingProfile
from media_house.modules.editing_intelligence.domain.timing import to_fraction
from media_house.modules.editing_intelligence.domain.values import (
    IssueKind,
    PlacementRole,
    Severity,
    SignalBasis,
    SignalName,
    TrackKind,
)

_FACE_VISIBLE = frozenset({"speaking_face", "quiet_face"})
_MOVING = frozenset({"pan", "tilt", "push_in", "pull_out", "mixed", "handheld"})
_EPS = Fraction(1, 1000)


def assess_cuts(
    placements: Sequence[Placement],
    evidence: Mapping[str, AssetEvidence],
    profile: EditingProfile,
    hooks: Hooks,
    issue_id: Callable[[], str],
    continuity: ContinuityFacts | None = None,
) -> tuple[list[Placement], list[Issue]]:
    """Attach a ``continuity_risk`` signal to every main picture clip that follows a real cut."""
    main = sorted(
        (
            p
            for p in placements
            if p.track is TrackKind.VIDEO and p.layer == 0 and p.role is not PlacementRole.COVER
        ),
        key=lambda p: p.timeline_start.value,
    )
    covers = [p for p in placements if p.layer > 0 and p.track is TrackKind.VIDEO]
    risks: dict[str, Signal] = {}
    issues: list[Issue] = []
    for before, after in pairwise(main):
        if before.cut_out is None and after.cut_in is None:
            continue  # a plain join of contiguous material: nothing was cut
        signal = _risk(before, after, covers, evidence, profile, hooks, continuity)
        risks[after.placement_id] = signal
        if signal.value is not None and signal.value >= profile.continuity_review:
            issues.append(
                Issue(
                    issue_id=issue_id(),
                    kind=IssueKind.CONTINUITY,
                    severity=Severity.WARNING,
                    detail=f"cut before {after.placement_id}: {signal.reason}",
                    subject=after.placement_id,
                )
            )
    out = [
        replace(p, continuity_risk=risks[p.placement_id]) if p.placement_id in risks else p
        for p in placements
    ]
    return out, issues


def _risk(
    before: Placement,
    after: Placement,
    covers: Sequence[Placement],
    evidence: Mapping[str, AssetEvidence],
    profile: EditingProfile,
    hooks: Hooks,
    continuity: ContinuityFacts | None = None,
) -> Signal:
    at = to_fraction(after.timeline_start)
    if any(to_fraction(c.timeline_start) <= at < to_fraction(c.timeline_end) for c in covers):
        return _signal(0.1, SignalBasis.HEURISTIC, "covered by cutaway footage")
    if continuity is not None:
        measured = continuity.verdict(
            before.source.asset_id,
            float(to_fraction(before.source.end)),
            after.source.asset_id,
            float(to_fraction(after.source.start)),
        )
        if measured is not None and measured.risk is not None:
            return _signal(measured.risk, SignalBasis.MEASURED, measured.reason)
    if before.source.asset_id != after.source.asset_id:
        return _signal(
            None, SignalBasis.HEURISTIC, "different recordings: continuity was not measured"
        )
    asset = evidence.get(after.source.asset_id)
    leaves, resumes = to_fraction(before.source.end), to_fraction(after.source.start)
    if hooks.continuity is not None:
        verdict = hooks.continuity(
            CutQuery(
                asset_id=after.source.asset_id, leaves_at=float(leaves), resumes_at=float(resumes)
            )
        )
        if verdict is not None:
            return _signal(verdict.risk, SignalBasis.MEASURED, verdict.reason)
    if asset is None or asset.video is None:
        return _signal(
            None, SignalBasis.HEURISTIC, "no video analysis: continuity was not assessed"
        )
    shot_a = asset.video.shot_at(float(leaves - _EPS))
    shot_b = asset.video.shot_at(float(resumes))
    if shot_a is None or shot_b is None:
        return _signal(None, SignalBasis.HEURISTIC, "the cut lies outside the analysed shots")
    if shot_b.black_in and shot_a.shot_id != shot_b.shot_id:
        return _signal(0.8, SignalBasis.MEASURED, f"{shot_b.shot_id} opens on black")
    if shot_a.shot_id == shot_b.shot_id:
        sides = (
            ()
            if asset.alignment is None
            else (
                asset.alignment.visibility_at(float(leaves) - 1.0, float(leaves)),
                asset.alignment.visibility_at(float(resumes), float(resumes) + 1.0),
            )
        )
        visible = any(side in _FACE_VISIBLE for side in sides)
        value = 0.6 if visible else 0.2
        if shot_a.camera in _MOVING:
            value += 0.2
        if profile.jump_cuts_ok:
            value *= 0.7
        reason = (
            f"a cut inside one continuous shot ({shot_a.shot_id}): the frame is unchanged on both "
            f"sides; a face is {'visible' if visible else 'not known to be visible'}"
            + ("; the style accepts jump cuts" if profile.jump_cuts_ok else "")
        )
        return _signal(round(min(1.0, value), 3), SignalBasis.HEURISTIC, reason)
    if shot_b.previous_shot_id == shot_a.shot_id:
        parts = {
            "jump-cut likelihood": shot_b.jump_cut_likelihood,
            "luma difference": shot_b.luma_delta,
            "colour difference": shot_b.color_delta,
        }
        known = {k: v for k, v in parts.items() if v is not None}
        if known:
            name, value = max(known.items(), key=lambda item: item[1])
            return _signal(
                round(value, 3),
                SignalBasis.MEASURED,
                f"neighbouring shots; highest: {name} {value:.2f}",
            )
    return _signal(
        None,
        SignalBasis.HEURISTIC,
        f"{shot_a.shot_id} and {shot_b.shot_id} are not neighbours: continuity was measured only "
        "between neighbours",
    )


def _signal(value: float | None, basis: SignalBasis, reason: str) -> Signal:
    return Signal(name=SignalName.CONTINUITY_RISK, value=value, basis=basis, reason=reason)
