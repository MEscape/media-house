"""Sound effects with restraint: an event earns a sound only when a rule asks for it, it is
strong enough, it does not mask speech or cover preserved original audio, and the density and
spacing budgets allow it. Every declined event is recorded with its reason. A sound comes only
from the library; when none fits, the gap becomes a missing-asset recommendation.

A sound is chosen by what the moment means (the rule's categories, then the words that say a
sound fits it and those that say it does not), never by chance, and every moment of one kind gets
the sound the first one got: the same card reveal always sounds the same.
"""

import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from fractions import Fraction

from media_house.modules.music_and_sound_design.domain.library import (
    LicencePolicy,
    SoundAsset,
    SoundIndex,
    SoundMatch,
    SoundQuery,
)
from media_house.modules.music_and_sound_design.domain.model import (
    Candidate,
    ItemDecision,
    MissingAsset,
    NoSoundEffect,
    SfxEvent,
)
from media_house.modules.music_and_sound_design.domain.profile import SfxRule, SoundProfile
from media_house.modules.music_and_sound_design.domain.spans import (
    Span,
    asset_ref,
    digest,
    frame,
    overlaps_any,
    seconds,
)
from media_house.modules.music_and_sound_design.domain.timeline import (
    SyncEvent,
    fraction,
    rational,
)
from media_house.modules.music_and_sound_design.domain.values import (
    Decision,
    Declined,
    Need,
    SfxAlign,
    SoundKind,
)

_WINDOW = Fraction(60)
_ALTERNATIVES = 3


@dataclass(frozen=True, slots=True, kw_only=True)
class SfxContext:
    timeline_end: Fraction
    fps: Fraction
    speech: list[Span]
    #: Spans whose original sound is the content (fireworks), with the reason.
    preserved: list[tuple[Span, str]]
    profile_at: Callable[[Fraction], SoundProfile]
    decisions: Mapping[str, ItemDecision]
    policy: LicencePolicy


@dataclass(slots=True)
class SfxOutcome:
    events: list[SfxEvent] = field(default_factory=list)
    declined: list[NoSoundEffect] = field(default_factory=list)
    missing: list[MissingAsset] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class _Wanted:
    event: SyncEvent
    at: Fraction
    rule: SfxRule
    profile: SoundProfile


def item_id(event: SyncEvent) -> str:
    return f"sfx:{event.source.value}:{event.event_id}"


def place_sfx(events: tuple[SyncEvent, ...], index: SoundIndex, ctx: SfxContext) -> SfxOutcome:
    out = SfxOutcome()

    def decline(event: SyncEvent, why: Declined, reason: str) -> None:
        out.declined.append(
            NoSoundEffect(
                item_id=item_id(event),
                sync_event_id=event.event_id,
                sync_source=event.source,
                sync_kind=event.kind,
                timeline_at=event.timeline_at,
                declined=why,
                reason=reason,
            )
        )

    wanted: list[_Wanted] = []
    for event in sorted(events, key=lambda e: (fraction(e.timeline_at), e.event_id)):
        at = fraction(event.timeline_at)
        profile = ctx.profile_at(at)
        rule = profile.sfx.rule_for(event.source, event.kind)
        decision = ctx.decisions.get(item_id(event))
        if at > ctx.timeline_end:
            decline(event, Declined.OUTSIDE_TIMELINE, "the event lies after the end of the edit")
        elif (
            decision is not None
            and decision.decision is Decision.REJECTED
            and not decision.asset_id
        ):
            decline(event, Declined.PERSON_REJECTED, decision.note or "rejected by a person")
        elif not profile.sfx.enabled:
            decline(event, Declined.SFX_DISABLED, f"profile {profile.name}: no sound effects")
        elif rule is None:
            decline(
                event,
                Declined.NO_RULE,
                f"no sound is planned for {event.source.value} '{event.kind}' (restraint)",
            )
        elif event.strength < rule.min_strength and decision is None:
            decline(
                event,
                Declined.TOO_WEAK,
                f"strength {event.strength:.2f} below {rule.min_strength:.2f} for {rule.purpose}",
            )
        elif (reason := _preserved(at, ctx)) is not None:
            decline(event, Declined.PRESERVED_AUDIO, reason)
        elif _masks_speech(at, rule, profile, ctx):
            decline(
                event,
                Declined.MASKS_SPEECH,
                f"a {rule.purpose} sound would cover speech ({profile.sfx.under_speech})",
            )
        else:
            wanted.append(_Wanted(event, at, rule, profile))

    accepted = _budget(wanted, ctx, decline)
    recent: list[str] = []
    #: (source, kind) -> the sound every moment of that kind gets
    chosen: dict[tuple[str, str], str] = {}
    missing: dict[tuple[str, tuple[str, ...]], MissingAsset] = {}
    for w in sorted(accepted, key=lambda w: (w.at, w.event.event_id)):
        kind = (w.rule.source, w.rule.kind)
        placed = _choose(w, index, ctx, recent, chosen.get(kind))
        if placed is None:
            decline(
                w.event,
                Declined.NO_ASSET,
                f"the library has no {'/'.join(w.rule.categories)} sound up to"
                f" {w.rule.max_seconds:g} s",
            )
            key = (w.rule.purpose, w.rule.categories)
            if key not in missing:
                missing[key] = _missing(w, ctx)
            continue
        out.events.append(placed)
        recent.append(placed.asset.asset_id)
        if w.profile.sfx.one_sound_per_kind and placed.decision is None:
            chosen.setdefault(kind, placed.asset.asset_id)
    out.missing.extend(missing.values())
    return out


def _preserved(at: Fraction, ctx: SfxContext) -> str | None:
    for (start, end), reason in ctx.preserved:
        if start <= at < end:
            return f"original audio is preserved here: {reason}"
    return None


def _masks_speech(at: Fraction, rule: SfxRule, profile: SoundProfile, ctx: SfxContext) -> bool:
    mode = profile.sfx.under_speech
    if mode == "allow" or (mode == "soft_only" and rule.soft):
        return False
    span = (at - Fraction(1, 10), at + seconds(min(rule.max_seconds, 0.5)))
    return overlaps_any(span, ctx.speech)


def _budget(
    wanted: list[_Wanted],
    ctx: SfxContext,
    decline: Callable[[SyncEvent, Declined, str], None],
) -> list[_Wanted]:
    """Strongest first: keep an event only if spacing and the per-minute budget allow it."""
    kept: list[_Wanted] = []
    for w in sorted(wanted, key=lambda w: (-_priority(w), w.at, w.event.event_id)):
        forced = ctx.decisions.get(item_id(w.event)) is not None
        spacing = seconds(w.profile.sfx.min_spacing_seconds)
        near = [k for k in kept if abs(k.at - w.at) < spacing]
        if near and not forced:
            decline(
                w.event,
                Declined.TOO_CLOSE,
                f"{float(abs(near[0].at - w.at)):.2f} s from a stronger effect"
                f" (minimum {w.profile.sfx.min_spacing_seconds:g} s)",
            )
            continue
        budget = w.profile.sfx.max_per_minute
        in_window = [k for k in kept if abs(k.at - w.at) < _WINDOW / 2]
        if len(in_window) + 1 > budget and not forced:
            decline(
                w.event,
                Declined.DENSITY,
                f"the minute around it already has {len(in_window)} effects (budget {budget:g})",
            )
            continue
        kept.append(w)
    return kept


def _priority(w: _Wanted) -> float:
    weight = {Need.REQUIRED.value: 1.0, Need.RECOMMENDED.value: 0.8}.get(w.rule.need, 0.6)
    return round(w.event.strength * weight, 6)


def _choose(
    w: _Wanted, index: SoundIndex, ctx: SfxContext, recent: list[str], same: str | None = None
) -> SfxEvent | None:
    """``same``: the sound the earlier moments of this kind got (kept unless it no longer fits)."""
    decision = ctx.decisions.get(item_id(w.event))
    window = 0 if w.profile.sfx.one_sound_per_kind else w.profile.sfx.repeat_window
    blocked = frozenset(recent[-window:]) if window else frozenset()
    rejected = frozenset(
        [decision.asset_id]
        if decision and decision.decision is Decision.REJECTED and decision.asset_id
        else []
    )
    query = SoundQuery(
        kind=SoundKind.SFX,
        categories=w.rule.categories,
        text=" ".join(w.rule.prefer) or w.event.kind.replace("_", " "),
        # a sound laid from its start is cut to the rule's length, so a longer file still fits
        max_seconds=None if w.rule.align == SfxAlign.START.value else w.rule.max_seconds,
        policy=ctx.policy,
        exclude=rejected,
    )
    matches = [m for m in index.search(query) if _creditable(m.asset)]
    fitting = [m for m in matches if not _avoided(m.asset, w.rule.avoid)]
    matches = fitting or matches
    if same is not None and (kept := [m for m in matches if m.asset.asset_id == same]):
        matches = kept
    conflicts: list[str] = []
    if decision and decision.decision is Decision.ACCEPTED and decision.asset_id:
        pinned = index.get(decision.asset_id)
        if pinned is not None and ctx.policy.allows(pinned.licence):
            matches = [SoundMatch(asset=pinned, score=1.0, reasons=("chosen by a person",))]
        else:
            conflicts.append("the person's chosen asset is missing or not licence-cleared")
    fresh = [m for m in matches if m.asset.asset_id not in blocked]
    if not fresh and matches:
        conflicts.append("repeats a recent sound: the library has no alternative")
        fresh = matches
    if not fresh:
        return None
    ranked = sorted(fresh, key=lambda m: (-_fit(m, w.rule), m.asset.asset_id))
    best = ranked[0]
    timing = _timing(best.asset, w, ctx)
    if timing is None:
        return None
    start, src_start, src_end, align, note = timing
    if note:
        conflicts.append(note)
    end = start + (src_end - src_start)
    gain, clip_note = _gain(best.asset, w)
    if clip_note:
        conflicts.append(clip_note)
    if overlaps_any((start, end), ctx.speech):
        conflicts.append("plays under speech (soft sound)" if w.rule.soft else "plays under speech")
    return SfxEvent(
        item_id=item_id(w.event),
        sync_event_id=w.event.event_id,
        sync_source=w.event.source,
        sync_kind=w.event.kind,
        purpose=w.rule.purpose,
        asset=asset_ref(best.asset),
        timeline_at=w.event.timeline_at,
        timeline_start=rational(start),
        timeline_end=rational(end),
        timeline_start_frame=frame(start, ctx.fps),
        timeline_end_frame=frame(end, ctx.fps),
        source_start=rational(src_start),
        source_end=rational(src_end),
        align=align,
        gain_db=gain,
        priority=_priority(w),
        confidence=None,
        reason=f"{w.rule.purpose}: {w.event.source.value} '{w.event.kind}' at strength"
        f" {w.event.strength:.2f}; " + ", ".join(best.reasons),
        alternatives=tuple(
            Candidate(
                asset_id=m.asset.asset_id,
                title=m.asset.title,
                score=round(_fit(m, w.rule), 4),
                reasons=m.reasons,
            )
            for m in ranked[1 : 1 + _ALTERNATIVES]
        ),
        conflicts=tuple(conflicts),
        decision=None if decision is None else decision.decision,
    )


def _avoided(sound: SoundAsset, words: tuple[str, ...]) -> bool:
    haystack = " ".join([sound.title, *sound.tags]).casefold()
    return any(word in haystack for word in words)


def _creditable(sound: SoundAsset) -> bool:
    """An asset that needs a credit but has no credit text cannot be used honestly."""
    return not sound.licence.attribution_required or bool(sound.licence.attribution_text)


def _fit(match: SoundMatch, rule: SfxRule) -> float:
    score = match.score
    profile = match.asset.sfx
    if profile is not None:
        m = profile.measurement
        if rule.align == SfxAlign.PEAK.value and m.attack_seconds <= 0.03:
            score += 0.05  # a sharp attack lands precisely on the event
        if rule.align == SfxAlign.END.value and m.attack_seconds >= 0.3:
            score += 0.05  # a slow rise suits a sound that ends on the event
    return round(score, 4)


def _timing(
    sound: SoundAsset, w: _Wanted, ctx: SfxContext
) -> tuple[Fraction, Fraction, Fraction, SfxAlign, str] | None:
    """Timeline start, file in/out and alignment so the right point lands on the event."""
    offset = seconds(w.rule.offset_seconds)
    align = SfxAlign(w.rule.align)
    note = ""
    if sound.sfx is not None:
        m = sound.sfx.measurement
        src_start, src_end = m.seconds(m.useful_start_sample), m.seconds(m.useful_end_sample)
        peak = m.seconds(max(m.peak_sample, m.useful_start_sample))
    else:
        src_start, src_end = Fraction(0), seconds(sound.duration_seconds)
        peak = src_start
        if align is SfxAlign.PEAK:
            align = SfxAlign.START
            note = "not measured: aligned by its start"
    if src_end <= src_start:
        return None
    if align is SfxAlign.START and src_end - src_start > seconds(w.rule.max_seconds):
        src_end = src_start + seconds(w.rule.max_seconds)
        note = (note + "; " if note else "") + f"cut to {w.rule.max_seconds:g} s"
    at = w.at + offset
    if align is SfxAlign.PEAK:
        start = at - (peak - src_start)
    elif align is SfxAlign.END:
        start = at - (src_end - src_start)
    else:
        start = at
    if start < 0:
        src_start -= start
        start = Fraction(0)
        note = (note + "; " if note else "") + "starts with the edit: its beginning is trimmed"
    if start >= ctx.timeline_end or src_start >= src_end:
        return None
    if start + (src_end - src_start) > ctx.timeline_end:
        src_end = src_start + (ctx.timeline_end - start)
        note = (note + "; " if note else "") + "cut at the end of the edit"
    return start, src_start, src_end, align, note


def _gain(sound: SoundAsset, w: _Wanted) -> tuple[float, str]:
    mix = w.profile.mix
    target = (
        mix.speech_reference_lufs
        - mix.sfx_below_speech_lu
        + w.rule.gain_db
        - (1.0 - w.event.strength) * mix.sfx_strength_range_db
    )
    if sound.sfx is None:
        return 0.0, "level not measured: played at file level"
    m = sound.sfx.measurement
    # heard over ~0.4 s: a short pop sounds quieter than its loudest 50 ms, a long swoosh does
    # not, so the reference is lowered by the share of that window the sound fills
    useful = float(m.seconds(m.useful_end_sample - m.useful_start_sample))
    heard = m.rms_max_dbfs + 10.0 * math.log10(min(1.0, max(useful, 0.05) / 0.4))
    gain = min(target - heard, mix.max_boost_db)
    note = ""
    if m.peak_dbfs + gain > mix.headroom_dbfs:
        note = f"predicted peak {m.peak_dbfs + gain:+.1f} dBFS (clipping risk)"
    return round(gain, 2), note


def _missing(w: _Wanted, ctx: SfxContext) -> MissingAsset:
    del ctx
    return MissingAsset(
        recommendation_id="miss:sfx:" + digest([w.rule.purpose, w.rule.categories])[:12],
        priority=Need(w.rule.need),
        kind=SoundKind.SFX,
        purpose=w.rule.purpose,
        required_asset=f"{'/'.join(w.rule.categories)} sound for {w.rule.purpose}",
        categories=w.rule.categories,
        timeline_start=w.event.timeline_at,
        timeline_end=None,
        min_seconds=None,
        max_seconds=w.rule.max_seconds,
        style=f"{w.profile.identity}: clean, restrained; lands on its "
        + ("peak" if w.rule.align == SfxAlign.PEAK.value else w.rule.align),
        reason="no suitable sound effect in the library (nothing was substituted)",
    )
