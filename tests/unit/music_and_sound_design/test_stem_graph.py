"""The stem filter graph carries the plan's exact cuts, gains, fades and ducking (no mastering)."""

from media_house.modules.music_and_sound_design.application.ports import StemOptions
from media_house.modules.music_and_sound_design.domain.library import SoundIndex
from media_house.modules.music_and_sound_design.domain.model import SoundDesignPlan
from media_house.modules.music_and_sound_design.domain.planner import (
    PlanInputs,
    PlanMeta,
    plan_sound_design,
)
from media_house.modules.music_and_sound_design.domain.profile import SoundDesignConfig
from media_house.modules.music_and_sound_design.domain.values import EventSource
from media_house.modules.music_and_sound_design.infrastructure.stem_renderer import (
    build_graph,
    ducking_expression,
)
from tests.support.fakes import T0
from tests.support.sound_fakes import event, music, section, sfx, speech, timeline


def _plan() -> SoundDesignPlan:
    index = SoundIndex([music("song", ("upbeat_positive",)), sfx("hit", ("impact",))])
    soundtrack = timeline(
        duration=30.0,
        sections=(section("a", 0, 30, "context"),),
        speech_spans=(speech(5, 10),),
        events=(event("flash", 20, "flash", source=EventSource.TRANSITION),),
    )
    config = SoundDesignConfig(overrides={"music.categories.body": ["upbeat_positive"]})
    return plan_sound_design(
        PlanInputs(timeline=soundtrack, index=index), config, config.resolve(), now=T0,
        meta=PlanMeta(),
    )  # fmt: skip


def test_graph_reads_each_item_sample_exactly_and_never_limits() -> None:
    plan = _plan()

    inputs, graph = build_graph(plan, StemOptions())

    assert inputs == ["song", "hit"]
    assert "atrim=start_sample=" in graph and "adelay=delays=" in graph
    assert "normalize=0" in graph
    assert f"apad=whole_len={30 * 48_000},atrim=end_sample={30 * 48_000}[out]" in graph
    for forbidden in ("loudnorm", "alimiter", "acompressor", "dynaudnorm"):
        assert forbidden not in graph
    assert "volume=volume='min(" not in graph  # one speech range: a single factor
    assert "volume=volume='(1-" in graph


def test_ducking_can_be_left_as_intent_only() -> None:
    plan = _plan()

    _, graph = build_graph(plan, StemOptions(include_ducking=False))

    assert "volume=volume='1':eval=frame" in graph


def test_the_deepest_duck_wins_where_ranges_overlap() -> None:
    plan = _plan()
    duck = plan.ducking[0]

    expression = ducking_expression([duck, duck])

    assert expression.startswith("min(")
    assert expression.count("clip(") == 2


def test_many_ducks_stay_within_ffmpegs_expression_depth() -> None:
    # E2E regression: 114 speech ducks of a 2-minute ranking excerpt nested 114 min() calls;
    # FFmpeg refused the expression ("Invalid argument") and no stem was rendered.
    duck = _plan().ducking[0]

    expression = ducking_expression([duck] * 114)

    depth = deepest = 0
    for ch in expression:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        deepest = max(deepest, depth)
    assert expression.count("clip(") == 114
    assert deepest < 20
