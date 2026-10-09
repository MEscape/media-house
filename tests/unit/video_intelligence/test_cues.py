"""Face, body and on-screen text cues derived from hand-made signals."""

import math

import pytest

from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.signals import (
    BodySignals,
    GeometrySignals,
    HandRow,
    PoseRow,
    TextRow,
    TextSignals,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    AnalyzerState,
    GestureKind,
    OverlayKind,
)
from tests.support.vi_signals import derived, face, faces, shot_signals

FRAMES = list(range(0, 120, 10))


def faces_of(**kw: float):  # type: ignore[no-untyped-def]
    return (
        derived(
            shot_signals(120),
            signals={AnalyzerId.FACES: faces(FRAMES, [face(f, **kw) for f in FRAMES])},
        )
        .shots[0]
        .faces
    )


class TestFaceCues:
    def test_a_face_looking_into_the_camera_has_eye_contact(self) -> None:
        cues = faces_of()

        assert cues.ok and cues.face_frames == len(FRAMES)
        assert cues.eye_contact == pytest.approx(1.0)
        assert cues.eyes_closed == 0 and cues.smile_cue == 0

    def test_a_turned_head_or_a_gaze_to_the_side_is_no_eye_contact(self) -> None:
        assert faces_of(yaw=40.0).eye_contact == 0.0
        assert faces_of(gaze_x=0.8).eye_contact == 0.0
        assert faces_of(pitch=-35.0).eye_contact == 0.0

    def test_closed_eyes_and_smiles_are_cues_with_shares(self) -> None:
        assert faces_of(eye_open_left=0.1, eye_open_right=0.1).eyes_closed == pytest.approx(1.0)
        assert faces_of(eye_open_left=0.1, eye_open_right=0.9).eyes_closed == 0.0  # one eye is open
        assert faces_of(smile=0.9).smile_cue == pytest.approx(1.0)

    def test_a_low_detector_confidence_is_reported_as_a_possible_occlusion(self) -> None:
        assert faces_of(confidence=0.55).possibly_occluded == pytest.approx(1.0)
        assert faces_of().possibly_occluded == 0.0

    def test_a_moving_mouth_reads_as_visual_speaking_and_a_still_one_does_not(self) -> None:
        talking = derived(
            shot_signals(120),
            signals={
                AnalyzerId.FACES: faces(
                    FRAMES, [face(f, mouth_open=0.05 + 0.3 * (i % 2)) for i, f in enumerate(FRAMES)]
                )
            },
        )
        still = faces_of(mouth_open=0.05)

        assert talking.shots[0].faces.visual_speaking is not None
        assert talking.shots[0].faces.visual_speaking > 0.8
        assert still.visual_speaking == pytest.approx(0.0)

    def test_the_mouth_curves_are_in_the_result(self) -> None:
        result = derived(
            shot_signals(120),
            signals={
                AnalyzerId.FACES: faces(
                    FRAMES, [face(f, mouth_open=0.1 * (i % 3)) for i, f in enumerate(FRAMES)]
                )
            },
        )

        assert {c.name for c in result.curves} >= {"mouth_activity", "visual_speaking"}
        mouth = next(c for c in result.curves if c.name == "mouth_activity")
        assert mouth.source is AnalyzerId.FACES and list(mouth.frames) == FRAMES

    def test_tiny_faces_are_not_used(self) -> None:
        from media_house.modules.video_intelligence.domain.signals import FaceRow

        small = FaceRow(0, BBox(0.5, 0.5, 0.52, 0.52), 0.9, 0, 0, 0, 0, 0, 1, 1, 0, 0, 0.5)
        result = (
            derived(shot_signals(120), signals={AnalyzerId.FACES: faces([0], [small])})
            .shots[0]
            .faces
        )

        assert result.ok and result.reasons == ("no_usable_face_found",)
        assert result.face_frames == 0

    def test_a_shot_without_a_face_is_ok_with_a_reason_and_without_analysed_frames_it_is_not(
        self,
    ) -> None:
        none_found = derived(
            shot_signals(120, cuts=(60,)), signals={AnalyzerId.FACES: faces([10, 20], [])}
        )

        assert none_found.shots[0].faces.reasons == ("no_usable_face_found",)
        assert none_found.shots[1].faces.state is AnalyzerState.NOT_ANALYZED
        assert none_found.shots[1].faces.reasons == ("no_analysed_frame_in_shot",)

    def test_without_the_analyzer_the_section_says_it_did_not_run(self) -> None:
        shot = derived(shot_signals(120)).shots[0]

        assert shot.faces.state is AnalyzerState.NOT_ANALYZED
        assert shot.faces.reasons == ("faces_analyzer_not_run",)


# --- body --------------------------------------------------------------------------------------
def hand(frame: int, extended: tuple[bool, bool, bool, bool]) -> HandRow:
    """A hand whose wrist is the origin; finger tips lie beyond (extended) or inside their pips."""
    landmarks = [0.0] * 42
    for finger, (tip, pip) in enumerate(((8, 6), (12, 10), (16, 14), (20, 18))):
        angle = math.radians(60 + 20 * finger)
        for index, radius in ((pip, 0.1), (tip, 0.22 if extended[finger] else 0.06)):
            landmarks[2 * index] = 0.5 + radius * math.cos(angle)
            landmarks[2 * index + 1] = 0.5 - radius * math.sin(angle)
    landmarks[0], landmarks[1] = 0.5, 0.5
    return HandRow(frame, "right", BBox(0.3, 0.1, 0.7, 0.7), tuple(landmarks))


def pose(frame: int, wrist_y: float = 0.9) -> PoseRow:
    joints = [0.5] * 66
    joints[2 * 11 + 1] = joints[2 * 12 + 1] = 0.4  # shoulders
    joints[2 * 15 + 1] = joints[2 * 16 + 1] = wrist_y
    return PoseRow(frame, BBox(0.2, 0.1, 0.8, 0.95), 0.9, tuple(joints))


def body(
    poses: list[PoseRow], hands: list[HandRow], frames: list[int] | None = None
) -> BodySignals:
    return BodySignals("fake", tuple(frames or FRAMES), tuple(poses), tuple(hands))


class TestBodyCues:
    def result(self, signals: BodySignals):  # type: ignore[no-untyped-def]
        return derived(shot_signals(120), signals={AnalyzerId.BODY: signals}).shots[0].body

    def test_hand_shapes_become_gestures_over_consecutive_frames(self) -> None:
        shapes = {
            GestureKind.OPEN_HAND: (True, True, True, True),
            GestureKind.FIST: (False, False, False, False),
            GestureKind.POINTING: (True, False, False, False),
        }
        for kind, fingers in shapes.items():
            observed = self.result(body([], [hand(f, fingers) for f in FRAMES[:4]]))

            assert [g.kind for g in observed.gestures] == [kind], kind
            assert observed.gestures[0].first.frame == 0 and observed.gestures[0].last.frame == 30

    def test_a_shape_seen_once_is_not_a_gesture(self) -> None:
        assert self.result(body([], [hand(0, (True, True, True, True))])).gestures == ()

    def test_a_wrist_above_the_shoulder_is_a_raised_hand(self) -> None:
        observed = self.result(body([pose(f, wrist_y=0.2) for f in FRAMES[:3]], []))

        assert [g.kind for g in observed.gestures] == [GestureKind.HAND_RAISED]
        assert self.result(body([pose(f) for f in FRAMES[:3]], [])).gestures == ()

    def test_pose_presence_is_a_share_of_the_analysed_frames(self) -> None:
        observed = self.result(body([pose(f) for f in FRAMES[:5]], []))

        assert observed.ok and observed.pose_fraction == pytest.approx(5 / len(FRAMES))

    def test_no_body_found_is_stated(self) -> None:
        observed = self.result(body([], []))

        assert (
            observed.ok and observed.reasons == ("no_body_found",) and observed.pose_fraction == 0
        )


# --- text and overlays -------------------------------------------------------------------------
def line(
    frame: int, text: str, box: tuple[float, float, float, float], conf: float = 0.9
) -> TextRow:
    return TextRow(frame, text, conf, BBox(*box))


def text_result(rows: list[TextRow], frames: list[int] | None = None, **kw):  # type: ignore[no-untyped-def]
    signals = TextSignals("fake", tuple(frames or FRAMES), tuple(rows))
    return derived(shot_signals(120, **kw), signals={AnalyzerId.TEXT: signals})


class TestOnScreenText:
    def test_text_seen_over_consecutive_frames_is_one_item_with_its_time_span(self) -> None:
        rows = [line(f, "Hello world", (0.3, 0.4, 0.7, 0.5)) for f in FRAMES[:4]]

        observed = text_result(rows).shots[0].text

        assert observed.ok and len(observed.items) == 1
        item = observed.items[0]
        assert (item.text, item.first.frame, item.last.frame) == ("Hello world", 0, 30)

    def test_low_confidence_lines_are_ignored(self) -> None:
        assert (
            text_result([line(0, "noise", (0.1, 0.1, 0.3, 0.2), conf=0.2)]).shots[0].text.items
            == ()
        )

    def test_text_that_changes_is_separate_items_and_a_shot_without_text_says_so(self) -> None:
        rows = [line(0, "one", (0.1, 0.1, 0.3, 0.2)), line(10, "two", (0.1, 0.1, 0.3, 0.2))]

        observed = text_result(rows).shots[0].text

        assert [i.text for i in observed.items] == ["one", "two"]
        empty = text_result([]).shots[0].text
        assert empty.ok and empty.reasons == ("no_text_found",)

    def test_a_screen_full_of_symbols_reads_as_code_and_of_sentences_as_a_document(self) -> None:
        code_rows = [
            line(
                0,
                f"def f{i}(x): return x[{i}] == {{}};",
                (0.1, 0.1 + 0.07 * i, 0.9, 0.16 + 0.07 * i),
            )
            for i in range(6)
        ]
        prose = [
            line(
                0,
                f"this is a normal sentence number {i}",
                (0.1, 0.05 + 0.1 * i, 0.9, 0.1 + 0.1 * i),
            )
            for i in range(9)
        ]

        assert text_result(code_rows).shots[0].text.screen_kind == "code"
        assert text_result(prose).shots[0].text.screen_kind == "document"
        assert text_result([line(0, "hi", (0.1, 0.1, 0.2, 0.2))]).shots[0].text.screen_kind is None

    def test_the_same_text_in_the_same_place_through_the_video_is_a_watermark(self) -> None:
        rows = [line(f, "@brand", (0.8, 0.9, 0.95, 0.97)) for f in FRAMES]

        overlays = text_result(rows).overlays

        assert [o.kind for o in overlays] == [OverlayKind.WATERMARK]
        assert overlays[0].text == "@brand" and overlays[0].first.frame == 0

    def test_short_lived_text_low_in_the_picture_is_a_lower_third(self) -> None:
        rows = [line(f, "Jane Doe, Reporter", (0.05, 0.75, 0.5, 0.85)) for f in FRAMES[2:5]]

        overlays = text_result(rows).overlays

        assert [o.kind for o in overlays] == [OverlayKind.LOWER_THIRD]

    def test_different_text_in_the_bottom_band_one_after_another_is_a_burned_in_caption(
        self,
    ) -> None:
        rows = [line(f, f"caption number {f}", (0.2, 0.8, 0.8, 0.9)) for f in FRAMES[:6]]

        overlays = text_result(rows).overlays

        assert [o.kind for o in overlays] == [OverlayKind.BURNED_IN_CAPTION]

    def test_text_in_the_middle_of_the_picture_is_no_overlay(self) -> None:
        rows = [line(f, "a title", (0.2, 0.3, 0.8, 0.45)) for f in FRAMES[:3]]

        assert text_result(rows).overlays == ()

    def test_text_never_crosses_a_cut(self) -> None:
        rows = [line(f, "same words", (0.2, 0.3, 0.8, 0.45)) for f in FRAMES]

        result = text_result(rows, cuts=(60,))

        assert [len(s.text.items) for s in result.shots] == [1, 1]
        assert (
            result.shots[0].text.items[0].last.frame
            < 60
            <= result.shots[1].text.items[0].first.frame
        )


def geometry(seams: list[float], frames: list[int]) -> GeometrySignals:
    n = len(frames)
    zeros = (0.0,) * n
    return GeometrySignals(
        frames=tuple(frames),
        horizon_tilt=zeros,
        horizon_support=zeros,
        vertical_tilt=zeros,
        vertical_support=zeros,
        mean_red=(0.5,) * n,
        mean_green=(0.5,) * n,
        mean_blue=(0.5,) * n,
        shadow_contrast=zeros,
        subject_luma=zeros,
        surround_luma=zeros,
        seam_vertical=tuple(seams),
        seam_horizontal=zeros,
        edge_outside=zeros,
        edge_inside=zeros,
    )


def test_a_seam_through_most_frames_of_a_shot_is_a_split_screen() -> None:
    result = derived(
        shot_signals(120),
        signals={AnalyzerId.GEOMETRY: geometry([0.9] * 10 + [0.0] * 2, FRAMES)},
    )

    assert [o.kind for o in result.overlays] == [OverlayKind.SPLIT_SCREEN]
    quiet = derived(shot_signals(120), signals={AnalyzerId.GEOMETRY: geometry([0.0] * 12, FRAMES)})
    assert quiet.overlays == ()
