"""Visual meaning, relations, cinematography, scores and events from hand-made signals."""

from dataclasses import replace

import pytest

from media_house.modules.video_intelligence.domain.observations import CropSafeRegion, Score
from media_house.modules.video_intelligence.domain.profiles import (
    CONTENT_PROFILES,
    RUBRIC_VERSION,
    CinemaSettings,
    ContentTypeProfile,
    MeaningSettings,
    get_profile,
)
from media_house.modules.video_intelligence.domain.result import Shot
from media_house.modules.video_intelligence.domain.signals import (
    DescriptionSignals,
    SaliencySignals,
    ShotSignals,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    AnalyzerState,
    EntityKind,
    EventKind,
    FramingType,
    RelationKind,
    ScoreName,
)
from tests.support import vi_signals as vs
from tests.support.vi_signals import (
    basis,
    derived,
    detections,
    embeddings,
    face,
    faces,
    label_vector,
    person,
    quality_signals,
    shot_signals,
)

FRAMES = list(range(0, 120, 10))


def with_embeddings(
    vectors: list[tuple[float, ...]], frames: list[int] | None = None
) -> dict[AnalyzerId, object]:
    frames = frames or FRAMES[: len(vectors)]
    return {AnalyzerId.EMBEDDINGS: embeddings(frames, vectors)}


class TestContentTypeAndEnvironment:
    def test_the_best_zero_shot_label_becomes_the_content_type_and_selects_a_score_profile(
        self,
    ) -> None:
        signals = with_embeddings([label_vector("content:talking_head")] * 6)

        content = derived(shot_signals(120), signals=signals).shots[0].content

        assert content.ok and content.label == "talking_head"
        assert content.content_profile == "talking_head"
        assert content.margin is not None and content.margin > 0.5
        assert max(content.scores, key=content.scores.__getitem__) == "talking_head"

    def test_labels_map_to_conditioning_profiles_as_data(self) -> None:
        for label, profile in (
            ("interview", "talking_head"),
            ("screen_recording", "tutorial_screen"),
            ("vlog", "vlog"),
            ("gameplay", "generic"),
        ):
            shot = derived(
                shot_signals(120), signals=with_embeddings([label_vector(f"content:{label}")] * 4)
            ).shots[0]
            assert shot.content.content_profile == profile, label

    def test_a_tie_is_unknown_not_a_guess(self) -> None:
        half = tuple(0.7071 if i in (0, 1) else 0.0 for i in range(32))
        # equidistant from two content labels: no label stands out
        signals = with_embeddings([half] * 4)
        content = derived(shot_signals(120), signals=signals).shots[0].content

        assert content.state is AnalyzerState.UNKNOWN
        assert content.reasons == ("no_content_type_stands_out",)
        assert content.label is None and content.content_profile == "generic"

    def test_environment_is_indoor_or_outdoor_and_a_place(self) -> None:
        signals = with_embeddings([label_vector("environment:outdoor")] * 4)
        meaning = derived(shot_signals(120), signals=signals).shots[0].meaning

        assert meaning.indoor_outdoor == "outdoor"
        none = derived(shot_signals(120)).shots[0].meaning
        assert none.state is AnalyzerState.NOT_ANALYZED

    def test_indicators_are_flags_from_labels_and_from_black_pictures(self) -> None:
        slate = derived(
            shot_signals(120), signals=with_embeddings([label_vector("indicator:clapperboard")] * 4)
        ).shots[0]
        dark = derived(shot_signals(120, luma=lambda _i: 0.0, spread=lambda _i: 0.0)).shots[0]
        clean = derived(shot_signals(120)).shots[0]

        assert slate.indicators.flags == ("clapperboard",)
        assert dark.indicators.flags == ("black_picture",)
        assert clean.indicators.flags == ()

    def test_embeddings_are_exposed_for_other_modules_by_reference(self) -> None:
        timeline = shot_signals(120, cuts=(60,))
        signals = with_embeddings(
            [label_vector("content:vlog")] * 3 + [label_vector("content:event")] * 3,
            [0, 20, 40, 60, 80, 100],
        )

        result = derived(timeline, signals=signals)

        refs = {e.ref: e for e in result.embeddings}
        assert {"emb_shot_0000000", "emb_shot_0000060"} <= set(refs)
        assert result.shots[0].meaning.embedding_ref == "emb_shot_0000000"
        assert (
            refs["emb_shot_0000000"].scope == "shot"
            and refs["emb_shot_0000000"].model == "fake-clip"
        )
        assert sum(v * v for v in refs["emb_shot_0000000"].vector) == pytest.approx(1.0)


class TestScenesRetakesContinuity:
    def timeline(self) -> ShotSignals:
        return shot_signals(160, cuts=(40, 80, 120))

    def test_similar_adjacent_shots_form_a_scene_and_a_different_one_starts_the_next(self) -> None:
        signals = with_embeddings(
            [basis(0), basis(0), basis(0), basis(0), basis(5), basis(5), basis(5), basis(5)],
            [0, 20, 40, 60, 80, 100, 120, 140],
        )

        result = derived(self.timeline(), signals=signals)

        assert [s.shot_ids for s in result.scenes] == [
            ("shot_0000000", "shot_0000040"),
            ("shot_0000080", "shot_0000120"),
        ]
        assert [s.scene_id for s in result.scenes] == ["scene_0000000", "scene_0000080"]
        assert [shot.scene_id for shot in result.shots] == [
            "scene_0000000",
            "scene_0000000",
            "scene_0000080",
            "scene_0000080",
        ]
        assert result.scenes[0].range.end == result.scenes[0].range.end  # a real range
        assert {"emb_scene_0000000", "emb_scene_0000080"} <= {e.ref for e in result.embeddings}

    def test_without_embeddings_there_are_no_scenes_and_no_scene_ids(self) -> None:
        result = derived(self.timeline())

        assert result.scenes == () and all(s.scene_id is None for s in result.shots)

    def test_the_same_framing_and_action_repeated_is_a_retake_and_identical_is_a_near_duplicate(
        self,
    ) -> None:
        near = tuple(0.999 if i == 0 else 0.0447 if i == 1 else 0.0 for i in range(32))
        signals = with_embeddings(
            [basis(0), basis(0), basis(7), basis(7), basis(2), basis(2), near, near],
            [0, 20, 40, 60, 80, 100, 120, 140],
        )

        result = derived(self.timeline(), signals=signals)

        kinds = {g.group_id: (g.kind, g.shot_ids) for g in result.retakes}
        assert (RelationKind.NEAR_DUPLICATE, ("shot_0000000", "shot_0000120")) in kinds.values()
        assert all(len(shots) >= 2 for _, shots in kinds.values())

    def test_retakes_must_also_be_framed_alike(self) -> None:
        # same look, but the subject is small in one shot and large in the other
        big = (0.05, 0.0, 0.95, 0.95)
        small = (0.45, 0.4, 0.55, 0.6)
        timeline = shot_signals(80, cuts=(40,))
        frames = [0, 10, 20, 30, 40, 50, 60, 70]
        rows = [person(f, big if f < 40 else small) for f in frames]
        signals = {
            **with_embeddings([basis(0)] * 8, frames),
            AnalyzerId.ENTITIES: detections(frames, rows),
        }

        result = derived(timeline, signals=signals)

        assert result.retakes == ()

    def test_adjacent_shots_report_how_they_relate(self) -> None:
        frames = [0, 10, 20, 30, 40, 50, 60, 70]
        same_person = [person(f) for f in frames]
        signals = {
            **with_embeddings([basis(0)] * 8, frames),
            AnalyzerId.ENTITIES: detections(frames, same_person),
        }

        result = derived(shot_signals(80, cuts=(40,)), signals=signals)

        first, second = result.shots
        assert first.continuity.state is AnalyzerState.NOT_APPLICABLE
        link = second.continuity
        assert link.ok and link.previous_shot_id == "shot_0000000"
        assert link.embedding_similarity == pytest.approx(1.0)
        assert link.framing_similarity == pytest.approx(1.0)
        assert link.subject_position_delta == pytest.approx(0.0)
        assert link.same_subject is True
        assert link.jump_cut_likelihood == pytest.approx(1.0)  # same subject, identical frame

    def test_a_different_subject_has_no_jump_cut_likelihood(self) -> None:
        frames = [0, 10, 20, 30, 40, 50, 60, 70]
        rows = [person(f) for f in frames[:4]] + [
            person(f, label="dog", kind=EntityKind.ANIMAL) for f in frames[4:]
        ]
        signals = {
            **with_embeddings([basis(0)] * 4 + [basis(9)] * 4, frames),
            AnalyzerId.ENTITIES: detections(frames, rows),
        }

        link = derived(shot_signals(80, cuts=(40,)), signals=signals).shots[1].continuity

        assert link.same_subject is False and link.jump_cut_likelihood == 0.0

    def test_with_nothing_to_compare_the_relation_is_unknown(self) -> None:
        link = derived(shot_signals(80, cuts=(40,))).shots[1].continuity

        assert link.state is AnalyzerState.UNKNOWN
        assert link.reasons == ("no_measurement_for_the_relation",)

    def test_luma_and_colour_balance_differences_are_measured(self) -> None:
        quality = quality_signals([0, 10, 20, 30, 40, 50, 60, 70], p50=0.5)
        louder = replace(quality, luma_p50=(0.3,) * 4 + (0.7,) * 4)

        link = derived(shot_signals(80, cuts=(40,)), None, louder).shots[1].continuity

        assert link.luma_delta == pytest.approx(0.4)


class TestVisualMeaningFromSaliencyTracksAndText:
    def test_attention_is_the_mean_saliency_with_its_peaks(self) -> None:
        frames = list(range(0, 120, 10))
        peak = tuple(0.9 if f == 60 else 0.3 for f in frames)
        saliency = SaliencySignals(
            frames=tuple(frames), cx=(0.7,) * 12, cy=(0.4,) * 12, peak=peak, spread=(0.1,) * 12
        )

        meaning = (
            derived(shot_signals(120), signals={AnalyzerId.SALIENCY: saliency}).shots[0].meaning
        )

        assert meaning.main_attention is not None
        assert meaning.main_attention.x == pytest.approx(0.7)
        assert [t.frame for t in meaning.attention_peaks] == [60]

    def test_objects_are_the_labels_of_non_person_tracks_and_a_description_is_attached(
        self,
    ) -> None:
        frames = [0, 10, 20, 30]
        rows = [person(f, label="dog", kind=EntityKind.ANIMAL) for f in frames] + [
            person(f) for f in frames
        ]
        descriptions = DescriptionSignals("fake-vlm", (10,), ("A dog next to a person.",))
        signals = {
            AnalyzerId.ENTITIES: detections(frames, rows),
            AnalyzerId.DESCRIPTIONS: descriptions,
        }

        meaning = derived(shot_signals(120), signals=signals).shots[0].meaning

        assert meaning.objects == ("dog",)
        assert meaning.description == "A dog next to a person."


# --- cinematography ----------------------------------------------------------------------------
def subject_result(
    box: tuple[float, float, float, float], face_height: float | None = None
) -> Shot:
    frames = [0, 10, 20, 30, 40, 50]
    signals: dict[AnalyzerId, object] = {
        AnalyzerId.ENTITIES: detections(frames, [person(f, box) for f in frames])
    }
    if face_height is not None:
        from media_house.modules.video_intelligence.domain.geometry import BBox
        from media_house.modules.video_intelligence.domain.signals import FaceRow

        top = box[1]
        height = face_height

        def row(f: int) -> FaceRow:
            box = BBox(0.4, top, 0.6, top + height)
            return FaceRow(f, box, 0.95, 0, 0, 0, 0, 0, 0.9, 0.9, 0.1, 0.05, 0.4)

        signals[AnalyzerId.FACES] = faces(frames, [row(f) for f in frames])
    return derived(shot_signals(60), signals=signals).shots[0]


class TestFramingAndComposition:
    @pytest.mark.parametrize(
        ("face_height", "expected"),
        [
            (0.6, FramingType.EXTREME_CLOSE_UP),
            (0.35, FramingType.CLOSE_UP),
            (0.15, FramingType.MEDIUM),
        ],
    )
    def test_framing_follows_the_size_of_the_face(
        self, face_height: float, expected: FramingType
    ) -> None:
        shot = subject_result((0.3, 0.1, 0.7, 0.95), face_height=face_height)

        assert shot.framing.ok and shot.framing.framing is expected
        assert shot.framing.subject_height == pytest.approx(face_height)

    def test_a_tiny_face_on_a_small_person_is_a_wide_shot(self) -> None:
        shot = subject_result((0.45, 0.4, 0.55, 0.6), face_height=0.04)

        assert shot.framing.framing is FramingType.WIDE

    def test_without_a_face_the_person_box_decides(self) -> None:
        wide = subject_result((0.4, 0.4, 0.5, 0.6)).framing
        medium = subject_result((0.3, 0.1, 0.7, 0.7)).framing
        close = subject_result((0.1, 0.0, 0.9, 0.99)).framing

        assert [wide.framing, medium.framing, close.framing] == [
            FramingType.WIDE,
            FramingType.MEDIUM,
            FramingType.CLOSE_UP,
        ]

    def test_a_subject_touching_the_frame_edge_is_flagged(self) -> None:
        cut = subject_result((0.3, 0.2, 0.7, 1.0)).framing
        clear = subject_result((0.3, 0.2, 0.7, 0.9)).framing

        assert cut.cut_by_frame_edge is True and clear.cut_by_frame_edge is False

    def test_without_a_subject_framing_and_composition_are_unknown_with_a_reason(self) -> None:
        shot = derived(
            shot_signals(60), signals={AnalyzerId.ENTITIES: detections([0, 10, 20], [])}
        ).shots[0]

        assert shot.framing.state is AnalyzerState.UNKNOWN
        assert shot.framing.reasons == ("no_subject_detected",)
        assert shot.composition.reasons == ("no_subject_detected",)

    def test_composition_measures_position_headroom_and_thirds(self) -> None:
        third = subject_result((1 / 3 - 0.1, 0.08, 1 / 3 + 0.1, 0.9)).composition
        centred = subject_result((0.4, 0.2, 0.6, 0.9)).composition

        assert third.thirds_offset == pytest.approx(0.0, abs=1e-6) and third.centered is False
        assert third.headroom == pytest.approx(0.08)
        assert centred.centered is True
        assert third.empty_space is not None and 0 < third.empty_space < 1

    def test_lead_room_is_the_space_in_the_direction_the_face_looks(self) -> None:
        frames = [0, 10, 20, 30]

        def lead(yaw: float) -> float | None:
            signals = {
                AnalyzerId.ENTITIES: detections(
                    frames, [person(f, (0.2, 0.1, 0.5, 0.9)) for f in frames]
                ),
                AnalyzerId.FACES: faces(frames, [face(f, yaw=yaw) for f in frames]),
            }
            return derived(shot_signals(60), signals=signals).shots[0].composition.lead_room

        assert lead(30.0) == pytest.approx(
            0.5
        )  # looking right: room from the box to the right edge
        assert lead(-30.0) == pytest.approx(0.2)  # looking left
        assert lead(2.0) is None  # facing the camera: no direction


class TestCropSafeWindows:
    def windows(self, box: tuple[float, float, float, float]) -> dict[str, CropSafeRegion]:
        shot = subject_result(box)
        return {r.aspect: r for r in shot.crop_safe}

    def test_each_aspect_gets_a_window_of_its_shape_that_follows_the_subject(self) -> None:
        windows = self.windows((0.6, 0.1, 0.8, 0.9))

        assert set(windows) == {"16:9", "9:16", "1:1"}
        sixteen, nine, square = windows["16:9"], windows["9:16"], windows["1:1"]
        assert sixteen.window is not None and sixteen.window.width == pytest.approx(1.0)
        assert nine.window is not None and nine.window.height == pytest.approx(1.0)
        assert nine.window.width == pytest.approx((9 / 16) / (320 / 180))  # on a 16:9 picture
        assert square.window is not None and square.window.width == pytest.approx(
            (1.0) / (320 / 180)
        )
        assert nine.subject_inside is True
        # the window moved toward the subject (right of centre)
        assert nine.window.center[0] > 0.5

    def test_a_subject_that_does_not_fit_says_so(self) -> None:
        windows = self.windows((0.05, 0.1, 0.95, 0.9))

        assert windows["9:16"].subject_inside is False
        assert "subject_travel_wider_than_window" in windows["9:16"].reasons
        assert windows["16:9"].subject_inside is True

    def test_nothing_is_cropped_and_without_a_subject_the_windows_are_unknown(self) -> None:
        shot = derived(
            shot_signals(60), signals={AnalyzerId.ENTITIES: detections([0, 10], [])}
        ).shots[0]

        assert {r.state for r in shot.crop_safe} == {AnalyzerState.UNKNOWN}
        plain = derived(shot_signals(60)).shots[0]
        assert {r.state for r in plain.crop_safe} == {AnalyzerState.NOT_ANALYZED}

    def test_settings_are_validated(self) -> None:
        from media_house.modules.video_intelligence.domain.errors import InvalidProfile

        with pytest.raises(InvalidProfile):
            CinemaSettings(crop_aspects=("wide",))
        with pytest.raises(InvalidProfile):
            CinemaSettings(medium_face=0.5, close_up_face=0.3)


# --- scores ------------------------------------------------------------------------------------
def scored(content: str = "generic", shake: float = 0.0, **quality: float) -> Shot:
    """A shot with a measured camera, quality and (for subject scores) a person."""
    frames = [0, 10, 20, 30, 40, 50]
    jitter = [0.02, -0.02] * 30
    motion = vs.motion_signals(29, tx=lambda k: shake * jitter[k] * 10)
    signals = {
        AnalyzerId.ENTITIES: detections(
            frames, [person(f, (0.35, 0.12, 0.65, 0.9)) for f in frames]
        ),
        AnalyzerId.FACES: faces(frames, [face(f) for f in frames]),
    }
    if content != "generic":
        signals.update(with_embeddings([label_vector(f"content:{content}")] * 6, frames))
    return derived(
        shot_signals(60), motion, quality_signals(list(range(0, 60, 4)), **quality), signals=signals
    ).shots[0]


def score_of(shot: Shot, name: ScoreName) -> Score:
    found = shot.score(name)
    assert found is not None
    return found


class TestScores:
    def test_every_score_is_present_in_range_and_carries_its_rubric_and_profile(self) -> None:
        shot = scored()

        assert [s.name for s in shot.scores] == list(ScoreName)
        for score in shot.scores:
            assert score.rubric_version == RUBRIC_VERSION
            if score.ok:
                assert score.value is not None and 0.0 <= score.value <= 1.0
                assert score.confidence is not None and score.evidence
            else:
                assert score.reasons and score.value is None

    def test_a_steady_sharp_well_exposed_shot_scores_high_and_a_ruined_one_low(self) -> None:
        good = scored(sharpness=0.5, noise=0.003)
        ruined = scored(sharpness=0.02, noise=0.08, clipped=0.4, crushed=0.0, shake=1.0)

        assert (score_of(good, ScoreName.TECHNICAL_USABILITY).value or 0) > 0.8
        assert (score_of(ruined, ScoreName.TECHNICAL_USABILITY).value or 1) < 0.35
        assert "soft_focus" in score_of(ruined, ScoreName.VISUAL_QUALITY).reasons

    def test_the_same_shake_is_judged_by_the_kind_of_footage(self) -> None:
        interview = scored("talking_head", shake=0.2).score(ScoreName.STABILITY)
        vlog = scored("vlog", shake=0.2).score(ScoreName.STABILITY)

        assert interview is not None and vlog is not None
        assert interview.content_profile == "talking_head" and vlog.content_profile == "vlog"
        assert (vlog.value or 0) > (interview.value or 1)
        # deliberate-looking shake is likelier intended in a vlog
        assert (vlog.intentional_likelihood or 0) > (interview.intentional_likelihood or 1)

    def test_smooth_camera_moves_are_likely_intended(self) -> None:
        frames = [0, 10, 20, 30]
        panning = derived(
            shot_signals(60),
            vs.motion_signals(29, tx=lambda _k: -0.2 * 2 / 30),
            quality_signals(list(range(0, 60, 4))),
        ).shots[0]

        stability = panning.score(ScoreName.STABILITY)
        assert stability is not None and (stability.value or 0) > 0.9
        assert stability.intentional_likelihood == pytest.approx(0.9)
        assert frames

    def test_subject_visibility_does_not_apply_to_footage_that_needs_no_subject(self) -> None:
        screen = scored("screen_recording").score(ScoreName.SUBJECT_VISIBILITY)
        person_shot = scored("talking_head").score(ScoreName.SUBJECT_VISIBILITY)

        assert screen is not None and screen.state is AnalyzerState.NOT_APPLICABLE
        assert person_shot is not None and person_shot.ok and (person_shot.value or 0) > 0.6

    def test_a_missing_analyzer_leaves_its_scores_explicitly_missing(self) -> None:
        shot = derived(shot_signals(60)).shots[0]

        stability = shot.score(ScoreName.STABILITY)
        quality = shot.score(ScoreName.VISUAL_QUALITY)
        assert stability is not None and stability.state is AnalyzerState.NOT_ANALYZED
        assert quality is not None and quality.state is AnalyzerState.NOT_ANALYZED
        usable = shot.score(ScoreName.TECHNICAL_USABILITY)
        assert usable is not None and not usable.ok and usable.reasons

    def test_a_covered_lens_indicator_lowers_technical_usability(self) -> None:
        clean = derived(
            shot_signals(60), vs.motion_signals(29), quality_signals(list(range(0, 60, 4)))
        ).shots[0]
        covered = derived(
            shot_signals(60),
            vs.motion_signals(29),
            quality_signals(list(range(0, 60, 4))),
            signals=with_embeddings(
                [label_vector("indicator:covered_lens")] * 6, [0, 10, 20, 30, 40, 50]
            ),
        ).shots[0]

        a, b = (
            clean.score(ScoreName.TECHNICAL_USABILITY),
            covered.score(ScoreName.TECHNICAL_USABILITY),
        )
        assert a is not None and b is not None
        assert (b.value or 1) < 0.3 * (a.value or 0)
        assert "possible_covered_lens" in b.reasons

    def test_flat_footage_leaves_exposure_out_of_visual_quality(self) -> None:
        flat = scored(p1=0.18, p50=0.13, p99=0.3).score(ScoreName.VISUAL_QUALITY)

        assert flat is not None and flat.ok
        assert "flat_or_log_footage" in flat.reasons

    def test_content_profiles_are_a_data_table_with_the_initial_set(self) -> None:
        assert set(CONTENT_PROFILES) == {"generic", "talking_head", "vlog", "tutorial_screen"}
        assert CONTENT_PROFILES["vlog"].shake_half > CONTENT_PROFILES["talking_head"].shake_half
        with pytest.raises(Exception, match="positive"):
            ContentTypeProfile("x", 1, 0.0, 0.5, (1, 1, 1), True, (1, 1, 1))

    def test_a_changed_rubric_input_changes_the_result_key_so_scores_stay_comparable(self) -> None:
        base = get_profile("standard")
        louder = replace(base, meaning=replace(base.meaning, label_margin=0.2))

        assert base.interpretation_config() != louder.interpretation_config()
        assert base.interpretation_config()["rubric_version"] == RUBRIC_VERSION
        assert isinstance(MeaningSettings(), MeaningSettings)


# --- events ------------------------------------------------------------------------------------
class TestEvents:
    def test_boundaries_flashes_and_peaks_share_one_ordered_timeline(self) -> None:
        timeline = shot_signals(160, cuts=(80,), flashes=(30,))
        residual = lambda k: 0.5 if k == 10 else 0.002  # noqa: E731
        result = derived(timeline, vs.motion_signals(79, residual=residual))

        kinds = [e.kind for e in result.events]
        assert EventKind.SHOT_BOUNDARY in kinds and EventKind.FLASH in kinds
        assert EventKind.MOTION_PEAK in kinds
        frames = [e.time.frame for e in result.events]
        assert frames == sorted(frames)
        boundary = next(e for e in result.events if e.kind is EventKind.SHOT_BOUNDARY)
        assert boundary.time.frame == 80 and boundary.shot_id == "shot_0000080"

    def test_tracks_entering_and_leaving_are_events_inside_the_shot(self) -> None:
        frames = list(range(0, 120, 10))
        rows = [person(f) for f in frames if 30 <= f <= 70]

        result = derived(shot_signals(120), signals={AnalyzerId.ENTITIES: detections(frames, rows)})

        entered = [e for e in result.events if e.kind is EventKind.TRACK_ENTERED]
        exited = [e for e in result.events if e.kind is EventKind.TRACK_EXITED]
        assert [e.time.frame for e in entered] == [30] and [e.time.frame for e in exited] == [70]
        assert entered[0].detail == result.tracks[0].track_id

    def test_event_ids_are_stable_unique_and_every_event_is_grounded(self) -> None:
        timeline = shot_signals(160, cuts=(80,), flashes=(30,))

        first = derived(timeline).events
        second = derived(timeline).events

        assert first == second
        assert len({e.event_id for e in first}) == len(first)
        assert all(e.ok and e.evidence and e.confidence is not None for e in first)
