"""A small labelled evaluation set and a scale check.

The set is SYNTHETIC (14 passages against a 333-shot library whose observations were written for
this test): it guards against regressions in ranking, evidence gating and rejection and measures
nothing about real footage or the real embedding models. Do not read these numbers as general
retrieval quality.
"""

import time
from dataclasses import dataclass, replace

from media_house.modules.broll_intelligence.domain.facts import ShotFact, TargetFact
from media_house.modules.broll_intelligence.domain.model import BrollAnalysis
from media_house.modules.broll_intelligence.domain.values import Support
from tests.support.broll_fakes import name, run, shot, target

K = 3


@dataclass(frozen=True)
class Case:
    """One passage, the shot ids that really show it, and what must never be recommended."""

    target: TargetFact
    relevant: frozenset[str]
    forbidden: frozenset[str] = frozenset()
    #: Nothing in the library qualifies: the right answer is a gap.
    absent: bool = False


TOPICS = [
    ("sand the board", "sanding a wooden board with sandpaper", "demonstration"),
    ("pour the concrete", "pouring concrete into a mould", "demonstration"),
    ("solder the joint", "soldering a joint on a circuit board", "demonstration"),
    ("knead the dough", "kneading bread dough on a floured table", "demonstration"),
    ("plant the seedling", "planting a seedling in garden soil", "demonstration"),
    ("tighten the bolt", "tightening a bolt with a wrench", "demonstration"),
    ("pump the tyre", "pumping air into a bicycle tyre", "demonstration"),
    ("bake the loaf", "baking a loaf of bread in an oven", "demonstration"),
]
PRODUCTS = [("Pixel 9", "Pixel 8"), ("Galaxy S24", "Galaxy S23"), ("iPhone 15", "iPhone 14")]
ABSENT = ["quantum computer wiring", "deep sea volcano vent", "medieval manuscript binding"]


def build() -> tuple[list[Case], list[ShotFact]]:
    cases: list[Case] = []
    shots: list[ShotFact] = []
    for n, (concept, description, purpose) in enumerate(TOPICS):
        right = shot(
            f"right{n}",
            "s1",
            description=description,
            events=[("gesture", 2.0)],
            static_fraction=0.2,
        )
        # the same subject without the action, and a lookalike of another job
        still = shot(f"still{n}", "s1", description=description, static_fraction=0.97)
        other = shot(
            f"other{n}", "s1", description="a person walking a dog in the park", static_fraction=0.3
        )
        shots += [right, still, other]
        cases.append(
            Case(
                target(
                    f"d{n}",
                    f"Next we {concept}.",
                    concept=concept,
                    purpose=purpose,
                    start=10.0 * n,
                    end=10.0 * n + 6,
                ),
                relevant=frozenset({right.key}),
            )
        )
    for n, (mine, theirs) in enumerate(PRODUCTS):
        text = f"The {mine} survived a drop test without a scratch."
        right = shot(
            f"mine{n}",
            "s1",
            description="a phone survives a drop test",
            texts=[mine],
            names=[(name(mine), "text")],
            events=[("motion_peak", 2.0)],
        )
        wrong = shot(
            f"theirs{n}",
            "s1",
            description="a phone survives a drop test",
            texts=[theirs],
            names=[(name(theirs), "text")],
            events=[("motion_peak", 2.0)],
        )
        generic = shot(f"generic{n}", "s1", description="a phone survives a drop test")
        shots += [right, wrong, generic]
        cases.append(
            Case(
                target(
                    f"p{n}",
                    text,
                    purpose="evidence",
                    necessity="essential",
                    concept=f"{mine} survived a drop test",
                    names=[name(mine)],
                    start=100.0 + 10 * n,
                    end=106.0 + 10 * n,
                ),
                relevant=frozenset({right.key}),
                forbidden=frozenset({wrong.key}),
            )
        )
    for n, concept in enumerate(ABSENT):
        cases.append(
            Case(
                target(
                    f"x{n}",
                    f"Here you see the {concept}.",
                    purpose="evidence",
                    necessity="essential",
                    concept=concept,
                    start=200.0 + 10 * n,
                    end=206.0 + 10 * n,
                ),
                relevant=frozenset(),
                absent=True,
            )
        )
    shots += [
        shot(f"noise{i}", "s1", description=f"an unrelated scene number {i} of a lake at dusk")
        for i in range(300)
    ]
    return cases, shots


def evaluate(analysis: BrollAnalysis, cases: list[Case]) -> dict[str, float]:
    at1 = recalled = misleading = right_gaps = duplicates = answered = 0
    reciprocal = 0.0
    present = [c for c in cases if not c.absent]
    for case in cases:
        rec = analysis.recommendation(case.target.target_id)
        assert rec is not None
        satisfying = [c for c in rec.candidates if c.support is Support.SATISFIES]
        keys = [f"{c.asset_id}#{c.shot_id}" for c in satisfying]
        if case.absent:
            right_gaps += int(
                rec.missing_coverage and analysis.gap_for(case.target.target_id) is not None
            )
            continue
        answered += 1
        at1 += int(bool(keys) and keys[0] in case.relevant)
        recalled += int(any(k in case.relevant for k in keys[:K]))
        rank = next((i for i, k in enumerate(keys, 1) if k in case.relevant), None)
        reciprocal += 1 / rank if rank else 0
        listed = [f"{c.asset_id}#{c.shot_id}" for c in rec.candidates]
        misleading += int(any(k in case.forbidden for k in listed))
        duplicates += len(listed) - len(set(listed))
    absent = [c for c in cases if c.absent]
    return {
        "precision_at_1": at1 / len(present),
        "recall_at_3": recalled / len(present),
        "mrr": reciprocal / len(present),
        "misleading_rate": misleading / len(present),
        "correct_rejection": right_gaps / len(absent),
        "duplicate_rate": duplicates / max(1, answered),
    }


def test_the_labelled_set_is_retrieved_ranked_and_rejected_correctly() -> None:
    cases, shots = build()

    started = time.perf_counter()
    analysis = run([c.target for c in cases], shots)
    seconds = time.perf_counter() - started
    metrics = evaluate(analysis, cases)

    print(f"\n{len(cases)} passages, {len(shots)} shots, {seconds:.2f}s: {metrics}")  # noqa: T201
    assert metrics["precision_at_1"] == 1.0
    assert metrics["recall_at_3"] == 1.0
    assert metrics["mrr"] == 1.0
    assert metrics["misleading_rate"] == 0.0  # the wrong model is never listed as a candidate
    assert metrics["correct_rejection"] == 1.0  # absent footage is a gap, not a forced match
    assert metrics["duplicate_rate"] == 0.0


def test_the_still_and_the_generic_are_only_ever_illustrative() -> None:
    cases, shots = build()
    analysis = run([c.target for c in cases], shots)

    for case in cases:
        rec = analysis.recommendation(case.target.target_id)
        assert rec is not None
        for c in rec.candidates:
            if f"{c.asset_id}#{c.shot_id}" not in case.relevant:
                assert c.support is not Support.SATISFIES, (case.target.target_id, c.asset_id)


def test_a_large_library_is_searched_in_seconds() -> None:
    """20 000 shots, 60 passages: words are looked up in one index, never scanned per passage."""
    cases, shots = build()
    vocabulary = ["lake", "forest", "city", "street", "kitchen", "garage", "beach", "office"]
    bulk = [
        shot(
            f"lib{i // 10}",
            f"s{i % 10}",
            description=f"{vocabulary[i % 8]} scene {i % 97} with {vocabulary[(i * 7) % 8]}",
        )
        for i in range(20_000)
    ]
    targets = [replace(cases[i % len(cases)].target, target_id=f"big{i}") for i in range(60)]

    started = time.perf_counter()
    analysis = run(targets, [*shots, *bulk])
    seconds = time.perf_counter() - started

    print(f"\n{len(targets)} passages, {len(shots) + len(bulk)} shots: {seconds:.2f}s")  # noqa: T201
    assert len(analysis.recommendations) == len(targets)
    assert seconds < 60
