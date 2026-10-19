"""Speech to pictures: what kind of evidence links a passage to a shot, and what never does."""

from media_house.modules.audio_visual_alignment.domain.config import AlignmentProfile
from media_house.modules.audio_visual_alignment.domain.inputs import ShotFacts
from media_house.modules.audio_visual_alignment.domain.model import AlignmentDocument, TextMatch
from media_house.modules.audio_visual_alignment.domain.values import EvidenceGrade, FlagKind
from tests.support.avl_facts import audio, content, inputs, shot, video
from tests.unit.audio_visual_alignment.helpers import run

TEXT = "The battery pack is mounted below the floor of the vehicle"


def grades(
    shots: list[ShotFacts],
    text: str = TEXT,
    passage: tuple[float, float] = (10.0, 20.0),
    sim: dict[tuple[str, str], float] | None = None,
    language: str = "en",
) -> tuple[dict[str, TextMatch], AlignmentDocument]:
    doc = run(
        inputs(
            audio([], language=language),
            video(shots),
            content_facts=content([("p1", *passage, text)]),
            similarities=sim,
        )
    )
    return {m.shot.shot_id: m for m in doc.text_matches}, doc


class TestGrades:
    def test_a_term_read_off_the_screen_is_observed_text(self) -> None:
        found, _ = grades([shot("a", 0, 60, text=("Battery pack 4680",))])

        assert found["a"].grade is EvidenceGrade.OBSERVED_TEXT
        assert "battery" in found["a"].terms

    def test_a_term_in_a_description_is_weaker_than_one_on_screen(self) -> None:
        text, _ = grades([shot("a", 0, 60, text=("battery",))])
        described, _ = grades([shot("a", 0, 60, description="a battery on a table")])

        assert described["a"].grade is EvidenceGrade.DESCRIBED
        assert (described["a"].scores.strength or 0.0) < (text["a"].scores.strength or 0.0)

    def test_an_object_label_is_the_weakest_textual_grade(self) -> None:
        found, _ = grades([shot("a", 0, 60, objects=("vehicle", "person"))])

        assert found["a"].grade is EvidenceGrade.ENTITY_LABEL

    def test_footage_with_evidence_but_no_match_is_none_found(self) -> None:
        found, _ = grades([shot("a", 0, 60, objects=("chair",), description="a quiet office")])

        assert found["a"].grade is EvidenceGrade.NONE_FOUND
        assert found["a"].scores.strength is None

    def test_footage_with_nothing_to_examine_is_unknown_not_unsupported(self) -> None:
        found, _ = grades([shot("a", 0, 60)])

        assert found["a"].grade is EvidenceGrade.UNKNOWN

    def test_no_grade_claims_a_demonstration_or_a_contradiction(self) -> None:
        names = {g.value for g in EvidenceGrade}

        assert not {"direct", "demonstrates", "contradicts", "unsupported"} & names


class TestWhichShots:
    def test_shots_just_before_and_after_are_candidates_but_far_ones_are_not(self) -> None:
        shots = [
            shot("far", 0, 5, text=("battery",)),
            shot("before", 5, 9, text=("battery",)),
            shot("during", 9, 21),
            shot("after", 21, 23, text=("battery",)),
            shot("later", 40, 50, text=("battery",)),
        ]

        found, _ = grades(shots)

        assert set(found) == {"before", "during", "after"}
        assert found["before"].gap_seconds == 1.0 and found["after"].gap_seconds == 1.0
        assert found["during"].gap_seconds == 0.0

    def test_a_nearer_shot_scores_higher_than_a_farther_one_with_the_same_evidence(self) -> None:
        shots = [
            shot("near", 20.2, 25, text=("battery",)),
            shot("far", 21.8, 25.5, text=("battery",)),
        ]

        found, _ = grades(shots)

        assert (found["near"].scores.strength or 0.0) > (found["far"].scores.strength or 0.0)

    def test_candidates_are_ranked_strongest_evidence_first(self) -> None:
        shots = [
            shot("c", 0, 8, objects=("vehicle",)),
            shot("a", 8, 12, text=("battery",)),
            shot("b", 12, 16, description="battery pack"),
        ]

        _, doc = grades(shots)

        ordered = sorted(doc.text_matches, key=lambda m: m.rank)
        assert [m.shot.shot_id for m in ordered] == ["a", "b", "c"]

    def test_the_number_of_candidates_is_bounded_by_the_profile(self) -> None:
        shots = [shot(f"s{i}", 10 + i, 11 + i, text=("battery",)) for i in range(8)]
        doc = run(
            inputs(audio([]), video(shots), content_facts=content([("p1", 10.0, 20.0, TEXT)])),
            AlignmentProfile(max_candidates=2),
        )

        matched = [m for m in doc.text_matches if m.grade is EvidenceGrade.OBSERVED_TEXT]
        assert len(matched) == 2

    def test_an_overlapping_shot_without_a_match_is_still_listed(self) -> None:
        found, _ = grades([shot("a", 0, 60, objects=("chair",))])

        assert "a" in found


class TestScoresAreSeparate:
    def test_a_short_passage_inside_a_long_shot_is_visible_as_such(self) -> None:
        found, _ = grades([shot("a", 0, 100, text=("battery",))])

        scores = found["a"].scores
        assert scores.overlap_seconds == 10.0
        assert scores.coverage_audio == 1.0
        assert scores.coverage_video == 0.1


class TestLanguageAndEmbeddings:
    def test_common_words_do_not_count_as_evidence(self) -> None:
        passages = [
            (f"p{i}", i * 10.0, i * 10.0 + 8, f"always something {w}")
            for i, w in enumerate(
                [
                    "alpha",
                    "beta",
                    "gamma",
                    "delta",
                    "epsilon",
                    "zeta",
                    "eta",
                    "theta",
                    "iota",
                    "kappa",
                ]
            )
        ]
        doc = run(
            inputs(
                audio([]),
                video([shot("a", 0, 100, text=("always something",))]),
                content_facts=content(passages),
            )
        )

        assert {m.grade for m in doc.text_matches} == {EvidenceGrade.NONE_FOUND}

    def test_a_non_english_speaker_gets_the_limit_stated(self) -> None:
        _, doc = grades([shot("a", 0, 60, objects=("car",))], language="de")

        assert FlagKind.LANGUAGE_MISMATCH in {f.kind for f in doc.flags}

    def test_an_english_speaker_gets_no_such_flag(self) -> None:
        _, doc = grades([shot("a", 0, 60, objects=("car",))], language="en")

        assert FlagKind.LANGUAGE_MISMATCH not in {f.kind for f in doc.flags}

    def test_a_shot_that_stands_out_by_embedding_is_a_semantic_candidate(self) -> None:
        shots = [shot(f"s{i}", 0, 60, objects=("chair",)) for i in range(8)]
        sim = {("p1", f"s{i}"): 0.2 + 0.001 * i for i in range(8)}
        sim[("p1", "s3")] = 0.9

        found, _ = grades(shots, sim=sim)

        assert found["s3"].grade is EvidenceGrade.SEMANTIC_CANDIDATE
        assert found["s3"].similarity_z and found["s3"].similarity_z > 1.5
        assert found["s2"].grade is EvidenceGrade.NONE_FOUND

    def test_similarities_without_a_spread_prove_nothing(self) -> None:
        shots = [shot(f"s{i}", 0, 60, objects=("chair",)) for i in range(8)]
        sim = {("p1", f"s{i}"): 0.5 for i in range(8)}

        found, _ = grades(shots, sim=sim)

        assert {m.grade for m in found.values()} == {EvidenceGrade.NONE_FOUND}

    def test_too_few_shots_are_not_enough_to_call_one_stand_out(self) -> None:
        shots = [shot(f"s{i}", 0, 60, objects=("chair",)) for i in range(3)]
        sim = {("p1", "s0"): 0.9, ("p1", "s1"): 0.1, ("p1", "s2"): 0.1}

        found, _ = grades(shots, sim=sim)

        assert EvidenceGrade.SEMANTIC_CANDIDATE not in {m.grade for m in found.values()}

    def test_without_content_the_stage_is_not_available_and_says_so(self) -> None:
        doc = run(inputs(audio([]), video([shot("a", 0, 60)])))

        stage = next(s for s in doc.stages if s.stage == "text")
        assert stage.state.value == "not_available" and doc.text_matches == ()
