"""Plan the soundtrack of one edit: music per section, effects, silence, original audio, ducking.

Pure: no I/O, no clock (``now`` is passed in). Deterministic: the same inputs, library and
configuration give the same plan (ties break on asset ids), so the plan hash is stable.

Order of decisions (each one can only remove sound, never invent it):

1. Sections get a role (explicit, else from their story beat functions; one implicit section
   when the timeline has none: the no-speech path).
2. Clip sound is classified: content (fireworks: the format or the section role says so) is
   DOMINANT and, by default, gets no music over it; speech is kept; muted clips stay muted.
3. Music presence per section from the profile (feature / bed / off / silence), minus
   preserved spans, minus spans too short for a cue. Short profile gaps are bridged.
4. Touching music slots with a compatible role form one cue (one track). The track is the best
   library match (stated category, MEASURED energy and tempo, length, reuse, licence); none ->
   a missing-asset recommendation and no music (never a random substitute).
5. Arrangement: cues start on a measured downbeat of the track (the section of the track whose
   level best fits the energy target, or the track start for openings), the last cue may be
   back-timed so the track ends naturally, a measured loop region extends a short track.
   Music changes follow J/L audio changes; picture is never moved.
6. Mix intent: levels against a speech reference, ducking under speech and under loud
   original moments, tail crossfades between cues, fades into music-free spans.
7. Effects (``sfx.py``), credits, QC.
"""

import itertools
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from fractions import Fraction
from typing import Final

from media_house.modules.music_and_sound_design.domain.analysis import MusicAnalysis
from media_house.modules.music_and_sound_design.domain.library import (
    SoundAsset,
    SoundIndex,
    SoundQuery,
)
from media_house.modules.music_and_sound_design.domain.model import (
    Candidate,
    Credit,
    Crossfade,
    DuckingIntent,
    InputRecord,
    ItemDecision,
    MissingAsset,
    MusicCue,
    MusicFreeSpan,
    MusicSegment,
    OriginalAudio,
    Provenance,
    SfxEvent,
    SoundDesignPlan,
    StageReport,
)
from media_house.modules.music_and_sound_design.domain.profile import (
    SoundDesignConfig,
    SoundProfile,
)
from media_house.modules.music_and_sound_design.domain.serialization import canonical
from media_house.modules.music_and_sound_design.domain.sfx import SfxContext, place_sfx
from media_house.modules.music_and_sound_design.domain.spans import (
    Span,
    asset_ref,
    digest,
    frame,
    merge,
    overlap,
    overlaps_any,
    seconds,
    subtract,
    track_energy,
)
from media_house.modules.music_and_sound_design.domain.timeline import (
    SoundtrackTimeline,
    fraction,
    rational,
)
from media_house.modules.music_and_sound_design.domain.validation import quality_checks
from media_house.modules.music_and_sound_design.domain.values import (
    ANALYZER_VERSION,
    PLANNER_VERSION,
    PROFILE_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    Alignment,
    ClipAudio,
    CrossfadeKind,
    Decision,
    DuckKey,
    EditPointKind,
    MusicMode,
    MusicPresence,
    Need,
    OriginalAudioMode,
    ResultState,
    SectionRole,
    Severity,
    SoundKind,
)

_ANTI_CLICK = Fraction(1, 100)  # 10 ms: a technical fade, not a creative one
_EVENT_PAD = Fraction(1, 4)
_ROLE_ENERGY_STEP = 0.15
_ALTERNATIVES = 3
_PLAN_REUSE_PENALTY = 0.15

_FUNCTION_ROLE: dict[str, SectionRole] = {
    "hook": SectionRole.HOOK,
    "setup": SectionRole.INTRO,
    "context": SectionRole.INTRO,
    "claim": SectionRole.EXPLANATION,
    "explanation": SectionRole.EXPLANATION,
    "evidence": SectionRole.EXPLANATION,
    "example": SectionRole.EXPLANATION,
    "counterpoint": SectionRole.EXPLANATION,
    "caveat": SectionRole.EXPLANATION,
    "question": SectionRole.EXPLANATION,
    "answer": SectionRole.EXPLANATION,
    "demonstration": SectionRole.DEMONSTRATION,
    "turning_point": SectionRole.REVEAL,
    "payoff": SectionRole.REVEAL,
    "recap": SectionRole.RECAP,
    "call_to_action": SectionRole.CALL_TO_ACTION,
    "transition": SectionRole.BODY,
    "unclassified": SectionRole.BODY,
}

LIMITATIONS: tuple[str, ...] = (
    "Mood and genre are the sources' own statements; no mood or genre is measured.",
    "Vocal presence in music is not measured (no separation or vocal model is installed).",
    "Downbeats assume 4/4; tempo can be read at half or double speed on ambiguous tracks.",
    "Fireworks audio is protected by section role, format profile and audio_intelligence"
    " energy spikes / impulses; nothing here recognises fireworks as such.",
    "Levels and ducking are intent; loudness, dynamics and mastering belong to audio_improvement.",
)


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanInputs:
    timeline: SoundtrackTimeline
    index: SoundIndex
    decisions: tuple[ItemDecision, ...] = ()
    #: Tracks used by earlier projects (diversity across videos).
    recently_used: frozenset[str] = frozenset()


@dataclass(frozen=True, slots=True, kw_only=True)
class PlanMeta:
    plan_version: int = 1
    supersedes: str | None = None
    inputs: tuple[InputRecord, ...] = ()
    stages: tuple[StageReport, ...] = ()
    engines: tuple[tuple[str, str], ...] = ()
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _Section:
    section_id: str
    start: Fraction
    end: Fraction
    role: SectionRole
    profile: SoundProfile


@dataclass(slots=True)
class _Free:
    span: Span
    mode: MusicMode
    reason: str
    section_id: str | None
    #: Only a span that is free because the profile says "off" may be bridged by a bed.
    bridgeable: bool = False


@dataclass(slots=True)
class _Slot:
    span: Span
    section: _Section
    presence: MusicPresence
    bridged: bool = False


@dataclass(slots=True)
class _Cue:
    cue_id: str
    slots: list[_Slot]
    start: Fraction
    end: Fraction
    follows_bridge: str | None = None
    bridge_kind: object = None
    notes: list[str] = field(default_factory=list)

    @property
    def section(self) -> _Section:
        return self.slots[0].section

    @property
    def presence(self) -> MusicPresence:
        if any(s.presence is MusicPresence.FEATURE for s in self.slots):
            return MusicPresence.FEATURE
        return MusicPresence.BED


def cache_key(
    inputs: PlanInputs, config: SoundDesignConfig, profile: SoundProfile
) -> dict[str, object]:
    """Everything that can change the plan, nothing that cannot (time, job, device)."""
    timeline = inputs.timeline
    return {
        "anchor": timeline.anchor_asset_id,
        "edit": None
        if timeline.edit is None
        else [timeline.edit.edit_plan_id, timeline.edit.plan_version],
        "timeline": digest(canonical(timeline)),
        "configuration": digest(config.fingerprint(profile)),
        "library": inputs.index.fingerprint(),
        "decisions": sorted(
            [d.item_id, d.decision.value, d.asset_id or "", d.note] for d in inputs.decisions
        ),
        "recently_used": sorted(inputs.recently_used),
        "versions": {
            "planner": PLANNER_VERSION,
            "analyzer": ANALYZER_VERSION,
            "schema": SCHEMA_VERSION,
            "vocabulary": VOCABULARY_VERSION,
            "profile_schema": PROFILE_VERSION,
        },
    }


def plan_sound_design(
    inputs: PlanInputs,
    config: SoundDesignConfig,
    profile: SoundProfile,
    *,
    now: datetime,
    meta: PlanMeta,
) -> SoundDesignPlan:
    timeline = inputs.timeline
    anchor = timeline.anchor_asset_id or ""
    end = timeline.timeline_end
    fps = fraction(timeline.frame_rate)
    decisions = {d.item_id: d for d in inputs.decisions}
    sections = _sections(timeline, config, profile, end)

    def profile_at(t: Fraction) -> SoundProfile:
        for s in sections:
            if s.start <= t < s.end:
                return s.profile
        return sections[-1].profile if sections else profile

    def section_at(t: Fraction) -> _Section | None:
        return next((s for s in sections if s.start <= t < s.end), None)

    speech_raw = [(fraction(s.timeline_start), fraction(s.timeline_end)) for s in timeline.speech]
    speech_raw += [
        (fraction(c.timeline_start), fraction(c.timeline_end))
        for c in timeline.clips
        if c.audio is ClipAudio.VOICEOVER
    ]
    speech = merge(speech_raw, seconds(profile.mix.speech_merge_gap_seconds))
    dominant, events = _preserved(timeline, profile_at, section_at, speech_raw)

    free: list[_Free] = []
    slots = _slots(sections, dominant, free)
    _bridge(slots, free)
    cues = _cues(slots, free, timeline, decisions)

    used: list[str] = []
    music: list[MusicCue] = []
    missing: list[MissingAsset] = []
    for cue in cues:
        built, gap, need = _music_cue(cue, inputs, config, decisions, used, end, fps, cues)
        music.append(built)
        if gap is not None:
            free.append(gap)
        if need is not None:
            missing.append(need)
        if built.asset is not None and built.segments:
            used.append(built.asset.asset_id)
    music = _join(music, cues, inputs.index, end, fps)
    music, dropouts = _dropouts(music, timeline, profile_at, fps)
    free += dropouts
    ducking = _ducking(music, speech, events, dominant, profile_at)
    crossfades = _crossfades(music, cues)

    sfx_outcome = place_sfx(
        timeline.events,
        inputs.index,
        SfxContext(
            timeline_end=end,
            fps=fps,
            speech=speech_raw,
            preserved=[(span, reason) for span, reason, _ in dominant]
            + [((a - _EVENT_PAD, b + _EVENT_PAD), why) for (a, b), why, _ in events],
            profile_at=profile_at,
            decisions=decisions,
            policy=config.licence,
        ),
    )
    missing += sfx_outcome.missing
    original = _original_audio(timeline, dominant, speech_raw, music)
    credit_list = _credits(music, sfx_outcome.events, inputs.index)
    music_free = tuple(
        MusicFreeSpan(
            span_id=f"free:{digest([str(f.span[0]), str(f.span[1]), f.mode.value])[:12]}",
            timeline_start=rational(f.span[0]),
            timeline_end=rational(f.span[1]),
            mode=f.mode,
            reason=f.reason,
            section_id=f.section_id,
        )
        for f in sorted(free, key=lambda f: (f.span, f.mode.value))
        if f.span[1] > f.span[0]
    )
    sfx_events = tuple(
        sorted(sfx_outcome.events, key=lambda e: (fraction(e.timeline_start), e.item_id))
    )
    no_sfx = tuple(sorted(sfx_outcome.declined, key=lambda e: (fraction(e.timeline_at), e.item_id)))
    checks = quality_checks(
        music=tuple(music),
        sfx=sfx_events,
        speech=speech,
        clips=timeline.clips,
        profile=profile,
        index=inputs.index,
        timeline_end=end,
        frame_rate_assumed=timeline.frame_rate_assumed,
        missing=tuple(missing),
        attributions=credit_list,
    )
    items = {
        "music": music,
        "music_free": music_free,
        "sfx": sfx_events,
        "no_sfx": no_sfx,
        "original_audio": original,
        "ducking": ducking,
        "crossfades": crossfades,
        "missing": missing,
        "credits": credit_list,
    }
    plan_hash = digest(canonical(items))
    partial = any(c.severity is Severity.ERROR for c in checks) or any(
        m.priority is Need.REQUIRED for m in missing
    )
    warnings = list(meta.warnings)
    unmeasured = sum(1 for s in inputs.index.sounds if not s.measured)
    if unmeasured:
        warnings.append(f"{unmeasured} library sounds have no stored measurement yet")
    if not inputs.index.sounds:
        warnings.append("the sound library is empty: import the owner's library first")
    return SoundDesignPlan(
        plan_id="sdp-" + digest(cache_key(inputs, config, profile))[:16],
        plan_version=meta.plan_version,
        supersedes=meta.supersedes,
        project_id=timeline.project_id,
        edit=timeline.edit,
        style=None,
        anchor_asset_id=anchor,
        created_at=now,
        state=ResultState.PARTIAL if partial else ResultState.OK,
        profile=profile.name,
        identity=profile.identity,
        identity_version=profile.identity_version,
        frame_rate=timeline.frame_rate,
        frame_rate_assumed=timeline.frame_rate_assumed,
        timeline_duration=rational(end),
        music=tuple(music),
        music_free=music_free,
        sfx=sfx_events,
        no_sfx=no_sfx,
        original_audio=original,
        ducking=ducking,
        crossfades=crossfades,
        missing_assets=tuple(missing),
        credits=credit_list,
        decisions=tuple(sorted(inputs.decisions, key=lambda d: d.item_id)),
        checks=checks,
        inputs=meta.inputs,
        stages=meta.stages,
        provenance=Provenance(
            schema_version=SCHEMA_VERSION,
            planner_version=PLANNER_VERSION,
            analyzer_version=ANALYZER_VERSION,
            vocabulary_version=VOCABULARY_VERSION,
            profile=profile.name,
            profile_version=profile.version,
            profile_schema_version=PROFILE_VERSION,
            identity=profile.identity,
            identity_version=profile.identity_version,
            configuration_fingerprint=digest(config.fingerprint(profile)),
            library_fingerprint=inputs.index.fingerprint(),
            engines=meta.engines,
        ),
        plan_hash=plan_hash,
        warnings=tuple(warnings),
        limitations=LIMITATIONS,
        schema_version=SCHEMA_VERSION,
    )


# --- sections and original audio -------------------------------------------------------------
def role_of(beat_functions: tuple[str, ...]) -> SectionRole:
    roles = [_FUNCTION_ROLE[f] for f in beat_functions if f in _FUNCTION_ROLE]
    if not roles:
        return SectionRole.BODY
    counts = Counter(roles)
    best = max(counts.values())
    return next(r for r in roles if counts[r] == best)


def _sections(
    timeline: SoundtrackTimeline,
    config: SoundDesignConfig,
    profile: SoundProfile,
    end: Fraction,
) -> list[_Section]:
    out: list[_Section] = []
    cursor = Fraction(0)
    if not timeline.sections:
        content = min(timeline.content_end, end) or end
        out.append(
            _Section(
                "all",
                Fraction(0),
                content,
                SectionRole(profile.music.no_section_role),
                config.for_section(profile, "all"),
            )
        )
        cursor = content
    ordered = sorted(timeline.sections, key=lambda s: (fraction(s.timeline_start), s.section_id))
    for i, s in enumerate(ordered):
        start, stop = max(fraction(s.timeline_start), cursor), min(fraction(s.timeline_end), end)
        if start > cursor:
            gap_id = f"gap{i}"
            out.append(
                _Section(
                    gap_id, cursor, start, SectionRole.BODY, config.for_section(profile, gap_id)
                )
            )
        if stop > start:
            role = s.role or role_of(s.beat_functions)
            out.append(
                _Section(s.section_id, start, stop, role, config.for_section(profile, s.section_id))
            )
            cursor = stop
    if cursor < end:
        out.append(
            _Section("tail", cursor, end, SectionRole.OUTRO, config.for_section(profile, "tail"))
        )
    return out


type _Dominant = tuple[Span, str, str]  # span, reason, clip id
#: A clip whose speech covers at least this share is a talking clip.
_TALKING_SHARE: Final = 0.5


def _preserved(
    timeline: SoundtrackTimeline,
    profile_at: Callable[[Fraction], SoundProfile],
    section_at: Callable[[Fraction], _Section | None],
    speech: list[Span],
) -> tuple[list[_Dominant], list[_Dominant]]:
    """Clips whose own sound is the content, and loud original moments, with their reasons."""
    dominant: list[_Dominant] = []
    for clip in timeline.clips:
        if clip.audio is not ClipAudio.SOURCE_SYNC:
            continue
        span = (fraction(clip.timeline_start), fraction(clip.timeline_end))
        middle = (span[0] + span[1]) / 2
        rules = profile_at(middle).original_audio
        if not rules.preserve_fireworks:
            continue
        section = section_at(middle)
        if section is not None and section.role.value in rules.content_roles:
            dominant.append(
                (span, f"the {section.role.value} section's own sound is the content", clip.clip_id)
            )
        elif rules.source_audio_is_content and _speech_share(span, speech) < (
            rules.incidental_speech_share
        ):
            dominant.append(
                (
                    span,
                    f"profile {profile_at(middle).name}: clip sound is the content",
                    clip.clip_id,
                )
            )
    talking = {
        clip.clip_id
        for clip in timeline.clips
        if _speech_share((fraction(clip.timeline_start), fraction(clip.timeline_end)), speech)
        >= _TALKING_SHARE
    }
    events: list[_Dominant] = []
    for event in timeline.original_events:
        span = (fraction(event.timeline_start), fraction(event.timeline_end))
        rules = profile_at(span[0]).original_audio
        # In a talking clip a loud moment inside the speech is the voice rising, not a bang.
        if event.clip_id in talking and _speech_share(span, speech) > 0:
            continue
        if event.strength >= rules.event_strength:
            events.append(
                (span, f"loud original {event.kind} (strength {event.strength:.2f})", event.clip_id)
            )
    return dominant, events


def _speech_share(span: Span, speech: list[Span]) -> float:
    """Share of ``span`` its speech covers (overlapping speech spans are merged first)."""
    length = span[1] - span[0]
    if length <= 0:
        return 0.0
    covered = sum((o[1] - o[0] for s in merge(speech) if (o := overlap(span, s))), Fraction(0))
    return float(covered / length)


def _original_audio(
    timeline: SoundtrackTimeline,
    dominant: list[_Dominant],
    speech: list[Span],
    music: list[MusicCue],
) -> tuple[OriginalAudio, ...]:
    music_spans = [
        (fraction(s.timeline_start), fraction(s.timeline_end)) for c in music for s in c.segments
    ]
    by_clip = {clip_id: reason for _, reason, clip_id in dominant}
    out = []
    for clip in sorted(
        timeline.clips, key=lambda c: (fraction(c.timeline_start), c.layer, c.clip_id)
    ):
        span = (fraction(clip.timeline_start), fraction(clip.timeline_end))
        under = overlaps_any(span, music_spans)
        if clip.audio is ClipAudio.MUTED:
            mode, reason = OriginalAudioMode.MUTED_BY_EDIT, "the edit mutes this clip"
        elif clip.audio is ClipAudio.VOICEOVER:
            mode, reason = OriginalAudioMode.VOICEOVER, "the clip's speech runs as voice-over"
        elif clip.clip_id in by_clip:
            mode, reason = OriginalAudioMode.DOMINANT, by_clip[clip.clip_id]
        elif overlaps_any(span, speech):
            mode, reason = OriginalAudioMode.SPEECH, "speech: kept and never covered"
        else:
            mode = OriginalAudioMode.UNDER_MUSIC
            reason = "kept as ambience under the music" if under else "kept: no music here"
        out.append(
            OriginalAudio(
                clip_id=clip.clip_id,
                asset_id=clip.asset_id,
                timeline_start=clip.timeline_start,
                timeline_end=clip.timeline_end,
                mode=mode,
                reason=reason,
                music_over_it=under,
            )
        )
    return tuple(out)


# --- music slots and cues ----------------------------------------------------------------------
def _slots(sections: list[_Section], dominant: list[_Dominant], free: list[_Free]) -> list[_Slot]:
    slots: list[_Slot] = []
    for s in sections:
        span = (s.start, s.end)
        presence = s.profile.music.presence.of(s.role)
        if presence is MusicPresence.SILENCE:
            free.append(
                _Free(
                    span,
                    MusicMode.INTENTIONAL_SILENCE,
                    f"deliberate silence in the {s.role.value}",
                    s.section_id,
                )
            )
            continue
        if presence is MusicPresence.OFF:
            free.append(
                _Free(
                    span,
                    MusicMode.MUSIC_DISABLED,
                    f"profile {s.profile.name}: no music in the {s.role.value}",
                    s.section_id,
                    bridgeable=True,
                )
            )
            continue
        rules = s.profile.original_audio
        holes = [d[0] for d in dominant] if rules.music_over_preserved == "off" else []
        pieces = subtract(span, holes)
        for hole in subtract(span, pieces):
            reason = next((r for d, r, _ in dominant if overlap(d, hole)), "original audio")
            free.append(
                _Free(hole, MusicMode.MUSIC_DISABLED, f"{reason}: no music over it", s.section_id)
            )
        slots += [_Slot(p, s, presence) for p in pieces]
    return sorted(slots, key=lambda x: x.span)


def _bridge(slots: list[_Slot], free: list[_Free]) -> None:
    """A short "no music" gap between two music slots keeps the bed running (no flicker)."""
    for gap in list(free):
        if not gap.bridgeable:
            continue
        before = next((s for s in slots if s.span[1] == gap.span[0]), None)
        after = next((s for s in slots if s.span[0] == gap.span[1]), None)
        limit = (
            seconds(before.section.profile.silence.min_music_gap_seconds) if before else Fraction(0)
        )
        if before is None or after is None or gap.span[1] - gap.span[0] >= limit:
            continue
        free.remove(gap)
        slots.append(_Slot(gap.span, before.section, MusicPresence.BED, bridged=True))
    slots.sort(key=lambda x: x.span)


def _compatible(a: _Slot, b: _Slot) -> bool:
    if a.section.section_id == b.section.section_id or b.bridged or a.bridged:
        return True
    rules = a.section.profile.music
    if not rules.change_on_role_change or a.section.role is b.section.role:
        return True
    energy_a = rules.energy.of(a.section.role)
    energy_b = b.section.profile.music.energy.of(b.section.role)
    shared = set(rules.categories.of(a.section.role)) & set(
        b.section.profile.music.categories.of(b.section.role)
    )
    return abs(energy_a - energy_b) < _ROLE_ENERGY_STEP and bool(shared)


def _cues(
    slots: list[_Slot],
    free: list[_Free],
    timeline: SoundtrackTimeline,
    decisions: Mapping[str, ItemDecision],
) -> list[_Cue]:
    cues: list[_Cue] = []
    for slot in slots:
        last = cues[-1] if cues else None
        if last is not None and last.end == slot.span[0] and _compatible(last.slots[-1], slot):
            last.slots.append(slot)
            last.end = slot.span[1]
            continue
        cues.append(_Cue("", [slot], slot.span[0], slot.span[1]))
    seen: Counter[str] = Counter()
    for cue in cues:
        base = f"mus:{cue.section.section_id}"
        seen[base] += 1
        cue.cue_id = base if seen[base] == 1 else f"{base}#{seen[base]}"
    kept: list[_Cue] = []
    for cue in cues:
        decision = decisions.get(cue.cue_id)
        rules = cue.section.profile.music
        if (
            decision is not None
            and decision.decision is Decision.REJECTED
            and not decision.asset_id
        ):
            free.append(
                _Free(
                    (cue.start, cue.end),
                    MusicMode.MUSIC_DISABLED,
                    decision.note or "music rejected by a person",
                    cue.section.section_id,
                )
            )
        elif cue.end - cue.start < seconds(rules.min_cue_seconds):
            free.append(
                _Free(
                    (cue.start, cue.end),
                    MusicMode.MUSIC_DISABLED,
                    f"only {float(cue.end - cue.start):.1f} s: shorter than the shortest cue"
                    f" ({rules.min_cue_seconds:g} s)",
                    cue.section.section_id,
                )
            )
        else:
            kept.append(cue)
    _follow_bridges(kept, timeline)
    return kept


def _follow_bridges(cues: list[_Cue], timeline: SoundtrackTimeline) -> None:
    """At a J/L cut the music changes with the AUDIO, not the picture (picture is not moved)."""
    for a, b in itertools.pairwise(cues):
        if a.end != b.start:
            continue
        for bridge in timeline.bridges:
            if fraction(bridge.timeline_picture_at) != a.end:
                continue
            audio_at = fraction(bridge.timeline_audio_at)
            if a.start < audio_at < b.end:
                a.end = b.start = audio_at
                b.follows_bridge = bridge.bridge_id
                b.bridge_kind = bridge.kind
                break


# --- track choice and arrangement --------------------------------------------------------------
def _music_cue(
    cue: _Cue,
    inputs: PlanInputs,
    config: SoundDesignConfig,
    decisions: Mapping[str, ItemDecision],
    used: list[str],
    end: Fraction,
    fps: Fraction,
    cues: list[_Cue],
) -> tuple[MusicCue, _Free | None, MissingAsset | None]:
    section = cue.section
    rules = section.profile.music
    role = section.role
    target = rules.energy.of(role)
    categories = rules.categories.of(role)
    length = cue.end - cue.start
    decision = decisions.get(cue.cue_id)
    ranked, conflicts = _rank(cue, inputs, config, decision, used, length, target)
    base = {
        "cue_id": cue.cue_id,
        "section_ids": tuple(dict.fromkeys(s.section.section_id for s in cue.slots)),
        "role": role,
        "presence": cue.presence,
        "timeline_start": rational(cue.start),
        "timeline_end": rational(cue.end),
        "energy_target": target,
        "decision": None if decision is None else decision.decision,
    }
    if not ranked:
        need = MissingAsset(
            recommendation_id="miss:music:" + digest([cue.cue_id, categories])[:12],
            priority=Need.RECOMMENDED if cue.presence is MusicPresence.FEATURE else Need.OPTIONAL,
            kind=SoundKind.MUSIC,
            purpose=f"music for the {role.value}",
            required_asset=f"{'/'.join(categories)} track, instrumental, at least"
            f" {float(length):.0f} s or loopable",
            categories=categories,
            timeline_start=rational(cue.start),
            timeline_end=rational(cue.end),
            min_seconds=round(float(length), 1),
            max_seconds=None,
            style=f"energy about {target:.2f}, {rules.tempo_min_bpm:g}-{rules.tempo_max_bpm:g} BPM",
            reason="no licence-cleared track of these categories in the library (nothing was"
            " substituted)",
        )
        empty = _Free(
            (cue.start, cue.end),
            MusicMode.MUSIC_DISABLED,
            "no suitable track in the library",
            section.section_id,
        )
        cue_out = MusicCue(
            mode=MusicMode.MUSIC_DISABLED,
            reason="no suitable track in the library: see the missing-asset recommendation",
            conflicts=tuple(conflicts),
            **base,  # type: ignore[arg-type]
        )
        return cue_out, empty, need
    best_sound, best_score, best_reasons = ranked[0]
    is_last = cue is cues[-1] or cue.end != cues[cues.index(cue) + 1].start
    opening = cue.start == 0 or role in (SectionRole.HOOK, SectionRole.INTRO)
    arrangement = _arrange(cue, best_sound, target, end, fps, is_last=is_last, opening=opening)
    gain, gain_note = _gain(best_sound, cue)
    segments = tuple(replace(s, gain_db=gain) for s in arrangement.segments)
    notes = conflicts + arrangement.notes + ([gain_note] if gain_note else []) + cue.notes
    gap = None
    if arrangement.covered_until < cue.end:
        gap = _Free(
            (arrangement.covered_until, cue.end),
            MusicMode.MUSIC_DISABLED,
            "the track ran out and has no measured loop region",
            section.section_id,
        )
    analysis = best_sound.music
    tempo = analysis.measurement.tempo if analysis else None
    return (
        MusicCue(
            mode=MusicMode.MUSIC_ENABLED,
            reason=f"{best_sound.title}: " + ", ".join(best_reasons),
            asset=asset_ref(best_sound),
            score=round(best_score, 4),
            energy_measured=track_energy(analysis),
            tempo_bpm=None if tempo is None else tempo.bpm,
            tempo_confidence=None if tempo is None else tempo.confidence,
            alignment=arrangement.alignment,
            segments=segments,
            edit_points_used=tuple(arrangement.points),
            alternatives=tuple(
                Candidate(asset_id=s.asset_id, title=s.title, score=round(sc, 4), reasons=r)
                for s, sc, r in ranked[1 : 1 + _ALTERNATIVES]
            ),
            conflicts=tuple(notes),
            **base,  # type: ignore[arg-type]
        ),
        gap,
        None,
    )


def _rank(
    cue: _Cue,
    inputs: PlanInputs,
    config: SoundDesignConfig,
    decision: ItemDecision | None,
    used: list[str],
    length: Fraction,
    target: float,
) -> tuple[list[tuple[SoundAsset, float, tuple[str, ...]]], list[str]]:
    rules = cue.section.profile.music
    conflicts: list[str] = []
    exclude = frozenset(
        [decision.asset_id]
        if decision and decision.decision is Decision.REJECTED and decision.asset_id
        else []
    )
    if decision is not None and decision.decision is Decision.ACCEPTED and decision.asset_id:
        pinned = inputs.index.get(decision.asset_id)
        if (
            pinned is not None
            and pinned.kind is SoundKind.MUSIC
            and config.licence.allows(pinned.licence)
        ):
            return [(pinned, 1.0, ("chosen by a person",))], conflicts
        conflicts.append("the person's chosen track is missing or not licence-cleared")
    matches = inputs.index.search(
        SoundQuery(
            kind=SoundKind.MUSIC,
            categories=rules.categories.of(cue.section.role),
            policy=config.licence,
            exclude=exclude,
            limit=200,
        )
    )
    matches = [
        m
        for m in matches
        if not m.asset.licence.attribution_required or m.asset.licence.attribution_text
    ]
    distinct = set(used)
    if len(distinct) >= rules.max_tracks:
        capped = [m for m in matches if m.asset.asset_id in distinct]
        if capped:
            matches = capped
            conflicts.append(
                f"track limit {rules.max_tracks} reached: reusing a track of this video"
            )
    ranked = []
    for m in matches:
        score, reasons = m.score, list(m.reasons)
        energy = track_energy(m.asset.music)
        if energy is None:
            reasons.append("energy not measured")
        else:
            score += 0.3 * (1.0 - abs(energy - target))
            reasons.append(f"measured energy {energy:.2f} for target {target:.2f}")
        tempo = m.asset.music.measurement.tempo if m.asset.music else None
        if tempo is not None and rules.tempo_min_bpm <= tempo.bpm <= rules.tempo_max_bpm:
            score += 0.1
            reasons.append(f"{tempo.bpm:.0f} BPM measured")
        available = Fraction(m.asset.duration_seconds).limit_denominator(1000)
        if (m.asset.music is not None and m.asset.music.loopable) or available >= length:
            score += 0.1
        else:
            score -= 0.2 * float(1 - available / length) if length else 0.0
            reasons.append(f"only {m.asset.duration_seconds:.0f} s and no loop")
        if m.asset.asset_id in inputs.recently_used:
            score -= rules.reuse_penalty
            reasons.append("used in a recent video")
        if m.asset.asset_id in distinct and len(distinct) < rules.max_tracks:
            score -= _PLAN_REUSE_PENALTY
            reasons.append("already used in this video")
        if m.asset.licence.commercial_use is not True:
            score -= 0.1
            reasons.append(f"licence {m.asset.licence.state.value}: the owner must confirm")
        ranked.append((m.asset, score, tuple(reasons)))
    ranked.sort(key=lambda r: (-r[1], r[0].asset_id))
    return ranked, conflicts


@dataclass(slots=True)
class _Arrangement:
    segments: list[MusicSegment]
    alignment: Alignment
    points: list[str]
    notes: list[str]
    covered_until: Fraction


def _usable_end(sound: SoundAsset) -> Fraction:
    duration = Fraction(sound.duration_seconds).limit_denominator(100_000)
    if sound.music is None:
        return duration
    ending = [p for p in sound.music.measurement.edit_points if p.kind is EditPointKind.ENDING]
    return min(duration, sound.music.seconds(ending[0].frame)) if ending else duration


def _in_point(
    sound: SoundAsset, analysis: MusicAnalysis, target: float, opening: bool, beat_ok: bool
) -> tuple[Fraction, str, bool]:
    m = analysis.measurement
    downbeats = set(m.downbeat_frames)
    if opening or not m.sections:
        start = next((p for p in m.edit_points if p.kind is EditPointKind.TRACK_START), None)
        frame_at = start.frame if start else 0
        return (
            analysis.seconds(frame_at),
            f"track_start@{float(analysis.seconds(frame_at)):.2f}",
            beat_ok and frame_at in downbeats,
        )
    levels = [s.energy_db for s in m.sections]
    low, high = min(levels), max(levels)
    span = (high - low) or 1.0
    usable = _usable_end(sound)
    best = min(
        (s for s in m.sections if analysis.seconds(s.start_frame) < usable),
        key=lambda s: (abs((s.energy_db - low) / span - target), s.start_frame),
    )
    return (
        analysis.seconds(best.start_frame),
        f"section_start@{float(analysis.seconds(best.start_frame)):.2f}"
        + (f" ({best.label.value})" if best.label.value != "unlabeled" else ""),
        beat_ok and best.start_frame in downbeats,
    )


def _arrange(
    cue: _Cue,
    sound: SoundAsset,
    target: float,
    end: Fraction,
    fps: Fraction,
    *,
    is_last: bool,
    opening: bool,
) -> _Arrangement:
    rules = cue.section.profile.music
    length = cue.end - cue.start
    usable = _usable_end(sound)
    analysis = sound.music
    notes: list[str] = []
    points: list[str] = []
    fade_in, fade_out = seconds(rules.fade_in_seconds), seconds(rules.fade_out_seconds)
    tempo = analysis.measurement.tempo if analysis else None
    beat_ok = (
        rules.beat_align and tempo is not None and tempo.confidence >= rules.min_tempo_confidence
    )
    if analysis is None:
        notes.append("not measured: placed from the track start without beat alignment")
    elif not beat_ok:
        notes.append("tempo too uncertain for beat alignment")

    def segment(
        i: int,
        src: Fraction,
        tl: Fraction,
        dur: Fraction,
        fin: Fraction,
        fout: Fraction,
        on_down: bool,
        loop_pass: int = 0,
    ) -> MusicSegment:
        return MusicSegment(
            segment_id=f"{cue.cue_id}/{i}",
            source_start=rational(src),
            source_end=rational(src + dur),
            timeline_start=rational(tl),
            timeline_end=rational(tl + dur),
            timeline_start_frame=frame(tl, fps),
            timeline_end_frame=frame(tl + dur, fps),
            gain_db=0.0,
            fade_in=rational(min(fin, dur / 2)),
            fade_out=rational(min(fout, dur / 2)),
            loop_pass=loop_pass,
            on_downbeat=on_down,
        )

    if (
        is_last
        and rules.natural_ending
        and analysis is not None
        and length <= usable
        and not opening
    ):
        src = usable - length
        points.append(f"ending@{float(usable):.2f}")
        return _Arrangement(
            [segment(0, src, cue.start, length, fade_in, Fraction(0), False)],
            Alignment.BACK_TIMED,
            points,
            [*notes, "back-timed: the track's own ending closes the cue"],
            cue.end,
        )
    if analysis is None:
        src, on_down = Fraction(0), False
    else:
        src, label, on_down = _in_point(sound, analysis, target, opening, beat_ok)
        points.append(label)
    if src + length <= usable:
        fin = _ANTI_CLICK if on_down else fade_in
        return _Arrangement(
            [segment(0, src, cue.start, length, fin, fade_out, on_down)],
            Alignment.FRONT_TIMED,
            points,
            notes,
            cue.end,
        )
    loop = analysis.measurement.loop if analysis else None
    if analysis is not None and loop is not None:
        loop_start, loop_end = analysis.seconds(loop.start_frame), analysis.seconds(loop.end_frame)
        if loop_end > src and loop_end - loop_start > 0:
            points += [f"loop_start@{float(loop_start):.2f}", f"loop_end@{float(loop_end):.2f}"]
            segments: list[MusicSegment] = []
            tl, remaining, read_from, n = cue.start, length, src, 0
            while remaining > 0:
                read_to = loop_end if read_from < loop_end else usable
                dur = min(read_to - read_from, remaining)
                last_piece = dur == remaining
                fin = (_ANTI_CLICK if on_down else fade_in) if n == 0 else _ANTI_CLICK
                segments.append(
                    segment(
                        n,
                        read_from,
                        tl,
                        dur,
                        fin,
                        fade_out if last_piece else _ANTI_CLICK,
                        n > 0 or on_down,
                        n,
                    )
                )
                tl, remaining, read_from, n = tl + dur, remaining - dur, loop_start, n + 1
            return _Arrangement(
                segments,
                Alignment.LOOPED,
                points,
                [
                    *notes,
                    f"loops the measured {float(loop_end - loop_start):.1f} s region"
                    f" ({loop.similarity:.2f} similar)",
                ],
                cue.end,
            )
    dur = max(Fraction(0), usable - src)
    if dur <= 0:
        return _Arrangement(
            [], Alignment.FRONT_TIMED, points, [*notes, "the track has no usable audio"], cue.start
        )
    notes.append(f"the track covers only {float(dur):.1f} s of {float(length):.1f} s")
    return _Arrangement(
        [segment(0, src, cue.start, dur, fade_in, fade_out, on_down)],
        Alignment.FRONT_TIMED,
        points,
        notes,
        cue.start + dur,
    )


def _gain(sound: SoundAsset, cue: _Cue) -> tuple[float, str]:
    mix = cue.section.profile.mix
    free_level = mix.speech_reference_lufs - mix.bed_below_speech_lu + mix.duck_depth_db
    if cue.presence is MusicPresence.FEATURE:
        free_level += mix.feature_lift_db
    measured = sound.music.measurement.loudness.integrated_lufs if sound.music else None
    if measured is None:
        return 0.0, "loudness not measured: played at file level"
    return round(min(free_level - measured, mix.max_boost_db), 2), ""


def _join(
    music: list[MusicCue], cues: list[_Cue], index: SoundIndex, end: Fraction, fps: Fraction
) -> list[MusicCue]:
    """Touching cues: the outgoing track keeps playing under the incoming one (tail crossfade)."""
    out = list(music)
    for i in range(len(out) - 1):
        a, b = out[i], out[i + 1]
        if not a.segments or not b.segments or a.timeline_end != b.timeline_start:
            continue
        last = a.segments[-1]
        sound = index.get(a.asset.asset_id) if a.asset else None
        rules = cues[i].section.profile.music
        if sound is None or last.timeline_end != a.timeline_end:
            continue
        room = min(
            seconds(rules.crossfade_seconds),
            _usable_end(sound) - fraction(last.source_end),
            end - fraction(last.timeline_end),
        )
        if room <= 0:
            continue
        tl_end = fraction(last.timeline_end) + room
        extended = replace(
            last,
            source_end=rational(fraction(last.source_end) + room),
            timeline_end=rational(tl_end),
            timeline_end_frame=frame(tl_end, fps),
            fade_out=rational(room),
        )
        out[i] = replace(a, segments=(*a.segments[:-1], extended))
    return out


def _dropouts(
    music: list[MusicCue],
    timeline: SoundtrackTimeline,
    profile_at: Callable[[Fraction], SoundProfile],
    fps: Fraction,
) -> tuple[list[MusicCue], list[_Free]]:
    """Music stops just before a held reveal (pacing ``hold_reveal``) and comes back on it."""
    free: list[_Free] = []
    out = list(music)
    for cue_hint in timeline.pacing:
        if cue_hint.kind != "hold_reveal":
            continue
        at = fraction(cue_hint.timeline_start)
        length = seconds(profile_at(at).silence.reveal_dropout_seconds)
        if length <= 0:
            continue
        hole = (max(Fraction(0), at - length), at)
        for ci, cue in enumerate(out):
            pieces: list[MusicSegment] = []
            hit = False
            for seg in cue.segments:
                span = (fraction(seg.timeline_start), fraction(seg.timeline_end))
                if overlap(span, hole) is None:
                    pieces.append(seg)
                    continue
                hit = True
                for k, (a, b) in enumerate(subtract(span, [hole])):
                    shift = a - span[0]
                    pieces.append(
                        replace(
                            seg,
                            segment_id=f"{seg.segment_id}.{k}",
                            source_start=rational(fraction(seg.source_start) + shift),
                            source_end=rational(fraction(seg.source_start) + shift + (b - a)),
                            timeline_start=rational(a),
                            timeline_end=rational(b),
                            timeline_start_frame=frame(a, fps),
                            timeline_end_frame=frame(b, fps),
                            fade_in=seg.fade_in if a == span[0] else rational(_ANTI_CLICK),
                            fade_out=seg.fade_out
                            if b == span[1]
                            else rational(min(Fraction(1, 10), (b - a) / 2)),
                        )
                    )
            if hit:
                out[ci] = replace(cue, segments=tuple(pieces))
                free.append(
                    _Free(
                        hole,
                        MusicMode.INTENTIONAL_SILENCE,
                        f"music drops before a held reveal ({cue_hint.cue_id})",
                        None,
                    )
                )
    return out, free


def _ducking(
    music: list[MusicCue],
    speech: list[Span],
    events: list[_Dominant],
    dominant: list[_Dominant],
    profile_at: Callable[[Fraction], SoundProfile],
) -> tuple[DuckingIntent, ...]:
    out: list[DuckingIntent] = []
    for cue in music:
        for seg in cue.segments:
            span = (fraction(seg.timeline_start), fraction(seg.timeline_end))
            mix = profile_at(span[0]).mix
            originals = profile_at(span[0]).original_audio
            keys: list[tuple[DuckKey, Span, float, str]] = []
            keys += [(DuckKey.SPEECH, s, mix.duck_depth_db, "speech") for s in speech]
            keys += [
                (DuckKey.ORIGINAL_AUDIO, (a - _EVENT_PAD, b + _EVENT_PAD), originals.duck_db, why)
                for (a, b), why, _ in events
            ]
            if originals.music_over_preserved == "duck":
                keys += [
                    (DuckKey.ORIGINAL_AUDIO, d, originals.duck_db, why) for d, why, _ in dominant
                ]
            for key, s, depth, why in keys:
                hit = overlap(span, (max(Fraction(0), s[0]), s[1]))
                if hit is None or depth <= 0:
                    continue
                out.append(
                    DuckingIntent(
                        duck_id=f"duck:{key.value}:"
                        + digest([cue.cue_id, str(hit[0]), str(hit[1])])[:12],
                        cue_id=cue.cue_id,
                        key=key,
                        timeline_start=rational(hit[0]),
                        timeline_end=rational(hit[1]),
                        depth_db=depth,
                        attack_seconds=mix.duck_attack_seconds,
                        release_seconds=mix.duck_release_seconds,
                        reason=why,
                    )
                )
    unique = {d.duck_id: d for d in out}
    return tuple(sorted(unique.values(), key=lambda d: (fraction(d.timeline_start), d.duck_id)))


def _crossfades(music: list[MusicCue], cues: list[_Cue]) -> tuple[Crossfade, ...]:
    out = []
    bridges = {c.cue_id: c for c in cues}
    playing = [c for c in music if c.segments]
    for i, cue in enumerate(playing):
        nxt = playing[i + 1] if i + 1 < len(playing) else None
        last = cue.segments[-1]
        at = fraction(cue.timeline_end)
        tail = fraction(last.timeline_end) - at
        # A track that ends before its cue leaves no tail to cross into the next cue: it fades out.
        touching = nxt is not None and nxt.timeline_start == cue.timeline_end and tail > 0
        if not touching and fraction(last.fade_out) <= 0:
            continue
        info = bridges.get(nxt.cue_id) if touching and nxt is not None else None
        out.append(
            Crossfade(
                crossfade_id=f"xf:{cue.cue_id}",
                kind=CrossfadeKind.TAIL if touching else CrossfadeKind.FADE_OUT,
                timeline_at=rational(
                    at if touching else fraction(last.timeline_end) - fraction(last.fade_out)
                ),
                length=rational(tail if touching else fraction(last.fade_out)),
                outgoing_cue_id=cue.cue_id,
                incoming_cue_id=nxt.cue_id if touching and nxt is not None else None,
                follows_bridge=None if info is None else info.follows_bridge,
                bridge_kind=None if info is None else info.bridge_kind,  # type: ignore[arg-type]
            )
        )
    return tuple(out)


def _credits(
    music: list[MusicCue], sfx: Sequence[SfxEvent], index: SoundIndex
) -> tuple[Credit, ...]:
    first_use: dict[str, Fraction] = {}
    for cue in music:
        if cue.asset is not None and cue.segments:
            first_use.setdefault(cue.asset.asset_id, fraction(cue.segments[0].timeline_start))
    for event in sfx:
        t = fraction(event.timeline_start)
        if event.asset.asset_id not in first_use or t < first_use[event.asset.asset_id]:
            first_use[event.asset.asset_id] = t
    out = []
    for asset_id in sorted(first_use, key=lambda a: (first_use[a], a)):
        sound = index.get(asset_id)
        if (
            sound is not None
            and sound.licence.attribution_required
            and sound.licence.attribution_text
        ):
            out.append(
                Credit(asset_id=asset_id, title=sound.title, text=sound.licence.attribution_text)
            )
    return tuple(out)
