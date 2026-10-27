"""The use case against fake owners: what it reads, what it reuses, what it stores."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from media_house.modules.asset_relationships.application.contracts import (
    Grade,
    RelationshipStatus,
    RelationshipType,
)
from media_house.modules.content_intelligence.application.contracts import (
    ContentAnalysis,
    ResultState,
)
from media_house.modules.story_architecture.application.plan_story import (
    Acquire,
    PlanStory,
    PlanStoryCommand,
)
from media_house.modules.story_architecture.application.results import StoryResults
from media_house.modules.story_architecture.domain.errors import MissingAnalysis, NothingToPlan
from media_house.modules.story_architecture.domain.profile import StoryConfig
from media_house.modules.story_architecture.domain.values import StageState
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Err, Ok, OperationCancelledError, ValidationError
from media_house.shared.filesystem import AppPaths
from tests.support.ci_texts import BATTERIES, GARDEN, THREE_TOPICS
from tests.support.fakes import FixedClock
from tests.support.story_fakes import (
    FakeAlignmentCatalog,
    FakeContentAnalyzer,
    FakeContentCatalog,
    FakeGenerator,
    FakeRelationshipCatalog,
    StoryLibrary,
    analysis_for,
    relationship_dto,
)

CTX = JobContext.detached()


class Rig:
    def __init__(
        self,
        tmp_path: Path,
        *,
        stored: dict[str, ContentAnalysis] | None = None,
        generator: FakeGenerator | None = None,
        assets: tuple[str, ...] = ("a1",),
        relationships: FakeRelationshipCatalog | None = None,
    ) -> None:
        self.library = StoryLibrary(tmp_path / "docs", assets)
        (tmp_path / "docs").mkdir()
        self.content = FakeContentCatalog(
            stored if stored is not None else {"a1": analysis_for(THREE_TOPICS)}
        )
        self.analyzer = FakeContentAnalyzer()
        self.alignments = FakeAlignmentCatalog()
        self.generator = generator
        self.relationships = relationships or FakeRelationshipCatalog()
        self.planner = PlanStory(
            self.library,  # type: ignore[arg-type]
            self.content,  # type: ignore[arg-type]
            self.analyzer,
            self.alignments,
            AppPaths.under_root(tmp_path / "app"),
            FixedClock(),
            generator,
            self.relationships,
        )

    def run(self, command: PlanStoryCommand):  # type: ignore[no-untyped-def]
        return self.planner.execute(command, CTX)


def plan_of(rig: Rig, **kw):  # type: ignore[no-untyped-def]
    result = rig.run(PlanStoryCommand(("a1",), **kw))
    assert isinstance(result, Ok), result
    return result.value


def test_a_stored_analysis_becomes_a_stored_plan(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    result = plan_of(rig)

    assert result.created and result.plan.plan_version == 1 and result.plan.supersedes is None
    assert [r.how for r in result.plan.inputs if r.kind == "content_analysis"] == ["reused"]
    assert rig.analyzer.commands == []  # nothing was recomputed
    stored = StoryResults(rig.library).latest("a1")  # type: ignore[arg-type]
    assert isinstance(stored, Ok) and stored.value is not None
    assert stored.value[0] == result.plan


def test_the_same_request_is_served_from_the_library(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    first = plan_of(rig)
    second = plan_of(rig)

    assert first.created and not second.created
    assert second.plan == first.plan and second.asset.id == first.asset.id
    assert rig.library.registered == 1


def test_a_new_configuration_makes_a_new_plan_that_follows_the_old_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    first = plan_of(rig)
    second = plan_of(rig, config=StoryConfig("generic", audience="viewers"))
    third = plan_of(rig, config=StoryConfig("generic", audience="owners"))

    assert (second.plan.plan_version, second.plan.supersedes) == (2, first.asset.id)
    assert (third.plan.plan_version, third.plan.supersedes) == (3, second.asset.id)
    history = StoryResults(rig.library).history("a1")  # type: ignore[arg-type]
    assert len(history) == 3 and history[-1].id == first.asset.id  # the first is still there


def test_a_changed_analysis_misses_the_cache(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    plan_of(rig)
    rig.content.stored["a1"] = analysis_for(BATTERIES + GARDEN)

    again = plan_of(rig)

    assert again.created and again.plan.plan_version == 2


def test_a_missing_analysis_is_an_error_unless_the_owner_may_produce_it(tmp_path: Path) -> None:
    rig = Rig(tmp_path, stored={})

    refused = rig.run(PlanStoryCommand(("a1",)))

    assert isinstance(refused, Err) and isinstance(refused.error, MissingAnalysis)
    assert rig.analyzer.commands == []

    rig.analyzer.answer = analysis_for(BATTERIES)
    asked = rig.run(PlanStoryCommand(("a1",), acquire=Acquire.REQUEST))

    assert isinstance(asked, Ok)
    assert [c.source_asset_id for c in rig.analyzer.commands] == ["a1"]
    assert [r.how for r in asked.value.plan.inputs] == ["requested", "not_available"]


def test_a_supplied_analysis_is_used_as_given(tmp_path: Path) -> None:
    rig = Rig(tmp_path, stored={})

    result = rig.run(PlanStoryCommand(("a1",), content_analyses={"a1": analysis_for(BATTERIES)}))

    assert isinstance(result, Ok)
    assert result.value.plan.inputs[0].how == "supplied"


def test_bad_input_is_a_validation_error_before_any_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    for command in (
        PlanStoryCommand(()),
        PlanStoryCommand(("a1", "a1")),
        PlanStoryCommand(("a1",), config=StoryConfig("nope")),
        PlanStoryCommand(("ghost",)),
    ):
        assert isinstance(rig.run(command), Err)
    assert rig.library.registered == 0


def test_speechless_media_has_nothing_to_plan(tmp_path: Path) -> None:
    analysis = analysis_for(BATTERIES)
    silent = replace(analysis, state=ResultState.NOT_APPLICABLE)
    rig = Rig(tmp_path, stored={"a1": silent})

    result = rig.run(PlanStoryCommand(("a1",)))

    assert isinstance(result, Err) and isinstance(result.error, NothingToPlan)


def test_several_recordings_make_one_story_anchored_on_the_first(tmp_path: Path) -> None:
    rig = Rig(
        tmp_path,
        stored={"a1": analysis_for(BATTERIES, "a1"), "a2": analysis_for(GARDEN, "a2")},
        assets=("a1", "a2"),
    )

    result = rig.run(PlanStoryCommand(("a1", "a2")))

    assert isinstance(result, Ok)
    assert [a.asset_id for a in result.value.plan.source_assets] == ["a1", "a2"]
    assert result.value.asset.derivation is not None
    assert result.value.asset.derivation.source_asset_id == "a1"


def test_cancellation_stops_the_work(tmp_path: Path) -> None:
    rig = Rig(tmp_path)
    ctx = JobContext.detached()
    ctx.cancellation.cancel()

    with pytest.raises(OperationCancelledError):
        rig.planner.execute(PlanStoryCommand(("a1",)), ctx)
    assert rig.library.registered == 0


# --- optional model wording ----------------------------------------------------------------
def test_without_a_model_the_plan_is_the_deterministic_one(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    plain = plan_of(rig)
    asked = plan_of(rig, llm=True)  # no generator is configured at all

    assert asked.plan.stages[-1].stage == "refinement"
    assert asked.plan.stages[-1].state is StageState.NOT_AVAILABLE
    assert asked.plan.sections == plain.plan.sections
    assert asked.plan.provenance.engines == ()


def test_an_unavailable_model_is_reported_and_never_keys_the_plan(tmp_path: Path) -> None:
    rig = Rig(tmp_path, generator=FakeGenerator(unavailable="model not downloaded"))

    result = plan_of(rig, llm=True)

    stage = result.plan.stages[-1]
    assert stage.state is StageState.NOT_AVAILABLE and "not downloaded" in stage.detail
    assert result.plan.provenance.engines == ()
    assert any("refinement" in w for w in result.plan.warnings)


def test_a_model_rewords_only_what_passes_the_checks_and_is_recorded(tmp_path: Path) -> None:
    generator = FakeGenerator()
    rig = Rig(tmp_path, generator=generator)
    plain = plan_of(rig)
    section = plain.plan.ordered_sections()[0]
    generator.reply = json.dumps(
        {
            "sections": [{"n": 1, "purpose": "Covers battery cells and charging."}],
            "main_message": "Charging the battery too fast heats the cells.",
        }
    )

    result = plan_of(rig, llm=True)

    plan = result.plan
    assert result.created and plan.provenance.engines == (("language_model", "fake/1/story-model"),)
    assert plan.provenance.prompt_version == 1
    assert dict(plan.provenance.usage)["calls"] == 2.0
    assert plan.stages[-1].state is StageState.OK
    assert plan.section(section.section_id) is not None
    assert plan.main_message is not None and plan.main_message.method.value == "model"
    assert plan.narrative_pattern == plain.plan.narrative_pattern  # structure never depends on it
    # asking again with the same model is a cache hit; the deterministic plan is separate
    assert not plan_of(rig, llm=True).created
    assert not plan_of(rig).created


def test_a_failing_model_keeps_the_plan_and_says_so(tmp_path: Path) -> None:
    class Broken(FakeGenerator):
        def generate(self, system: str, user: str, cancellation: CancellationToken) -> str:
            raise ValidationError("server down", user_message="The model server is down.")

    rig = Rig(tmp_path, generator=Broken())

    result = plan_of(rig, llm=True)

    assert result.plan.stages[-1].state is StageState.FAILED
    assert "server down" in result.plan.stages[-1].detail or "down" in result.plan.stages[-1].detail
    assert result.plan.sections


def test_the_clock_port_is_the_only_source_of_time(tmp_path: Path) -> None:
    rig = Rig(tmp_path)

    result = plan_of(rig)

    assert result.plan.created_at == FixedClock().now()


# --- relations between the recordings -------------------------------------------------------
def two_assets(tmp_path: Path, *items) -> Rig:  # type: ignore[no-untyped-def]
    return Rig(
        tmp_path,
        stored={"a1": analysis_for(BATTERIES, "a1"), "a2": analysis_for(GARDEN, "a2")},
        assets=("a1", "a2"),
        relationships=FakeRelationshipCatalog(*items),
    )


def copy_of(revision: int = 0):  # type: ignore[no-untyped-def]
    return relationship_dto(
        RelationshipType.EXACT_DUPLICATE_OF,
        "a2",
        "a1",
        status=None,
        grade=Grade.RECORDED,
        revision=revision,
    )


def test_one_recording_never_asks_for_relations(tmp_path: Path) -> None:
    rig = Rig(tmp_path, relationships=FakeRelationshipCatalog(copy_of()))

    plan_of(rig)

    assert rig.relationships.calls == []


def test_several_recordings_read_how_they_relate_and_say_so(tmp_path: Path) -> None:
    rig = two_assets(tmp_path, copy_of())

    result = rig.run(PlanStoryCommand(("a1", "a2")))

    assert isinstance(result, Ok)
    plan = result.value.plan
    assert [r.treatment.value for r in plan.asset_relations] == ["redundant_copy"]
    assert any(r.kind == "asset_relationships" and r.how == "reused" for r in plan.inputs)
    assert sorted(rig.relationships.calls) == ["a1", "a2"]  # one question per recording


def test_the_relations_can_be_switched_off(tmp_path: Path) -> None:
    rig = two_assets(tmp_path, copy_of())

    result = rig.run(PlanStoryCommand(("a1", "a2"), use_relationships=False))

    assert isinstance(result, Ok) and result.value.plan.asset_relations == ()
    assert rig.relationships.calls == []


def test_a_catalog_that_cannot_answer_costs_the_relations_not_the_plan(tmp_path: Path) -> None:
    rig = two_assets(tmp_path, copy_of())
    rig.relationships.fail = True

    result = rig.run(PlanStoryCommand(("a1", "a2")))

    assert isinstance(result, Ok)
    plan = result.value.plan
    assert plan.asset_relations == () and plan.sections
    assert any(r.kind == "asset_relationships" and r.how == "not_available" for r in plan.inputs)
    assert any("relations" in w for w in plan.warnings)


def test_a_changed_relation_replans_and_an_unchanged_one_is_a_cache_hit(tmp_path: Path) -> None:
    rig = two_assets(tmp_path)
    command = PlanStoryCommand(("a1", "a2"))
    first = rig.run(command)
    again = rig.run(command)
    rig.relationships.items.append(copy_of())

    related = rig.run(command)
    related_again = rig.run(command)
    rig.relationships.items[:] = [copy_of(revision=1)]  # a decision or refresh moved updated_at
    moved = rig.run(command)

    assert isinstance(first, Ok) and isinstance(again, Ok) and isinstance(related, Ok)
    assert isinstance(related_again, Ok) and isinstance(moved, Ok)
    assert first.value.created and not again.value.created
    assert related.value.created and related.value.plan.plan_version == 2
    assert not related_again.value.created
    assert moved.value.created and moved.value.plan.plan_version == 3
    assert moved.value.plan.supersedes == related.value.asset.id  # history is kept


def test_a_rejected_claim_reaches_the_plan_but_is_not_acted_on(tmp_path: Path) -> None:
    rig = two_assets(
        tmp_path,
        relationship_dto(
            RelationshipType.ALTERNATE_TAKE_OF, "a1", "a2", status=RelationshipStatus.REJECTED
        ),
    )

    result = rig.run(PlanStoryCommand(("a1", "a2")))

    assert isinstance(result, Ok)
    assert [r.treatment.value for r in result.value.plan.asset_relations] == ["not_used"]
