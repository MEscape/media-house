"""A small labelled evaluation set for continuity, and the tool that scores it.

The cases are SYNTHETIC evidence written to describe plausible editorial situations; the report
therefore measures regressions of the detectors' rules, not perception of real footage. Nothing here
validates calibration: ``detectability_confidence`` and the severity scores are ordinal rankings.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field

from media_house.modules.continuity_intelligence.domain.facts import AudioEdge, TransitionFact
from media_house.modules.continuity_intelligence.domain.values import IssueType, TransitionKind
from tests.support.cont_fakes import edge, name, run, secs, side, subject, transition

NOTE = (
    "synthetic evidence only: rule regressions, not perception; confidence and severity are "
    "ordinal rankings and are not calibrated"
)


@dataclass(frozen=True)
class Case:
    name: str
    transition: TransitionFact
    profile: str
    #: The issue types a careful editor would want surfaced (empty = leave the cut alone).
    expected: frozenset[IssueType] = frozenset()
    #: 0 none .. 3 severe: only used to rank cases against each other.
    severity: int = 0


@dataclass(frozen=True)
class Report:
    cases: int
    precision: float
    recall: float
    false_positives: tuple[tuple[str, str], ...]
    missed: tuple[tuple[str, str], ...]
    ranking_agreement: float
    seconds: float
    model_calls: int = 0
    note: str = NOTE
    by_type: dict[str, tuple[int, int, int]] = field(default_factory=dict)


def _cut(
    a_edge: object,
    b_edge: object,
    *,
    a_audio: AudioEdge | None = None,
    b_audio: AudioEdge | None = None,
    **extra: object,
) -> TransitionFact:
    a = side("c1", edge_=a_edge, audio=a_audio)  # type: ignore[arg-type]
    b = side("c2", outgoing=False, start=12.0, end=20.0, edge_=b_edge, audio=b_audio)  # type: ignore[arg-type]
    return transition(a=a, b=b, gap=2.0, **extra)  # type: ignore[arg-type]


def cases() -> list[Case]:
    jump = _cut(
        edge(subject=subject(cx=0.4)), edge(subject=subject(cx=0.52)), kind=TransitionKind.SAME_SHOT
    )
    return [
        Case("talking-head jump cut", jump, "talking_head", frozenset(), 1),
        Case(
            "the same jump cut in a cinematic dialogue",
            jump,
            "cinematic_dialogue",
            frozenset({IssueType.JUMP_CUT}),
            2,
        ),
        Case(
            "screen direction reversed",
            _cut(edge(subject=subject(vx=0.25, vy=0.0)), edge(subject=subject(vx=-0.25, vy=0.0))),
            "cinematic_dialogue",
            frozenset({IssueType.SCREEN_DIRECTION, IssueType.ACTION_CONTINUITY}),
            3,
        ),
        Case(
            "travel continues",
            _cut(
                edge(subject=subject(cx=0.3, vx=0.2, vy=0.0)),
                edge(subject=subject(cx=0.62, vx=0.2, vy=0.0)),
            ),
            "cinematic_dialogue",
            frozenset(),
        ),
        Case(
            "two product models",
            _cut(edge(names=(name("Pixel 9"),)), edge(names=(name("Pixel 8"),))),
            "product_review",
            frozenset({IssueType.IDENTITY_MISMATCH}),
            3,
        ),
        Case(
            "same product model",
            _cut(edge(names=(name("Pixel 9"),)), edge(names=(name("Pixel 9"),))),
            "product_review",
            frozenset(),
        ),
        Case(
            "unexplained exposure step",
            _cut(edge(luma=0.3), edge(luma=0.7)),
            "generic",
            frozenset({IssueType.LIGHTING_MISMATCH}),
            2,
        ),
        Case("matching exposure", _cut(edge(luma=0.5), edge(luma=0.52)), "generic", frozenset()),
        Case(
            "word cut through",
            _cut(
                edge(),
                edge(),
                a_audio=AudioEdge(
                    straddled_word="battery", word_edge_distance=0.03, speech_near=True
                ),
            ),
            "interview_podcast",
            frozenset({IssueType.CLIPPED_SPEECH}),
            3,
        ),
        Case(
            "cut at the edge of a word",
            _cut(
                edge(),
                edge(),
                a_audio=AudioEdge(
                    straddled_word="battery", word_edge_distance=0.2, speech_near=True
                ),
            ),
            "interview_podcast",
            frozenset(),
        ),
        Case(
            "level and floor step",
            _cut(
                edge(),
                edge(),
                a_audio=AudioEdge(level_db=-20.0, floor_db=-60.0, speech_near=True),
                b_audio=AudioEdge(level_db=-34.0, floor_db=-42.0, speech_near=True),
            ),
            "interview_podcast",
            frozenset({IssueType.AUDIO_DISCONTINUITY}),
            2,
        ),
        Case(
            "matching sound",
            _cut(
                edge(),
                edge(),
                a_audio=AudioEdge(level_db=-20.0, floor_db=-60.0),
                b_audio=AudioEdge(level_db=-21.0, floor_db=-59.0),
            ),
            "interview_podcast",
            frozenset(),
        ),
        Case(
            "montage contrast",
            _cut(
                edge(luma=0.2, embedding=(1.0, 0.0), embedding_model="m", environment="studio"),
                edge(luma=0.8, embedding=(0.0, 1.0), embedding_model="m", environment="street"),
                kind=TransitionKind.CROSS_RECORDING,
            ),
            "fast_montage",
            frozenset(),
        ),
        Case(
            "flashback reorder",
            transition(
                a=side("c1", end=60.0),
                b=side("c2", outgoing=False, start=10.0, end=20.0),
                reordered=True,
                section_change=True,
                cold_open=True,
            ),
            "documentary",
            frozenset(),
        ),
        Case(
            "unexplained backwards jump",
            transition(
                a=side("c1", end=60.0),
                b=side("c2", outgoing=False, start=10.0, end=20.0),
                reordered=True,
            ),
            "tutorial",
            frozenset({IssueType.TEMPORAL_INCONSISTENCY}),
            2,
        ),
        Case(
            "covered jump cut",
            _cut(
                edge(subject=subject(cx=0.4)),
                edge(subject=subject(cx=0.55)),
                kind=TransitionKind.SAME_SHOT,
                covered=True,
            ),
            "cinematic_dialogue",
            frozenset(),
        ),
        Case(
            "skipped action",
            _cut(edge(subject=subject(cx=0.2, vx=0.2, vy=0.0)), edge(subject=subject(cx=0.9))),
            "tutorial",
            frozenset({IssueType.ACTION_CONTINUITY}),
            2,
        ),
        Case(
            "no evidence at all",
            transition(
                a=side("c1", edge_=None),
                b=side("c2", outgoing=False, start=12.0, end=20.0, edge_=None),
                gap=2.0,
            ),
            "generic",
            frozenset(),
        ),
    ]


def evaluate(selected: Sequence[Case] | None = None) -> Report:
    chosen = list(selected or cases())
    started = time.perf_counter()
    tp = fp = fn = 0
    false_positives: list[tuple[str, str]] = []
    missed: list[tuple[str, str]] = []
    by_type: dict[str, list[int]] = {}
    peaks: list[tuple[int, float]] = []
    for case in chosen:
        analysis = run([case.transition], profile=case.profile)
        found = {i.type for i in analysis.surfaced()}
        for kind in found & case.expected:
            tp += 1
            by_type.setdefault(kind.value, [0, 0, 0])[0] += 1
        for kind in found - case.expected:
            fp += 1
            false_positives.append((case.name, kind.value))
            by_type.setdefault(kind.value, [0, 0, 0])[1] += 1
        for kind in case.expected - found:
            fn += 1
            missed.append((case.name, kind.value))
            by_type.setdefault(kind.value, [0, 0, 0])[2] += 1
        top = max((i.peak or 0.0 for i in analysis.surfaced()), default=0.0)
        peaks.append((case.severity, top))
    seconds = time.perf_counter() - started
    agree = total = 0
    for a in range(len(peaks)):
        for b in range(a + 1, len(peaks)):
            if peaks[a][0] != peaks[b][0]:
                total += 1
                higher = a if peaks[a][0] > peaks[b][0] else b
                lower = b if higher == a else a
                agree += int(peaks[higher][1] > peaks[lower][1])
    return Report(
        cases=len(chosen),
        precision=tp / (tp + fp) if tp + fp else 1.0,
        recall=tp / (tp + fn) if tp + fn else 1.0,
        false_positives=tuple(false_positives),
        missed=tuple(missed),
        ranking_agreement=agree / total if total else 1.0,
        seconds=seconds,
        by_type={k: (v[0], v[1], v[2]) for k, v in by_type.items()},
    )


__all__ = ["Case", "Report", "cases", "evaluate", "secs"]
