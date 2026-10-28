"""A long recording is analysed in a bounded time and survives its stored form."""

import time

from media_house.modules.pacing_intelligence.domain.profile import PacingConfig
from media_house.modules.pacing_intelligence.domain.serialization import (
    analysis_from_json,
    analysis_to_json,
)
from tests.support.pace_fakes import Sec, T, analyse, shots, speak, tell

SENTENCES = 3000
#: Generous: the measured time is a few seconds; this only catches quadratic behaviour.
LIMIT_SECONDS = 60.0


def test_three_thousand_sentences_are_analysed_and_stored_without_quadratic_cost() -> None:
    evidence = speak(
        [
            T(
                f"sentence number {i} says something about the topic here",
                gap=2.0 if i % 7 == 0 else 0.2,
                density=0.8 if i % 5 == 0 else 0.3,
            )
            for i in range(SENTENCES)
        ]
    )
    total = evidence.source.duration or 0.0
    cuts = [(float(t), float(min(total, t + 5))) for t in range(0, int(total), 5)]
    evidence = shots(evidence, cuts, static=0.4)
    story = tell(
        evidence,
        [
            Sec(f"s{k}", tuple(range(k * 15, (k + 1) * 15)), "supporting", seconds=45.0)
            for k in range(SENTENCES // 15)
        ],
    )

    started = time.perf_counter()
    result = analyse(evidence, PacingConfig(profile="talking_head"), story=story)
    restored = analysis_from_json(analysis_to_json(result))
    elapsed = time.perf_counter() - started

    assert len(result.passages) == SENTENCES and result.recommendations
    assert restored == result
    assert elapsed < LIMIT_SECONDS
