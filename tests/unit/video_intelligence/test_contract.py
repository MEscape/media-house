"""The public result: stable serialization, honest states, a query API and no recommendations."""

import ast
import dataclasses
import json
import re
import typing
from enum import Enum
from itertools import pairwise
from pathlib import Path

import pytest

from media_house.core.domain import FrameTime, Rational, TimeRange
from media_house.modules.video_intelligence.application.contracts import (
    SCHEMA_VERSION,
    AnalyzerState,
    BoundaryKind,
    CameraMovement,
    Evidence,
    InvalidAnalysisDocument,
    RankBy,
    ShotFilter,
    VideoAnalysis,
    analysis_from_json,
    analysis_to_json,
    find_shots,
    validate_analysis,
)
from media_house.modules.video_intelligence.domain import values as module_values
from media_house.modules.video_intelligence.domain.observations import (
    Assessed,
    BoundaryObservation,
    QualityMetrics,
)
from media_house.modules.video_intelligence.domain.query import RankBy as QueryRank
from media_house.modules.video_intelligence.domain.serialization import (
    document_from_json,
    document_to_json,
    signals_kind,
)
from media_house.modules.video_intelligence.domain.signals import (
    MotionSignals,
    QualitySignals,
    ShotSignals,
)
from media_house.modules.video_intelligence.domain.values import AnalyzerId
from media_house.shared.errors import InvariantViolation
from tests.support.vi_signals import (
    IMPROVED,
    analysis,
    motion_signals,
    quality_signals,
    shot_signals,
)

SRC = Path(__file__).resolve().parents[3] / "src" / "media_house" / "modules" / "video_intelligence"
TB = Rational(1, 30000)


def time(frame: int) -> FrameTime:
    return FrameTime(frame, frame * 1000, TB)


def full_analysis() -> VideoAnalysis:
    timeline = shot_signals(120, cuts=(40, 90))
    return analysis(
        timeline,
        motion_signals(50, tx=lambda _k: -0.01),
        quality_signals(list(range(0, 120, 4))),
        history=IMPROVED,
    )


class TestSerialization:
    def test_the_whole_result_survives_a_round_trip_unchanged(self) -> None:
        original = full_analysis()

        assert analysis_from_json(analysis_to_json(original)) == original

    def test_the_text_is_deterministic(self) -> None:
        assert analysis_to_json(full_analysis()) == analysis_to_json(full_analysis())

    def test_the_document_declares_what_it_is_for_the_media_library(self) -> None:
        raw = json.loads(analysis_to_json(full_analysis()))

        assert raw["document_type"] == "video_intelligence.analysis"
        assert raw["schema_version"] == SCHEMA_VERSION

    def test_every_kind_of_signal_round_trips(self) -> None:
        cases = {
            AnalyzerId.SHOTS: shot_signals(60, cuts=(20,)),
            AnalyzerId.MOTION: motion_signals(8, tx=lambda k: 0.001 * k),
            AnalyzerId.QUALITY: quality_signals([0, 4, 8]),
        }
        types = {
            AnalyzerId.SHOTS: ShotSignals,
            AnalyzerId.MOTION: MotionSignals,
            AnalyzerId.QUALITY: QualitySignals,
        }
        for analyzer, signals in cases.items():
            text = document_to_json(signals_kind(analyzer), signals)

            assert document_from_json(signals_kind(analyzer), types[analyzer], text) == signals

    @pytest.mark.parametrize(
        ("mutate", "why"),
        [
            (lambda d: d.update(schema_version=SCHEMA_VERSION + 1), "schema"),
            (lambda d: d.update(document_type="something.else"), "kind"),
            (lambda d: d["payload"].update(surprise=1), "unexpected"),
            (lambda d: d["payload"].pop("shots"), "missing"),
            (lambda d: d["payload"].update(asset_id=7), "type"),
        ],
    )
    def test_a_document_that_does_not_fit_is_refused_not_guessed(self, mutate, why: str) -> None:  # type: ignore[no-untyped-def]
        raw = json.loads(analysis_to_json(full_analysis()))
        mutate(raw)

        with pytest.raises(InvalidAnalysisDocument):
            analysis_from_json(json.dumps(raw))

    def test_text_that_is_not_json_is_refused(self) -> None:
        with pytest.raises(InvalidAnalysisDocument):
            analysis_from_json("not json")

    def test_a_document_that_breaks_a_model_rule_is_refused(self) -> None:
        raw = json.loads(analysis_to_json(full_analysis()))
        raw["payload"]["shots"][0]["camera"]["confidence"] = 7.0

        with pytest.raises(InvalidAnalysisDocument):
            analysis_from_json(json.dumps(raw))

    def test_a_non_finite_number_is_never_stored(self) -> None:
        bad = dataclasses.replace(
            full_analysis().curves[0],
            values=(float("nan"),) * len(full_analysis().curves[0].values),
        )

        with pytest.raises(InvalidAnalysisDocument):
            document_to_json("x", bad)


class TestExplicitStatesAndEvidence:
    def test_the_assembled_result_obeys_its_own_contract(self) -> None:
        assert validate_analysis(full_analysis()) == ()

    def test_an_ok_observation_needs_confidence_and_supporting_frames(self) -> None:
        with pytest.raises(InvariantViolation):
            Assessed(state=AnalyzerState.OK, evidence=(Evidence(frames=(time(1),), metric="m"),))
        with pytest.raises(InvariantViolation):
            Assessed(state=AnalyzerState.OK, confidence=0.5)
        with pytest.raises(InvariantViolation):
            Assessed(
                state=AnalyzerState.OK,
                confidence=1.5,
                evidence=(Evidence(frames=(time(1),), metric="m"),),
            )

    @pytest.mark.parametrize("state", [s for s in AnalyzerState if s is not AnalyzerState.OK])
    def test_every_other_state_needs_a_reason_and_carries_no_confidence(
        self, state: AnalyzerState
    ) -> None:
        with pytest.raises(InvariantViolation):
            Assessed(state=state)
        with pytest.raises(InvariantViolation):
            Assessed(state=state, confidence=0.5, reasons=("why",))
        assert Assessed(state=state, reasons=("why",)).ok is False

    def test_every_observation_in_a_real_result_is_either_grounded_or_explained(self) -> None:
        for shot in full_analysis().shots:
            for section in (
                shot.boundary_in,
                shot.boundary_out,
                shot.handles,
                shot.keyframes,
                shot.camera,
                shot.motion,
                shot.quality,
            ):
                if section.ok:
                    assert section.confidence is not None and 0 <= section.confidence <= 1
                    assert any(e.frames for e in section.evidence)
                else:
                    assert section.reasons

    def test_times_are_valid_and_monotonic(self) -> None:
        result = full_analysis()

        ranges = [s.range for s in result.shots]
        assert all(r.start.pts < r.end.pts for r in ranges)
        assert all(a.end == b.start for a, b in pairwise(ranges))
        for curve in result.curves:
            assert list(curve.pts) == sorted(curve.pts)

    def test_validation_reports_a_broken_tiling(self) -> None:
        result = full_analysis()
        gap = dataclasses.replace(
            result.shots[1],
            range=TimeRange(time(45), result.shots[1].range.end),
        )
        broken = dataclasses.replace(result, shots=(result.shots[0], gap, *result.shots[2:]))

        findings = validate_analysis(broken)

        assert any("does not end where" in f for f in findings)

    def test_validation_reports_evidence_outside_the_video(self) -> None:
        result = full_analysis()
        outside = dataclasses.replace(
            result.shots[0].camera,
            evidence=(Evidence(frames=(time(9999),), metric="m"),),
        )
        broken = dataclasses.replace(
            result, shots=(dataclasses.replace(result.shots[0], camera=outside), *result.shots[1:])
        )

        assert any("outside the video" in f for f in validate_analysis(broken))

    def test_a_frame_time_and_a_range_refuse_nonsense(self) -> None:
        with pytest.raises(InvariantViolation):
            FrameTime(-1, 0, TB)
        with pytest.raises(InvariantViolation):
            TimeRange(time(10), time(5))
        with pytest.raises(InvariantViolation):
            TimeRange(time(1), FrameTime(2, 2000, Rational(1, 90000)))


class TestQuery:
    def result(self) -> VideoAnalysis:
        # shot 0 still and sharp, shot 1 panning and soft, shot 2 still and soft
        timeline = shot_signals(120, cuts=(40, 90))
        motion = motion_signals(59, tx=lambda k: -0.01 if 20 <= k < 45 else 0.0)
        quality = quality_signals(list(range(0, 120, 4)))
        sharpness = [0.6 if f < 40 else 0.05 for f in quality.frames]
        quality = dataclasses.replace(quality, sharpness=tuple(sharpness))
        return analysis(timeline, motion, quality)

    def test_filter_on_camera_movement_and_sharpness(self) -> None:
        found = find_shots(
            self.result(),
            ShotFilter(camera_movements=frozenset({CameraMovement.STATIC}), min_sharpness=0.3),
        )

        assert [s.index for s in found] == [0]

    def test_filter_on_duration_and_reason_codes(self) -> None:
        result = self.result()

        long_ones = find_shots(result, ShotFilter(min_seconds=1.3))
        soft = find_shots(result, ShotFilter(with_reasons=frozenset({"soft_focus"})))
        not_soft = find_shots(result, ShotFilter(without_reasons=frozenset({"soft_focus"})))

        assert [s.index for s in long_ones] == [0, 1]
        assert [s.index for s in soft] == [1, 2]
        assert [s.index for s in not_soft] == [0]

    def test_ranking_puts_the_best_first_for_the_named_property(self) -> None:
        result = self.result()

        by_sharpness = find_shots(result, rank_by=RankBy.SHARPNESS)
        by_duration = find_shots(result, rank_by=RankBy.DURATION)
        by_steadiness = find_shots(result, rank_by=RankBy.STEADINESS)

        assert by_sharpness[0].index == 0
        assert by_duration[0].index == 1  # 50 frames
        assert by_steadiness[0].camera.shake_residual is not None
        assert [s.index for s in find_shots(result)] == [0, 1, 2]
        assert QueryRank is RankBy

    def test_a_value_that_was_not_measured_never_matches_a_condition_on_it(self) -> None:
        unmeasured = analysis(shot_signals(120, cuts=(40,)))  # no motion, no quality analyzer

        assert find_shots(unmeasured, ShotFilter(max_shake_residual=1.0)) == ()
        assert find_shots(unmeasured, ShotFilter(min_sharpness=0.0)) == ()
        assert len(find_shots(unmeasured)) == 2

    def test_lookup_by_frame_and_id(self) -> None:
        result = self.result()

        assert result.shot_at(45) is result.shots[1]
        assert result.shot_at(120) is None
        assert result.shot("shot_0000090") is result.shots[2]
        assert result.shot("nope") is None


# --- no recommendations ---------------------------------------------------------------------
#: Words that turn a name into an instruction or a decision. Property names never contain them.
ACTION_WORDS = frozenset(
    [
        "recommend",
        "recommended",
        "recommendation",
        "should",
        "must",
        "fix",
        "trim",
        "remove",
        "discard",
        "reject",
        "keep",
        "apply",
        "delete",
        "replace",
        "enhance",
        "improve",
        "needs",
        "need",
        "use",
        "select",
        "choose",
        "stabilize",
        "denoise",
        "sharpen",
        "highlight",
        "best",
        "worst",
    ]
)
#: Phrases that would turn a docstring or message into advice.
ADVICE = ("you should", "we recommend", "should be cut", "should be removed", "recommended to")


def _field_names(tp: type, seen: set[type]) -> set[str]:
    names: set[str] = set()
    if tp in seen or not dataclasses.is_dataclass(tp):
        return names
    seen.add(tp)
    hints = typing.get_type_hints(tp)
    for f in dataclasses.fields(tp):
        names.add(f.name)
        stack = [hints[f.name]]
        while stack:
            item = stack.pop()
            stack.extend(typing.get_args(item))
            if isinstance(item, type):
                names |= _field_names(item, seen)
    return names


def _enum_values() -> set[str]:
    values: set[str] = set()
    for member in vars(module_values).values():
        if isinstance(member, type) and issubclass(member, Enum) and member is not Enum:
            values |= {str(m.value) for m in member} | {m.name.lower() for m in member}
    values |= {str(m.value) for m in QueryRank}
    return values


def _tokens(name: str) -> set[str]:
    return set(name.lower().split("_"))


def test_no_field_or_enum_name_expresses_an_action() -> None:
    names = _field_names(VideoAnalysis, set()) | _enum_values()

    offending = sorted(n for n in names if _tokens(n) & ACTION_WORDS)

    assert offending == []


def _domain_sources() -> list[Path]:
    return sorted((SRC / "domain").glob("*.py")) + sorted((SRC / "application").glob("*.py"))


def test_no_reason_code_or_message_expresses_an_action() -> None:
    identifier = re.compile(r"^[a-z]+(?:_[a-z]+)+$")
    offending: list[str] = []
    for path in _domain_sources():
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if identifier.match(text) and _tokens(text) & ACTION_WORDS:
                    offending.append(f"{path.name}: {text}")
                if any(phrase in text.lower() for phrase in ADVICE):
                    offending.append(f"{path.name}: {text[:60]}")
    assert offending == []


def test_the_analysis_never_names_an_editing_decision_in_its_boundary_kinds() -> None:
    assert {k.value for k in BoundaryKind} == {
        "start",
        "end",
        "hard_cut",
        "dissolve",
        "fade_through_black",
    }
    assert isinstance(QualityMetrics().sampled_frames, int)
    assert BoundaryObservation(state=AnalyzerState.NOT_ANALYZED, reasons=("r",)).kind is None
