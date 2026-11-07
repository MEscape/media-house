"""Checks of the constructed timeline itself: media, tracks, gaps, overlaps, geometry, transitions,
what the builder reported, and the intended-versus-actual comparisons (specification, Resolve)."""

from itertools import pairwise

from media_house.modules.quality_control.domain.classify import make_issue
from media_house.modules.quality_control.domain.facts import (
    ClipFacts,
    ResolveFacts,
    TimelineFacts,
)
from media_house.modules.quality_control.domain.model import QualityIssue
from media_house.modules.quality_control.domain.profile import QualityProfile
from media_house.modules.quality_control.domain.values import Severity

_BUILDER = {
    "error": "structure.builder_error",
    "review": "structure.builder_review",
    "unsupported": "structure.builder_unsupported",
}
#: A clip scaled less / more than this relative to the other axis is stretched.
_STRETCH = 0.02
_UPSCALE = 1.6


def check_media(timeline: TimelineFacts) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    used = {c.clip_id: c for c in timeline.clips}
    for source in timeline.sources:
        if source.available:
            continue
        clips = tuple(used[i] for i in source.used_by if i in used)
        issues.append(
            make_issue(
                timeline,
                "structure.media_missing",
                f"'{source.name}' is not available (offline or removed from the library).",
                clips=clips,
                sources=(source.original_asset_id,),
                start_frame=min((c.start_frame for c in clips), default=None),
                end_frame=max((c.end_frame for c in clips), default=None),
                evidence=(f"source {source.source_id} is marked unavailable",),
                key=source.source_id,
            )
        )
    for clip in timeline.clips:
        if not clip.unresolved:
            continue
        code = (
            "graphic.missing_asset"
            if clip.kind in ("graphic", "caption")
            else "structure.unresolved_clip"
        )
        issues.append(
            make_issue(
                timeline,
                code,
                f"Clip '{clip.name}' has an unresolved dependency and cannot show what was "
                f"planned.",
                severity=Severity.CRITICAL if clip.kind == "primary" else None,
                start_frame=clip.start_frame,
                end_frame=clip.end_frame,
                clips=(clip,),
                evidence=("clip state is 'unresolved'",),
            )
        )
    for dependency in timeline.dependencies:
        if dependency.resolved or not dependency.required_by:
            continue
        clips = tuple(c for c in (timeline.clip(i) for i in dependency.required_by) if c)
        issues.append(
            make_issue(
                timeline,
                "structure.dependency_open",
                f"Dependency {dependency.kind} '{dependency.reference}' is not resolved.",
                clips=clips,
                evidence=(f"required by {len(dependency.required_by)} element(s)",),
                key=dependency.dependency_id,
            )
        )
    return issues


def check_layout(timeline: TimelineFacts) -> list[QualityIssue]:
    """Tracks, gaps and overlaps on the main picture, clips beyond the end, the settings."""
    issues: list[QualityIssue] = []
    settings = timeline.settings
    primary = timeline.primary()
    if not any(t.role == "primary" for t in timeline.tracks) or not primary:
        issues.append(
            make_issue(
                timeline,
                "structure.no_primary_track",
                "The timeline has no main picture clips.",
                confidence=0.95,
            )
        )
    for previous, current in pairwise(primary):
        gap = current.start_frame - previous.end_frame
        if gap > 1:
            issues.append(
                make_issue(
                    timeline,
                    "structure.gap",
                    f"{gap} frames ({gap / float(settings.fps):.2f} s) with no main picture "
                    f"between '{previous.name}' and '{current.name}'.",
                    start_frame=previous.end_frame,
                    end_frame=current.start_frame,
                    clips=(previous, current),
                    evidence=(
                        f"previous ends at frame {previous.end_frame}, next starts at "
                        f"{current.start_frame}",
                    ),
                    measurements=(
                        ("gap_frames", str(gap)),
                        ("previous_clip", previous.clip_id),
                        ("next_clip", current.clip_id),
                    ),
                )
            )
    if primary:
        if primary[0].start_frame > 1:
            issues.append(
                make_issue(
                    timeline,
                    "structure.gap",
                    f"The main picture starts at frame {primary[0].start_frame}, not at the start.",
                    start_frame=0,
                    end_frame=primary[0].start_frame,
                    clips=(primary[0],),
                    key="head",
                    measurements=(
                        ("gap_frames", str(primary[0].start_frame)),
                        ("next_clip", primary[0].clip_id),
                    ),
                )
            )
        tail = settings.duration_frames - primary[-1].end_frame
        if tail > 1:
            issues.append(
                make_issue(
                    timeline,
                    "structure.gap",
                    f"The main picture ends {tail} frames before the end of the timeline.",
                    start_frame=primary[-1].end_frame,
                    end_frame=settings.duration_frames,
                    clips=(primary[-1],),
                    key="tail",
                    measurements=(
                        ("gap_frames", str(tail)),
                        ("previous_clip", primary[-1].clip_id),
                    ),
                )
            )
    by_track: dict[str, list[ClipFacts]] = {}
    for clip in timeline.clips:
        if clip.enabled:
            by_track.setdefault(clip.track_id, []).append(clip)
    for track_id, clips in by_track.items():
        ordered = sorted(clips, key=lambda c: c.start_frame)
        for a, b in pairwise(ordered):
            if b.start_frame < a.end_frame:
                issues.append(
                    make_issue(
                        timeline,
                        "structure.overlap",
                        f"'{a.name}' and '{b.name}' overlap on {track_id} for "
                        f"{a.end_frame - b.start_frame} frames.",
                        start_frame=b.start_frame,
                        end_frame=min(a.end_frame, b.end_frame),
                        clips=(a, b),
                        measurements=(
                            ("overlap_frames", str(a.end_frame - b.start_frame)),
                            ("earlier_clip", a.clip_id),
                            ("later_clip", b.clip_id),
                        ),
                    )
                )
    for clip in timeline.clips:
        if clip.end_frame > settings.duration_frames:
            issues.append(
                make_issue(
                    timeline,
                    "structure.beyond_timeline",
                    f"'{clip.name}' runs {clip.end_frame - settings.duration_frames} frames past "
                    "the end of the timeline.",
                    start_frame=settings.duration_frames,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                    measurements=(
                        ("overrun_frames", str(clip.end_frame - settings.duration_frames)),
                        ("clip", clip.clip_id),
                        ("timeline_end", str(settings.duration_frames)),
                    ),
                )
            )
    if not timeline.sounds:
        issues.append(
            make_issue(timeline, "structure.no_sound", "The timeline has no sound clips.")
        )
    if settings.fps_assumed:
        issues.append(
            make_issue(
                timeline,
                "structure.frame_rate_assumed",
                f"The timeline frame rate ({float(settings.fps):g}) was assumed, not read from the "
                f"footage.",
                confidence=0.8,
            )
        )
    if settings.width % 2 or settings.height % 2:
        issues.append(
            make_issue(
                timeline,
                "structure.odd_size",
                f"The timeline is {settings.width}x{settings.height}; an even size is needed.",
            )
        )
    return issues


def check_geometry(timeline: TimelineFacts) -> list[QualityIssue]:
    issues: list[QualityIssue] = []
    stretch_ok = timeline.settings.fit_mode == "stretch"
    for clip in timeline.clips:
        if clip.kind not in ("primary", "cover"):
            continue
        ratio = clip.scale_x / clip.scale_y if clip.scale_y else 1.0
        if not stretch_ok and abs(ratio - 1.0) > _STRETCH:
            issues.append(
                make_issue(
                    timeline,
                    "structure.stretch",
                    f"'{clip.name}' is scaled {clip.scale_x:.2f} wide and {clip.scale_y:.2f} high.",
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                    evidence=(f"scale_x / scale_y = {ratio:.3f}",),
                )
            )
        if max(clip.scale_x, clip.scale_y) > _UPSCALE:
            issues.append(
                make_issue(
                    timeline,
                    "structure.heavy_upscale",
                    f"'{clip.name}' is enlarged to {max(clip.scale_x, clip.scale_y):.2f} times.",
                    confidence=0.7,
                    start_frame=clip.start_frame,
                    end_frame=clip.end_frame,
                    clips=(clip,),
                )
            )
    for transition in timeline.transitions:
        clips = tuple(
            c
            for c in (timeline.clip(i or "") for i in (transition.from_clip, transition.to_clip))
            if c
        )
        if transition.mode == "unsupported" or not transition.supported:
            issues.append(
                make_issue(
                    timeline,
                    "structure.transition_unsupported",
                    f"The '{transition.kind}' transition cannot be carried out; the cut stays "
                    f"hard.",
                    start_frame=transition.cut_frame,
                    end_frame=transition.cut_frame,
                    clips=clips,
                    key=transition.transition_id,
                )
            )
        elif not transition.handles_sufficient:
            issues.append(
                make_issue(
                    timeline,
                    "structure.handles_short",
                    f"The '{transition.kind}' transition needs more footage beyond the cut than "
                    f"exists.",
                    start_frame=transition.start_frame,
                    end_frame=transition.end_frame,
                    clips=clips,
                    key=transition.transition_id,
                )
            )
        if transition.execution == "unavailable":
            issues.append(
                make_issue(
                    timeline,
                    "structure.transition_unavailable",
                    f"The '{transition.kind}' transition could not be produced.",
                    start_frame=transition.cut_frame,
                    end_frame=transition.cut_frame,
                    clips=clips,
                    key=transition.transition_id,
                )
            )
    return issues


def carry_builder_findings(timeline: TimelineFacts) -> list[QualityIssue]:
    """What the builder already found, said once: errors, unsupported items, decisions waiting."""
    issues: list[QualityIssue] = []
    for found in timeline.builder_issues:
        code = _BUILDER.get(found.severity)
        if code is None:
            continue
        clip = timeline.clip(found.element_id or "")
        issues.append(
            make_issue(
                timeline,
                code,
                found.message,
                start_frame=found.start_frame,
                end_frame=found.end_frame,
                clips=(clip,) if clip else (),
                evidence=(f"builder finding {found.code}",),
                origin="timeline_builder",
                key=found.code + (found.element_id or ""),
            )
        )
    return issues


def compare_with_specification(timeline: TimelineFacts) -> list[QualityIssue]:
    """The specification said what to build; the timeline is what was built (and a person "
    "changed)."""
    if timeline.specification_items is None:
        return []
    wanted = set(timeline.specification_items)
    have = {c.clip_id for c in timeline.clips} | {s.clip_id for s in timeline.sounds}
    have |= {t.transition_id for t in timeline.transitions}
    deleted = set(timeline.deleted_by_human)
    issues = [
        make_issue(
            timeline,
            "structure.element_missing",
            f"Element {item} of the specification is not on the timeline.",
            confidence=0.8,
            evidence=("not found among clips, sounds or transitions",),
            key=item,
        )
        for item in sorted(wanted - have - deleted)
    ]
    human = {c.clip_id for c in timeline.clips if c.human}
    unexpected = sorted(i for i in have - wanted if i not in human)
    issues += [
        make_issue(
            timeline,
            "structure.element_unexpected",
            f"Element {item} is on the timeline but not in the specification.",
            confidence=0.6,
            key=item,
        )
        for item in unexpected
    ]
    return issues


def check_resolve(timeline: TimelineFacts, resolve: ResolveFacts | None) -> list[QualityIssue]:
    if resolve is None or not resolve.bound:
        return []
    issues: list[QualityIssue] = []
    if not resolve.bound_hash_matches:
        issues.append(
            make_issue(
                timeline,
                "resolve.stale_version",
                f"Resolve was last synchronised to build version {resolve.bound_version}; the "
                f"timeline is at version {timeline.version}.",
                evidence=("the binding's build hash differs from the timeline's",),
            )
        )
    for conflict in resolve.open_conflicts:
        clip = timeline.clip(conflict)
        issues.append(
            make_issue(
                timeline,
                "resolve.conflict_open",
                f"Both Media House and Resolve changed {conflict}; nobody has decided.",
                clips=(clip,) if clip else (),
                key=conflict,
            )
        )
    if resolve.unverified_elements:
        issues.append(
            make_issue(
                timeline,
                "resolve.unverified",
                f"{resolve.unverified_elements} clips could not be read back from Resolve.",
                confidence=0.6,
            )
        )
    if resolve.live_changes:
        issues.append(
            make_issue(
                timeline,
                "resolve.manual_changes",
                f"{resolve.live_changes} changes made in Resolve are not in the built timeline "
                f"yet.",
                confidence=0.9,
                evidence=(resolve.live_note,) if resolve.live_note else (),
            )
        )
    return issues


def run_structure_checks(
    timeline: TimelineFacts, resolve: ResolveFacts | None, _profile: QualityProfile
) -> list[QualityIssue]:
    return [
        *check_media(timeline),
        *check_layout(timeline),
        *check_geometry(timeline),
        *carry_builder_findings(timeline),
        *compare_with_specification(timeline),
        *check_resolve(timeline, resolve),
    ]
