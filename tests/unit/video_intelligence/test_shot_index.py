"""The shot index: a compact, searchable projection of one analysis (pure, no library)."""

from dataclasses import replace

import pytest

from media_house.modules.video_intelligence.domain.errors import InvalidAnalysisDocument
from media_house.modules.video_intelligence.domain.result import VideoAnalysis
from media_house.modules.video_intelligence.domain.serialization import (
    document_from_json,
    document_to_json,
)
from media_house.modules.video_intelligence.domain.shot_index import (
    INDEX_DOCUMENT,
    ShotIndex,
    analysis_identity,
    build_shot_index,
    pack_vector,
    unpack_vector,
)
from media_house.modules.video_intelligence.domain.values import AnalyzerId
from tests.support import vi_signals as vs
from tests.support.vi_signals import analysis, embeddings, label_vector, shot_signals

FRAMES = [0, 20, 40, 60, 80, 100]


def two_shot_analysis(with_vectors: bool = True) -> VideoAnalysis:
    signals = (
        {
            AnalyzerId.EMBEDDINGS: embeddings(
                FRAMES,
                [label_vector("content:vlog")] * 3 + [label_vector("content:event")] * 3,
            )
        }
        if with_vectors
        else None
    )
    return analysis(shot_signals(120, cuts=(60,)), signals=signals)


class TestProjection:
    def test_every_shot_becomes_one_row_with_exact_times(self) -> None:
        source = two_shot_analysis()

        index = build_shot_index(source)

        assert [s.shot_id for s in index.shots] == [s.shot_id for s in source.shots]
        first = index.shots[0]
        assert (first.start.numerator, first.start.denominator) == (0, 1)
        assert first.frames == source.shots[0].range.frames
        # 60 frames at 30 fps is exactly two seconds, kept exact rather than a float
        assert (index.shots[0].end.numerator, index.shots[0].end.denominator) == (2, 1)
        assert index.asset_id == "asset-1"
        assert index.duration_seconds == 4.0

    def test_the_embedding_survives_half_precision_with_ranking_accuracy(self) -> None:
        source = two_shot_analysis()
        original = source.embedding(source.shots[0].meaning.embedding_ref or "")
        assert original is not None

        index = build_shot_index(source)

        restored = index.shots[0].vector(index.embedding_dimension)
        assert index.embedding_dimension == len(original.vector)
        assert index.embedding_model == original.model
        assert restored == pytest.approx(original.vector, abs=1e-3)

    def test_without_embeddings_there_are_no_vectors_and_no_dimension(self) -> None:
        index = build_shot_index(two_shot_analysis(with_vectors=False))

        assert index.embedding_dimension == 0
        assert index.embedding_model is None
        assert all(s.embedding_f16 == "" and s.vector(0) == () for s in index.shots)

    def test_what_was_not_measured_is_unknown_never_zero(self) -> None:
        index = build_shot_index(analysis(shot_signals(120)))

        only = index.shots[0]
        assert only.sharpness is None
        assert only.motion_energy is None
        assert only.face_fraction is None
        assert only.visual_speaking is None
        assert only.description is None
        assert only.objects == () and only.texts == ()
        assert only.scores == {}

    def test_measured_motion_and_quality_are_carried(self) -> None:
        timeline = shot_signals(120)
        source = analysis(
            timeline,
            motion=vs.motion_signals(50),
            quality=vs.quality_signals(list(range(0, 120, 2)), sharpness=0.8),
        )

        index = build_shot_index(source)

        assert index.shots[0].motion_energy is not None
        assert index.shots[0].sharpness is not None


class TestIdentityAndStorage:
    def test_the_identity_changes_with_the_analysed_version(self) -> None:
        source = two_shot_analysis()

        assert analysis_identity(source) == analysis_identity(source)
        assert analysis_identity(source) != analysis_identity(replace(source, asset_checksum="zzz"))

    def test_the_index_round_trips_through_its_document(self) -> None:
        index = build_shot_index(two_shot_analysis())

        text = document_to_json(INDEX_DOCUMENT, index)

        assert document_from_json(INDEX_DOCUMENT, ShotIndex, text) == index

    def test_another_kind_of_document_is_refused(self) -> None:
        text = document_to_json(
            "video_intelligence.something_else", build_shot_index(two_shot_analysis())
        )

        with pytest.raises(InvalidAnalysisDocument):
            document_from_json(INDEX_DOCUMENT, ShotIndex, text)

    def test_packing_a_vector_checks_its_size(self) -> None:
        packed = pack_vector((0.5, -0.25, 1.0))

        assert unpack_vector(packed, 3) == (0.5, -0.25, 1.0)
        with pytest.raises(Exception, match="expected 4"):
            unpack_vector(packed, 4)
        assert unpack_vector("", 0) == ()
