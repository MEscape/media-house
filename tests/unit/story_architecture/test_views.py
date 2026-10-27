"""Reading the real upstream documents into planner facts: copied, never recomputed."""

from media_house.modules.content_intelligence.application.contracts import ScoreName
from media_house.modules.story_architecture.application.views import (
    alignment_identity,
    analysis_identity,
    content_facts,
)
from tests.support.avl_facts import audio, content, inputs, shot, video
from tests.support.ci_texts import BATTERIES, THREE_TOPICS
from tests.support.story_fakes import analysis_for
from tests.unit.audio_visual_alignment.helpers import run


def test_every_segment_keeps_its_ids_text_scores_and_roles() -> None:
    analysis = analysis_for(THREE_TOPICS)

    facts = content_facts(analysis, "a1", "clip")

    assert [s.segment_id for s in facts.segments] == [s.segment_id for s in analysis.segments]
    for fact, segment in zip(facts.segments, analysis.segments, strict=True):
        assert fact.unit_ids == segment.unit_ids
        assert fact.importance == segment.value(ScoreName.IMPORTANCE)
        assert fact.roles == tuple((r.role.value, r.confidence) for r in segment.roles)
        assert fact.text == " ".join(analysis.unit(u).text for u in segment.unit_ids)  # type: ignore[union-attr]
        assert fact.start == segment.span.start and fact.end == segment.span.end
    assert facts.topics and all(t.segment_ids for t in facts.topics)


def test_relations_are_placed_on_the_passages_that_hold_their_ends() -> None:
    analysis = analysis_for(BATTERIES)
    facts = content_facts(analysis, "a1", "clip")
    known = {s.segment_id for s in facts.segments}

    assert all(r.source_segment in known and r.target_segment in known for r in facts.relations)
    assert all(r.source_segment != r.target_segment for r in facts.relations)


def test_the_identity_names_the_analysis_not_the_time_it_was_read() -> None:
    first = analysis_for(BATTERIES)
    again = analysis_for(BATTERIES)
    other = analysis_for(THREE_TOPICS)

    assert analysis_identity(first) == analysis_identity(again)
    assert analysis_identity(first) != analysis_identity(other)


def test_alignment_is_attached_only_for_its_own_recording_and_its_own_passages() -> None:
    analysis = analysis_for(BATTERIES)
    first = analysis.segments[0].segment_id
    document = run(
        inputs(
            audio([]),
            video([shot("shot-1", 0, 60, text=("Battery pack 4680",))], asset_id="a1"),
            content_facts=content(
                [(first, 10.0, 20.0, "The battery pack is mounted below the floor")]
            ),
        )
    )
    assert document.text_matches  # the fixture really produced a pairing

    attached = content_facts(analysis, "a1", "clip", document)
    foreign = content_facts(analysis, "a2", "clip", document)

    assert {(m.passage_id, m.shot_id) for m in attached.matches} == {(first, "shot-1")}
    assert all(m.end > m.start for m in attached.matches)
    assert attached.alignment_identity == alignment_identity(document)
    assert foreign.matches == () and foreign.alignment_identity is None
