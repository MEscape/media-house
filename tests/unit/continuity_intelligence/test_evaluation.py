"""The labelled evaluation set: issue precision, recall, ranking and what goes wrong."""

from dataclasses import replace

from media_house.modules.continuity_intelligence.domain.values import IssueType
from tests.support.cont_eval import cases, evaluate


def test_the_labelled_set_is_judged_as_a_careful_editor_would() -> None:
    report = evaluate()

    print(f"\n{report}")  # noqa: T201
    assert report.cases == len(cases())
    assert report.false_positives == (), report.false_positives
    assert report.missed == (), report.missed
    assert report.precision == 1.0 and report.recall == 1.0
    assert report.ranking_agreement >= 0.8
    assert report.model_calls == 0  # continuity reads stored results; it runs no model


def test_the_report_states_what_it_cannot_claim() -> None:
    assert "synthetic" in evaluate().note and "not calibrated" in evaluate().note


def test_a_wrong_expectation_shows_up_as_a_miss_not_a_pass() -> None:
    wrong = [c for c in cases() if c.name == "talking-head jump cut"]
    report = evaluate(
        [replace(c, expected=frozenset({IssueType.IDENTITY_MISMATCH})) for c in wrong]
    )

    assert report.recall == 0.0 and report.missed
