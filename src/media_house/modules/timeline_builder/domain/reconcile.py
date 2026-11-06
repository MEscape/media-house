"""A person's work and the next build: nothing is overwritten silently.

Each element of the new specification is compared with what the previous build holds:

* untouched by anyone and unchanged: carried over (counted as reused, not rebuilt);
* changed by the specification only: rebuilt;
* changed by a person and unchanged in the specification: the person's version stays;
* changed by a person AND by the specification: a conflict, resolved by the configured policy
  (``preserve`` keeps the person's version, ``replace`` uses the specification's, ``review``
  keeps the person's version, holds the new one and asks), and always reported;
* created by a person: carried over untouched;
* deleted by a person (a tombstone): stays deleted unless the specification changed it too.
"""

from collections.abc import Callable
from dataclasses import dataclass, field, replace

from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    HumanMark,
    Issue,
    Tombstone,
    VideoClip,
)
from media_house.modules.timeline_builder.domain.values import (
    ConflictPolicy,
    Provenance,
    Severity,
)

type Clip = VideoClip | AudioClip


@dataclass(slots=True)
class Events:
    """What reconciliation did, for the findings and the change set."""

    reused: int = 0
    conflicted: list[str] = field(default_factory=list)
    tombstones: list[Tombstone] = field(default_factory=list)
    issues: list[Issue] = field(default_factory=list)

    def note(
        self, code: str, severity: Severity, message: str, element_id: str, resolution: str = ""
    ) -> None:
        self.issues.append(
            Issue(
                code=code,
                severity=severity,
                message=message,
                element_id=element_id,
                resolution=resolution,
                origin="reconcile",
            )
        )


def resolve[C: (VideoClip, AudioClip)](
    element_id: str,
    digest: str,
    make: Callable[[], C],
    prior: C | None,
    tombstone: Tombstone | None,
    policy: ConflictPolicy,
    events: Events,
    *,
    reuse: bool,
) -> C | None:
    """The element as it must be in the new build, or ``None`` when it stays deleted."""
    if tombstone is not None:
        return _deleted(element_id, digest, make, tombstone, policy, events)
    if prior is not None and prior.provenance is Provenance.HUMAN_MODIFIED and prior.human:
        return _modified(element_id, digest, make, prior, policy, events)
    if (
        reuse
        and prior is not None
        and prior.provenance is Provenance.AI
        and prior.source_digest == digest
    ):
        events.reused += 1
        return prior
    return make()


def _deleted[C: (VideoClip, AudioClip)](
    element_id: str,
    digest: str,
    make: Callable[[], C],
    tomb: Tombstone,
    policy: ConflictPolicy,
    events: Events,
) -> C | None:
    if tomb.base_digest == digest:
        events.tombstones.append(replace(tomb, pending_digest=None))
        return None
    events.conflicted.append(element_id)
    if policy is ConflictPolicy.REPLACE:
        events.note(
            "human_deletion_replaced",
            Severity.WARNING,
            f"{element_id} was deleted by a person, but the specification changed it; "
            "the specification's version is back",
            element_id,
            "replaced",
        )
        return make()
    if policy is ConflictPolicy.PRESERVE:
        events.tombstones.append(replace(tomb, base_digest=digest, pending_digest=None))
        events.note(
            "human_deletion_kept",
            Severity.INFO,
            f"{element_id} stays deleted although the specification changed it",
            element_id,
            "preserved",
        )
        return None
    events.tombstones.append(replace(tomb, pending_digest=digest))
    events.note(
        "human_deletion_review",
        Severity.REVIEW,
        f"{element_id} was deleted by a person and the specification has since changed it: "
        "it stays deleted until someone decides",
        element_id,
        "held for review",
    )
    return None


def _modified[C: (VideoClip, AudioClip)](
    element_id: str,
    digest: str,
    make: Callable[[], C],
    prior: C,
    policy: ConflictPolicy,
    events: Events,
) -> C:
    mark = prior.human
    if mark is None:  # pragma: no cover - guarded by the caller
        return prior
    if mark.base_digest == digest:
        return (
            prior
            if mark.pending_digest is None
            else replace(prior, human=replace(mark, pending_digest=None))
        )
    events.conflicted.append(element_id)
    fields = ", ".join(mark.fields) or "its content"
    if policy is ConflictPolicy.REPLACE:
        events.note(
            "human_change_replaced",
            Severity.WARNING,
            f"{element_id}: a person changed {fields}; the specification changed it too and its "
            "version replaced the person's",
            element_id,
            "replaced",
        )
        return make()
    if policy is ConflictPolicy.PRESERVE:
        events.note(
            "human_change_kept",
            Severity.INFO,
            f"{element_id}: a person's change to {fields} was kept although the specification "
            "changed the element",
            element_id,
            "preserved",
        )
        kept: HumanMark = replace(mark, base_digest=digest, pending_digest=None)
        return replace(prior, human=kept)
    events.note(
        "human_change_review",
        Severity.REVIEW,
        f"{element_id}: a person changed {fields} and the specification changed the element "
        "too: the person's version is kept, the new one is held for review",
        element_id,
        "held for review",
    )
    return replace(prior, human=replace(mark, pending_digest=digest))


def carry_over[C: (VideoClip, AudioClip)](
    prior: list[C],
    in_spec: set[str],
    policy: ConflictPolicy,
    events: Events,
) -> list[C]:
    """Previous elements the new specification does not mention: a person's own creations stay;
    a person's change to an element the specification dropped follows the policy."""
    kept: list[C] = []
    for p in prior:
        if p.provenance is Provenance.HUMAN_CREATED:
            kept.append(p)
        elif (
            p.provenance is Provenance.HUMAN_MODIFIED
            and p.human is not None
            and p.clip_id not in in_spec
        ):
            events.conflicted.append(p.clip_id)
            if policy is ConflictPolicy.REPLACE:
                events.note(
                    "human_change_dropped",
                    Severity.WARNING,
                    f"{p.clip_id} was changed by a person but the specification no longer has it: "
                    "it is removed",
                    p.clip_id,
                    "replaced",
                )
            else:
                severity = Severity.INFO if policy is ConflictPolicy.PRESERVE else Severity.REVIEW
                events.note(
                    "human_change_orphaned",
                    severity,
                    f"{p.clip_id} was changed by a person but the specification no longer has it: "
                    "it stays in the timeline",
                    p.clip_id,
                    "preserved" if policy is ConflictPolicy.PRESERVE else "held for review",
                )
                kept.append(p)
    return kept


def heal(
    video: list[VideoClip],
    sound: list[AudioClip],
    dropped_transitions: set[str],
    dropped_mix: set[str],
    gone: set[str],
) -> tuple[list[VideoClip], list[AudioClip]]:
    """Drop the references that point at what a person deleted (and at what that deletion took
    with it). Any other broken reference is left for the validation to report."""
    if not gone:
        return video, sound
    healed_v: list[VideoClip] = []
    for c in video:
        v_changes: dict[str, object] = {}
        if c.audio_link in gone:
            v_changes["audio_link"] = None
        if c.transition_in in dropped_transitions:
            v_changes["transition_in"] = None
        if c.transition_out in dropped_transitions:
            v_changes["transition_out"] = None
        healed_v.append(replace(c, **v_changes) if v_changes else c)  # type: ignore[arg-type]
    healed_a: list[AudioClip] = []
    for a in sound:
        a_changes: dict[str, object] = {}
        if a.video_link in gone:
            a_changes["video_link"] = None
        mix_ids = tuple(m for m in a.mix_ids if m not in dropped_mix)
        if mix_ids != a.mix_ids:
            a_changes["mix_ids"] = mix_ids
        healed_a.append(replace(a, **a_changes) if a_changes else a)  # type: ignore[arg-type]
    return healed_v, healed_a
