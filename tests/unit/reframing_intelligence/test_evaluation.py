"""The synthetic evaluation: plans keep their word, crops are valid, results are stable.

The cases were written with the rules, so a perfect score guards regressions and says nothing about
real footage, a real tracker or how a picture looks (see ``tests/support/refr_eval.py``).
"""

from tests.support.refr_eval import evaluate, format_report


def test_every_labelled_case_reaches_its_outcome_with_valid_stable_crops() -> None:
    report = evaluate()

    assert report.agreement == 1.0, format_report(report)
    assert report.valid == 1.0 and report.stability >= 0.9, format_report(report)


def test_no_executable_crop_clips_what_it_calls_required_and_speed_stays_within_the_limit() -> None:
    for row in evaluate().rows:
        if row.min_inside is not None:
            assert row.min_inside >= 0.999, row
        if row.peak_speed is not None:
            assert row.peak_speed <= 0.4, row  # the profiles' limits are 0.2-0.5 crop widths/s


def test_the_report_names_what_it_cannot_claim() -> None:
    assert "SYNTHETIC" in format_report(evaluate())
