"""The validation boundary: is this plan consistent, and what could it not check?

Four kinds of finding, never mixed:

* ``ERROR``: the plan is internally inconsistent or breaks a hard constraint (a source range out of
  bounds, a section out of the story's order, essential information removed, overlapping clips on
  one layer, an unknown reference). A plan with an error is never stored.
* ``WARNING``: merits review (low-confidence boundaries, a cover shot that may contradict, a
  missed target length, a manual override that removes something the story needs).
* ``RECOMMENDATION``: subjective, optional.
* ``UNRESOLVED``: the evidence needed to judge is missing.

A check that cannot be made from an editorial proposal (black frames, silence at joins, rendered
sync) is reported as ``NOT_CHECKED`` with the reason. It is never reported as passed.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from fractions import Fraction
from itertools import pairwise

from media_house.modules.editing_intelligence.domain.facts import AssetEvidence, StoryFacts
from media_house.modules.editing_intelligence.domain.model import (
    Check,
    Constraint,
    EditPlan,
    Issue,
    Placement,
    SourceRange,
    TargetRecord,
)
from media_house.modules.editing_intelligence.domain.timing import to_fraction
from media_house.modules.editing_intelligence.domain.values import (
    Action,
    CheckState,
    ConstraintKind,
    ConstraintState,
    DurationMode,
    IssueKind,
    Origin,
    PlacementRole,
    Precision,
    Severity,
    TrackKind,
)

_TOLERANCE = Fraction(1, 10)
_WEAK = frozenset({Precision.ESTIMATED, Precision.SENTENCE_SPAN})


@dataclass(frozen=True, slots=True)
class Validation:
    issues: tuple[Issue, ...]
    checks: tuple[Check, ...]
    constraints: tuple[Constraint, ...]

    @property
    def errors(self) -> tuple[Issue, ...]:
        return tuple(i for i in self.issues if i.severity is Severity.ERROR)


def validate(
    plan: EditPlan,
    story: StoryFacts,
    evidence: Mapping[str, AssetEvidence],
    target: TargetRecord | None,
    issue_id: Callable[[], str],
) -> Validation:
    v = _Validator(plan, story, evidence, target, issue_id)
    return v.run()


class _Validator:
    def __init__(
        self,
        plan: EditPlan,
        story: StoryFacts,
        evidence: Mapping[str, AssetEvidence],
        target: TargetRecord | None,
        issue_id: Callable[[], str],
    ) -> None:
        self.plan = plan
        self.story = story
        self.evidence = evidence
        self.target = target
        self._next = issue_id
        self.issues: list[Issue] = []
        self.checks: list[Check] = []
        self.constraints: list[Constraint] = []
        self.decisions = {d.decision_id: d for d in plan.decisions}
        self.main = sorted(
            (
                p
                for p in plan.placements
                if p.layer == 0 and p.role is not PlacementRole.COVER and p.track is self._lead()
            ),
            key=lambda p: (p.timeline_start.value, p.placement_id),
        )

    def _lead(self) -> TrackKind:
        """The track that leads the order: sound when there is any, else the picture."""
        return (
            TrackKind.AUDIO
            if any(p.track is TrackKind.AUDIO for p in self.plan.placements)
            else TrackKind.VIDEO
        )

    def run(self) -> Validation:
        self._ranges()
        self._references()
        self._order()
        self._essential()
        self._context()
        self._layers()
        self._bridges()
        self._duration()
        self._decision_integrity()
        self._unsupported()
        self._linked()
        self._black()
        self._not_checkable()
        return Validation(tuple(self.issues), tuple(self.checks), tuple(self.constraints))

    # --- helpers ---------------------------------------------------------------------------
    def _issue(
        self, kind: IssueKind, severity: Severity, detail: str, subject: str | None = None
    ) -> None:
        self.issues.append(
            Issue(
                issue_id=self._next(), kind=kind, severity=severity, detail=detail, subject=subject
            )
        )

    def _check(
        self,
        name: str,
        errors: int,
        ok_detail: str,
        state: CheckState | None = None,
        detail: str = "",
    ) -> None:
        if state is not None:
            self.checks.append(Check(name=name, state=state, detail=detail))
        elif errors:
            self.checks.append(
                Check(name=name, state=CheckState.FAILED, detail=f"{errors} problem(s)")
            )
        else:
            self.checks.append(Check(name=name, state=CheckState.PASSED, detail=ok_detail))

    def _errors_since(self, mark: int) -> int:
        return sum(1 for i in self.issues[mark:] if i.severity is Severity.ERROR)

    def _constraint(
        self, name: str, kind: ConstraintKind, state: ConstraintState, detail: str
    ) -> None:
        self.constraints.append(
            Constraint(
                constraint_id=f"c{len(self.constraints) + 1}",
                kind=kind,
                name=name,
                state=state,
                detail=detail,
            )
        )

    # --- checks ----------------------------------------------------------------------------
    def _ranges(self) -> None:
        mark = len(self.issues)
        unchecked = 0
        weak = 0
        seen: set[SourceRange] = set()
        pending: list[tuple[str, SourceRange, bool]] = [
            (p.placement_id, p.source, True) for p in self.plan.placements
        ]
        for decision in self.plan.decisions:
            if decision.subject is not None:
                pending.append((decision.decision_id, decision.subject, False))
            pending.extend((decision.decision_id, t.range, True) for t in decision.trims)
        for request in self.plan.coverage:
            if request.chosen is not None:
                pending.append((request.request_id, request.chosen.range, True))
        for subject, rng, strict in pending:
            if rng in seen:
                continue
            seen.add(rng)
            asset = self.evidence.get(rng.asset_id)
            if asset is None:
                self._issue(
                    IssueKind.UNKNOWN_REFERENCE,
                    Severity.ERROR,
                    f"{subject}: recording {rng.asset_id} is not part of the edit",
                    subject,
                )
                continue
            start, end = to_fraction(rng.start), to_fraction(rng.end)
            if start < 0 or end < start or (strict and end == start):
                self._issue(
                    IssueKind.INVALID_RANGE,
                    Severity.ERROR,
                    f"{subject}: range {float(start):.3f}-{float(end):.3f} s is not valid",
                    subject,
                )
                continue
            duration = asset.source.duration
            if duration is None:
                unchecked += 1
            elif end > Fraction(duration).limit_denominator(10**6) + _TOLERANCE:
                self._issue(
                    IssueKind.INVALID_RANGE,
                    Severity.ERROR,
                    f"{subject}: ends at {float(end):.3f} s, after the recording ends "
                    f"({duration:.3f} s)",
                    subject,
                )
            if strict and (rng.start_precision in _WEAK or rng.end_precision in _WEAK):
                weak += 1
        if weak:
            self._issue(
                IssueKind.TIME_BASIS_LIMITED,
                Severity.WARNING,
                f"{weak} range(s) rest on sentence-level or estimated times: no frame accuracy is "
                "claimed for them",
            )
        for asset_id, asset in self.evidence.items():
            if (
                asset.source.has_video is not False
                and asset.source.constant_frame_rate is None
                and any(
                    p.source.asset_id == asset_id and p.track is TrackKind.VIDEO
                    for p in self.plan.placements
                )
            ):
                self._issue(
                    IssueKind.TIME_BASIS_LIMITED,
                    Severity.WARNING,
                    f"{asset.source.name or asset_id}: the frame rate is variable or unknown, so "
                    "only seconds are offered, no frame positions",
                    asset_id,
                )
        errors = self._errors_since(mark)
        if unchecked == len(seen) and seen:
            self._check(
                "source_ranges",
                0,
                "",
                CheckState.NOT_CHECKED,
                "no recording duration is known to check against",
            )
        else:
            self._check(
                "source_ranges", errors, f"{len(seen)} range(s) valid and inside their recordings"
            )
        self._constraint(
            "source ranges inside the recordings",
            ConstraintKind.HARD,
            ConstraintState.VIOLATED if errors else ConstraintState.SATISFIED,
            "every range is valid and in bounds" if not errors else "see issues",
        )

    def _references(self) -> None:
        mark = len(self.issues)
        sections = {s.section_id for s in self.story.sections}
        beats = {b.beat_id for b in self.story.beats}
        for d in self.plan.decisions:
            if d.section_id is not None and d.section_id not in sections:
                self._issue(
                    IssueKind.UNKNOWN_REFERENCE,
                    Severity.ERROR,
                    f"{d.decision_id}: section {d.section_id} is not in the story plan",
                    d.decision_id,
                )
            if d.beat_id is not None and d.beat_id not in beats:
                self._issue(
                    IssueKind.UNKNOWN_REFERENCE,
                    Severity.ERROR,
                    f"{d.decision_id}: beat {d.beat_id} is not in the story plan",
                    d.decision_id,
                )
        for p in self.plan.placements:
            if p.section_id is not None and p.section_id not in sections:
                self._issue(
                    IssueKind.UNKNOWN_REFERENCE,
                    Severity.ERROR,
                    f"{p.placement_id}: section {p.section_id} is not in the story plan",
                    p.placement_id,
                )
        self._check(
            "story_references",
            self._errors_since(mark),
            "every section and beat exists in the story plan",
        )

    def _order(self) -> None:
        mark = len(self.issues)
        order = {s.section_id: s.order for s in self.story.sections}
        sequence = [
            p for p in self.main if p.role is PlacementRole.MAIN and p.section_id is not None
        ]
        previous = -1
        for p in sequence:
            here = order.get(p.section_id or "", previous)
            if here < previous:
                self._issue(
                    IssueKind.ORDER_VIOLATED,
                    Severity.ERROR,
                    f"{p.placement_id}: section {p.section_id} plays before an earlier part of "
                    "the story",
                    p.placement_id,
                )
            previous = max(previous, here)
        position: dict[str, int] = {}
        blocks: list[str] = []
        for p in sequence:
            if p.section_id is not None and (not blocks or blocks[-1] != p.section_id):
                blocks.append(p.section_id)
                position.setdefault(p.section_id, len(blocks) - 1)
        for c in self.story.constraints:
            if c.first not in position or c.second not in position:
                continue
            hard = c.strength == "hard"
            if c.kind == "must_precede" and position[c.first] > position[c.second]:
                self._issue(
                    IssueKind.ORDER_VIOLATED,
                    Severity.ERROR if hard else Severity.WARNING,
                    f"constraint {c.constraint_id}: {c.first} must come before {c.second} "
                    f"({c.reason})",
                    c.constraint_id,
                )
            if c.kind == "keep_adjacent" and position[c.second] != position[c.first] + 1:
                self._issue(
                    IssueKind.ORDER_VIOLATED,
                    Severity.ERROR if hard else Severity.WARNING,
                    f"constraint {c.constraint_id}: {c.second} must directly follow {c.first} "
                    f"({c.reason})",
                    c.constraint_id,
                )
        errors = self._errors_since(mark)
        self._check("section_order", errors, "sections follow the story's order and constraints")
        self._constraint(
            "story order and ordering constraints",
            ConstraintKind.HARD,
            ConstraintState.VIOLATED if errors else ConstraintState.SATISFIED,
            "sections follow the story plan",
        )

    def _essential(self) -> None:
        mark = len(self.issues)
        by_unit = {
            (d.asset_id, uid): d
            for d in self.plan.decisions
            for uid in d.unit_ids
            if d.action in (Action.KEEP, Action.CUT, Action.SHORTEN)
        }
        for info in self.story.key_info:
            if info.priority != "essential":
                continue
            states = [by_unit.get((info.source.asset_id, uid)) for uid in info.source.unit_ids]
            known = [d for d in states if d is not None]
            if not known:
                self._issue(
                    IssueKind.EVIDENCE_MISSING,
                    Severity.UNRESOLVED,
                    f"key information {info.info_id}: its sentences are not part of the edit's "
                    "material",
                    info.info_id,
                )
                continue
            kept = [d for d in known if d.action is not Action.CUT]
            if not kept:
                manual = any(d.origin is Origin.MANUAL for d in known)
                self._issue(
                    IssueKind.MANUAL_OVERRIDE_RISK if manual else IssueKind.ESSENTIAL_MISSING,
                    Severity.WARNING if manual else Severity.ERROR,
                    f"essential key information {info.info_id} ('{info.text[:60]}') is removed"
                    + (" by a person" if manual else ""),
                    info.info_id,
                )
                continue
            for q in info.qualified_by:
                d = by_unit.get((info.source.asset_id, q))
                if d is not None and d.action is Action.CUT:
                    manual = d.origin is Origin.MANUAL
                    self._issue(
                        IssueKind.MANUAL_OVERRIDE_RISK
                        if manual
                        else IssueKind.QUALIFICATION_SEPARATED,
                        Severity.WARNING if manual else Severity.ERROR,
                        f"the qualification of {info.info_id} is removed while the information "
                        "stays",
                        info.info_id,
                    )
        sections_with_clips = {p.section_id for p in self.main}
        for s in self.story.sections:
            if s.priority == "essential" and s.section_id not in sections_with_clips:
                members = [
                    d
                    for d in self.plan.decisions
                    if d.section_id == s.section_id
                    and d.action in (Action.KEEP, Action.CUT, Action.SHORTEN)
                ]
                if members and all(
                    d.origin is Origin.MANUAL for d in members if d.action is Action.CUT
                ):
                    self._issue(
                        IssueKind.MANUAL_OVERRIDE_RISK,
                        Severity.WARNING,
                        f"essential section {s.section_id} is empty after a manual decision",
                        s.section_id,
                    )
                elif members:
                    self._issue(
                        IssueKind.ESSENTIAL_MISSING,
                        Severity.ERROR,
                        f"essential section {s.section_id} has no clip",
                        s.section_id,
                    )
        errors = self._errors_since(mark)
        self._check(
            "essential_content",
            errors,
            "essential information and its qualifications are represented",
        )
        self._constraint(
            "essential information and qualifications kept",
            ConstraintKind.HARD,
            ConstraintState.VIOLATED if errors else ConstraintState.SATISFIED,
            "every essential key information is represented with its qualifications",
        )

    def _context(self) -> None:
        first: dict[str, int] = {}
        for i, p in enumerate(self.main):
            if p.section_id is not None:
                first.setdefault(p.section_id, i)
        mark = len(self.issues)
        for need in self.story.contexts:
            if need.section_id not in first:
                continue
            if need.requires_section_id not in first:
                self._issue(
                    IssueKind.CONTEXT_MISSING,
                    Severity.WARNING,
                    f"section {need.section_id} needs section {need.requires_section_id} "
                    f"({need.reason}), which has no clip",
                    need.section_id,
                )
            elif first[need.requires_section_id] > first[need.section_id]:
                self._issue(
                    IssueKind.CONTEXT_MISSING,
                    Severity.WARNING,
                    f"section {need.requires_section_id} plays after the section that needs it as "
                    "context",
                    need.section_id,
                )
        self._check(
            "context_dependencies",
            self._errors_since(mark)
            + sum(1 for i in self.issues[mark:] if i.kind is IssueKind.CONTEXT_MISSING),
            "needed context is present and plays first",
        )
        missing = any(i.kind is IssueKind.CONTEXT_MISSING for i in self.issues)
        self._constraint(
            "context a section needs",
            ConstraintKind.HARD,
            ConstraintState.VIOLATED if missing else ConstraintState.SATISFIED,
            "required sections are present and play first" if not missing else "see issues",
        )

    def _layers(self) -> None:
        mark = len(self.issues)
        groups: dict[tuple[TrackKind, int], list[Placement]] = {}
        for p in self.plan.placements:
            groups.setdefault((p.track, p.layer), []).append(p)
        for (track, layer), items in groups.items():
            items.sort(key=lambda p: (p.timeline_start.value, p.placement_id))
            for a, b in pairwise(items):
                end, start = to_fraction(a.timeline_end), to_fraction(b.timeline_start)
                if start < end:
                    self._issue(
                        IssueKind.OVERLAP,
                        Severity.ERROR,
                        f"{a.placement_id} and {b.placement_id} overlap on {track.value} layer "
                        f"{layer} with no transition or bridge",
                        b.placement_id,
                    )
                elif start > end and layer == 0:
                    self._issue(
                        IssueKind.GAP,
                        Severity.ERROR,
                        f"{float(start - end):.3f} s gap on {track.value} layer 0 before "
                        f"{b.placement_id}",
                        b.placement_id,
                    )
        self._check(
            "timeline_layout", self._errors_since(mark), "no accidental gap or overlap on any layer"
        )

    def _bridges(self) -> None:
        by_id = {p.placement_id: p for p in self.plan.placements}
        mark = len(self.issues)
        for b in self.plan.audio_bridges:
            for pid in b.placement_ids:
                p = by_id.get(pid)
                if p is None:
                    self._issue(
                        IssueKind.UNKNOWN_REFERENCE,
                        Severity.ERROR,
                        f"bridge {b.bridge_id} names unknown clip {pid}",
                        b.bridge_id,
                    )
                    continue
                at = to_fraction(b.audio_changes_at)
                if not to_fraction(p.timeline_start) < at < to_fraction(p.timeline_end):
                    self._issue(
                        IssueKind.OVERLAP,
                        Severity.ERROR,
                        f"bridge {b.bridge_id}: the sound changes outside the clip that should "
                        "carry the picture",
                        b.bridge_id,
                    )
        self._check(
            "audio_bridges",
            self._errors_since(mark),
            "every J/L bridge overlaps its sound change explicitly",
        )

    def _duration(self) -> None:
        end = max((to_fraction(p.timeline_end) for p in self.main), default=Fraction(0))
        estimated = to_fraction(self.plan.duration.estimated)
        if estimated != end:
            self._issue(
                IssueKind.DURATION_INCONSISTENT,
                Severity.ERROR,
                f"estimated length {float(estimated):.3f} s differs from the end of the last clip "
                f"{float(end):.3f} s",
            )
        self._check(
            "duration_consistency",
            0 if estimated == end else 1,
            "the estimate equals the end of the last clip",
        )
        t = self.target
        if t is None or t.mode is DurationMode.NONE:
            return
        seconds = float(estimated)
        essential = (
            "unknown"
            if self.plan.duration.essential_seconds is None
            else f"{self.plan.duration.essential_seconds:.1f}"
        )
        high_miss = t.high is not None and seconds > t.high + 0.05
        low_miss = t.low is not None and seconds < t.low - 0.05
        hard = t.mode is DurationMode.HARD
        if high_miss:
            self._issue(
                IssueKind.TARGET_NOT_MET,
                Severity.WARNING,
                f"{'hard' if hard else 'soft'} target of at most {t.high:.1f} s is missed: the "
                f"edit is about {seconds:.1f} s "
                f"({seconds - (t.high or 0):.1f} s over). Essential material alone is "
                f"{essential} s",
            )
        elif low_miss:
            self._issue(
                IssueKind.TARGET_NOT_MET,
                Severity.RECOMMENDATION,
                f"the edit is about {seconds:.1f} s, under the target of at least {t.low:.1f} s; "
                "nothing is added to fill the time",
            )
        missed = high_miss or low_miss
        self._constraint(
            "target duration",
            ConstraintKind.HARD if hard else ConstraintKind.SOFT,
            ConstraintState.VIOLATED if missed else ConstraintState.SATISFIED,
            "estimated length is within the target" if not missed else "see issues",
        )

    def _decision_integrity(self) -> None:
        mark = len(self.issues)
        placements = {p.placement_id for p in self.plan.placements}
        for d in self.plan.decisions:
            if not d.evidence:
                self._issue(
                    IssueKind.UNKNOWN_REFERENCE,
                    Severity.ERROR,
                    f"{d.decision_id}: a decision without evidence",
                    d.decision_id,
                )
        for r in self.plan.coverage:
            if r.placement_id is not None and r.placement_id not in placements:
                self._issue(
                    IssueKind.UNKNOWN_REFERENCE,
                    Severity.ERROR,
                    f"{r.request_id}: its clip {r.placement_id} does not exist",
                    r.request_id,
                )
            if r.chosen is not None:
                target = self.evidence.get(r.chosen.asset_id)
                shot = (
                    target.video.shot(r.chosen.shot_id)
                    if target is not None and target.video
                    else None
                )
                if shot is None:
                    self._issue(
                        IssueKind.UNKNOWN_REFERENCE,
                        Severity.ERROR,
                        f"{r.request_id}: shot {r.chosen.shot_id} is not in the video analysis",
                        r.request_id,
                    )
        self._check(
            "decision_evidence",
            self._errors_since(mark),
            "every decision names its evidence; chosen shots exist",
        )

    def _unsupported(self) -> None:
        mark = len(self.issues)
        concepts = {v.concept for v in self.story.visuals}
        sections = {s.section_id for s in self.story.sections}
        for r in self.plan.coverage:
            if r.concept not in concepts:
                self._issue(
                    IssueKind.UNSUPPORTED_CONTENT,
                    Severity.ERROR,
                    f"{r.request_id}: the concept '{r.concept}' is not in the story plan",
                    r.request_id,
                )
        for e in self.plan.effect_requests:
            if e.section_id is not None and e.section_id not in sections:
                self._issue(
                    IssueKind.UNSUPPORTED_CONTENT,
                    Severity.ERROR,
                    f"{e.request_id}: request for an unknown section",
                    e.request_id,
                )
        self._check(
            "no_unsupported_content",
            self._errors_since(mark),
            "concepts and requests all come from the story plan",
        )

    def _linked(self) -> None:
        by_id = {p.placement_id: p for p in self.plan.placements}
        mark = len(self.issues)
        for p in self.plan.placements:
            if p.linked is None:
                continue
            other = by_id.get(p.linked)
            if (
                other is None
                or other.source != p.source
                or other.timeline_start != p.timeline_start
                or other.timeline_duration != p.timeline_duration
            ):
                self._issue(
                    IssueKind.INVALID_RANGE,
                    Severity.ERROR,
                    f"{p.placement_id}: picture and sound are not the same source range at the "
                    "same place",
                    p.placement_id,
                )
        self._check(
            "av_linked",
            self._errors_since(mark),
            "every picture clip and its sound clip share one source range and one place (sync by "
            "construction)",
        )

    def _black(self) -> None:
        video_assets = {
            p.source.asset_id for p in self.plan.placements if p.track is TrackKind.VIDEO
        }
        if not video_assets:
            return
        if any(self.evidence[a].video is None for a in video_assets if a in self.evidence):
            self.checks.append(
                Check(
                    name="black_frames",
                    state=CheckState.NOT_CHECKED,
                    detail="no video analysis for every recording: black frames at cuts cannot be "
                    "ruled out",
                )
            )
            return
        bad = [
            p.placement_id
            for p in self.plan.placements
            if p.track is TrackKind.VIDEO
            and p.layer == 0
            and p.cut_in is not None
            and self._opens_on_black(p)
        ]
        if bad:
            self.checks.append(
                Check(
                    name="black_frames",
                    state=CheckState.FAILED,
                    detail=f"{len(bad)} cut(s) start on a shot that opens on black",
                )
            )
            self._issue(
                IssueKind.CONTINUITY,
                Severity.WARNING,
                f"cut(s) start on a black picture: {', '.join(bad[:3])}",
            )
        else:
            self.checks.append(
                Check(
                    name="black_frames",
                    state=CheckState.PASSED,
                    detail="no cut starts on a shot analysis found black; frames inside shots "
                    "were not examined",
                )
            )

    def _opens_on_black(self, placement: Placement) -> bool:
        video = self.evidence[placement.source.asset_id].video
        shot = None if video is None else video.shot_at(placement.source.start.value)
        return shot is not None and shot.black_in

    def _not_checkable(self) -> None:
        self.checks.append(
            Check(
                name="audio_gaps_and_silence",
                state=CheckState.NOT_CHECKED,
                detail="no sound has been assembled; silence at joins can only be judged on a "
                "compiled timeline",
            )
        )
        self.checks.append(
            Check(
                name="rendered_sync",
                state=CheckState.NOT_CHECKED,
                detail="synchronisation of a rendered result is not assessable from a proposal",
            )
        )
