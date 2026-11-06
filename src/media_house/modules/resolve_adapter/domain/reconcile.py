"""Decide, element by element, what to do in Resolve so that nobody's work is lost.

Three states meet:

* ``base``  - what the adapter wrote last time and read back (the binding),
* ``mine``  - what Media House wants now (the translated timeline),
* ``theirs`` - what Resolve holds right now (a person may have changed it).

Per field: only Media House changed it -> write it; only the person changed it -> keep it; both
changed it differently -> a conflict, settled by the execution policy and always recorded. The
same comparison makes a retry safe: a clip that is already in place is ``KEEP``, never placed
twice, and an operation that half-finished is found by what Resolve holds, not by what was
intended.

Pure functions over plain records.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum

from media_house.modules.resolve_adapter.domain.binding import (
    ElementBinding,
    FieldConflict,
    MarkerBinding,
)
from media_house.modules.resolve_adapter.domain.live import LiveItem, LiveMarker
from media_house.modules.resolve_adapter.domain.translate import ExpectedItem, ExpectedMarker
from media_house.modules.resolve_adapter.domain.values import (
    TAG_PREFIX,
    ChangeOrigin,
    ExecutionPolicy,
    Resolution,
)

#: A change of one of these cannot be made in place: the clip is removed and placed again.
STRUCTURAL = frozenset({"track", "start", "duration", "src_start", "asset"})

#: What a property is when nobody set it (so a removed instruction can be undone).
DEFAULT_PROPS: Mapping[str, float] = {
    "Pan": 0.0,
    "Tilt": 0.0,
    "ZoomX": 1.0,
    "ZoomY": 1.0,
    "RotationAngle": 0.0,
    "Opacity": 100.0,
}


class Action(StrEnum):
    CREATE = "create"  # place it
    KEEP = "keep"  # already as wanted
    UPDATE = "update"  # change properties in place
    REPLACE = "replace"  # remove and place again (position, length, source or track changed)
    DELETE = "delete"  # Media House no longer wants it and nobody touched it
    LEAVE = "leave"  # touch nothing: a person's work, or a conflict kept for review


@dataclass(frozen=True, slots=True)
class Decision:
    element_id: str
    action: Action
    #: The comparable state the element will have once the decision is carried out.
    final: tuple[tuple[str, str], ...] = ()
    #: The fields to write (name, value) for an in-place update.
    write: tuple[tuple[str, str], ...] = ()
    #: Fields where the person's value stays (name, value).
    kept: tuple[tuple[str, str], ...] = ()
    conflicts: tuple[FieldConflict, ...] = ()
    resolution: Resolution | None = None
    origin: ChangeOrigin = ChangeOrigin.MEDIA_HOUSE
    note: str = ""

    @property
    def changed_by_person(self) -> bool:
        return self.origin in {ChangeOrigin.RESOLVE, ChangeOrigin.BOTH}


def live_fields(item: LiveItem, asset_id: str | None, names: frozenset[str]) -> dict[str, str]:
    """The comparable state of a live item, for the fields ``names`` that matter."""
    found = {
        "asset": asset_id or "?",
        "duration": str(item.duration),
        "enabled": "1" if item.enabled else "0",
        "src_start": str(item.left_offset),
        "start": str(item.start),
        "track": f"{item.track_kind}:{item.track_index}",
    }
    for name in names:
        if name.startswith("prop:"):
            value = item.prop(name[5:])
            if value is not None:
                found[name] = f"{round(value, 4):g}"
    return found


def decide(
    element_id: str,
    expected: ExpectedItem | None,
    base: ElementBinding | None,
    live: LiveItem | None,
    asset_of_media: Mapping[str, str],
    policy: ExecutionPolicy,
) -> Decision:
    """What to do about one element (any of ``expected`` / ``base`` / ``live`` may be missing)."""
    base_fields = dict(base.fields) if base else None
    if expected is None:
        return _removed(element_id, base_fields, live, asset_of_media, policy)
    mine = dict(expected.fields)
    if live is None:
        return _missing(element_id, mine, base_fields, policy)
    theirs = live_fields(
        live,
        asset_of_media.get(live.media_item_id or ""),
        frozenset(set(mine) | set(base_fields or {})),
    )
    return _merge(element_id, mine, base_fields, theirs, policy)


def _merge(
    element_id: str,
    mine: dict[str, str],
    base: dict[str, str] | None,
    theirs: dict[str, str],
    policy: ExecutionPolicy,
) -> Decision:
    final: dict[str, str] = {}
    write: dict[str, str] = {}
    kept: dict[str, str] = {}
    conflicts: list[FieldConflict] = []
    for name in sorted(set(mine) | set(base or {})):
        m, t = mine.get(name), theirs.get(name)
        b = base.get(name) if base is not None else None
        if b is None and base is not None and name.startswith("prop:"):
            b = _default(name)  # a property nobody set is at Resolve's default
        if m == t:
            _put(final, name, t)
            continue
        if t is None:
            # Resolve did not answer for this field: nothing is known, nothing is overwritten
            _put(final, name, m)
            continue
        if base is not None and t == b:
            _put(final, name, m)  # only Media House changed it
            write[name] = m if m is not None else _default(name)
        elif base is not None and m == b:
            _put(final, name, t)  # only the person changed it
            kept[name] = t
        else:
            conflicts.append(FieldConflict(name, b, m, t))
            if policy is ExecutionPolicy.UPDATE_MANAGED_ELEMENTS:
                _put(final, name, m)
                write[name] = m if m is not None else _default(name)
            else:
                _put(final, name, t)
                kept[name] = t
    resolution: Resolution | None = None
    if conflicts:
        resolution = {
            ExecutionPolicy.UPDATE_MANAGED_ELEMENTS: Resolution.APPLIED_MEDIA_HOUSE,
            ExecutionPolicy.PRESERVE_MANUAL_EDITS: Resolution.KEPT_PERSON,
        }.get(policy, Resolution.OPEN)
    origin = ChangeOrigin.MEDIA_HOUSE
    if conflicts:
        origin = ChangeOrigin.BOTH
    elif kept:
        origin = ChangeOrigin.RESOLVE
    if not write:
        action = Action.LEAVE if (kept or conflicts) else Action.KEEP
    elif any(name in STRUCTURAL for name in write):
        action = Action.REPLACE
    else:
        action = Action.UPDATE
    return Decision(
        element_id=element_id,
        action=action,
        final=tuple(sorted(final.items())),
        write=tuple(sorted(write.items())),
        kept=tuple(sorted(kept.items())),
        conflicts=tuple(conflicts),
        resolution=resolution,
        origin=origin,
    )


def _missing(
    element_id: str,
    mine: dict[str, str],
    base: dict[str, str] | None,
    policy: ExecutionPolicy,
) -> Decision:
    """Wanted, but not in Resolve."""
    final = tuple(sorted(mine.items()))
    if base is None:
        return Decision(element_id, Action.CREATE, final=final)
    if mine == base:
        # the person deleted it and Media House did not change it: it stays deleted
        return Decision(
            element_id,
            Action.LEAVE,
            origin=ChangeOrigin.RESOLVE,
            note="deleted in Resolve; Media House did not change it",
        )
    conflict = FieldConflict("element", "present", "changed", "deleted")
    if policy is ExecutionPolicy.UPDATE_MANAGED_ELEMENTS:
        return Decision(
            element_id,
            Action.CREATE,
            final=final,
            conflicts=(conflict,),
            resolution=Resolution.APPLIED_MEDIA_HOUSE,
            origin=ChangeOrigin.BOTH,
            note="deleted in Resolve, changed by Media House: placed again",
        )
    return Decision(
        element_id,
        Action.LEAVE,
        conflicts=(conflict,),
        resolution=(
            Resolution.KEPT_PERSON
            if policy is ExecutionPolicy.PRESERVE_MANUAL_EDITS
            else Resolution.OPEN
        ),
        origin=ChangeOrigin.BOTH,
        note="deleted in Resolve, changed by Media House",
    )


def _removed(
    element_id: str,
    base: dict[str, str] | None,
    live: LiveItem | None,
    asset_of_media: Mapping[str, str],
    policy: ExecutionPolicy,
) -> Decision:
    """Media House no longer wants it."""
    if live is None:
        return Decision(element_id, Action.KEEP, note="gone from both sides")
    if base is None:
        return Decision(element_id, Action.LEAVE, note="not managed here")
    theirs = live_fields(live, asset_of_media.get(live.media_item_id or ""), frozenset(base))
    if all(theirs.get(k, v) == v for k, v in base.items()):
        return Decision(element_id, Action.DELETE)
    conflict = FieldConflict("element", "present", "removed", "changed")
    if policy is ExecutionPolicy.UPDATE_MANAGED_ELEMENTS:
        return Decision(
            element_id,
            Action.DELETE,
            conflicts=(conflict,),
            resolution=Resolution.APPLIED_MEDIA_HOUSE,
            origin=ChangeOrigin.BOTH,
            note="changed in Resolve, removed by Media House: removed",
        )
    return Decision(
        element_id,
        Action.LEAVE,
        conflicts=(conflict,),
        resolution=(
            Resolution.KEPT_PERSON
            if policy is ExecutionPolicy.PRESERVE_MANUAL_EDITS
            else Resolution.OPEN
        ),
        origin=ChangeOrigin.BOTH,
        note="changed in Resolve, removed by Media House",
    )


def _put(target: dict[str, str], name: str, value: str | None) -> None:
    if value is not None:
        target[name] = value


def _default(name: str) -> str:
    if name.startswith("prop:"):
        return f"{DEFAULT_PROPS.get(name[5:], 0.0):g}"
    return ""


# --- markers --------------------------------------------------------------------------------------


def marker_digest(m: ExpectedMarker) -> str:
    return f"{m.frame}|{m.color}|{m.name}|{m.note}|{m.duration}"


class MarkerAction(StrEnum):
    ADD = "add"
    REPLACE = "replace"  # Media House changed it and nobody removed or edited it
    KEEP = "keep"
    REMOVE = "remove"  # Media House no longer wants it
    LEAVE = "leave"  # a person removed or edited it: not recreated


@dataclass(frozen=True, slots=True)
class MarkerDecision:
    custom: str
    action: MarkerAction


def decide_markers(
    expected: tuple[ExpectedMarker, ...],
    base: tuple[MarkerBinding, ...],
    live: tuple[LiveMarker, ...],
) -> tuple[MarkerDecision, ...]:
    """Managed markers carry ``mh:`` in their custom data; a person's own markers are never read."""
    live_by = {m.custom_data: m for m in live if m.custom_data.startswith(TAG_PREFIX)}
    base_by = {m.custom: m for m in base}
    found: list[MarkerDecision] = []
    wanted = {m.custom for m in expected}
    for m in expected:
        there = live_by.get(m.custom)
        before = base_by.get(m.custom)
        if there is None:
            if before is None:
                found.append(MarkerDecision(m.custom, MarkerAction.ADD))
            elif before.digest == marker_digest(m):
                found.append(MarkerDecision(m.custom, MarkerAction.LEAVE))  # done by a person
            else:
                found.append(MarkerDecision(m.custom, MarkerAction.ADD))
            continue
        now = f"{there.frame}|{there.color}|{there.name}|{there.note}|{there.duration}"
        if now == marker_digest(m):
            found.append(MarkerDecision(m.custom, MarkerAction.KEEP))
        elif before is not None and now == before.digest:
            found.append(MarkerDecision(m.custom, MarkerAction.REPLACE))
        elif before is None:
            found.append(MarkerDecision(m.custom, MarkerAction.KEEP))
        else:
            found.append(MarkerDecision(m.custom, MarkerAction.LEAVE))  # a person edited it
    for custom, there in sorted(live_by.items()):
        if custom in wanted or (
            not custom.startswith(f"{TAG_PREFIX}marker:")
            and not custom.startswith(f"{TAG_PREFIX}gap:")
        ):
            continue
        before = base_by.get(custom)
        now = f"{there.frame}|{there.color}|{there.name}|{there.note}|{there.duration}"
        if before is not None and before.digest == now:
            found.append(MarkerDecision(custom, MarkerAction.REMOVE))
        else:
            found.append(MarkerDecision(custom, MarkerAction.LEAVE))
    return tuple(found)
