"""The dependency manifest of the built timeline.

Every file, font, brand asset, identity and handle the timeline relies on, with whether it is
available NOW (the Media Library may have changed since the specification was compiled) and which
clips cannot be carried out without it. A ``requested`` entry names the module that can produce it.
Nothing is produced or substituted here.
"""

from media_house.modules.timeline_builder.domain.facts import DependencyFact, LiveSources, SpecFacts
from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    BuiltTransition,
    DependencyRecord,
    VideoClip,
)
from media_house.modules.timeline_builder.domain.values import (
    DependencyKind,
    DependencyState,
    Execution,
    TransitionMode,
)

KINDS: dict[str, DependencyKind] = {
    "source_media": DependencyKind.SOURCE_MEDIA,
    "derivative": DependencyKind.DERIVATIVE,
    "rendered_graphic": DependencyKind.RENDERED,
    "rendered_caption": DependencyKind.RENDERED,
    "rendered_overlay": DependencyKind.RENDERED,
    "rendered_clip": DependencyKind.RENDERED,
    "image": DependencyKind.RENDERED,
    "sound": DependencyKind.SOUND,
    "font": DependencyKind.FONT,
    "brand_asset": DependencyKind.BRAND_ASSET,
    "identity": DependencyKind.IDENTITY,
}
STATES: dict[str, DependencyState] = {
    "resolved": DependencyState.RESOLVED,
    "missing": DependencyState.MISSING,
    "fallback": DependencyState.MISSING,
    "requested": DependencyState.REQUESTED,
}
_FILES = frozenset(
    {
        DependencyKind.SOURCE_MEDIA,
        DependencyKind.DERIVATIVE,
        DependencyKind.RENDERED,
        DependencyKind.SOUND,
        DependencyKind.BRAND_ASSET,
    }
)
#: Kinds whose absence stops the build: footage and sound cannot be left out silently.
CRITICAL_KINDS = frozenset(
    {DependencyKind.SOURCE_MEDIA, DependencyKind.DERIVATIVE, DependencyKind.SOUND}
)


def known_dependency_vocabulary(dep: DependencyFact) -> bool:
    return dep.kind in KINDS and dep.state in STATES


def dependency_manifest(
    spec: SpecFacts,
    live: LiveSources,
    video: tuple[VideoClip, ...],
    audio: tuple[AudioClip, ...],
    transitions: tuple[BuiltTransition, ...],
) -> tuple[DependencyRecord, ...]:
    users: dict[str, set[str]] = {}
    clips: list[VideoClip | AudioClip] = [*video, *audio]
    for c in clips:
        if c.source is not None:
            users.setdefault(c.source.source_id, set()).add(c.clip_id)
    records: list[DependencyRecord] = []
    for d in spec.dependencies:
        kind = KINDS[d.kind]
        state = STATES[d.state]
        detail = d.detail
        if d.state == "fallback":
            detail = (detail + "; " if detail else "") + "a fallback stood in when it was compiled"
        if kind in _FILES:
            found = live.get(d.reference)
            if (
                state is DependencyState.RESOLVED
                and found is not None
                and (not found.exists or not found.available)
            ):
                state = DependencyState.MISSING
                detail = "not available in the library now"
        required = (
            tuple(sorted(users[d.reference]))
            if kind in _FILES and d.reference in users
            else d.required_by
        )
        records.append(
            DependencyRecord(
                dependency_id=d.dependency_id,
                kind=kind,
                reference=d.reference,
                state=state,
                required_by=required,
                detail=detail,
                owner=d.owner,
                request=d.request,
            )
        )
    for t in transitions:
        if t.mode in {TransitionMode.DISSOLVE, TransitionMode.FADE, TransitionMode.DIP}:
            ok = t.handles.sufficient and t.supported
            records.append(
                DependencyRecord(
                    dependency_id=f"handle:{t.transition_id}",
                    kind=DependencyKind.TRANSITION_HANDLE,
                    reference=t.transition_id,
                    state=DependencyState.RESOLVED if ok else DependencyState.MISSING,
                    required_by=(t.transition_id,),
                    detail=f"needs {t.tail_frames} frames after the cut and {t.head_frames} before",
                )
            )
    for c in video:
        for e in c.effects:
            if e.execution is not Execution.NATIVE:
                continue
            for m in e.masks:
                for ref in m.track_refs:
                    records.append(
                        DependencyRecord(
                            dependency_id=f"tracking:{ref}",
                            kind=DependencyKind.TRACKING,
                            reference=ref,
                            state=DependencyState.UNVERIFIED,
                            required_by=(e.effect_id,),
                            detail="tracking data named by a mask; nothing here can verify it",
                        )
                    )
    unique = {r.dependency_id: r for r in records}
    return tuple(unique[k] for k in sorted(unique))
