"""The stored document: strict round trip, cache key identity, render outcomes, views."""

import json
from dataclasses import replace

import pytest

from media_house.modules.continuity_intelligence.application.contracts import (
    IssueType,
    Owner,
    RepairKind,
)
from media_house.modules.transition_engine.application.views import (
    continuity_facts,
    source_facts,
)
from media_house.modules.transition_engine.domain.engine import (
    RenderOutcome,
    apply_renders,
    cache_key,
)
from media_house.modules.transition_engine.domain.errors import InvalidTransitionDocument
from media_house.modules.transition_engine.domain.facts import Bookend
from media_house.modules.transition_engine.domain.model import TransitionPlan
from media_house.modules.transition_engine.domain.planner import PlanInputs
from media_house.modules.transition_engine.domain.profile import (
    TransitionConfig,
    TransitionDecision,
)
from media_house.modules.transition_engine.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.transition_engine.domain.values import (
    BookendKind,
    DecisionAction,
    DecisionStatus,
    RenderState,
    ResultState,
    TransitionKind,
)
from tests.support.analysis_fakes import timeline
from tests.support.asset_dto import make_asset
from tests.support.cont_fakes import jump_analysis
from tests.support.trans_fakes import chain, plan, r, sequence, source, style

TWO = (("c1", "A", 100.0, 110.0), ("c2", "B", 100.0, 110.0))
ENDS = (
    Bookend(kind=BookendKind.MEMBERSHIP_PLUG, timeline_at=r(10)),
    Bookend(kind=BookendKind.END_SCREEN, timeline_at=r(20)),
)


def _rich() -> TransitionPlan:
    decision = TransitionDecision("start:c1", DecisionAction.USE, TransitionKind.FADE_IN)
    return plan(
        sequence(chain(*TWO), bookends=ENDS),
        profile="ranking",
        style_limits=style(3),
        decisions=(decision,),
    )


def test_a_plan_round_trips_exactly() -> None:
    p = _rich()
    text = plan_to_json(p)
    assert json.loads(text)["document_type"] == "transition_plan"
    assert plan_from_json(text) == p


def test_reading_is_strict() -> None:
    raw = json.loads(plan_to_json(_rich()))
    for broken in (
        {**raw, "unknown": 1},
        {**raw, "schema_version": 99},
        {**raw, "document_type": "other"},
        {k: v for k, v in raw.items() if k != "transitions"},
    ):
        with pytest.raises(InvalidTransitionDocument):
            plan_from_json(json.dumps(broken))


def test_the_cache_key_changes_with_what_changes_the_plan() -> None:
    seq = sequence(chain(*TWO))
    config = TransitionConfig()
    profile = config.resolve()
    inputs = PlanInputs(sequence=seq, sources={"A": source("A"), "B": source("B")})

    def key(i: PlanInputs = inputs, render: bool = False) -> object:
        return cache_key(i, config, profile, render=render, upstream={"video:A": "v1"})

    assert key() == key()
    assert key(render=True) != key()
    assert key(replace(inputs, style=style(2))) != key()
    decided = {"cut:c1>c2": TransitionDecision("cut:c1>c2", DecisionAction.HARD_CUT)}
    assert key(replace(inputs, decisions=decided)) != key()
    longer = replace(inputs, sources={"A": source("A", 700.0), "B": source("B")})
    assert key(longer) != key()


def test_render_outcomes_are_recorded_and_a_failure_falls_back_to_a_cut() -> None:
    p = _rich()
    plug = p.between("c1", "c2")
    end = p.transitions[-1]
    assert plug is not None
    assert plug.overlay is not None
    assert end.overlay is not None
    outcomes = {
        plug.overlay.request_id: RenderOutcome(
            request_id=plug.overlay.request_id, state=RenderState.RENDERED, asset_id="mov-1"
        ),
        end.overlay.request_id: RenderOutcome(
            request_id=end.overlay.request_id, state=RenderState.FAILED, detail="boom"
        ),
    }
    done = apply_renders(p, outcomes)
    rendered = done.between("c1", "c2")
    assert rendered is not None
    assert rendered.overlay is not None
    assert (rendered.overlay.asset_id, rendered.overlay.render_state) == (
        "mov-1",
        RenderState.RENDERED,
    )
    failed = done.transitions[-1]
    assert (failed.kind, failed.status, failed.overlay) == (
        TransitionKind.HARD_CUT,
        DecisionStatus.FALLBACK,
        None,
    )
    assert done.state is ResultState.PARTIAL
    assert [e.kind for e in done.events] == [TransitionKind.FADE_IN, TransitionKind.ZOOM_TUNNEL]


def test_views_read_lengths_and_words_without_probing() -> None:
    audio = timeline([[("hallo", 0.5, 0.9), ("welt", 1.0, 1.4)]])
    facts = source_facts("A", None, make_asset("A", duration_seconds=12.5), audio)
    assert (facts.duration_basis, float(facts.duration or 0)) == ("library", 12.5)
    assert facts.words_known
    assert [(float(w.start), float(w.end)) for w in facts.words] == [(0.5, 0.9), (1.0, 1.4)]
    unknown = source_facts("B", None, None, None)
    assert (unknown.duration, unknown.words_known) == (None, False)


def test_views_take_continuity_repairs_owned_by_transitions() -> None:
    analysis = jump_analysis()
    facts = continuity_facts(analysis)
    assert facts
    for (out_id, in_id), fact in facts.items():
        assert (fact.outgoing_clip, fact.incoming_clip) == (out_id, in_id)
        wanted = any(
            r.kind is RepairKind.AUDIO_CROSSFADE and r.owner is Owner.TRANSITION
            for i in analysis.issues
            for r in i.repairs
        )
        assert fact.audio_crossfade is wanted
        assert any(i.startswith(IssueType.JUMP_CUT.value) for i in fact.issues)
