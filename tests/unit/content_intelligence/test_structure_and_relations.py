"""Segmentation, topics, key information and relationships on small deterministic transcripts."""

from dataclasses import replace

from media_house.modules.content_intelligence.domain.config import (
    ContentConfig,
    Profile,
    Thresholds,
)
from media_house.modules.content_intelligence.domain.context import VisualContext, VisualShot
from media_house.modules.content_intelligence.domain.model import ContentAnalysis, Topic
from media_house.modules.content_intelligence.domain.validation import validate_analysis
from media_house.modules.content_intelligence.domain.values import (
    Adds,
    KeyKind,
    RefKind,
    RelationKind,
    RhetoricalKind,
    SegmentLevel,
)
from tests.support.ci_texts import (
    BATTERIES,
    GARDEN,
    GERMAN,
    THREE_TOPICS,
    analysis_of,
    lines_to_transcript,
)


def text_of(a: ContentAnalysis, unit_id: str) -> str:
    unit = a.unit(unit_id)
    assert unit is not None
    return unit.text


def starts(a: ContentAnalysis) -> list[str]:
    return [text_of(a, s.unit_ids[0]) for s in a.segments]


def kinds(a: ContentAnalysis, kind: KeyKind) -> list[str]:
    return [k.text for k in a.key_items if k.kind is kind]


class TestSegmentation:
    def test_topic_changes_become_boundaries_and_the_result_is_valid(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))

        validate_analysis(a)
        assert "Now let's talk about gardening." in starts(a)
        assert "Finally the budget." in starts(a)

    def test_topics_are_labelled_with_their_actual_subject(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))

        labels = [t.label.casefold() for t in a.topics if t.level is SegmentLevel.TOPIC]

        assert any("batter" in label for label in labels)
        assert any("tomato" in label or "garden" in label for label in labels)
        assert any("budget" in label for label in labels)
        assert not any(label in {"discussion", "content"} for label in labels)

    def test_segments_cover_every_sentence_once_in_order(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))

        assert [u for s in a.segments for u in s.unit_ids] == [u.unit_id for u in a.units]

    def test_every_boundary_names_its_evidence_and_weak_ones_are_flagged(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))

        boundaries = [s.boundary for s in a.segments if s.boundary]
        assert boundaries
        assert all(b.evidence and 0 <= b.strength <= 1 for b in boundaries)
        assert all(b.weak == (b.strength < 0.35) for b in boundaries)

    def test_coarser_levels_nest_inside_finer_ones(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))
        segment_starts = {s.unit_ids[0] for s in a.segments}

        for topic in a.topics:
            assert topic.boundary is None or topic.boundary.before_unit in segment_starts
        for topic in a.topics:
            if topic.parent_id:
                parent = a.topic(topic.parent_id)
                assert parent is not None and set(topic.segment_ids) <= set(parent.segment_ids)

    def test_a_pause_adds_evidence_but_is_not_a_boundary_by_itself(self) -> None:
        same_topic = (*BATTERIES[3:], *BATTERIES[3:])
        quiet = analysis_of(lines_to_transcript(same_topic))
        paused = analysis_of(lines_to_transcript(same_topic, pauses={4: 3.0}))

        assert len(paused.segments) <= len(quiet.segments) + 1
        marked = [
            s
            for s in paused.segments
            if s.boundary and any(e.kind == "pause" for e in s.boundary.evidence)
        ]
        assert all(
            any(e.kind == "similarity_valley" for e in s.boundary.evidence)
            for s in marked
            if s.boundary
        )

    def test_a_visual_cut_alone_never_splits_a_topic(self) -> None:
        source = lines_to_transcript((*BATTERIES[3:], *BATTERIES[3:]))
        plain = analysis_of(source)
        cuts = tuple(
            VisualShot(shot_id=f"sh{i}", start=i * 3.0, end=i * 3.0 + 3.0, scene_id=f"sc{i}")
            for i in range(12)
        )
        with_cuts = analysis_of(
            source, visual=VisualContext(asset_id="v", fingerprint="f", duration=40.0, shots=cuts)
        )

        def topics(a: ContentAnalysis) -> list[Topic]:
            return [t for t in a.topics if t.level is SegmentLevel.TOPIC]

        assert len(topics(with_cuts)) == len(topics(plain)) == 1

    def test_a_short_recording_is_one_segment(self) -> None:
        a = analysis_of(lines_to_transcript(BATTERIES[:3]))

        assert len(a.segments) == 1 and len(a.topics) == 1

    def test_german_text_is_segmented_too(self) -> None:
        a = analysis_of(lines_to_transcript(GERMAN, language="de"))

        validate_analysis(a)
        assert "Kommen wir nun zum Garten." in starts(a)
        assert not any("no cue lists" in w for w in a.warnings)

    def test_an_unsupported_language_still_works_and_says_what_is_limited(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS, language="sv"))

        validate_analysis(a)
        assert any("no cue lists exist for language 'sv'" in w for w in a.warnings)
        assert any("without cue lists" in lim for lim in a.limitations)


class TestKeyInformation:
    def test_items_are_verbatim_sentences_with_their_cue_as_evidence(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))

        assert a.key_items
        for item in a.key_items:
            assert item.evidence
            assert item.text in " ".join(text_of(a, u) for u in item.unit_ids)

    def test_a_qualification_stays_attached_to_what_it_qualifies(self) -> None:
        source = lines_to_transcript(
            ["The battery lasts 10 hours on a charge.", "But that only holds below 45 degrees."]
        )
        a = analysis_of(source)

        fact = next(k for k in a.key_items if k.kind is KeyKind.FACT)

        assert fact.qualified_by == (a.units[1].unit_id,)

    def test_a_summary_that_picks_a_qualified_statement_includes_its_qualifier(self) -> None:
        source = lines_to_transcript(
            [
                "The battery lasts 10 hours on a charge.",
                "But that only holds below 45 degrees.",
                "Tomatoes need sunlight and water in the garden soil.",
                "Compost feeds the tomato roots in the garden soil.",
            ]
        )
        a = analysis_of(source)

        assert a.summary is not None
        if "10 hours" in a.summary.text:
            assert "only holds below 45 degrees" in a.summary.text

    def test_hedges_are_visible_and_lower_the_strength(self) -> None:
        sure = analysis_of(lines_to_transcript(["The decision is that we will go with plan A."]))
        unsure = analysis_of(
            lines_to_transcript(["Maybe the decision is that we will go with plan A."])
        )

        a = next(k for k in sure.key_items if k.kind is KeyKind.DECISION)
        b = next(k for k in unsure.key_items if k.kind is KeyKind.DECISION)

        assert any(e.kind == "cue:hedge" for e in b.evidence)
        assert a.confidence is not None and b.confidence is not None and b.confidence < a.confidence

    def test_definitions_examples_and_decisions_are_recognised(self) -> None:
        a = analysis_of(
            lines_to_transcript(
                [
                    "A battery cell is defined as a unit that stores chemical energy.",
                    "For example, a phone uses one cell.",
                    "We decided to ship the new charger in March.",
                ]
            )
        )

        assert (
            kinds(a, KeyKind.DEFINITION)
            and kinds(a, KeyKind.EXAMPLE)
            and kinds(a, KeyKind.DECISION)
        )


class TestQuestionsAndAnswers:
    def answered(self, a: ContentAnalysis) -> list[tuple[str, str]]:
        out = []
        for r in a.relationships:
            if r.kind is RelationKind.ANSWERS:
                answer, question = a.key_item(r.source.id), a.key_item(r.target.id)
                assert answer is not None and question is not None
                out.append((question.text, answer.text))
        return out

    def test_an_immediate_answer_is_linked(self) -> None:
        a = analysis_of(
            lines_to_transcript(
                ["Why is the sky blue?", "Because air scatters blue light more than red light."]
            )
        )

        assert self.answered(a) == [
            ("Why is the sky blue?", "Because air scatters blue light more than red light.")
        ]

    def test_a_delayed_answer_is_found_and_marked_as_delayed(self) -> None:
        lines = [
            "Why is the sky blue?",
            "Tomatoes need sunlight and water in the garden soil.",
            "Compost feeds the tomato roots in the garden soil.",
            "The finance team reviews the yearly budget every quarter.",
            "Salaries are the largest part of the yearly budget.",
            "The sky looks blue because air scatters blue light.",
        ]
        a = analysis_of(lines_to_transcript(lines))

        (rel,) = [r for r in a.relationships if r.kind is RelationKind.ANSWERS]

        assert rel.note == "delayed answer"
        answer = a.key_item(rel.source.id)
        assert answer is not None and "scatters blue light" in answer.text

    def test_a_question_to_another_speaker_without_reply_is_an_open_question(self) -> None:
        lines = ["What is the budget for next year?", "Let me check the schedule for the garden."]
        a = analysis_of(lines_to_transcript(lines, speakers={0: "A", 1: "B"}))

        assert kinds(a, KeyKind.OPEN_QUESTION) == ["What is the budget for next year?"]
        assert any(o.kind == "open_questions" for o in a.global_observations)

    def test_a_question_answered_by_the_same_speaker_is_a_rhetorical_device_not_open(self) -> None:
        a = analysis_of(
            lines_to_transcript(["Why does it matter?", "Because everything depends on it."])
        )

        assert not kinds(a, KeyKind.OPEN_QUESTION)
        markers = [m.kind for s in a.segments for m in s.rhetoric]
        assert RhetoricalKind.RHETORICAL_QUESTION in markers


class TestRelationships:
    def build(self) -> ContentAnalysis:
        lines = [
            "Photosynthesis means that plants turn light into sugar.",
            "Plants need light to grow and make sugar from carbon dioxide.",
            "Leaves collect the light and carbon dioxide for the plant.",
            "Now let's talk about taxes.",
            "Income tax rules differ between countries and regions.",
            "Companies file their tax returns once every year.",
            "Tax deadlines are set by the revenue office of the country.",
            "Back to plants. Photosynthesis explains why the plants need light.",
            "Photosynthesis is the reason leaves collect light and carbon dioxide.",
            "Plants turn light into sugar by photosynthesis in the leaves.",
        ]
        return analysis_of(lines_to_transcript(lines))

    def test_a_later_use_of_a_defined_term_depends_on_the_definition(self) -> None:
        a = self.build()

        deps = [r for r in a.relationships if r.kind is RelationKind.DEPENDS_ON]

        assert deps
        assert all(r.evidence and r.confidence for r in deps)
        first = a.segments[0].segment_id
        assert any(r.target.id == first for r in deps)

    def test_every_relationship_points_at_things_that_exist(self) -> None:
        a = self.build()
        known = {
            RefKind.SEGMENT: {s.segment_id for s in a.segments},
            RefKind.TOPIC: {t.topic_id for t in a.topics},
            RefKind.ITEM: {k.item_id for k in a.key_items},
        }

        for r in a.relationships:
            assert r.source.id in known[r.source.kind] and r.target.id in known[r.target.kind]
            assert r.source != r.target

    def test_a_caveat_opening_a_passage_qualifies_the_previous_one(self) -> None:
        lines = [*BATTERIES[3:7], "However, this only matters above 45 degrees.", *GARDEN]
        a = analysis_of(lines_to_transcript(lines, pauses={4: 2.0}))

        qualifies = [r for r in a.relationships if r.kind is RelationKind.QUALIFIES]

        for r in qualifies:
            assert r.evidence and "caveat" in r.evidence[0].kind

    def test_topic_transitions_are_recorded_between_topics(self) -> None:
        a = analysis_of(lines_to_transcript(THREE_TOPICS))

        transitions = [r for r in a.relationships if r.kind is RelationKind.TRANSITIONS_TO]
        topics = [t for t in a.topics if t.level is SegmentLevel.TOPIC]

        assert len(transitions) >= len(topics) - 1 >= 1


class TestRepetition:
    def lines(self, repeat: str) -> list[str]:
        return [
            *BATTERIES,
            *GARDEN,
            repeat,
            "Salaries are the largest part of the yearly budget.",
            "Rent is the second largest budget item.",
            "The finance team reviews the budget every quarter.",
        ]

    def overlaps(self, a: ContentAnalysis) -> list[tuple[RelationKind, Adds | None]]:
        return [
            (r.kind, r.adds)
            for r in a.relationships
            if r.kind in {RelationKind.REPEATS, RelationKind.OVERLAPS}
        ]

    def test_an_exact_repetition_is_a_repeat_that_adds_nothing(self) -> None:
        a = analysis_of(lines_to_transcript(self.lines("Heat damages the battery over time.")))

        assert (RelationKind.REPEATS, Adds.NOTHING_NEW) in self.overlaps(a)

    def test_a_restatement_with_new_numbers_adds_evidence(self) -> None:
        a = analysis_of(
            lines_to_transcript(
                self.lines("Heat damages the battery over time, losing 20 percent in two years.")
            )
        )

        assert any(adds is Adds.NEW_EVIDENCE for _, adds in self.overlaps(a))

    def test_a_recap_is_classified_as_a_recap_not_as_nothing_new(self) -> None:
        a = analysis_of(
            lines_to_transcript(self.lines("To summarize, heat damages the battery over time."))
        )

        assert any(adds is Adds.RECAP for _, adds in self.overlaps(a))

    def test_nothing_in_a_link_is_an_instruction(self) -> None:
        a = analysis_of(lines_to_transcript(self.lines("Heat damages the battery over time.")))

        assert all(
            "cut" not in (r.note or "").casefold() and "remove" not in (r.note or "").casefold()
            for r in a.relationships
        )


class TestProfiles:
    def test_profiles_differ_in_measurable_work(self) -> None:
        fast, standard, deep = (ContentConfig(profile=p).settings for p in Profile)

        assert fast.neighbours_k < standard.neighbours_k < deep.neighbours_k
        assert fast.llm_scope.value == "none" and deep.llm_scope.value == "all"
        assert deep.context_segments > standard.context_segments
        assert not fast.prefer_embeddings and standard.prefer_embeddings

    def test_thresholds_are_part_of_the_analysis_fingerprint(self) -> None:
        base = ContentConfig(profile=Profile.FAST)
        changed = replace(base, thresholds=Thresholds(min_segment_units=4))

        assert base.analysis_fingerprint() != changed.analysis_fingerprint()
        assert (
            base.analysis_fingerprint()
            == ContentConfig(profile=Profile.FAST).analysis_fingerprint()
        )
