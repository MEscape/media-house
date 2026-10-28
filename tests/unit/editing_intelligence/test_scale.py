"""A long recording is planned in a bounded time and survives its stored form."""

import time

from media_house.modules.editing_intelligence.domain.profile import EditConfig
from media_house.modules.editing_intelligence.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.editing_intelligence.domain.values import TrackKind
from tests.support.edit_fakes import S, Sec, edit, story, talk

SENTENCES = 3000
#: Generous: the measured time is about one second; this only catches quadratic behaviour.
LIMIT_SECONDS = 30.0


def test_three_thousand_sentences_are_planned_and_stored_without_quadratic_cost() -> None:
    rec = talk(
        [
            S(
                f"sentence number {i} says something about the topic here",
                gap=2.0 if i % 7 == 0 else 0.2,
                removal=0.9 if i % 11 == 0 else None,
            )
            for i in range(SENTENCES)
        ]
    )
    sections = [
        Sec(f"s{k}", tuple(range(k * 15, (k + 1) * 15)), "optional" if k % 3 == 0 else "essential")
        for k in range(SENTENCES // 15)
    ]
    st = story(rec, sections)

    started = time.perf_counter()
    plan = edit(st, rec, EditConfig(profile="talking_head"))
    restored = plan_from_json(plan_to_json(plan))
    elapsed = time.perf_counter() - started

    assert len(plan.decisions) >= SENTENCES
    assert plan.track(TrackKind.AUDIO)
    assert restored == plan
    assert elapsed < LIMIT_SECONDS
