"""The opt-in evaluation script: its scoring, and a smoke run on SYNTHETIC clips.

The smoke run proves the script works end to end. It is not a measurement of the detectors: the
clips are generated, the truth is written by the test, and the report says so.
"""

import importlib.util
import json
import shutil
from pathlib import Path
from types import ModuleType

import pytest

from tests.support.media_factories import ffmpeg

SCRIPT = Path(__file__).resolve().parents[3] / "scratch" / "evaluate_relationships.py"


@pytest.fixture(scope="module")
def evaluation() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_relationships", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scoring_counts_each_detector_against_the_labels(evaluation: ModuleType) -> None:
    truth = [
        {"a": "s1", "b": "s2", "relationships": ["temporally_overlaps_with"]},  # found
        {"a": "s1", "b": "s3", "relationships": ["temporally_overlaps_with"]},  # missed
        {"a": "s2", "b": "s3", "relationships": []},  # wrongly proposed
        {"a": "s3", "b": "s4", "relationships": []},  # correctly left alone
        {"a": "s4", "b": "s5", "relationships": None},  # uncertain: never scored
        {"a": "s1", "b": "s5", "relationships": ["alternate_take_of"]},  # found by the takes
    ]
    predicted = {
        "wall_clock_overlap": {("s1", "s2"), ("s2", "s3")},
        "production_tag_takes": {("s1", "s5")},
    }

    scored = evaluation.score(truth, predicted)

    overlap, takes = scored["wall_clock_overlap"], scored["production_tag_takes"]
    assert (overlap.tp, overlap.fn, overlap.fp, overlap.tn, overlap.inconclusive) == (1, 1, 1, 2, 1)
    assert overlap.precision() == 0.5 and overlap.recall() == 0.5
    assert (takes.tp, takes.fp, takes.fn) == (1, 0, 0)
    assert not overlap.meaningful and not takes.meaningful  # six cases prove nothing


def test_a_pair_is_the_same_pair_in_either_order(evaluation: ModuleType) -> None:
    assert evaluation.pair_key("b", "a") == evaluation.pair_key("a", "b") == ("a", "b")


def test_the_figures_are_only_meaningful_with_enough_of_each_class(evaluation: ModuleType) -> None:
    enough = evaluation.Tally(tp=20, fn=0, fp=0, tn=20)
    too_few = evaluation.Tally(tp=19, fn=0, fp=0, tn=100)

    assert enough.meaningful and not too_few.meaningful


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="FFmpeg is required")
def test_the_script_runs_end_to_end_and_says_when_the_truth_is_synthetic(
    evaluation: ModuleType, tmp_path: Path
) -> None:
    media = tmp_path / "media"
    media.mkdir()
    for name, start, take in (
        ("a.mkv", "05:00:00", "1"),
        ("b.mkv", "05:00:02", "2"),
        ("c.mkv", "09:00:00", "3"),
    ):
        ffmpeg(
            "-f", "lavfi", "-i", "testsrc=duration=6:size=160x120:rate=25",
            "-metadata", f"creation_time=2026-03-04T{start}Z", "-metadata", "scene=12A",
            "-metadata", f"take={take}", "-metadata", "device=Cam One",
            "-c:v", "libx264", "-pix_fmt", "yuv420p", str(media / name),
        )  # fmt: skip
    truth = tmp_path / "truth.json"
    truth.write_text(
        json.dumps(
            {
                "source": "synthetic",
                "samples": {"s1": "a.mkv", "s2": "b.mkv", "s3": "c.mkv"},
                "pairs": [
                    {
                        "a": "s1",
                        "b": "s2",
                        "relationships": ["temporally_overlaps_with", "alternate_take_of"],
                        "how": "generated",
                    },
                    {
                        "a": "s1",
                        "b": "s3",
                        "relationships": ["alternate_take_of"],
                        "how": "generated",
                    },
                    {"a": "s2", "b": "s3", "relationships": None, "how": "undecided"},
                ],
            }
        ),
        encoding="utf-8",
    )
    out = tmp_path / "report.json"

    code = evaluation.main(
        [
            "--media",
            str(media),
            "--truth",
            str(truth),
            "--out",
            str(out),
            "--home",
            str(tmp_path / "home"),
        ]
    )

    report = json.loads(out.read_text(encoding="utf-8"))
    assert code == 0 and report["ground_truth_source"] == "synthetic"
    assert report["problems"] == [] and report["samples"] == 3
    verdicts = {tuple(c["pair"]): c["verdict"] for c in report["cases"]}
    assert verdicts[("s1", "s2")] == "correct" and verdicts[("s2", "s3")] == "inconclusive"
    # the takes detector found s1-s3 too (same scene, other take, same device), as labelled
    assert verdicts[("s1", "s3")] == "correct"
    assert all(not d["statistically_meaningful"] for d in report["detectors"].values())
    assert {d["status"] for c in report["cases"] for d in c["observed"]} <= {"proposed"}
    assert (media / "a.mkv").exists()  # the originals are still there, untouched
