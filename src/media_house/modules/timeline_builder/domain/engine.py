"""Build the timeline: a validated specification in, one constructed timeline out.

Order: preflight (can it be built at all?) -> construct every element (carrying over what is
unchanged and what a person did) -> link transitions, sound and groups -> dependency manifest ->
validate the finished timeline -> findings, status, change set, content hash.

Deterministic: the same specification, files, configuration and people's edits give the same
``build_hash``. Nothing here reads a clock, a file or another module.
"""

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from fractions import Fraction
from functools import partial
from math import gcd

from media_house.modules.timeline_builder.domain.construct import (
    Ctx,
    audio_clip,
    audio_digest,
    built_track,
    built_transition,
    mix_instruction,
    overlay_clip,
    overlay_digest,
    source_media,
    subtitle_track,
    transition_digest,
    video_clip,
    video_digest,
)
from media_house.modules.timeline_builder.domain.dependencies import dependency_manifest
from media_house.modules.timeline_builder.domain.digest import digest
from media_house.modules.timeline_builder.domain.facts import (
    IssueFact,
    LiveSources,
    SpecFacts,
)
from media_house.modules.timeline_builder.domain.model import (
    AudioClip,
    BrandAssetReference,
    BuildInfo,
    BuiltSection,
    BuiltTimeline,
    BuiltTrack,
    BuiltTransition,
    ChangeSet,
    ColorSettings,
    Counts,
    DependencyRecord,
    EditReference,
    ExternalCheck,
    FontReference,
    Group,
    IdentityReference,
    Issue,
    Marker,
    MixInstruction,
    TimelineSettings,
    Validation,
    VideoClip,
    ZoneReference,
)
from media_house.modules.timeline_builder.domain.preflight import IdentityExpectation, preflight
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.modules.timeline_builder.domain.reconcile import Events, carry_over, heal, resolve
from media_house.modules.timeline_builder.domain.timing import fr, frame_at
from media_house.modules.timeline_builder.domain.validation import (
    external_issues,
    validate_against_spec,
    validate_structure,
    validation_of,
)
from media_house.modules.timeline_builder.domain.values import (
    BUILDER_VERSION,
    SCHEMA_VERSION,
    VOCABULARY_VERSION,
    BuildMode,
    BuildStatus,
    DependencyState,
    Provenance,
    Severity,
    TrackKind,
)

LIMITATIONS: tuple[str, ...] = (
    "Nested or compound sequences are not built: the specification holds none, and nesting "
    "would be a creative choice. Sections are organised as markers and a section table.",
    "Rendered-result checks (black frames, missing frames, silence, sync of the render) are not "
    "made from timeline metadata; `validation.not_run` lists them until an external check "
    "reports.",
    "A person's changes are recorded for clips (picture and sound); transitions, mix "
    "instructions and tracks are always carried out from the specification.",
    "Pixel aspect ratio and field order are not in the specification: square pixels, "
    "progressive picture are assumed, and the fit mode is applied by the timeline application.",
)

_RANK = {
    Severity.ERROR: 0,
    Severity.UNSUPPORTED: 1,
    Severity.REVIEW: 2,
    Severity.WARNING: 3,
    Severity.INFO: 4,
}
_SPEC_SEVERITY = {
    "critical": Severity.ERROR,
    "warning": Severity.WARNING,
    "creative_conflict": Severity.REVIEW,
    "auto_resolved": Severity.INFO,
}


@dataclass(frozen=True, slots=True)
class BuildMeta:
    build_version: int
    supersedes: str | None
    built_at: datetime
    spec_asset_id: str | None


@dataclass(frozen=True, slots=True)
class Built:
    """``timeline`` is ``None`` exactly when ``errors`` is not empty: nothing is stored."""

    timeline: BuiltTimeline | None
    errors: tuple[Issue, ...] = ()


def human_state(previous: BuiltTimeline | None) -> str:
    """Digest of everything a person did (it changes the next build, so it is in the cache key)."""
    # no previous build and a previous build nobody touched are the same state
    clips: list[VideoClip | AudioClip] = (
        [] if previous is None else [*previous.video_clips(), *previous.audio_clips()]
    )
    mine = [
        c for c in clips if c.provenance in {Provenance.HUMAN_MODIFIED, Provenance.HUMAN_CREATED}
    ]
    return digest(
        {
            "clips": mine,
            "gone": () if previous is None else previous.removed_by_human,
            "log": () if previous is None else previous.edit_log,
        }
    )


def input_hash(
    spec: SpecFacts,
    live: LiveSources,
    config: BuilderConfig,
    caps: Capabilities,
    expect: IdentityExpectation | None,
    checks: tuple[ExternalCheck, ...],
    *,
    full_rebuild: bool = False,
) -> str:
    """Everything that can change the result except a person's work (time, job and device
    never can). Stored on the build, so a recorded edit can be keyed without the specification."""
    return digest(
        {
            "spec": spec,
            "live": live.found,
            "config": config.fingerprint(),
            "capabilities": caps,
            "expect": expect,
            "checks": checks,
            "full": full_rebuild,
            "versions": {
                "builder": BUILDER_VERSION,
                "schema": SCHEMA_VERSION,
                "vocabulary": VOCABULARY_VERSION,
            },
        }
    )


def cache_key(inputs: str, people: BuiltTimeline | None) -> dict[str, object]:
    """The inputs plus what people have done to the latest build: the same pair always gives
    the same timeline, so the library fingerprint of this key is the cache."""
    return {"inputs": inputs, "people": human_state(people)}


def build_timeline(
    spec: SpecFacts,
    live: LiveSources,
    config: BuilderConfig,
    *,
    meta: BuildMeta,
    previous: BuiltTimeline | None = None,
    caps: Capabilities | None = None,
    expect: IdentityExpectation | None = None,
    checks: tuple[ExternalCheck, ...] = (),
    full_rebuild: bool = False,
) -> Built:
    config.check()
    caps = caps or Capabilities()
    problems = preflight(spec, live, expect)
    if problems:
        return Built(None, tuple(problems))
    ctx = Ctx.of(spec, live, config, caps)
    inputs = input_hash(spec, live, config, caps, expect, checks, full_rebuild=full_rebuild)
    settings = _settings(spec, config)
    mode, reason, reuse = _mode(previous, settings, full_rebuild)
    events = Events()
    policy = config.conflict_policy

    prior_v = {c.clip_id: c for c in previous.video_clips()} if previous else {}
    prior_a = {c.clip_id: c for c in previous.audio_clips()} if previous else {}
    tombs = {t.element_id: t for t in previous.removed_by_human} if previous else {}

    section_of = _audio_sections(spec)
    video: list[VideoClip] = []
    for v in spec.video:
        d = video_digest(ctx, v)
        clip = resolve(
            v.item_id,
            d,
            partial(video_clip, ctx, v, d),
            prior_v.get(v.item_id),
            tombs.get(v.item_id),
            policy,
            events,
            reuse=reuse,
        )
        if clip is not None:
            video.append(clip)
    for o in spec.overlays:
        d = overlay_digest(ctx, o)
        clip = resolve(
            o.item_id,
            d,
            partial(overlay_clip, ctx, o, d),
            prior_v.get(o.item_id),
            tombs.get(o.item_id),
            policy,
            events,
            reuse=reuse,
        )
        if clip is not None:
            video.append(clip)
    sound: list[AudioClip] = []
    for a in spec.audio:
        d = audio_digest(ctx, a)
        item = resolve(
            a.item_id,
            d,
            partial(audio_clip, ctx, a, d, section_of),
            prior_a.get(a.item_id),
            tombs.get(a.item_id),
            policy,
            events,
            reuse=reuse,
        )
        if item is not None:
            sound.append(item)
    video += carry_over(
        list(prior_v.values()),
        {v.item_id for v in spec.video} | {o.item_id for o in spec.overlays},
        policy,
        events,
    )
    sound += carry_over(list(prior_a.values()), {a.item_id for a in spec.audio}, policy, events)

    gone = {t.element_id for t in events.tombstones}
    track_of = {c.clip_id: c.track_id for c in video}
    transitions: list[BuiltTransition] = []
    reusable = {t.transition_id: t for t in previous.transitions} if previous and reuse else {}
    for t in spec.transitions:
        ends = (t.outgoing_item_id, t.incoming_item_id, t.overlay_item_id)
        if any(e in gone for e in ends if e):
            continue
        d = transition_digest(ctx, t)
        old = reusable.get(t.transition_id)
        if old is not None and old.source_digest == d:
            events.reused += 1
            transitions.append(old)
        else:
            transitions.append(built_transition(ctx, t, d, track_of))
    mixes: list[MixInstruction] = []
    for m in spec.automation:
        alive = tuple(i for i in m.target_item_ids if i not in gone)
        if m.target_item_ids and not alive:
            continue
        instruction = mix_instruction(m)
        mixes.append(
            replace(instruction, clip_ids=alive) if alive != instruction.clip_ids else instruction
        )

    dropped_t = {t.transition_id for t in spec.transitions} - {t.transition_id for t in transitions}
    dropped_m = {m.automation_id for m in spec.automation} - {m.mix_id for m in mixes}
    video, sound = heal(video, sound, dropped_t, dropped_m, gone)
    tracks = _tracks(spec, video, sound, previous)
    sections = _sections(spec, video, sound)
    sources = source_media(ctx, _users(video, sound))
    markers = _section_markers(sections) if config.section_markers else ()
    deps = dependency_manifest(spec, live, tuple(video), tuple(sound), tuple(transitions))
    all_audio = tuple(sound)

    draft = BuiltTimeline(
        timeline_id=spec.timeline_id,
        project_id=spec.project_id,
        edit=EditReference(edit_plan_id=spec.edit.edit_plan_id, plan_version=spec.edit.plan_version)
        if spec.edit
        else None,
        anchor_asset_id=spec.anchor_asset_id,
        settings=settings,
        identity=_identity(spec),
        sources=sources,
        tracks=tracks,
        transitions=tuple(sorted(transitions, key=lambda t: (t.cut_frame, t.transition_id))),
        mix=tuple(sorted(mixes, key=lambda m: (fr(m.timeline_start), m.mix_id))),
        sections=sections,
        markers=markers,
        groups=_groups(all_audio, transitions),
        subtitles=subtitle_track(ctx, spec.subtitles),
        credits=spec.credits,
        dependencies=deps,
        issues=(),
        validation=Validation(checks_run=(), passed=True),
        counts=_counts(tracks, transitions, markers, sections, sources),
        build=_placeholder_build(meta, mode, reason, events, spec, config, inputs),
        status=BuildStatus.OK,
        removed_by_human=tuple(sorted(events.tombstones, key=lambda t: t.element_id)),
        edit_log=previous.edit_log if previous else (),
        limitations=LIMITATIONS,
    )
    structural, ran = validate_structure(draft, config, caps)
    consistent, ran2 = validate_against_spec(draft, spec)
    issues = [
        *_spec_issues(spec),
        *structural,
        *consistent,
        *events.issues,
        *_dependency_issues(draft, config),
        *external_issues(checks),
    ]
    issues = _promoted(issues, config)
    if any(i.severity is Severity.ERROR for i in issues):
        return Built(None, tuple(i for i in issues if i.severity is Severity.ERROR))
    ordered = _ordered(issues)
    markers = (*markers, *_review_markers(ordered)) if config.review_markers else markers
    finished = replace(
        draft,
        markers=markers,
        issues=ordered,
        validation=validation_of(ordered, (*ran, *ran2), checks),
        counts=_counts(tracks, transitions, markers, sections, sources),
        status=_status(ordered, deps),
    )
    return Built(_with_info(finished, previous, meta, mode, reason, events, spec, config, inputs))


def revalidate(
    timeline: BuiltTimeline, config: BuilderConfig, caps: Capabilities | None = None
) -> tuple[BuiltTimeline, list[Issue]]:
    """Judge a timeline again after a person's change: the structural findings are remade, the
    specification's and the reconciliation's are kept. Returns the timeline and its ERRORs."""
    caps = caps or Capabilities()
    structural, ran = validate_structure(timeline, config, caps)
    kept = [i for i in timeline.issues if i.origin != "builder"]
    issues = _ordered(_promoted([*kept, *structural], config))
    errors = [i for i in issues if i.severity is Severity.ERROR]
    deps = timeline.dependencies
    return (
        replace(
            timeline,
            issues=issues,
            validation=validation_of(issues, ran, timeline.validation.external),
            status=_status(issues, deps),
            counts=_counts(
                timeline.tracks,
                timeline.transitions,
                timeline.markers,
                timeline.sections,
                timeline.sources,
            ),
        ),
        errors,
    )


# --- assembling ----------------------------------------------------------------------------------


def _settings(spec: SpecFacts, config: BuilderConfig) -> TimelineSettings:
    tb = spec.timebase
    divisor = gcd(tb.width, tb.height) or 1
    return TimelineSettings(
        width=tb.width,
        height=tb.height,
        aspect=(tb.width // divisor, tb.height // divisor),
        frame_rate=tb.frame_rate,
        frame_rate_assumed=tb.frame_rate_assumed,
        sample_rate=tb.sample_rate,
        audio_channels=config.audio_channels,
        duration=tb.duration,
        duration_frames=tb.duration_frames,
        color=ColorSettings(
            profile=tb.color.profile,
            space=tb.color.space,
            primaries=tb.color.primaries,
            transfer=tb.color.transfer,
            range=tb.color.range,
        ),
        fit_mode=config.fit_mode,
    )


def _mode(
    previous: BuiltTimeline | None, settings: TimelineSettings, full_rebuild: bool
) -> tuple[BuildMode, str, bool]:
    """``(mode, why a full rebuild, whether unchanged elements may be carried over)``."""
    if previous is None:
        return BuildMode.NEW, "", False
    if full_rebuild:
        return BuildMode.FULL, "a full rebuild was requested", False
    if previous.build.builder_version != BUILDER_VERSION:
        return BuildMode.FULL, "the builder version changed", False
    if previous.settings != settings:
        return BuildMode.FULL, "the timeline settings changed", False
    return BuildMode.INCREMENTAL, "", True


def _identity(spec: SpecFacts) -> IdentityReference:
    c = spec.creator
    return IdentityReference(
        identity_id=c.identity_id,
        identity_version=c.identity_version,
        style_document_id=c.style_document_id,
        style_fingerprint=c.style_fingerprint,
        format_profile=c.format_profile,
        sponsored=c.sponsored,
        sound_identity=c.sound_identity,
        sound_identity_version=c.sound_identity_version,
        rules=c.rules,
        fonts=tuple(
            FontReference(
                key=f.key, family=f.family, weight=f.weight, sha256=f.sha256, available=f.available
            )
            for f in c.fonts
        ),
        zones=tuple(
            ZoneReference(
                name=z.name, kind=z.kind, left=z.left, top=z.top, width=z.width, height=z.height
            )
            for z in c.zones
        ),
        brand_assets=tuple(
            BrandAssetReference(slot=b.slot, required=b.required, asset_id=b.asset_id)
            for b in c.brand_assets
        ),
        presets=c.presets,
    )


def _audio_sections(spec: SpecFacts) -> dict[str, str]:
    by_video = {v.item_id: v.section_id for v in spec.video if v.section_id}
    return {
        a.item_id: by_video[a.linked_item_id]
        for a in spec.audio
        if a.linked_item_id and a.linked_item_id in by_video
    }


def _users(video: list[VideoClip], sound: list[AudioClip]) -> dict[str, list[str]]:
    users: dict[str, list[str]] = defaultdict(list)
    for c in video:
        if c.source is not None:
            users[c.source.source_id].append(c.clip_id)
    for a in sound:
        if a.source is not None:
            users[a.source.source_id].append(a.clip_id)
    return users


def _tracks(
    spec: SpecFacts,
    video: list[VideoClip],
    sound: list[AudioClip],
    previous: BuiltTimeline | None,
) -> tuple[BuiltTrack, ...]:
    by_track_v: dict[str, list[VideoClip]] = defaultdict(list)
    by_track_a: dict[str, list[AudioClip]] = defaultdict(list)
    for c in video:
        by_track_v[c.track_id].append(c)
    for a in sound:
        by_track_a[a.track_id].append(a)
    built = [built_track(t) for t in spec.tracks]
    known = {t.track_id for t in built}
    if previous is not None:
        for p in previous.tracks:
            if p.track_id not in known and (p.track_id in by_track_v or p.track_id in by_track_a):
                built.append(replace(p, video=(), audio=(), provenance=Provenance.SYSTEM))
    return tuple(
        replace(
            t,
            video=tuple(
                sorted(by_track_v.get(t.track_id, ()), key=lambda c: (c.start_frame, c.clip_id))
            ),
            audio=tuple(
                sorted(by_track_a.get(t.track_id, ()), key=lambda c: (c.start_sample, c.clip_id))
            ),
        )
        for t in sorted(
            built, key=lambda t: (t.kind is TrackKind.AUDIO, t.index, t.lane, t.track_id)
        )
    )


def _sections(
    spec: SpecFacts, video: list[VideoClip], sound: list[AudioClip]
) -> tuple[BuiltSection, ...]:
    members: dict[str, list[str]] = defaultdict(list)
    for c in video:
        if c.section_id:
            members[c.section_id].append(c.clip_id)
    for a in sound:
        if a.section_id:
            members[a.section_id].append(a.clip_id)
    return tuple(
        BuiltSection(
            section_id=s.section_id,
            order=s.order,
            name=s.label,
            timeline_start=s.timeline_start,
            timeline_end=s.timeline_end,
            start_frame=s.start_frame,
            end_frame=s.end_frame,
            clip_ids=tuple(sorted(members.get(s.section_id, ()))),
        )
        for s in sorted(spec.sections, key=lambda s: (s.order, s.section_id))
    )


def _section_markers(sections: tuple[BuiltSection, ...]) -> tuple[Marker, ...]:
    return tuple(
        Marker(
            marker_id=f"marker:section:{s.section_id}",
            kind="section",
            frame=s.start_frame,
            duration_frames=max(0, s.end_frame - s.start_frame),
            name=s.name,
            ref=s.section_id,
        )
        for s in sections
    )


def _review_markers(issues: tuple[Issue, ...]) -> tuple[Marker, ...]:
    out: list[Marker] = []
    for i in issues:
        if i.start_frame is None or i.severity not in {
            Severity.REVIEW,
            Severity.UNSUPPORTED,
            Severity.WARNING,
        }:
            continue
        end = i.end_frame if i.end_frame is not None else i.start_frame
        out.append(
            Marker(
                marker_id="marker:issue:" + digest([i.code, i.element_id, i.start_frame])[:12],
                kind="review",
                frame=i.start_frame,
                duration_frames=max(0, end - i.start_frame),
                name=i.code.replace("_", " "),
                note=i.message,
                ref=i.element_id or "",
                severity=i.severity,
            )
        )
    unique = {m.marker_id: m for m in out}
    return tuple(unique[k] for k in sorted(unique, key=lambda k: (unique[k].frame, k)))


def _groups(
    sound: tuple[AudioClip, ...],
    transitions: list[BuiltTransition],
) -> tuple[Group, ...]:
    found: list[Group] = []
    for a in sound:
        if a.video_link is not None:
            found.append(
                Group(
                    group_id=f"group:av:{a.video_link}",
                    kind="av_link",
                    name=f"Picture and sound · {a.video_link}",
                    members=(a.video_link, a.clip_id),
                )
            )
    for t in transitions:
        members = tuple(
            m for m in (t.from_clip, t.to_clip, t.overlay_clip, t.mix_id, t.transition_id) if m
        )
        if len(members) > 1:
            found.append(
                Group(
                    group_id=f"group:transition:{t.transition_id}",
                    kind="transition",
                    name=f"Transition · {t.kind} · {t.transition_id}",
                    members=members,
                )
            )
    unique = {g.group_id: g for g in found}
    return tuple(unique[k] for k in sorted(unique))


def _counts(
    tracks: tuple[BuiltTrack, ...],
    transitions: list[BuiltTransition] | tuple[BuiltTransition, ...],
    markers: tuple[Marker, ...],
    sections: tuple[BuiltSection, ...],
    sources: tuple[object, ...],
) -> Counts:
    return Counts(
        video_tracks=sum(1 for t in tracks if t.kind is TrackKind.VIDEO),
        audio_tracks=sum(1 for t in tracks if t.kind is TrackKind.AUDIO),
        video_clips=sum(len(t.video) for t in tracks),
        audio_clips=sum(len(t.audio) for t in tracks),
        transitions=len(transitions),
        effects=sum(len(c.effects) for t in tracks for c in t.video),
        markers=len(markers),
        sections=len(sections),
        sources=len(sources),
    )


# --- findings -------------------------------------------------------------------------------------


def _spec_issues(spec: SpecFacts) -> list[Issue]:
    fps = fr(spec.timebase.frame_rate)
    out: list[Issue] = []
    for i in spec.issues:
        out.append(_carried(i, fps))
    return out


def _carried(i: IssueFact, fps: Fraction) -> Issue:
    return Issue(
        code=i.code,
        severity=_SPEC_SEVERITY.get(i.klass, Severity.WARNING),
        message=i.message,
        element_id=i.item_id,
        start_frame=None if i.timeline_start is None else frame_at(fr(i.timeline_start), fps),
        end_frame=None if i.timeline_end is None else frame_at(fr(i.timeline_end), fps),
        evidence=i.evidence,
        origin="specification",
        resolution=i.resolution,
    )


def _dependency_issues(t: BuiltTimeline, config: BuilderConfig) -> list[Issue]:
    out: list[Issue] = []
    for d in t.dependencies:
        if d.state is DependencyState.RESOLVED or d.kind.value in {"tracking", "transition_handle"}:
            continue
        # The manifest lists every gap. A finding is added where someone can act (the owner can
        # produce it) or something relies on it; the rest is already said by the compiler's own
        # findings, carried over, so it is not repeated.
        actionable = d.state is DependencyState.REQUESTED or bool(d.required_by)
        if not actionable and not config.block_on_unresolved:
            continue
        out.append(
            Issue(
                code="dependency_unresolved",
                severity=Severity.ERROR if config.block_on_unresolved else Severity.WARNING,
                message=f"{d.kind.value} {d.reference} is {d.state.value}"
                + (f" ({d.detail})" if d.detail else "")
                + (f"; ask {d.owner} for {d.request}" if d.owner else ""),
                element_id=d.dependency_id,
            )
        )
    return out


def _promoted(issues: list[Issue], config: BuilderConfig) -> list[Issue]:
    if not config.block_on_unsupported:
        return issues
    return [
        replace(i, severity=Severity.ERROR) if i.severity is Severity.UNSUPPORTED else i
        for i in issues
    ]


def _ordered(issues: list[Issue]) -> tuple[Issue, ...]:
    unique = dict.fromkeys(issues)
    return tuple(
        sorted(
            unique,
            key=lambda i: (
                _RANK[i.severity],
                -1 if i.start_frame is None else i.start_frame,
                i.code,
                i.element_id or "",
                i.message,
            ),
        )
    )


def _status(issues: tuple[Issue, ...], deps: tuple[DependencyRecord, ...]) -> BuildStatus:
    """Partial when anything needs a look or a dependency is still open; ok otherwise."""
    loud = any(i.severity is not Severity.INFO for i in issues)
    open_ = any(d.state in {DependencyState.MISSING, DependencyState.REQUESTED} for d in deps)
    return BuildStatus.PARTIAL if loud or open_ else BuildStatus.OK


# --- provenance of the build ----------------------------------------------------------------------


def _placeholder_build(
    meta: BuildMeta,
    mode: BuildMode,
    reason: str,
    events: Events,
    spec: SpecFacts,
    config: BuilderConfig,
    inputs: str,
) -> BuildInfo:
    return BuildInfo(
        build_version=meta.build_version,
        supersedes=meta.supersedes,
        builder_version=BUILDER_VERSION,
        vocabulary_version=VOCABULARY_VERSION,
        mode=mode,
        reason=reason,
        spec_asset_id=meta.spec_asset_id,
        spec_hash=spec.compilation.spec_hash,
        spec_compilation_version=spec.compilation.compilation_version,
        config_hash=digest(config.fingerprint()),
        input_hash=inputs,
        build_hash="",
        built_at=meta.built_at,
        elements_total=0,
        elements_reused=events.reused,
    )


def _index(t: BuiltTimeline) -> dict[str, str]:
    found: dict[str, str] = {}
    for tr in t.tracks:
        found[tr.track_id] = digest([tr.track_id, tr.name, tr.index, tr.lane, tr.blend])
    clips: list[VideoClip | AudioClip] = [*t.video_clips(), *t.audio_clips()]
    for c in clips:
        found[c.clip_id] = digest(c)
    for x in t.transitions:
        found[x.transition_id] = digest(x)
    for m in t.mix:
        found[m.mix_id] = digest(m)
    return found


def _section_of(t: BuiltTimeline | None, element: str) -> str | None:
    if t is None:
        return None
    clips: list[VideoClip | AudioClip] = [*t.video_clips(), *t.audio_clips()]
    return next((c.section_id for c in clips if c.clip_id == element), None)


def _change(previous: BuiltTimeline | None, now: BuiltTimeline, events: Events) -> ChangeSet:
    new = _index(now)
    old = _index(previous) if previous else {}
    added = tuple(sorted(set(new) - set(old))) if previous else ()
    removed = tuple(sorted(set(old) - set(new)))
    changed = tuple(sorted(k for k in set(new) & set(old) if new[k] != old[k]))
    touched = {s for k in (*added, *changed) if (s := _section_of(now, k)) is not None} | {
        s for k in removed if (s := _section_of(previous, k)) is not None
    }
    return ChangeSet(
        added=added,
        removed=removed,
        changed=changed,
        conflicted=tuple(sorted(set(events.conflicted))),
        unchanged=len(set(new) & set(old)) - len(changed),
        sections=tuple(sorted(touched)),
    )


def content_hash(t: BuiltTimeline) -> str:
    return digest(
        [
            t.settings,
            t.identity,
            t.sources,
            t.tracks,
            t.transitions,
            t.mix,
            t.sections,
            t.markers,
            t.groups,
            t.subtitles,
            t.credits,
            t.dependencies,
            t.issues,
            t.validation,
            t.removed_by_human,
            t.status,
            t.limitations,
        ]
    )


def _with_info(
    t: BuiltTimeline,
    previous: BuiltTimeline | None,
    meta: BuildMeta,
    mode: BuildMode,
    reason: str,
    events: Events,
    spec: SpecFacts,
    config: BuilderConfig,
    inputs: str,
) -> BuiltTimeline:
    info = replace(
        _placeholder_build(meta, mode, reason, events, spec, config, inputs),
        build_hash=content_hash(t),
        elements_total=sum(len(tr.video) + len(tr.audio) for tr in t.tracks) + len(t.transitions),
        elements_reused=events.reused,
        change=_change(previous, t, events),
    )
    return replace(t, build=info)


__all__ = [
    "LIMITATIONS",
    "BuildMeta",
    "Built",
    "build_timeline",
    "cache_key",
    "content_hash",
    "human_state",
    "input_hash",
    "revalidate",
]
