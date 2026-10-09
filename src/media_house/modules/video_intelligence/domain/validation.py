"""The result's own contract, checked before anything is stored or handed out."""

from collections.abc import Sequence

from media_house.core.domain import FrameTime
from media_house.modules.video_intelligence.domain.observations import Assessed
from media_house.modules.video_intelligence.domain.result import Shot, VideoAnalysis
from media_house.modules.video_intelligence.domain.values import SCHEMA_VERSION


def validate_analysis(analysis: VideoAnalysis) -> tuple[str, ...]:
    """Every violation found (empty when the result is sound)."""
    findings: list[str] = []
    if analysis.schema_version != SCHEMA_VERSION:
        findings.append(f"schema version {analysis.schema_version} is not {SCHEMA_VERSION}")
    if not analysis.shots:
        findings.append("a video has at least one shot")
        return tuple(findings)

    ids = [s.shot_id for s in analysis.shots]
    if len(set(ids)) != len(ids):
        findings.append("shot ids are not unique")
    if analysis.shots[0].range.start.frame != 0:
        findings.append("the first shot does not start at frame 0")
    for before, after in zip(analysis.shots, analysis.shots[1:], strict=False):
        if before.range.end != after.range.start:
            findings.append(f"{before.shot_id} does not end where {after.shot_id} starts")
    frame_count = analysis.shots[-1].range.end.frame
    for shot in analysis.shots:
        if shot.range.frames < 1:
            findings.append(f"{shot.shot_id} has no frames")
        findings.extend(_check_shot(shot, frame_count))
    for curve in analysis.curves:
        if any(b < a for a, b in zip(curve.frames, curve.frames[1:], strict=False)):
            findings.append(f"curve {curve.name} runs backwards")
        if curve.frames and curve.frames[-1] >= frame_count:
            findings.append(f"curve {curve.name} reaches past the last frame")
    names = [a.analyzer for a in analysis.analyzers]
    if len(set(names)) != len(names):
        findings.append("an analyzer is reported twice")
    findings.extend(_check_collections(analysis, set(ids), frame_count))
    return tuple(findings)


def _inside(time: FrameTime, frame_count: int) -> bool:
    return 0 <= time.frame <= frame_count


def _check_sections(owner: str, sections: Sequence[Assessed], frame_count: int) -> list[str]:
    return [
        f"{owner} cites a frame outside the video"
        for section in sections
        for evidence in section.evidence
        for time in evidence.frames
        if not _inside(time, frame_count)
    ]


def _check_shot(shot: Shot, frame_count: int) -> list[str]:
    sections: list[Assessed] = [
        shot.boundary_in,
        shot.boundary_out,
        shot.handles,
        shot.keyframes,
        shot.camera,
        shot.motion,
        shot.quality,
        shot.entities,
        shot.faces,
        shot.body,
        shot.text,
        shot.content,
        shot.meaning,
        shot.continuity,
        shot.indicators,
        shot.framing,
        shot.composition,
        shot.lighting,
        shot.geometry,
        *shot.crop_safe,
        *shot.scores,
    ]
    findings = _check_sections(shot.shot_id, sections, frame_count)
    for score in shot.scores:
        if score.ok and (score.value is None or not 0.0 <= score.value <= 1.0):
            findings.append(f"{shot.shot_id} score {score.name.value} is outside [0, 1]")
    return findings


def _check_collections(analysis: VideoAnalysis, shot_ids: set[str], frame_count: int) -> list[str]:
    findings: list[str] = []
    track_ids = [t.track_id for t in analysis.tracks]
    if len(set(track_ids)) != len(track_ids):
        findings.append("track ids are not unique")
    for track in analysis.tracks:
        if track.shot_id not in shot_ids:
            findings.append(f"{track.track_id} belongs to an unknown shot")
        if any(
            b.time.pts < a.time.pts for a, b in zip(track.points, track.points[1:], strict=False)
        ):
            findings.append(f"{track.track_id} runs backwards")
    for cluster in analysis.identities:
        if not set(cluster.track_ids) <= set(track_ids):
            findings.append(f"{cluster.cluster_id} names an unknown track")
    scene_shots = [sid for scene in analysis.scenes for sid in scene.shot_ids]
    if analysis.scenes and sorted(scene_shots) != sorted(shot_ids):
        findings.append("scenes do not contain every shot exactly once")
    for group in analysis.retakes:
        if not set(group.shot_ids) <= shot_ids:
            findings.append(f"{group.group_id} names an unknown shot")
    event_ids = [e.event_id for e in analysis.events]
    if len(set(event_ids)) != len(event_ids):
        findings.append("event ids are not unique")
    if any(
        b.time.pts < a.time.pts for a, b in zip(analysis.events, analysis.events[1:], strict=False)
    ):
        findings.append("events are not in time order")
    owned: list[Assessed] = [
        *analysis.tracks,
        *analysis.identities,
        *analysis.scenes,
        *analysis.retakes,
        *analysis.overlays,
        *analysis.events,
    ]
    findings.extend(_check_sections("the result", owned, frame_count))
    refs = {e.ref for e in analysis.embeddings}
    for shot in analysis.shots:
        ref = shot.meaning.embedding_ref
        if ref is not None and ref not in refs:
            findings.append(f"{shot.shot_id} refers to a missing embedding")
    return findings
