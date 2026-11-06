"""Before anything is built: can this specification be built at all?

An invalid specification must never produce a successful timeline, so these are the questions
that stop the build: a version this builder cannot read, vocabulary it does not know, references
that point nowhere, footage or sound that is missing or has changed since it was compiled, and a
creator identity other than the one the caller expects.
"""

from dataclasses import dataclass
from fractions import Fraction

from media_house.modules.timeline_builder.domain.dependencies import (
    CRITICAL_KINDS,
    KINDS,
    STATES,
    known_dependency_vocabulary,
)
from media_house.modules.timeline_builder.domain.facts import LiveSources, SpecFacts
from media_house.modules.timeline_builder.domain.model import Issue
from media_house.modules.timeline_builder.domain.values import (
    SUPPORTED_SPEC_SCHEMAS,
    SUPPORTED_SPEC_VOCABULARIES,
    BlendMode,
    DependencyState,
    Execution,
    Role,
    Severity,
    TrackKind,
)

#: Spec item origins the builder knows.
_ORIGINS = frozenset({"edit", "vfx", "graphics", "captions", "transition", "sound"})
_OVERLAY_ORIGINS = frozenset({"graphics", "captions", "transition"})


@dataclass(frozen=True, slots=True)
class IdentityExpectation:
    """The identity the caller says this timeline must carry (a channel's, across videos)."""

    identity_id: str
    identity_version: int | None = None
    style_fingerprint: str | None = None


def _enum_ok(enum: type, value: str) -> bool:
    try:
        enum(value)
    except ValueError:
        return False
    return True


def preflight(
    spec: SpecFacts, live: LiveSources, expect: IdentityExpectation | None
) -> list[Issue]:
    """Every reason the specification cannot be built (all of them, not the first)."""
    found: list[Issue] = []

    def error(code: str, message: str, element: str | None = None) -> None:
        found.append(Issue(code=code, severity=Severity.ERROR, message=message, element_id=element))

    if spec.schema_version not in SUPPORTED_SPEC_SCHEMAS:
        error("schema_unsupported", f"specification schema {spec.schema_version} is not readable")
    if spec.compilation.vocabulary_version not in SUPPORTED_SPEC_VOCABULARIES:
        error(
            "vocabulary_unsupported",
            f"specification vocabulary {spec.compilation.vocabulary_version} is not known to "
            "this builder",
        )
    if spec.state not in {"ok", "partial"}:
        error("state_unsupported", f"the specification state {spec.state!r} is not buildable")
    tb = spec.timebase
    if (
        Fraction(tb.frame_rate.numerator, tb.frame_rate.denominator) <= 0
        or tb.width <= 0
        or tb.height <= 0
        or tb.sample_rate <= 0
        or tb.duration_frames < 0
    ):
        error("timebase_invalid", "the specification's frame rate, size or sample rate is invalid")
    tracks = {t.track_id: t for t in spec.tracks}
    if len(tracks) != len(spec.tracks):
        error("duplicate_track", "the specification lists a track id twice")
    for t in spec.tracks:
        if not _enum_ok(TrackKind, t.kind) or not _enum_ok(Role, t.role):
            error("unknown_vocabulary", f"track {t.track_id}: unknown kind or role", t.track_id)
        if not _enum_ok(BlendMode, t.composite):
            error("unknown_vocabulary", f"track {t.track_id}: unknown composite {t.composite}")
    assets = {a.asset_id for a in spec.assets}
    for v in spec.video:
        track = tracks.get(v.track_id)
        if track is None or track.kind != TrackKind.VIDEO.value:
            error("unknown_track", f"{v.item_id} is on {v.track_id}, not a video track", v.item_id)
        if v.origin not in _ORIGINS:
            error("unknown_vocabulary", f"{v.item_id}: unknown origin {v.origin}", v.item_id)
        if v.asset_id not in assets:
            error("unknown_asset", f"{v.item_id} uses {v.asset_id}, which is not listed", v.item_id)
    for o in spec.overlays:
        track = tracks.get(o.track_id)
        if track is None or track.kind != TrackKind.VIDEO.value:
            error("unknown_track", f"{o.item_id} is on {o.track_id}, not a video track", o.item_id)
        if o.origin not in _OVERLAY_ORIGINS:
            error("unknown_vocabulary", f"{o.item_id}: unknown origin {o.origin}", o.item_id)
        if not _enum_ok(Execution, o.execution) or not _enum_ok(BlendMode, o.composite):
            error("unknown_vocabulary", f"{o.item_id}: unknown execution or composite", o.item_id)
        if o.asset_id is not None and o.asset_id not in assets:
            error("unknown_asset", f"{o.item_id} uses {o.asset_id}, which is not listed", o.item_id)
    for au in spec.audio:
        track = tracks.get(au.track_id)
        if track is None or track.kind != TrackKind.AUDIO.value:
            error(
                "unknown_track", f"{au.item_id} is on {au.track_id}, not an audio track", au.item_id
            )
        if not _enum_ok(Role, au.role):
            error("unknown_vocabulary", f"{au.item_id}: unknown role {au.role}", au.item_id)
        if au.asset_id not in assets:
            error(
                "unknown_asset", f"{au.item_id} uses {au.asset_id}, which is not listed", au.item_id
            )
    for fx in spec.effects:
        if not _enum_ok(Execution, fx.execution):
            error("unknown_vocabulary", f"effect {fx.effect_id}: unknown execution", fx.effect_id)
    for tr in spec.transitions:
        if not _enum_ok(Execution, tr.execution):
            error("unknown_vocabulary", f"transition {tr.transition_id}: unknown execution")
    for d in spec.dependencies:
        if not known_dependency_vocabulary(d):
            error("unknown_vocabulary", f"dependency {d.dependency_id}: unknown kind or state")
    # footage and sound that is gone or changed since the specification was compiled
    used = _used_assets(spec)
    for a in spec.assets:
        if a.asset_id not in used:
            continue
        found_live = live.get(a.asset_id)
        if found_live is None:
            continue
        if not found_live.exists:
            error("source_missing", f"{a.asset_id} is not in the library any more", a.asset_id)
        elif not found_live.available:
            error("source_unavailable", f"the file of {a.asset_id} cannot be found", a.asset_id)
        elif a.checksum and found_live.checksum and a.checksum != found_live.checksum:
            error(
                "source_changed",
                f"{a.asset_id} changed after the specification was compiled (checksum differs)",
                a.asset_id,
            )
    placed = (
        {v.item_id for v in spec.video}
        | {o.item_id for o in spec.overlays}
        | {a.item_id for a in spec.audio}
    )
    for d in spec.dependencies:
        if not known_dependency_vocabulary(d):
            continue
        missing = STATES[d.state] in {DependencyState.MISSING, DependencyState.REQUESTED}
        # only what a placed clip plays stops the build; a need the library could not meet (no
        # clip was placed for it) is reported by the dependency manifest instead
        if (
            KINDS[d.kind] in CRITICAL_KINDS
            and missing
            and (d.reference in used or placed & set(d.required_by))
        ):
            error("critical_dependency", f"{d.reference} is {d.state} ({d.kind})", d.dependency_id)
    if expect is not None:
        ident = spec.creator
        if ident.identity_id != expect.identity_id:
            error(
                "identity_mismatch",
                f"the specification was compiled for identity {ident.identity_id}, "
                f"not {expect.identity_id}",
            )
        elif (
            expect.identity_version is not None
            and ident.identity_version != expect.identity_version
        ):
            error(
                "identity_mismatch",
                f"identity {ident.identity_id} is at version {ident.identity_version}, "
                f"expected {expect.identity_version}",
            )
        if expect.style_fingerprint and ident.style_fingerprint != expect.style_fingerprint:
            error("identity_mismatch", "the specification was compiled with another style")
    return found


def _used_assets(spec: SpecFacts) -> set[str]:
    """Files whose absence cannot be tolerated: footage and sound (graphics are 'unresolved')."""
    return {v.asset_id for v in spec.video} | {a.asset_id for a in spec.audio}
