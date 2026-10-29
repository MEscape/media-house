"""Planning one shot, on synthetic tracks.

Each test names a situation (not an implementation) and checks what the arithmetic must conclude:
what stays inside the window, what is recommended, what is said when it cannot be done. The
fixtures are plain boxes and times, so they prove the geometry and the decision rules and say
nothing about how a real tracker or picture behaves.
"""

from dataclasses import replace

import pytest

from media_house.modules.reframing_intelligence.domain.geometry import Box, Crop
from media_house.modules.reframing_intelligence.domain.model import crop_at
from media_house.modules.reframing_intelligence.domain.profile import SafeArea
from media_house.modules.reframing_intelligence.domain.values import (
    CameraMotion,
    Feasibility,
    Importance,
    IntervalState,
    IssueKind,
    ScoreName,
    Strategy,
)
from tests.support.refr_fakes import (
    ASSET,
    T11,
    T169,
    T916,
    ShotPlan,
    VisualNeed,
    beat,
    f,
    geom,
    plan,
    profile,
    shot,
    still,
    walk,
)

PERSON = (0.40, 0.15, 0.60, 0.90)


def score(p: ShotPlan, name: ScoreName) -> float | None:
    return next(s.value for s in p.scores if s.name is name)


def inside(p: ShotPlan, box: tuple[float, float, float, float], seconds: float) -> float:
    crop = crop_at(p.keyframes, seconds)
    return crop.inside_share(Box(*box))


def test_a_centred_talking_head_gets_one_still_crop_that_holds_the_person() -> None:
    p = plan(shot(tracks=(still("p", PERSON),)))

    assert p.strategy is Strategy.STATIC_CROP
    assert p.camera_motion is CameraMotion.NONE
    assert len(p.keyframes) == 1 and p.executable
    assert inside(p, PERSON, 2.0) == pytest.approx(1.0)
    assert score(p, ScoreName.MOTION_SMOOTHNESS) == 1.0
    assert score(p, ScoreName.EDGE_CLIPPING_RISK) == 0.0
    # 1080p to 9:16 enlarges the crop 1.78x: said, not hidden
    assert p.resolution.scale_max == pytest.approx(1.7778, abs=0.001)
    assert any(i.kind is IssueKind.UPSCALE for i in p.issues)


def test_a_speaker_near_the_source_edge_stays_inside_with_the_window_pushed_to_the_edge() -> None:
    p = plan(shot(tracks=(still("p", (0.02, 0.15, 0.20, 0.90)),)))
    crop = p.keyframes[0].crop

    assert crop.x == pytest.approx(0.0, abs=1e-6)  # cannot move further left than the picture
    assert inside(p, (0.02, 0.15, 0.20, 0.90), 1.0) == pytest.approx(1.0)
    assert (
        p.strategy is Strategy.REPOSITIONED_CROP
    )  # the subject sits off-centre, on purpose or not
    assert p.feasibility is Feasibility.TRADEOFFS
    assert any("could not be placed" in t for t in p.tradeoffs)


def test_a_moving_speaker_is_followed_smoothly_within_the_speed_limit() -> None:
    track = walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=10, step=0.25)
    p = plan(shot(end=10, tracks=(track,)), target=T916)

    assert p.strategy is Strategy.DYNAMIC_CROP and p.camera_motion is CameraMotion.FOLLOW
    assert len(p.keyframes) >= 2
    # every observed moment keeps the whole person inside the window
    for s in track.samples:
        box = s.box
        t = float(s.t)
        assert inside(p, (box.x0, box.y0, box.x1, box.y1), t) == pytest.approx(1.0)
    # the window never moves faster than the profile allows, and does not jump
    speed = max(
        abs(b.crop.x - a.crop.x) / a.crop.w / float(b.time.value - a.time.value)
        for a, b in zip(p.keyframes, p.keyframes[1:], strict=False)
    )
    assert speed <= profile().motion.max_speed * 1.05
    assert all(k.interpolation.value == "linear" for k in p.keyframes[:-1])
    assert len(p.keyframes) < len(track.samples)  # simplified: not a keyframe per observation


def test_a_steady_walker_is_followed_in_one_continuous_pan_not_in_lurches() -> None:
    track = walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=10, step=0.25)
    p = plan(shot(end=10, tracks=(track,)))

    # wait inside the dead zone, ease in, one long pan at the walker's pace, ease out
    assert len(p.keyframes) <= 6
    pairs = list(zip(p.keyframes, p.keyframes[1:], strict=False))
    a, b = max(pairs, key=lambda ab: ab[1].time.value - ab[0].time.value)  # the long pan
    window = (b.crop.center[0] - a.crop.center[0]) / a.crop.w / float(b.time.value - a.time.value)
    walker = 0.45 / 10 / p.keyframes[0].crop.w  # crop widths per second
    assert window == pytest.approx(walker, rel=0.25)


def test_tracking_jitter_is_smoothed_away_and_does_not_shake_the_window() -> None:
    clean = walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=10, step=0.1)
    wobble = [0.012 * (1 if i % 2 else -1) for i in range(len(clean.samples))]
    noisy_samples = tuple(
        replace(s, box=Box(s.box.x0 + d, s.box.y0, s.box.x1 + d, s.box.y1))
        for s, d in zip(clean.samples, wobble, strict=True)
    )
    noisy = replace(clean, samples=noisy_samples)

    steady = plan(shot(end=10, tracks=(clean,)))
    shaky = plan(shot(end=10, tracks=(noisy,)))

    assert len(shaky.keyframes) <= len(steady.keyframes) + 2
    speed = max(
        abs(b.crop.x - a.crop.x) / a.crop.w / float(b.time.value - a.time.value)
        for a, b in zip(shaky.keyframes, shaky.keyframes[1:], strict=False)
    )
    assert speed <= profile().motion.max_speed * 1.05


def test_a_speaker_who_barely_moves_gets_no_movement_at_all() -> None:
    p = plan(shot(tracks=(walk("p", (0.40, 0.2, 0.55, 0.9), (0.41, 0.2, 0.56, 0.9)),)))

    assert len(p.keyframes) == 1 and p.camera_motion is CameraMotion.NONE


def test_a_deliberately_still_profile_never_moves_the_window() -> None:
    track = walk("p", (0.10, 0.2, 0.25, 0.9), (0.45, 0.2, 0.60, 0.9), end=6)
    p = plan(shot(end=6, tracks=(track,)), prof=profile("cinematic"))

    assert len(p.keyframes) == 1  # the profile forbids a moving window; the subject still fits


def test_an_occlusion_holds_the_window_and_is_reported_not_interpolated_across() -> None:
    track = walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=6, skip=(2.0, 3.6))
    p = plan(shot(end=6, tracks=(track,)))

    gaps = [i for i in p.intervals if i.state is IntervalState.GAP]
    assert gaps and gaps[0].start.value == pytest.approx(1.8, abs=0.21)
    assert any(i.kind is IssueKind.TRACKING_GAP for i in p.issues)
    assert any("not observed" in t for t in p.tradeoffs)
    # inside the gap the window does not travel (it holds), whatever the subject might have done
    inside_gap = [k for k in p.keyframes if 2.1 < k.time.value < 3.5]
    assert all(k.crop == inside_gap[0].crop for k in inside_gap)


def test_a_product_demonstration_keeps_the_presenter_and_the_named_product_together() -> None:
    need = VisualNeed("v1", "sec1", "demonstration", True, "the laptop screen", ASSET, f(0), f(4))
    presenter = still("p", (0.10, 0.2, 0.30, 0.9))
    laptop = still("o", (0.45, 0.5, 0.65, 0.8), kind="object", label="laptop")
    p = plan(
        shot(tracks=(presenter, laptop)), needs=(need,), target=T169, geometry=geom(3840, 2160)
    )

    names = {e.label: e for e in p.elements}
    assert names["laptop"].required and names["laptop"].importance is Importance.SECONDARY
    assert "visual need v1" in names["laptop"].reason
    assert p.feasibility in (Feasibility.FEASIBLE, Feasibility.TRADEOFFS)
    # no hands are tracked upstream: the demonstration says so instead of guessing a box
    assert not any(i.kind is IssueKind.NOT_TRACKED for i in p.issues)


def test_a_demonstration_that_names_nothing_trackable_says_it_cannot_protect_it() -> None:
    need = VisualNeed("v1", "sec1", "demonstration", True, "the soldering iron", ASSET, f(0), f(4))
    p = plan(shot(tracks=(still("p", PERSON),)), needs=(need,))

    notes = [i for i in p.issues if i.kind is IssueKind.NOT_TRACKED]
    assert notes and "no hands" in notes[0].detail


def test_two_speakers_who_cannot_share_the_crop_are_never_silently_dropped() -> None:
    left, right = still("a", (0.05, 0.2, 0.22, 0.9)), still("b", (0.75, 0.2, 0.92, 0.9))
    p = plan(shot(tracks=(left, right)), target=T916)

    assert p.strategy is Strategy.UNREFRAMEABLE and p.feasibility is Feasibility.IMPOSSIBLE
    assert not p.keyframes and not p.executable
    assert any(i.kind is IssueKind.ELEMENT_OMITTED for i in p.issues)
    assert p.omitted  # names what cannot be held
    assert score(p, ScoreName.CROP_FEASIBILITY) == 0.0


def test_a_profile_that_allows_it_proposes_the_fill_treatment_not_a_crop() -> None:
    left, right = still("a", (0.05, 0.2, 0.22, 0.9)), still("b", (0.75, 0.2, 0.92, 0.9))
    p = plan(shot(tracks=(left, right)), prof=profile("interview_podcast"))

    assert p.strategy is Strategy.FILL_TREATMENT and not p.executable and not p.keyframes


def test_a_required_object_that_the_crop_would_cut_off_makes_the_shot_unreframeable() -> None:
    need = VisualNeed("v1", "sec1", "demonstration", True, "the laptop", ASSET, f(0), f(4))
    speaker = still("p", (0.05, 0.2, 0.25, 0.9))
    laptop = still("o", (0.70, 0.5, 0.90, 0.8), kind="object", label="laptop")
    p = plan(shot(tracks=(speaker, laptop)), needs=(need,), target=T916)

    assert p.strategy is Strategy.UNREFRAMEABLE
    assert any("laptop" in o for o in p.omitted)


def test_a_source_too_small_for_the_crop_is_low_quality_not_hidden() -> None:
    p = plan(shot(tracks=(still("p", PERSON),)), geometry=geom(640, 360), target=T916)

    assert p.feasibility is Feasibility.LOW_QUALITY
    assert p.resolution.scale_max == pytest.approx(5.333, abs=0.01)
    assert p.executable  # valid geometry; the editing stage decides whether to accept the softness
    assert score(p, ScoreName.EFFECTIVE_RESOLUTION) == 0.0
    assert any(i.kind is IssueKind.LOW_QUALITY for i in p.issues)


def test_enlarging_footage_with_no_quality_measurement_is_uncertain() -> None:
    p = plan(shot(usability=None, tracks=(still("p", PERSON),)), target=T916)

    assert p.feasibility is Feasibility.UNCERTAIN and not p.executable
    assert any(i.kind is IssueKind.MISSING_EVIDENCE for i in p.issues)


def test_a_source_that_already_has_the_target_shape_keeps_its_framing() -> None:
    p = plan(shot(tracks=(still("p", PERSON),)), target=T169)

    assert p.strategy is Strategy.ORIGINAL
    assert p.keyframes[0].crop == Crop(0.0, 0.0, 1.0, 1.0)
    assert p.feasibility is Feasibility.FEASIBLE and p.resolution.scale_max == pytest.approx(1.0)


def test_a_portrait_source_is_reframed_to_another_target_with_the_headroom_it_allows() -> None:
    p = plan(
        shot(tracks=(still("p", (0.3, 0.30, 0.7, 0.45)),)), target=T169, geometry=geom(1080, 1920)
    )
    crop = p.keyframes[0].crop

    assert crop.w == pytest.approx(1.0) and crop.h == pytest.approx(0.3164, abs=1e-3)
    assert (
        0.0 < crop.y < 1.0 - crop.h
    )  # there is vertical freedom: the window is placed on the head
    assert inside(p, (0.3, 0.30, 0.7, 0.45), 1.0) == pytest.approx(1.0)


def test_a_whole_person_taller_than_the_window_falls_back_to_the_face() -> None:
    body = still("p", (0.3, 0.1, 0.7, 0.6))
    face = still("fc", (0.42, 0.10, 0.58, 0.22), kind="face", label="face")
    p = plan(shot(tracks=(body, face), main="p"), target=T169, geometry=geom(1080, 1920))

    primary = next(e for e in p.elements if e.importance is Importance.PRIMARY)
    assert primary.kind == "face" and "does not fit" in primary.reason
    assert p.strategy.has_crop and p.executable


def test_a_shot_without_tracks_is_a_placeholder_that_must_not_be_applied_unseen() -> None:
    p = plan(shot(), target=T916)

    assert p.feasibility is Feasibility.UNCERTAIN and not p.executable
    assert p.confidence.value is None
    assert any(i.kind is IssueKind.MISSING_EVIDENCE for i in p.issues)
    assert score(p, ScoreName.SUBJECT_VISIBILITY) is None  # not stated, never read as good


def test_missing_tracks_in_a_source_of_the_right_shape_leave_the_framing_alone() -> None:
    p = plan(shot(), target=T169)

    assert p.strategy is Strategy.ORIGINAL and p.executable


def test_caption_space_moves_the_window_off_a_subject_where_the_picture_allows() -> None:
    areas = (SafeArea("title", 0.05, 0.0, 0.95, 0.25),)  # a title across the top of the output
    source = geom(1080, 1920)
    person = (0.4, 0.40, 0.6, 0.55)
    free = plan(shot(tracks=(still("p", person),)), target=T11, geometry=source)
    guarded = plan(
        shot(tracks=(still("p", person),)), target=T11, geometry=source, safe_areas=areas
    )

    assert score(free, ScoreName.CAPTION_SAFE_AREA) is None  # no area configured: not assumed
    assert score(guarded, ScoreName.CAPTION_SAFE_AREA) == 1.0
    # the window slid up so the subject sits below the title; the free plan left it in the title
    assert guarded.keyframes[0].crop.y < free.keyframes[0].crop.y


def test_caption_space_that_no_crop_can_give_is_reported() -> None:
    areas = (SafeArea("captions", 0.0, 0.0, 1.0, 1.0),)  # the whole picture is reserved
    p = plan(shot(tracks=(still("p", PERSON),)), safe_areas=areas)

    assert score(p, ScoreName.CAPTION_SAFE_AREA) == pytest.approx(0.0, abs=0.01)
    assert any(i.kind is IssueKind.SAFE_AREA for i in p.issues)


def test_a_push_in_needs_a_beat_the_profile_wants_emphasised() -> None:
    track = still("p", PERSON, end=8)
    quiet = plan(shot(end=8, tracks=(track,)), prof=profile("talking_head"), target=T169,
                 geometry=geom(3840, 2160), beats=(beat("b1", "explanation", 0, 8),))  # fmt: skip
    strong = plan(shot(end=8, tracks=(track,)), prof=profile("talking_head"), target=T169,
                  geometry=geom(3840, 2160), beats=(beat("b2", "claim", 0, 8),))  # fmt: skip

    assert quiet.strategy is Strategy.ORIGINAL  # nothing earns movement
    assert strong.strategy in (Strategy.PUSH_IN, Strategy.DYNAMIC_CROP)
    assert strong.beat_ids == ("b2",) and any("b2" in r for r in strong.reasons)
    first, last = strong.keyframes[0].crop, strong.keyframes[-1].crop
    assert last.w < first.w  # tighter at the end
    assert inside(strong, PERSON, 8.0) == pytest.approx(1.0)  # the push-in never loses the person


def test_a_push_in_is_dropped_when_the_footage_cannot_take_the_enlargement() -> None:
    track = still("p", PERSON, end=8)
    p = plan(shot(end=8, usability=0.2, tracks=(track,)), prof=profile("talking_head"),
             target=T169, beats=(beat("b2", "claim", 0, 8),))  # fmt: skip

    assert p.strategy is Strategy.ORIGINAL  # 1080p, soft footage: no tighter window


def test_a_low_confidence_track_is_not_trusted_for_placement() -> None:
    weak = still("p", PERSON, confidence=0.1)
    p = plan(shot(tracks=(weak,)), target=T916)

    assert not p.elements  # nothing trusted was observed
    assert p.feasibility is Feasibility.UNCERTAIN


def test_the_same_facts_give_the_same_plan() -> None:
    track = walk("p", (0.10, 0.2, 0.25, 0.9), (0.55, 0.2, 0.70, 0.9), end=6)

    assert plan(shot(end=6, tracks=(track,))) == plan(shot(end=6, tracks=(track,)))
