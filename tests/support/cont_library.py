"""A library for continuity integration tests: real assets, synthetic video analyses with tracks."""

from dataclasses import replace
from fractions import Fraction
from pathlib import Path
from typing import Any

from media_house.bootstrap.application import Application
from media_house.modules.continuity_intelligence.application.contracts import (
    ProposedClip,
    ProposedSequence,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.video_intelligence.application.contracts import (
    Evidence,
    VideoAnalysis,
    analysis_to_json,
)
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.observations import (
    EntitiesObservation,
    Track,
    TrackPoint,
)
from media_house.modules.video_intelligence.domain.values import (
    RESULT_OPERATION,
    AnalyzerState,
    EntityKind,
)
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.support import vi_signals as vs
from tests.support.media_factories import make_video


def import_clip(app: Application, folder: Path, name: str, seconds: int = 5) -> MediaAssetDto:
    folder.mkdir(parents=True, exist_ok=True)
    imported = app.container.resolve(MediaLibrary).import_file(
        make_video(folder / f"{name}.mp4", seconds=seconds), display_name=name
    )
    assert isinstance(imported, Ok), imported
    return imported.value.asset


def walking_analysis(
    asset: MediaAssetDto, speeds: tuple[float, float] = (0.2, -0.2), cluster: str = "person_A"
) -> VideoAnalysis:
    """Two shots (0-2 s, 2-4 s); a person walks through each at ``speeds`` picture units/second."""
    base = vs.analysis(vs.shot_signals(120, cuts=(60,)))
    tracks: list[Track] = []
    shots = list(base.shots)
    for index, speed in enumerate(speeds):
        shot = shots[index]
        start = shot.range.start
        timebase = start.timebase

        def at(seconds: float, start: Any = start, timebase: Any = timebase) -> Any:
            return replace(start, frame=round(seconds * 30), pts=round(seconds / timebase.value))

        begin = index * 2.0
        points = tuple(
            TrackPoint(
                time=at(begin + t / 10),
                box=BBox(
                    min(0.6, max(0.1, 0.35 + speed * (t / 10 - 1.0))),
                    0.2,
                    min(0.9, max(0.3, 0.55 + speed * (t / 10 - 1.0))),
                    0.8,
                ),
                confidence=0.9,
            )
            for t in range(0, 20)
        )
        track = Track(
            state=AnalyzerState.OK,
            confidence=0.9,
            evidence=(Evidence(frames=(points[0].time,), metric="track"),),
            track_id=f"t{index}",
            kind=EntityKind.PERSON,
            label="person",
            shot_id=shot.shot_id,
            first=points[0].time,
            last=points[-1].time,
            points=points,
            occluded_frames=0,
            identity_cluster=cluster,
        )
        tracks.append(track)
        shots[index] = replace(
            shot,
            scene_id="sc0",
            entities=EntitiesObservation(
                state=AnalyzerState.OK,
                confidence=0.9,
                evidence=(Evidence(frames=(points[0].time,), metric="track"),),
                track_ids=(track.track_id,),
                main_subject_track_id=track.track_id,
                analysed_frames=20,
            ),
        )
    return replace(
        base,
        asset_id=asset.id,
        asset_checksum=asset.checksum,
        shots=tuple(shots),
        tracks=tuple(tracks),
    )


def store_analysis(app: Application, asset: MediaAssetDto, analysis: VideoAnalysis) -> None:
    library = app.container.resolve(MediaLibrary)
    stored = DerivedDocuments(library, app.container.resolve(AppPaths)).store(
        asset,
        analysis_to_json(analysis),
        filename="analysis.json",
        spec=DocumentSpec(RESULT_OPERATION, 1, {"case": "continuity", "asset": asset.id}),
        display_name="video analysis",
        metadata={},
    )
    assert isinstance(stored, Ok), stored


def clip(
    clip_id: str,
    asset: str,
    start: float,
    end: float,
    at: float,
    *,
    section: str | None = "sec1",
    layer: int = 0,
) -> ProposedClip:
    def f(x: float) -> Fraction:
        return Fraction(x).limit_denominator(1000)

    return ProposedClip(
        clip_id=clip_id,
        asset_id=asset,
        source_start=f(start),
        source_end=f(end),
        timeline_start=f(at),
        timeline_end=f(at + (end - start)),
        section_id=section,
        layer=layer,
    )


def sequence(*clips: ProposedClip, plan: str = "ep1", version: int = 1) -> ProposedSequence:
    return ProposedSequence(edit_plan_id=plan, edit_plan_version=version, clips=tuple(clips))
