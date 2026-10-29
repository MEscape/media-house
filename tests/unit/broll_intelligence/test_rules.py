"""Profiles, thresholds, needs, reuse, scores and the stored form."""

from dataclasses import replace
from typing import Any

import pytest

from media_house.core.domain import Rational
from media_house.modules.broll_intelligence.domain.engine import analyse_broll
from media_house.modules.broll_intelligence.domain.errors import (
    InvalidBrollConfig,
    InvalidBrollDocument,
)
from media_house.modules.broll_intelligence.domain.facts import (
    MainPicture,
    PoolFact,
    RetrievalFacts,
    ShotFact,
)
from media_house.modules.broll_intelligence.domain.model import BrollAnalysis, Score
from media_house.modules.broll_intelligence.domain.profile import (
    PROFILES,
    BrollConfig,
    BrollProfile,
)
from media_house.modules.broll_intelligence.domain.retrieve import build_index, lexical_candidates
from media_house.modules.broll_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from media_house.modules.broll_intelligence.domain.text import query_terms, reduce, stem
from media_house.modules.broll_intelligence.domain.validation import validate
from media_house.modules.broll_intelligence.domain.values import (
    EvidenceRole,
    NeedKind,
    ScoreBasis,
    ScoreName,
    Severity,
    Support,
    TargetOrigin,
    UseKind,
)
from media_house.shared.errors import InvariantViolation
from tests.support.broll_fakes import first, meta, name, run, shot, target, visual


def sanding(**changes: Any) -> ShotFact:
    return shot(
        "bench",
        "s1",
        description="a person sanding a wooden board",
        events=[("gesture", 2.0)],
        static_fraction=0.2,
        **changes,
    )


class TestWords:
    @pytest.mark.parametrize(
        ("a", "b"),
        [
            ("pours", "pouring"),
            ("dropped", "drop"),
            ("running", "run"),
            ("boards", "board"),
            ("bake", "baking"),
            ("brighter", "bright"),
        ],
    )
    def test_related_forms_meet(self, a: str, b: str) -> None:
        assert stem(a) == stem(b)

    def test_filler_words_are_not_terms(self) -> None:
        assert reduce("Now I will sand the board") == ("sand", "board")

    def test_context_joins_the_query_only_when_the_passage_is_thin(self) -> None:
        clear = target(context=["We talked about ladders earlier."])
        thin = target(concept="", context=["We talked about ladders earlier."])

        assert "ladder" not in dict(query_terms(clear))
        assert "ladder" in dict(query_terms(thin))

    def test_a_named_product_is_a_strong_term(self) -> None:
        terms = dict(query_terms(target(names=[name("Pixel 9")], concept="")))

        assert terms["pixel"] == 1.0 and terms["9"] == 1.0


class TestProfiles:
    def test_every_profile_is_valid(self) -> None:
        assert all(p.problems() == [] for p in PROFILES.values())

    def test_an_unknown_profile_is_refused(self) -> None:
        with pytest.raises(InvalidBrollConfig):
            BrollConfig(profile="nonsense").resolve()

    def test_the_story_format_picks_the_profile(self) -> None:
        assert BrollConfig().resolve("review").name == "product_review"
        assert BrollConfig().resolve("something else").name == "generic"

    def test_a_documentary_does_not_accept_illustration_for_a_demonstration(self) -> None:
        library = [shot("sky", "s1")]
        match = {("t1", "sky#s1"): visual(3.5)}

        documentary = run([target()], library, profile="documentary", matches=match)
        montage = run([target()], library, profile="cinematic_montage", matches=match)

        assert documentary.recommendation("t1").missing_coverage  # type: ignore[union-attr]
        assert not montage.recommendation("t1").missing_coverage  # type: ignore[union-attr]

    def test_a_montage_never_lets_illustration_stand_for_evidence(self) -> None:
        passage = target(purpose="evidence", necessity="essential", concept="the contract")
        match = {("t1", "sky#s1"): visual(3.5)}

        analysis = run([passage], [shot("sky", "s1")], profile="cinematic_montage", matches=match)

        assert analysis.recommendation("t1").missing_coverage  # type: ignore[union-attr]

    def test_the_profile_changes_the_insert_length(self) -> None:
        library = [sanding()]
        long = target(start=0.0, end=30.0)

        tutorial = first(run([long], library, profile="tutorial"))
        shorts = first(run([long], library, profile="shorts_reels"))

        assert tutorial is not None and shorts is not None
        assert tutorial.insert_max_seconds > shorts.insert_max_seconds


class TestQualityThreshold:
    def test_a_relevant_but_unusable_shot_is_refused(self) -> None:
        library = [sanding(scores=(("technical_usability", 0.1),))]

        rec = run([target()], library).recommendation("t1")

        assert rec is not None and rec.candidates == () and rec.missing_coverage
        assert "usability" in rec.rejected[0].warnings[-1]

    def test_the_best_of_a_bad_lot_is_not_forced(self) -> None:
        weak = shot(
            "ok", "s1", description="a person sanding a wooden board", scores=(), framing=None
        )
        custom = replace(PROFILES["generic"], min_overall=0.99)

        analysis = run_with(custom, [weak])

        rec = analysis.recommendation("t1")
        assert rec is not None and rec.missing_coverage
        assert "quality threshold" in rec.rejected[0].warnings[-1]

    def test_a_flagged_shot_is_refused(self) -> None:
        library = [sanding(indicators=("black_picture",))]

        rec = run([target()], library).recommendation("t1")

        assert rec is not None and rec.candidates == ()

    def test_a_shot_where_a_face_speaks_is_refused(self) -> None:
        library = [sanding(visual_speaking=0.8)]

        rec = run([target()], library).recommendation("t1")

        assert rec is not None and rec.candidates == ()


def run_with(profile: BrollProfile, shots: list[ShotFact]) -> BrollAnalysis:
    targets = [target()]
    lexical = lexical_candidates(build_index(shots), targets, 50)
    pool = PoolFact(
        assets=len(shots), with_shots=len(shots), shots=len(shots), shots_with_embedding=0
    )
    return analyse_broll(
        targets,
        shots,
        RetrievalFacts(),
        lexical,
        pool,
        "p",
        BrollConfig(custom=profile),
        meta(with_story=True),
    )


class TestNegation:
    def test_a_passage_that_negates_cannot_be_proven_with_footage(self) -> None:
        passage = target(negated=True)

        best = first(run([passage], [sanding()]))

        assert best is not None and best.support is Support.ILLUSTRATIVE_ONLY
        assert any("negates" in w for w in best.warnings)


class TestNeeds:
    def test_a_graphic_is_not_searched_as_footage(self) -> None:
        passage = target(purpose="explainer_graphic", concept="a diagram of the engine")

        analysis = run([passage], [sanding()])

        assert analysis.recommendations == ()
        assert analysis.no_change[0].reason_code == "graphic_not_footage"

    def test_already_covered_passages_keep_their_picture(self) -> None:
        analysis = run([target(already_covered=True)], [sanding()])

        assert analysis.no_change[0].reason_code == "already_covered"

    def test_a_passage_too_short_for_an_insert_is_left_alone(self) -> None:
        analysis = run([target(start=0.0, end=0.8)], [sanding()])

        assert analysis.no_change[0].reason_code == "passage_too_short"

    def test_variety_is_offered_only_where_the_profile_wants_it(self) -> None:
        passage = target(
            origin=TargetOrigin.PACING,
            purpose="",
            reason="the same picture for 20 s",
            concept="",
            text="the board is sanded",
            story_visual_id=None,
        )
        library = [sanding()]

        offered = run([passage], library, profile="generic", with_story=False)
        refused = run([passage], library, profile="interview_podcast", with_story=False)

        assert offered.recommendation("t1").need is NeedKind.VARIETY  # type: ignore[union-attr]
        assert refused.no_change[0].reason_code == "profile_adds_no_variety"

    def test_a_jump_cut_is_covered_with_related_footage(self) -> None:
        passage = target(
            origin=TargetOrigin.JUMP_CUT,
            purpose="",
            concept="",
            text="the board is sanded smooth",
            start=12.0,
            end=15.0,
            main=MainPicture(shot_id="m2", jump_cut_likelihood=0.9),
            story_visual_id=None,
        )

        analysis = run([passage], [sanding()], with_story=False)

        rec = analysis.recommendation("t1")
        assert rec is not None and rec.need is NeedKind.JUMP_CUT_COVER
        assert rec.candidates and rec.candidates[0].use is UseKind.CUTAWAY

    def test_the_most_important_passage_gets_first_pick_of_scarce_footage(self) -> None:
        important = target("t1", necessity="essential", start=10.0, end=16.0)
        minor = target("t2", necessity="optional", start=40.0, end=46.0, strength=0.3)
        library = [sanding()]

        analysis = run([minor, important], library)

        assert not analysis.recommendation("t1").missing_coverage  # type: ignore[union-attr]
        second = first(analysis, "t2")
        assert second is not None and second.reused
        assert second.value(ScoreName.NOVELTY) == pytest.approx(0.3)

    def test_max_targets_keeps_the_most_important(self) -> None:
        passages = [
            target("t1", necessity="essential"),
            target("t2", necessity="optional", start=30.0, end=36.0, strength=0.2),
        ]

        analysis = run(passages, [sanding()], max_targets=1)

        assert [r.target_id for r in analysis.recommendations] == ["t1"]


class TestScores:
    def test_a_score_that_is_unknown_says_why_and_is_not_zero(self) -> None:
        with pytest.raises(InvariantViolation):
            Score(name=ScoreName.VISUAL_QUALITY, value=None, basis=ScoreBasis.MEASURED, reason="")
        with pytest.raises(InvariantViolation):
            Score(name=ScoreName.VISUAL_QUALITY, value=1.2, basis=ScoreBasis.MEASURED, reason="x")

    def test_every_score_is_present_separately(self) -> None:
        best = first(run([target()], [sanding()]))

        assert best is not None
        assert {s.name for s in best.scores} == set(ScoreName)

    def test_bad_quality_cannot_be_hidden_by_relevance(self) -> None:
        good = sanding(scores=(("technical_usability", 0.95), ("visual_quality", 0.95)))
        poor = sanding(scores=(("technical_usability", 0.45), ("visual_quality", 0.2)))

        best_good = first(run([target()], [good]))
        best_poor = first(run([target()], [poor]))

        assert best_good is not None and best_poor is not None
        assert best_good.value(ScoreName.SEMANTIC_RELEVANCE) == best_poor.value(
            ScoreName.SEMANTIC_RELEVANCE
        )
        assert (best_good.overall or 0) > (best_poor.overall or 0)

    def test_the_confidence_is_lower_without_confirmed_identity(self) -> None:
        passage = target(purpose="illustration", names=[name("Pixel 9")], concept="Pixel 9 phone")
        confirmed = shot(
            "a", "s1", description="a phone", texts=["Pixel 9"], names=[(name("Pixel 9"), "text")]
        )
        unverified = shot("b", "s1", description="a Pixel phone")

        ranked = run([passage], [confirmed, unverified]).recommendation("t1")

        assert ranked is not None
        by_asset = {c.asset_id: c.value(ScoreName.CONFIDENCE) for c in ranked.candidates}
        assert (by_asset["a"] or 0) > (by_asset["b"] or 0)

    def test_the_description_alone_never_confirms_identity(self) -> None:
        passage = target(purpose="evidence", names=[name("Pixel 9")], concept="Pixel 9 display")
        liar = shot(
            "a", "s1", description="the Pixel 9 display", names=[(name("Pixel 9"), "description")]
        )

        best = first(run([passage], [liar]))

        assert best is not None and best.support is Support.ILLUSTRATIVE_ONLY


class TestStoredForm:
    def analysis(self) -> BrollAnalysis:
        return run([target()], [sanding()], profile="tutorial")

    def test_it_round_trips_exactly(self) -> None:
        analysis = self.analysis()

        assert analysis_from_json(analysis_to_json(analysis)) == analysis

    def test_times_are_exact_rationals(self) -> None:
        best = first(self.analysis())

        assert best is not None
        assert isinstance(best.source.start, Rational)

    def test_another_schema_or_document_is_refused(self) -> None:
        text = analysis_to_json(self.analysis())

        with pytest.raises(InvalidBrollDocument):
            analysis_from_json(text.replace('"schema_version": 1', '"schema_version": 99'))
        with pytest.raises(InvalidBrollDocument):
            analysis_from_json(text.replace('"broll_analysis"', '"something_else"', 1))
        with pytest.raises(InvalidBrollDocument):
            analysis_from_json("{not json")


class TestValidation:
    def test_a_range_outside_its_shot_is_an_error(self) -> None:
        analysis = run([target()], [sanding()])
        rec = analysis.recommendations[0]
        bad = replace(
            rec.candidates[0],
            source=replace(rec.candidates[0].source, end=Rational(9999, 1)),
        )
        broken = replace(analysis, recommendations=(replace(rec, candidates=(bad,)),))

        errors = [i for i in validate(broken) if i.severity is Severity.ERROR]

        assert any("outside its shot" in i.detail for i in errors)

    def test_a_contradicted_candidate_cannot_satisfy_a_need(self) -> None:
        analysis = run([target()], [sanding()])
        rec = analysis.recommendations[0]
        bad = replace(rec.candidates[0], role=EvidenceRole.CONTRADICTORY)
        broken = replace(analysis, recommendations=(replace(rec, candidates=(bad,)),))

        assert any("contradicted" in i.detail for i in validate(broken))

    def test_missing_coverage_must_agree_with_the_candidates(self) -> None:
        analysis = run([target()], [sanding()])
        rec = analysis.recommendations[0]
        broken = replace(analysis, recommendations=(replace(rec, missing_coverage=True),))

        assert any("disagrees" in i.detail for i in validate(broken))

    def test_a_gap_for_a_covered_passage_is_an_error(self) -> None:
        analysis = run(
            [target(purpose="evidence", necessity="essential", concept="nothing here")],
            [shot("x", "s1", description="waves")],
        )
        assert analysis.gaps
        covered = replace(
            analysis,
            recommendations=(replace(analysis.recommendations[0], missing_coverage=False),),
        )

        assert any(i.severity is Severity.ERROR for i in validate(covered))
