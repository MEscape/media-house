"""Verification: read Resolve back and compare it with what was meant to be there.

A call that returned success proves nothing. Each result says what it is: ``verified`` (read back
and equal), ``partially_verified`` (some of it could not be read), ``not_verifiable`` (the API
cannot show it) or ``failed``. Something that cannot be inspected is never reported as verified.
"""

from collections.abc import Mapping

from media_house.modules.resolve_adapter.domain.live import LiveItem, TimelineSnapshot
from media_house.modules.resolve_adapter.domain.reconcile import Decision, live_fields
from media_house.modules.resolve_adapter.domain.report import CheckRecord
from media_house.modules.resolve_adapter.domain.translate import Expected, ExpectedMarker
from media_house.modules.resolve_adapter.domain.values import TAG_PREFIX, Area, Verification


def verify_item(
    final: tuple[tuple[str, str], ...], live: LiveItem | None, asset_id: str | None
) -> tuple[Verification, str]:
    """One element: its comparable state, as written, against what Resolve reports now."""
    if live is None:
        return Verification.FAILED, "not found in the timeline"
    names = frozenset(k for k, _ in final)
    theirs = live_fields(live, asset_id, names)
    wrong = [
        f"{k}: wanted {v}, found {theirs[k]}" for k, v in final if k in theirs and theirs[k] != v
    ]
    if wrong:
        return Verification.FAILED, "; ".join(wrong)
    unread = [k for k, _ in final if k not in theirs]
    if unread:
        return Verification.PARTIALLY_VERIFIED, "not readable: " + ", ".join(unread)
    return Verification.VERIFIED, ""


def verify_items(
    decisions: Mapping[str, Decision],
    snapshot: TimelineSnapshot,
    element_item: Mapping[str, str],
    asset_of_media: Mapping[str, str],
) -> dict[str, tuple[Verification, str]]:
    """Every element this run wrote or confirmed. A person's version is theirs, not checked."""
    found: dict[str, tuple[Verification, str]] = {}
    for element_id, decision in decisions.items():
        if decision.action.value in {"leave", "delete"}:
            continue
        item = snapshot.item(element_item.get(element_id, "")) or snapshot.item_for(element_id)
        asset = asset_of_media.get(item.media_item_id or "") if item is not None else None
        found[element_id] = verify_item(decision.final, item, asset)
    return found


def verify_tracks(expected: Expected, snapshot: TimelineSnapshot) -> tuple[CheckRecord, ...]:
    checks: list[CheckRecord] = []
    for kind, have in (
        ("video", snapshot.info.video_tracks),
        ("audio", snapshot.info.audio_tracks),
    ):
        want = expected.track_count(kind)
        state = Verification.VERIFIED if have >= want else Verification.FAILED
        checks.append(
            CheckRecord(Area.TRACK, f"{kind} tracks", state, f"wanted {want}, Resolve has {have}")
        )
    named = [t for t in expected.tracks if t.name]
    wrong = [
        t.name
        for t in named
        if (live := snapshot.track(t.kind, t.index)) is not None and live.name != t.name
    ]
    if wrong:
        checks.append(
            CheckRecord(
                Area.TRACK,
                "track names",
                Verification.PARTIALLY_VERIFIED,
                "renamed in Resolve or not applied: " + ", ".join(sorted(wrong)),
            )
        )
    return tuple(checks)


def verify_markers(expected: tuple[ExpectedMarker, ...], snapshot: TimelineSnapshot) -> CheckRecord:
    live = {m.custom_data for m in snapshot.markers if m.custom_data.startswith(TAG_PREFIX)}
    missing = [m.custom for m in expected if m.custom not in live]
    if missing:
        return CheckRecord(
            Area.MARKER,
            "markers",
            Verification.FAILED,
            f"{len(missing)} of {len(expected)} not found: " + ", ".join(missing[:3]),
        )
    return CheckRecord(Area.MARKER, "markers", Verification.VERIFIED, f"{len(expected)} present")


def verify_duration(expected_end: int, snapshot: TimelineSnapshot) -> CheckRecord:
    have = snapshot.duration
    if have == expected_end:
        return CheckRecord(Area.CLIP, "timeline duration", Verification.VERIFIED, f"{have} frames")
    return CheckRecord(
        Area.CLIP,
        "timeline duration",
        Verification.FAILED,
        f"wanted {expected_end} frames, Resolve holds {have}",
    )


def summarize(
    area: Area, name: str, results: Mapping[str, tuple[Verification, str]]
) -> CheckRecord:
    """The per-element results as one line of the report."""
    if not results:
        return CheckRecord(area, name, Verification.NOT_CHECKED, "nothing to check")
    failed = [k for k, (v, _) in results.items() if v is Verification.FAILED]
    partial = [k for k, (v, _) in results.items() if v is Verification.PARTIALLY_VERIFIED]
    total = len(results)
    if failed:
        return CheckRecord(
            area, name, Verification.FAILED, f"{len(failed)} of {total} differ: {failed[0]}"
        )
    if partial:
        return CheckRecord(
            area,
            name,
            Verification.PARTIALLY_VERIFIED,
            f"{len(partial)} of {total} only partly readable",
        )
    return CheckRecord(area, name, Verification.VERIFIED, f"{total} read back and equal")
