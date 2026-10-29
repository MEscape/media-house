"""Upstream results become narrow facts: exact times, the real geometry, nothing guessed."""

from dataclasses import replace
from fractions import Fraction

import pytest

from media_house.core.domain import FrameTime, Rational
from media_house.modules.media_inspection.application.contracts import InspectionResult
from media_house.modules.media_inspection.domain.model import Geometry
from media_house.modules.media_inspection.domain.values import Sourced
from media_house.modules.reframing_intelligence.application.views import (
    continuity_facts,
    exact,
    geometry_of,
    source_facts,
    story_facts,
)
from media_house.modules.reframing_intelligence.domain.geometry import (
    Crop,
    TargetFormat,
    effective_scale,
)
from media_house.modules.video_intelligence.application.contracts import VideoAnalysis
from tests.support.asset_dto import make_asset
from tests.support.cont_fakes import jump_analysis
from tests.support.cont_library import walking_analysis
from tests.support.inspection_fakes import inspect, observed, video


def analysis(width: int | None = None, height: int | None = None) -> VideoAnalysis:
    base = walking_analysis(make_asset("a1"))
    info = replace(
        base.source, width=width or base.source.width, height=height or base.source.height
    )
    return replace(base, source=info)


def inspection_of(geometry: Geometry) -> InspectionResult:
    media = observed(videos=(video(geometry=geometry),))
    return InspectionResult(inspect(media), make_asset("insp"), created=True)


def geometry(
    width: int, height: int, sar: Rational, *, rotation: int = 0, display: Rational | None = None
) -> Geometry:
    return Geometry(
        width,
        height,
        Sourced.declared(sar),
        Sourced.declared(display or Rational(16, 9)),
        Sourced.declared(rotation),
    )


# --- time --------------------------------------------------------------------------------------
def test_a_frames_time_is_its_pts_times_the_time_base_exactly() -> None:
    ntsc = FrameTime(frame=48, pts=144144, timebase=Rational(1, 90000))

    assert exact(ntsc, Fraction(0)) == Fraction(144144, 90000)
    assert exact(ntsc, Fraction(1, 50)) == Fraction(144144, 90000) + Fraction(1, 50)


def test_the_frame_index_is_not_the_address_so_variable_frame_rate_cannot_skew_the_time() -> None:
    a = FrameTime(frame=10, pts=3003, timebase=Rational(1, 30000))
    b = FrameTime(frame=99, pts=3003, timebase=Rational(1, 30000))  # same pts, other index

    assert exact(a, Fraction(0)) == exact(b, Fraction(0)) == Fraction(3003, 30000)


def test_tracks_become_exact_samples_on_the_common_clock() -> None:
    facts = source_facts(analysis(), "a1", None, 0.0, corrected=False)

    first = facts.shots[0]
    track = first.tracks[0]
    assert (first.start, first.end) == (Fraction(0), Fraction(2))
    assert track.kind == "person" and track.cluster == "person_A"
    assert len(track.samples) == 20 and track.samples[1].t - track.samples[0].t == Fraction(1, 10)
    assert first.main_track_id == track.track_id
    assert facts.corrected is False and facts.clock_correction == 0


def test_the_alignments_video_correction_shifts_every_time_and_is_recorded() -> None:
    plain = source_facts(analysis(), "a1", None, 0.0, corrected=False)
    shifted = source_facts(analysis(), "a1", None, 0.25, corrected=True)

    assert shifted.corrected and shifted.clock_correction == Fraction(1, 4)
    assert shifted.shots[1].start == plain.shots[1].start + Fraction(1, 4)
    assert shifted.shots[0].tracks[0].samples[3].t == plain.shots[0].tracks[0].samples[
        3
    ].t + Fraction(1, 4)
    assert shifted.identity == plain.identity  # the evidence is the same; the clock is separate


# --- geometry ----------------------------------------------------------------------------------
def test_without_an_inspection_only_the_display_size_is_known_and_that_is_said() -> None:
    g = geometry_of(analysis(width=1920, height=1080), None)

    assert not g.exact
    assert (g.display_width, g.sample_width, g.sample_height) == (1920, 1920, 1080)


def test_a_stored_inspection_gives_the_stored_size_and_pixel_shape() -> None:
    anamorphic = inspection_of(geometry(1440, 1080, Rational(4, 3)))

    g = geometry_of(analysis(width=1920, height=1080), anamorphic)

    assert g.exact and (g.display_width, g.display_height) == (1920, 1080)
    assert (g.sample_width, g.sample_height) == (1440, 1080)  # fewer samples than displayed pixels
    whole = Crop(0, 0, 1, 1)
    assert effective_scale(whole, g, TargetFormat.of("16:9")) == pytest.approx(1920 / 1440)


def test_a_rotation_tag_swaps_the_stored_axes_into_display_orientation() -> None:
    portrait_file = inspection_of(geometry(1080, 1920, Rational(1, 1), rotation=90))

    g = geometry_of(analysis(width=1920, height=1080), portrait_file)

    assert g.exact and g.rotation == 90
    assert (g.sample_width, g.sample_height) == (1920, 1080)  # as displayed, not as stored


def test_an_inspection_that_describes_another_picture_is_not_trusted() -> None:
    other = inspection_of(geometry(3840, 2160, Rational(1, 1)))

    g = geometry_of(analysis(width=1920, height=1080), other)

    assert not g.exact and (g.sample_width, g.sample_height) == (1920, 1080)


# --- story and continuity ----------------------------------------------------------------------
def test_the_story_becomes_beats_and_visual_needs_on_the_recordings_clock() -> None:
    from tests.support.ci_texts import BATTERIES
    from tests.support.story_fakes import facts_for, plan_of

    plan = plan_of(facts_for(BATTERIES, "a"))

    facts = story_facts(plan)

    assert facts.story_plan_id == plan.story_plan_id and facts.story_format == plan.target_format
    assert {b.beat_id for b in facts.beats} == {b.beat_id for b in plan.beats}
    assert all(b.asset_id == "a" for b in facts.beats)
    assert len(facts.needs) == len(plan.visual_opportunities)
    assert {n.purpose for n in facts.needs} <= {
        "demonstration",
        "evidence",
        "comparison",
        "explainer_graphic",
        "illustration",
        "text_on_screen",
    }


def test_judged_cuts_become_source_moments_with_their_risk_and_whether_a_jump_cut() -> None:
    found = jump_analysis()

    facts = continuity_facts(found)

    assert len(facts.cuts) == len(found.transitions) >= 1
    cut = facts.cuts[0]
    assert cut.jump_cut and cut.risk is not None and cut.risk >= 0.5
    assert facts.analysis_id == found.continuity_analysis_id and facts.identity
