"""The fifteen realistic scenarios: recommendation quality, evidence correctness and rejection.

Each test builds a small library of shots that say what they show, a passage that says what it
needs, and asserts what a careful editor would conclude, including when nothing should be
recommended.
"""

from fractions import Fraction
from typing import Any

import pytest

from media_house.modules.broll_intelligence.domain.facts import (
    MainPicture,
    ReferenceFact,
    RelationFact,
    ShotFact,
    SoundActionFact,
    SourceAudioFact,
)
from media_house.modules.broll_intelligence.domain.model import Candidate
from media_house.modules.broll_intelligence.domain.values import (
    AssetVia,
    AudioBehavior,
    ClockBasis,
    EvidenceRole,
    GapKind,
    Identity,
    NeedKind,
    Precision,
    ScoreName,
    Severity,
    Support,
    TargetOrigin,
)
from tests.support.broll_fakes import first, name, run, secs, shot, target, visual


def seconds(rational: object) -> float:
    return float(Fraction(rational.numerator, rational.denominator))  # type: ignore[attr-defined]


def candidates(analysis: object, target_id: str = "t1") -> list[Candidate]:
    rec = analysis.recommendation(target_id)  # type: ignore[attr-defined]
    return list(rec.candidates)


class TestTutorialDemonstration:
    """1. A tutorial passage with footage of the exact action and its result."""

    def library(self) -> list[ShotFact]:
        return [
            shot(
                "bench",
                "s1",
                end=12.0,
                description="a person sanding a wooden board with sandpaper",
                events=[("gesture", 2.0), ("motion_peak", 3.5)],
                static_fraction=0.2,
            ),
            shot(
                "bench",
                "s2",
                start=12.0,
                end=20.0,
                description="a smooth sanded board in close up",
                static_fraction=0.95,
                framing="close_up",
            ),
            shot("park", "s1", description="a dog running in a park", static_fraction=0.3),
        ]

    def test_the_footage_of_the_action_is_the_demonstration(self) -> None:
        analysis = run([target()], self.library(), profile="tutorial")

        best = first(analysis)
        assert best is not None
        assert (best.asset_id, best.shot_id) == ("bench", "s1")
        assert best.role is EvidenceRole.DIRECT_DEMONSTRATION
        assert best.support is Support.SATISFIES
        assert not analysis.recommendation("t1").missing_coverage  # type: ignore[union-attr]

    def test_the_still_result_is_a_subject_not_a_demonstration(self) -> None:
        analysis = run([target()], self.library(), profile="tutorial")

        result = next(c for c in candidates(analysis) if c.shot_id == "s2")
        assert result.role is EvidenceRole.SPECIFIC_SUBJECT
        assert result.support is Support.ILLUSTRATIVE_ONLY
        assert "action_not_observed" in result.reason_codes

    def test_unrelated_footage_is_not_recommended_at_all(self) -> None:
        analysis = run([target()], self.library(), profile="tutorial")

        assert "park" not in {c.asset_id for c in candidates(analysis)}

    def test_the_range_follows_the_action_not_the_start_of_the_shot(self) -> None:
        best = first(run([target()], self.library(), profile="tutorial"))

        assert best is not None
        assert best.source.precision is Precision.ESTIMATED
        assert 1.0 <= seconds(best.source.start) <= 2.0  # a lead-in before the gesture at 2 s
        assert seconds(best.source.end) > 3.5  # the movement peak is inside


class TestProductSpecificClaims:
    """2. A product claim with correct-model, wrong-model and generic footage."""

    claim = target(
        text="The Pixel 9 survived a drop test without a scratch.",
        purpose="evidence",
        necessity="essential",
        concept="Pixel 9 survived a drop test",
        names=[name("Pixel 9")],
    )

    def library(self, correct: bool = True) -> list[ShotFact]:
        shots = [
            shot(
                "wrong",
                "s1",
                description="a phone survives a drop test on concrete",
                texts=["Pixel 8"],
                names=[(name("Pixel 8"), "text")],
                events=[("motion_peak", 2.0)],
            ),
            shot(
                "generic",
                "s1",
                description="a phone survives a drop test on concrete",
                events=[("motion_peak", 2.0)],
            ),
        ]
        if correct:
            shots.append(
                shot(
                    "right",
                    "s1",
                    description="a phone survives a drop test on concrete",
                    texts=["Pixel 9"],
                    names=[(name("Pixel 9"), "text")],
                    events=[("motion_peak", 2.0)],
                )
            )
        return shots

    def test_only_the_shot_shown_to_be_that_model_satisfies_the_claim(self) -> None:
        analysis = run([self.claim], self.library())

        ranked = candidates(analysis)
        assert ranked[0].asset_id == "right"
        assert ranked[0].identity is Identity.CONFIRMED
        assert ranked[0].role is EvidenceRole.DIRECT_EVIDENCE
        assert ranked[0].support is Support.SATISFIES

    def test_generic_footage_is_listed_as_illustrative_never_as_proof(self) -> None:
        analysis = run([self.claim], self.library())

        generic = next(c for c in candidates(analysis) if c.asset_id == "generic")
        assert generic.support is Support.ILLUSTRATIVE_ONLY
        assert generic.identity is Identity.UNVERIFIED
        assert generic.rank > 1

    def test_footage_of_another_model_is_refused_and_explained(self) -> None:
        analysis = run([self.claim], self.library())

        rec = analysis.recommendation("t1")
        assert rec is not None
        assert "wrong" not in {c.asset_id for c in rec.candidates}
        refused = next(c for c in rec.rejected if c.asset_id == "wrong")
        assert refused.role is EvidenceRole.CONTRADICTORY
        assert any("Pixel 8" in w for w in (*refused.warnings, refused.audio_reason)) or (
            refused.identity is Identity.CONFLICT
        )

    def test_without_the_right_footage_the_claim_is_a_gap_not_a_forced_match(self) -> None:
        analysis = run([self.claim], self.library(correct=False))

        rec = analysis.recommendation("t1")
        assert rec is not None and rec.missing_coverage
        assert all(c.support is not Support.SATISFIES for c in rec.candidates)
        gap = analysis.gap_for("t1")
        assert gap is not None and gap.repurposable
        assert gap.kind is GapKind.SUPPORTING_EVIDENCE
        assert "Pixel 9" in gap.resolution
        assert "illustrative" in gap.reason


class TestAbstractConcepts:
    """3. Relevant illustration that is not evidence."""

    passage: dict[str, Any] = {
        "text": "It gave her a deep feeling of freedom.",
        "concept": "freedom",
        "start": 20.0,
        "end": 26.0,
    }

    def test_an_illustration_is_welcome_for_an_illustrative_passage(self) -> None:
        library = [shot("sky", "s1", description=None)]
        match = {("t1", "sky#s1"): visual(3.0)}

        analysis = run([target(purpose="illustration", **self.passage)], library, matches=match)

        best = first(analysis)
        assert best is not None and best.support is Support.SATISFIES
        assert best.role is EvidenceRole.CONTEXTUAL_ILLUSTRATION
        assert best.association.value == "none"
        assert "visual_embedding" in best.reason_codes

    def test_the_same_footage_is_not_evidence_for_a_claim_that_needs_it(self) -> None:
        library = [shot("sky", "s1", description=None)]
        match = {("t1", "sky#s1"): visual(3.0)}

        analysis = run(
            [target(purpose="evidence", necessity="essential", **self.passage)],
            library,
            matches=match,
        )

        rec = analysis.recommendation("t1")
        assert rec is not None and rec.missing_coverage
        assert rec.candidates[0].support is Support.ILLUSTRATIVE_ONLY
        assert analysis.gap_for("t1") is not None


class TestContextualReferences:
    """4. "This model" and "the previous version"."""

    text = "This model has a brighter display than the previous version."

    def test_references_resolved_by_the_content_analysis_name_both_models(self) -> None:
        passage = target(
            text=self.text,
            purpose="evidence",
            concept="brighter display",
            names=[name("Pixel 9"), name("Pixel 8")],
            references=[
                ReferenceFact(phrase="this model", state="resolved", name=name("Pixel 9")),
                ReferenceFact(
                    phrase="the previous version", state="resolved", name=name("Pixel 8")
                ),
            ],
        )
        library = [
            shot(
                "nine",
                "s1",
                description="a bright phone display in a dark room",
                texts=["Pixel 9"],
                names=[(name("Pixel 9"), "text")],
            )
        ]

        best = first(run([passage], library))

        assert best is not None and best.identity is Identity.CONFIRMED
        assert best.support is Support.SATISFIES

    def test_an_unresolved_reference_keeps_its_uncertainty(self) -> None:
        passage = target(
            text=self.text,
            purpose="evidence",
            concept="brighter display",
            references=[ReferenceFact(phrase="this model", state="unresolved", reason="no name")],
        )
        library = [shot("some", "s1", description="a bright phone display in a dark room")]

        analysis = run([passage], library)

        rec = analysis.recommendation("t1")
        assert rec is not None and rec.missing_coverage
        best = rec.candidates[0]
        assert best.identity is Identity.REFERENCE_UNRESOLVED
        assert best.support is Support.ILLUSTRATIVE_ONLY
        assert any("could not be resolved" in w for w in rec.warnings)

    def test_a_pointer_at_an_activity_does_not_block_footage(self) -> None:
        passage = target(
            text="Then repeat the same process on the second board.",
            concept="repeat the process on the second board",
            references=[
                ReferenceFact(phrase="the same process", state="unresolved", about_activity=True)
            ],
        )
        library = [
            shot(
                "bench",
                "s1",
                description="repeating the process on a second board",
                events=[("gesture", 3.0)],
            )
        ]

        best = first(run([passage], library))

        assert best is not None and best.identity is Identity.NOT_APPLICABLE
        assert best.support is Support.SATISFIES


class TestAlternateAngles:
    """5. Several recordings of the same scene."""

    def library(self) -> list[ShotFact]:
        confirmed = [
            RelationFact(other_asset_id="camB", kind="alternate_take_of", certainty="confirmed")
        ]
        return [
            shot(
                "camA",
                "s1",
                description="a machine milling a metal part",
                relations=confirmed,
                events=[("motion_peak", 3.0)],
            ),
            shot(
                "camB",
                "s1",
                description="a machine milling a metal part",
                events=[("motion_peak", 3.0)],
            ),
        ]

    passage = target(
        text="The machine mills the part in one pass.",
        purpose="demonstration",
        concept="machine mills the part",
    )
    twins = {frozenset({"camA#s1", "camB#s1"}): 0.97}

    def test_a_confirmed_angle_is_kept_with_lower_novelty(self) -> None:
        analysis = run([self.passage], self.library(), similar=self.twins)

        both = candidates(analysis)
        assert {c.asset_id for c in both} == {"camA", "camB"}
        second = both[1]
        novelty = second.value(ScoreName.NOVELTY)
        assert novelty is not None and novelty < 0.5

    def test_an_unconfirmed_near_twin_is_dropped_as_a_repeat(self) -> None:
        library = [
            shot(s.asset_id, s.shot_id, description=s.description, events=[("motion_peak", 3.0)])
            for s in self.library()
        ]

        analysis = run([self.passage], library, similar=self.twins)

        rec = analysis.recommendation("t1")
        assert rec is not None and len(rec.candidates) == 1
        assert any(c.duplicate_of for c in rec.rejected)


class TestLongRecordingShortAction:
    """6. Only a few seconds of a ten-minute shot are useful."""

    def test_the_recommendation_is_the_moment_not_the_recording(self) -> None:
        long_shot = shot(
            "reel",
            "s1",
            end=600.0,
            description="soldering a joint on a circuit board",
            events=[("gesture", 312.0), ("motion_peak", 314.0)],
            static_fraction=0.3,
        )
        passage = target(text="Solder the joint.", concept="solder the joint", start=5.0, end=11.0)

        best = first(run([passage], [long_shot]))

        assert best is not None
        assert seconds(best.source.start) >= 311.0
        assert seconds(best.source.end) <= 322.0
        assert seconds(best.source.end) - seconds(best.source.start) <= 8.0
        assert seconds(best.shot_range.end) == 600.0

    def test_two_separate_stretches_of_action_give_two_ranges(self) -> None:
        long_shot = shot(
            "reel",
            "s1",
            end=600.0,
            description="soldering a joint on a circuit board",
            events=[("gesture", 100.0), ("gesture", 400.0)],
            static_fraction=0.3,
        )
        passage = target(text="Solder the joint.", concept="solder the joint", start=5.0, end=11.0)

        found = candidates(run([passage], [long_shot]))

        starts = sorted(seconds(c.source.start) for c in found)
        assert len(found) == 2 and starts[0] < 105 and starts[1] > 395


class TestDemonstrationArc:
    """7. A demonstration with a clear beginning, action and result."""

    def test_the_range_holds_the_start_the_action_and_the_result(self) -> None:
        shot_ = shot(
            "bench",
            "s1",
            end=30.0,
            description="pouring concrete into a mould",
            events=[("gesture", 5.0), ("motion_peak", 7.0), ("screen_content_change", 11.0)],
            static_fraction=0.3,
        )
        passage = target(
            text="Pour the concrete into the mould.",
            concept="pour the concrete",
            start=0.0,
            end=8.0,
        )

        best = first(run([passage], [shot_]))

        assert best is not None
        assert seconds(best.source.start) <= 5.0 and seconds(best.source.end) >= 11.0
        assert best.value(ScoreName.TEMPORAL_SUITABILITY) == pytest.approx(1.0)


class TestProtectedMoments:
    """8. The speaker's reaction stays on screen."""

    def test_an_emphatic_speaking_face_is_not_covered(self) -> None:
        passage = target(
            purpose="illustration",
            necessity="optional",
            concept="the crowd",
            text="And then the whole crowd just went silent.",
            main=MainPicture(shot_id="m1", speaking_face=True),
            emphatic=True,
        )
        library = [shot("crowd", "s1", description="a silent crowd in a stadium")]

        analysis = run([passage], library, profile="talking_head")

        assert analysis.recommendations == ()
        (kept,) = analysis.no_change
        assert kept.reason_code == "protect_speaker"

    def test_an_essential_demonstration_overrides_the_protection(self) -> None:
        passage = target(
            necessity="essential",
            main=MainPicture(shot_id="m1", speaking_face=True),
            emphatic=True,
        )
        library = [
            shot("bench", "s1", description="sanding a wooden board", events=[("gesture", 2.0)])
        ]

        analysis = run([passage], library)

        assert analysis.recommendation("t1") is not None


class TestMissingEvidence:
    """9. A claim that needs evidence the library does not have."""

    def test_no_suitable_footage_is_reported_with_the_extent_of_the_search(self) -> None:
        passage = target(
            text="The new chip doubled the benchmark score.",
            purpose="evidence",
            necessity="essential",
            concept="chip doubled benchmark score",
        )
        library = [
            shot("beach", "s1", description="waves on a beach"),
            shot("city", "s1", description="a busy city street at night"),
        ]

        analysis = run([passage], library)

        rec = analysis.recommendation("t1")
        assert rec is not None and rec.missing_coverage and rec.candidates == ()
        gap = analysis.gap_for("t1")
        assert gap is not None
        assert gap.searched.assets == 2 and gap.searched.shots == 2
        assert gap.fallback.startswith("keep the main picture")
        assert not gap.repurposable


class TestDuplicates:
    """10. Near-identical candidates are not all recommended."""

    def test_shots_of_one_duplicate_group_count_once(self) -> None:
        library = [
            shot(
                f"copy{i}",
                "s1",
                description="a hand sanding a board",
                events=[("gesture", 2.0)],
                duplicate_group="grp1",
            )
            for i in range(3)
        ]

        analysis = run([target()], library)

        rec = analysis.recommendation("t1")
        assert rec is not None and len(rec.candidates) == 1


class TestSoundAndSync:
    """11. An action sound whose synchronisation is known."""

    passage = target(text="Hammer the nail in.", concept="hammer the nail")

    def hammer(self, **changes: Any) -> ShotFact:
        return shot(
            "shed",
            "s1",
            description="a hammer driving a nail",
            events=[("gesture", 3.0)],
            **changes,
        )

    def test_a_synchronised_action_sound_is_kept(self) -> None:
        library = [
            self.hammer(
                audio=SourceAudioFact(),
                sound_actions=[
                    SoundActionFact(
                        video_time=secs(3.0), audio_kind="impact", delta=0.02, grade="corroborated"
                    )
                ],
                sync_established=True,
            )
        ]

        best = first(run([self.passage], library))

        assert best is not None and best.audio is AudioBehavior.RETAIN_SOURCE_AUDIO

    def test_unanalysed_sound_is_an_editorial_decision_not_a_default(self) -> None:
        best = first(run([self.passage], [self.hammer()]))

        assert best is not None and best.audio is AudioBehavior.FLAG_FOR_EDITOR
        assert best.value(ScoreName.AUDIO_SUITABILITY) is None

    def test_speech_in_the_footage_is_muted(self) -> None:
        talk = SourceAudioFact(speech=((secs(0.0), secs(30.0)),))

        best = first(run([self.passage], [self.hammer(audio=talk)]))

        assert best is not None and best.audio is AudioBehavior.MUTE_SOURCE

    def test_silence_lets_the_narration_carry_on(self) -> None:
        best = first(run([self.passage], [self.hammer(audio=SourceAudioFact())]))

        assert best is not None and best.audio is AudioBehavior.RETAIN_VOICEOVER

    def test_a_sound_pairing_of_another_recording_does_not_apply(self) -> None:
        library = [
            self.hammer(audio=SourceAudioFact()),
            shot(
                "other",
                "s1",
                description="a hammer driving a nail",
                events=[("gesture", 3.0)],
                audio=SourceAudioFact(),
            ),
        ]

        found = {c.asset_id: c for c in candidates(run([self.passage], library))}

        assert found["shed"].audio is AudioBehavior.RETAIN_VOICEOVER
        assert found["other"].audio is AudioBehavior.RETAIN_VOICEOVER


class TestUnrelatedClocks:
    """12. Assets with unrelated source clocks."""

    def test_identical_times_in_different_recordings_are_different_footage(self) -> None:
        library = [
            shot("camA", "s1", description="a hand sanding a board", events=[("gesture", 2.0)]),
            shot("camB", "s1", description="a hand sanding a board", events=[("gesture", 2.0)]),
        ]

        rec = run([target()], library).recommendation("t1")

        assert rec is not None and {c.asset_id for c in rec.candidates} == {"camA", "camB"}

    def test_an_unverified_clock_is_said_so(self) -> None:
        library = [
            shot(
                "camA",
                "s1",
                description="a hand sanding a board",
                events=[("gesture", 2.0)],
                clock=ClockBasis.VIDEO_ANALYSIS,
            )
        ]

        best = first(run([target()], library))

        assert best is not None and best.clock is ClockBasis.VIDEO_ANALYSIS
        assert any("no container offset" in w for w in best.warnings)

    def test_footage_of_the_passages_own_recording_is_never_its_own_cutaway(self) -> None:
        library = [
            shot(
                "talk",
                "s5",
                start=9.0,
                end=17.0,
                description="a hand sanding a board",
                events=[("gesture", 11.0)],
            ),
            shot(
                "talk",
                "s9",
                start=40.0,
                end=50.0,
                description="a hand sanding a board",
                events=[("gesture", 42.0)],
            ),
        ]

        found = candidates(run([target()], library))

        assert [c.shot_id for c in found] == ["s9"]


class TestUnreliableAnalysis:
    """13. Missing or unreliable visual analysis."""

    def test_unmeasured_quality_is_unknown_not_bad(self) -> None:
        library = [
            shot("bare", "s1", description="a hand sanding a board", scores=(), framing=None)
        ]

        best = first(run([target()], library))

        assert best is not None
        quality = best.score(ScoreName.VISUAL_QUALITY)
        assert quality is not None and quality.value is None and quality.reason
        assert best.overall is not None and "left out" in best.overall_basis

    def test_a_shot_with_no_observations_at_all_is_not_a_candidate(self) -> None:
        library = [shot("empty", "s1", scores=())]

        rec = run([target()], library).recommendation("t1")

        assert rec is not None and rec.candidates == () and rec.missing_coverage

    def test_a_shot_found_only_by_its_picture_says_so(self) -> None:
        library = [shot("blind", "s1", scores=(), framing=None)]
        match = {("t1", "blind#s1"): visual(3.5)}

        best = first(run([target()], library, matches=match))

        assert best is not None
        assert best.role is EvidenceRole.CONTEXTUAL_ILLUSTRATION
        assert best.support is Support.ILLUSTRATIVE_ONLY  # a demonstration needs the action shown
        evidence = best.score(ScoreName.EVIDENCE_STRENGTH)
        assert evidence is not None and evidence.value is not None and evidence.value < 0.5


class TestProxiesAndOriginals:
    """14. Evidence found on a proxy still recommends the original."""

    def test_the_recommendation_names_the_original_asset(self) -> None:
        library = [
            shot(
                "orig",
                "s1",
                description="a hand sanding a board",
                events=[("gesture", 2.0)],
                via=AssetVia.PROXY,
                analyzed="orig-proxy",
            )
        ]

        best = first(run([target()], library))

        assert best is not None
        assert best.asset_id == "orig" and best.analyzed_asset_id == "orig-proxy"
        assert best.via is AssetVia.PROXY


class TestAttractiveButWeak:
    """15. A beautiful shot of the wrong thing loses to the plain right one."""

    def library(self) -> list[ShotFact]:
        return [
            shot(
                "pretty",
                "s1",
                description=None,
                scores=(
                    ("technical_usability", 1.0),
                    ("visual_quality", 1.0),
                    ("subject_visibility", 1.0),
                ),
                framing="medium",
            ),
            shot(
                "plain",
                "s1",
                description="a hand sanding a board",
                events=[("gesture", 2.0)],
                scores=(("technical_usability", 0.6), ("visual_quality", 0.55)),
            ),
        ]

    def test_relevance_outranks_polish_for_a_demonstration(self) -> None:
        match = {("t1", "pretty#s1"): visual(2.8)}

        ranked = candidates(run([target()], self.library(), matches=match))

        assert [c.asset_id for c in ranked] == ["plain", "pretty"]
        assert ranked[0].support is Support.SATISFIES
        assert ranked[1].support is Support.ILLUSTRATIVE_ONLY

        def quality(c: Candidate) -> float:
            return c.value(ScoreName.VISUAL_QUALITY) or 0.0

        assert quality(ranked[1]) > quality(ranked[0])  # the weak match really is the prettier one

    def test_the_order_is_deterministic(self) -> None:
        match = {("t1", "pretty#s1"): visual(2.8)}

        first_run = run([target()], self.library(), matches=match)
        second_run = run([target()], list(reversed(self.library())), matches=match)

        assert first_run == second_run


class TestOutcomeConsistency:
    def test_a_valid_run_reports_no_error_issues(self) -> None:
        library = TestTutorialDemonstration().library()

        analysis = run([target()], library, profile="tutorial")

        assert analysis.issues_of(Severity.ERROR) == []

    def test_needs_are_classified_from_what_the_story_asked(self) -> None:
        passages = [
            target("t1", purpose="demonstration"),
            target("t2", purpose="evidence", start=30.0, end=36.0, concept="chart"),
            target("t3", purpose="illustration", start=50.0, end=56.0, concept="sunrise"),
        ]

        analysis = run(passages, [shot("x", "s1", description="a sunrise over a chart")])

        needs = {r.target_id: r.need for r in analysis.recommendations}
        assert needs == {
            "t1": NeedKind.DEMONSTRATION,
            "t2": NeedKind.REQUIRED_EVIDENCE,
            "t3": NeedKind.ILLUSTRATIVE,
        }

    def test_content_opportunities_without_a_story_still_match(self) -> None:
        passage = target(
            origin=TargetOrigin.CONTENT_OPPORTUNITY, purpose="", category="process_footage"
        )
        library = [
            shot("bench", "s1", description="sanding a wooden board", events=[("gesture", 2.0)])
        ]

        analysis = run([passage], library, with_story=False)

        best = first(analysis)
        assert best is not None and best.support is Support.SATISFIES
        narrative = best.score(ScoreName.NARRATIVE_RELEVANCE)
        assert narrative is not None and narrative.value is None
        assert any("No story plan" in text for text in analysis.limitations)
