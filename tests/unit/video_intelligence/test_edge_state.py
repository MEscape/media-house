"""edge_state: what the frame holds at one second, from the stored analysis only."""

from dataclasses import replace

import pytest

from media_house.core.domain import FrameTime
from media_house.modules.video_intelligence.domain.edges import edge_state
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.observations import (
    Evidence,
    Track,
    TrackPoint,
)
from media_house.modules.video_intelligence.domain.result import VideoAnalysis
from media_house.modules.video_intelligence.domain.values import AnalyzerState, EntityKind
from tests.support.vi_signals import analysis, shot_signals


def at(analysis_: VideoAnalysis, seconds: float) -> FrameTime:
    base = analysis_.shots[0].range.start
    return replace(base, frame=round(seconds * 30), pts=round(seconds / base.timebase.value))


def moving(analysis_: VideoAnalysis, *, speed: float = 0.1) -> VideoAnalysis:
    """A person walking right at ``speed`` of the picture per second through the first shot."""
    points = tuple(
        TrackPoint(
            time=at(analysis_, t / 10),
            box=BBox(0.1 + speed * t / 10, 0.2, 0.3 + speed * t / 10, 0.8),
            confidence=0.9,
        )
        for t in range(0, 21)
    )
    track = Track(
        state=AnalyzerState.OK,
        confidence=0.9,
        evidence=(Evidence(frames=(points[0].time,), metric="track"),),
        track_id="t1",
        kind=EntityKind.PERSON,
        label="person",
        shot_id=analysis_.shots[0].shot_id,
        first=points[0].time,
        last=points[-1].time,
        points=points,
        occluded_frames=0,
        identity_cluster="person_A",
    )
    return replace(analysis_, tracks=(track,))


def test_the_subject_has_a_place_a_size_and_a_direction_of_travel() -> None:
    state = edge_state(moving(analysis(shot_signals(120, cuts=(90,)))), 1.0)

    assert state is not None and state.subject is not None
    assert state.subject.cx == pytest.approx(0.3, abs=0.02)
    assert state.subject.height == pytest.approx(0.6)
    assert state.subject.vx == pytest.approx(0.1, abs=0.01)
    assert state.subject.identity_cluster == "person_A"


def test_a_subject_that_is_not_there_has_no_state_not_a_guess() -> None:
    state = edge_state(moving(analysis(shot_signals(120, cuts=(90,)))), 3.0)  # second shot

    assert state is not None and state.subject is None


def test_a_moment_outside_every_shot_has_no_edge_state() -> None:
    assert edge_state(analysis(shot_signals(120)), 99.0) is None


def test_what_was_not_measured_is_none_never_zero() -> None:
    state = edge_state(analysis(shot_signals(120)), 1.0)

    assert state is not None
    assert state.head_yaw is None and state.luma is None and state.color_balance is None
    assert state.embedding == () and state.objects == ()
