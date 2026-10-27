"""The dependency graph and the stage table: the rules that decide what may run when."""

import pytest

from media_house.modules.media_pipeline.domain.graph import PipelineGraph, TaskSpec
from media_house.modules.media_pipeline.domain.plan import STAGES, plan_asset
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskKind
from media_house.shared.errors import InvariantViolation

K = TaskKind


def tid(kind: TaskKind, asset: str = "a") -> TaskId:
    return TaskId(asset, kind)


def kinds(plan_specs: tuple[TaskSpec, ...]) -> set[TaskKind]:
    return {s.id.kind for s in plan_specs}


# --- graph ------------------------------------------------------------------------------------
def test_cycle_is_rejected_with_the_path() -> None:
    a, b, c = (TaskId("x", k) for k in (K.INSPECT, K.PROXY, K.THUMBNAIL))
    specs = [
        TaskSpec(a, Lane.LIGHT, needs=frozenset({c})),
        TaskSpec(b, Lane.CPU, needs=frozenset({a})),
        TaskSpec(c, Lane.LIGHT, wants=frozenset({b})),
    ]

    with pytest.raises(InvariantViolation) as caught:
        PipelineGraph(specs)

    assert "Dependency cycle" in str(caught.value)
    assert len(list(caught.value.details["cycle"])) == 4  # type: ignore[call-overload]  # a -> b -> c -> a


def test_unknown_dependency_and_duplicates_are_rejected() -> None:
    a = tid(K.INSPECT)
    with pytest.raises(InvariantViolation, match="unknown task"):
        PipelineGraph([TaskSpec(a, Lane.LIGHT, needs=frozenset({tid(K.PROXY)}))])
    with pytest.raises(InvariantViolation, match="Duplicate"):
        PipelineGraph([TaskSpec(a, Lane.LIGHT), TaskSpec(a, Lane.LIGHT)])
    with pytest.raises(InvariantViolation, match="itself"):
        PipelineGraph([TaskSpec(a, Lane.LIGHT, wants=frozenset({a}))])


def test_order_respects_dependencies_and_is_deterministic() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=set(K))
    first = PipelineGraph(plan.specs).order
    second = PipelineGraph(reversed(plan.specs)).order

    for graph_order in (first, second):
        position = {t: i for i, t in enumerate(graph_order)}
        for spec in plan.specs:
            assert all(position[u] < position[spec.id] for u in spec.upstream)
    assert PipelineGraph(plan.specs).order == first


def test_levels_group_independent_tasks_and_weights_count_waiters() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.ALIGNMENT})
    graph = PipelineGraph(plan.specs)

    rows = [{t.kind for t in row} for row in graph.levels()]

    assert rows[0] == {K.INSPECT}
    assert rows[1] == {K.AUDIO_ANALYSIS, K.VIDEO_ANALYSIS}  # independent: start together
    assert graph.weight(tid(K.AUDIO_ANALYSIS)) >= 1
    assert graph.weight(tid(K.ALIGNMENT)) == 0


# --- plan -------------------------------------------------------------------------------------
def test_alignment_pulls_in_what_it_needs_and_marks_it_implied() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.ALIGNMENT})

    assert kinds(plan.specs) == {K.ALIGNMENT, K.AUDIO_ANALYSIS, K.VIDEO_ANALYSIS, K.INSPECT}
    assert plan.implied == {K.AUDIO_ANALYSIS, K.VIDEO_ANALYSIS, K.INSPECT}


def test_inspection_joins_every_run_as_a_want_never_a_need() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.AUDIO_ANALYSIS})

    assert K.INSPECT in kinds(plan.specs)
    audio = next(s for s in plan.specs if s.id.kind is K.AUDIO_ANALYSIS)
    assert audio.wants == {tid(K.INSPECT)}
    assert audio.needs == frozenset()  # a failed inspection must not block the analysis


def test_an_empty_request_plans_nothing() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested=set())

    assert plan.specs == ()


def test_content_waits_for_video_only_when_asked_to() -> None:
    def content(uses_video: bool) -> TaskSpec:
        plan = plan_asset(
            "a",
            has_video=True,
            has_audio=True,
            requested={K.CONTENT_ANALYSIS, K.VIDEO_ANALYSIS},
            content_uses_video=uses_video,
        )
        return next(s for s in plan.specs if s.id.kind is K.CONTENT_ANALYSIS)

    assert tid(K.VIDEO_ANALYSIS) not in content(False).upstream
    assert tid(K.VIDEO_ANALYSIS) in content(True).wants
    assert content(True).needs == {tid(K.AUDIO_ANALYSIS)}


def test_audio_only_asset_drops_picture_stages_and_their_dependents() -> None:
    plan = plan_asset("a", has_video=False, has_audio=True, requested=set(K))

    assert kinds(plan.specs) == {
        K.INSPECT,
        K.AUDIO_IMPROVEMENT,
        K.AUDIO_ANALYSIS,
        K.CONTENT_ANALYSIS,
    }
    assert {K.THUMBNAIL, K.PROXY, K.VIDEO_ANALYSIS, K.ALIGNMENT} <= plan.not_applicable


def test_silent_video_drops_audio_stages_and_alignment() -> None:
    plan = plan_asset("a", has_video=True, has_audio=False, requested=set(K))

    assert K.ALIGNMENT in plan.not_applicable
    assert K.AUDIO_ANALYSIS in plan.not_applicable
    assert K.CONTENT_ANALYSIS in plan.not_applicable
    assert K.VIDEO_ANALYSIS in kinds(plan.specs)


def test_unrequested_stages_are_disabled_not_missing() -> None:
    plan = plan_asset("a", has_video=True, has_audio=True, requested={K.INSPECT})

    assert plan.disabled == set(STAGES) - {K.INSPECT}
    assert plan.not_applicable == frozenset()


def test_every_stage_in_the_table_plans_into_a_valid_graph() -> None:
    for kind in STAGES:
        plan = plan_asset("a", has_video=True, has_audio=True, requested={kind})
        PipelineGraph(plan.specs)  # raises on a cycle or an unknown dependency
