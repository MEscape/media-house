"""Measure the relationship detectors against YOUR ground truth. Opt-in, local, read-only on media.

    uv run python scratch/evaluate_relationships.py --media DIR --truth truth.json [--out report.json]

``truth.json`` (keep it next to the footage, not in the repository):

    {
      "source": "real",                       // "real" or "synthetic": printed on the report
      "samples": {"s01": "cam_a/C0001.MP4", "s02": "cam_b/C0044.MP4"},   // id -> path under --media
      "pairs": [
        {"a": "s01", "b": "s02", "relationships": ["temporally_overlaps_with"],
         "how": "filmed together, clap at 00:03"},
        {"a": "s01", "b": "s03", "relationships": [], "how": "different days"},
        {"a": "s02", "b": "s03", "relationships": null, "how": "cannot tell"}   // inconclusive
      ]
    }

``relationships`` lists every type the pair truly has (``[]`` = related by none of them, ``null`` =
the truth is uncertain: reported, never scored). A pair you do not list is not scored.

What it does: imports the footage into a THROWAWAY library (``--home``, default a temp directory
deleted afterwards; importing COPIES the bytes there, the originals are never touched), inspects each
file, runs both detectors, reads the claims back and compares them with the truth. Nothing leaves
the machine, no model runs, and nothing is confirmed: detected claims stay ``proposed``.

Read the numbers with care. Below 20 labelled positives and 20 labelled negatives per detector the
report says the figures are not statistically meaningful, and it does not turn that into advice:
the defaults change only on evidence, never on a handful of examples.
"""

import argparse
import json
import shutil
import sys
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

DETECTOR_TYPES: Mapping[str, str] = {
    "wall_clock_overlap": "temporally_overlaps_with",
    "production_tag_takes": "alternate_take_of",
}
MINIMUM_PER_CLASS = 20


@dataclass
class Tally:
    tp: int = 0
    fp: int = 0
    fn: int = 0
    tn: int = 0
    inconclusive: int = 0
    wrong: list[str] = field(default_factory=list)

    @property
    def positives(self) -> int:
        return self.tp + self.fn

    @property
    def negatives(self) -> int:
        return self.fp + self.tn

    @property
    def meaningful(self) -> bool:
        return self.positives >= MINIMUM_PER_CLASS and self.negatives >= MINIMUM_PER_CLASS

    def precision(self) -> float | None:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else None

    def recall(self) -> float | None:
        return self.tp / self.positives if self.positives else None

    def as_dict(self) -> dict[str, object]:
        return {
            "true_positives": self.tp,
            "false_positives": self.fp,
            "false_negatives": self.fn,
            "true_negatives": self.tn,
            "inconclusive": self.inconclusive,
            "precision": self.precision(),
            "recall": self.recall(),
            "statistically_meaningful": self.meaningful,
            "mistakes": self.wrong,
        }


def pair_key(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a <= b else (b, a)


def score(
    truth: Iterable[Mapping[str, object]],
    predicted: Mapping[str, set[tuple[str, str]]],
) -> dict[str, Tally]:
    """Per detector: compare ``predicted`` pairs with the labelled pairs. Pure.

    ``predicted[detector]`` is the set of (sorted) sample-id pairs the detector proposed.
    """
    tallies = {name: Tally() for name in DETECTOR_TYPES}
    for case in truth:
        key = pair_key(str(case["a"]), str(case["b"]))
        labels = case.get("relationships")
        for name, relationship in DETECTOR_TYPES.items():
            tally = tallies[name]
            if labels is None:
                tally.inconclusive += 1
                continue
            truly = relationship in list(labels)  # type: ignore[call-overload]
            found = key in predicted.get(name, set())
            if truly and found:
                tally.tp += 1
            elif truly:
                tally.fn += 1
                tally.wrong.append(f"{key[0]}-{key[1]}: missed")
            elif found:
                tally.fp += 1
                tally.wrong.append(f"{key[0]}-{key[1]}: proposed but not related")
            else:
                tally.tn += 1
    return tallies


def run(media: Path, truth_file: Path, home: Path) -> dict[str, object]:
    from media_house.bootstrap.application import Application, StartupOptions
    from media_house.modules.asset_relationships.application.contracts import (
        DETECTOR_IDS,
        DetectCommand,
        DetectStatus,
        RelationshipCatalog,
        RelationshipDetection,
        RelationshipQuery,
    )
    from media_house.modules.media_inspection.application.contracts import (
        InspectMediaCommand,
        MediaInspector,
    )
    from media_house.modules.media_library.application.contracts import MediaLibrary
    from media_house.shared.concurrency import JobContext
    from media_house.shared.configuration import Environment
    from media_house.shared.errors import Ok

    truth = json.loads(truth_file.read_text(encoding="utf-8"))
    samples: dict[str, str] = truth["samples"]
    ctx = JobContext.detached()
    app = Application.start(StartupOptions(env={}, home=home, environment=Environment.DEVELOPMENT))
    try:
        library = app.container.resolve(MediaLibrary)
        inspector = app.container.resolve(MediaInspector)
        asset_of: dict[str, str] = {}
        problems: list[str] = []
        for sample, relative in sorted(samples.items()):
            imported = library.import_file(media / relative)
            if not isinstance(imported, Ok):
                problems.append(f"{sample}: not imported")
                continue
            asset_of[sample] = imported.value.asset.id
            inspected = inspector.execute(InspectMediaCommand(asset_of[sample]), ctx)
            if not isinstance(inspected, Ok):
                problems.append(f"{sample}: not inspected")
        detection = app.container.resolve(RelationshipDetection)
        status = None
        for _ in range(1000):
            done = detection.detect(DetectCommand(frozenset(DETECTOR_IDS), full=True), ctx)
            if not isinstance(done, Ok):
                raise SystemExit(f"detection refused: {done.error}")
            status = done.value.status
            if status is not DetectStatus.PARTIAL:
                break
        catalog = app.container.resolve(RelationshipCatalog)
        sample_of = {asset: sample for sample, asset in asset_of.items()}
        predicted: dict[str, set[tuple[str, str]]] = {n: set() for n in DETECTOR_TYPES}
        details: dict[tuple[str, str], list[dict[str, object]]] = {}
        for asset in asset_of.values():
            page = catalog.relationships_of(asset, RelationshipQuery(include_projected=False))
            if not isinstance(page, Ok):
                problems.append(f"claims of {sample_of[asset]} unreadable")
                continue
            for claim in page.value.items:
                a, b = sample_of.get(claim.source_asset_id), sample_of.get(claim.target_asset_id)
                if a is None or b is None:
                    continue
                key = pair_key(a, b)
                predicted.setdefault(claim.basis_detector, set()).add(key)
                details.setdefault(key, []).append(
                    {
                        "detector": claim.basis_detector,
                        "type": claim.type.value,
                        "status": claim.status.value if claim.status else None,
                        "grade": claim.grade.value,
                        "evidence": [e.kind.value for e in claim.evidence if not e.superseded],
                    }
                )
    finally:
        app.shutdown()
    tallies = score(truth["pairs"], predicted)
    cases = []
    for case in truth["pairs"]:
        key = pair_key(case["a"], case["b"])
        labels = case.get("relationships")
        found = details.get(key, [])
        if labels is None:
            verdict = "inconclusive"
        else:
            expected = set(labels) & set(DETECTOR_TYPES.values())
            got = {str(d["type"]) for d in found}
            verdict = "correct" if got == expected else "incorrect"
        cases.append(
            {
                "pair": list(key),
                "ground_truth": labels,
                "how_established": case.get("how", ""),
                "observed": found,
                "verdict": verdict,
            }
        )
    return {
        "ground_truth_source": truth.get("source", "unspecified"),
        "samples": len(samples),
        "labelled_pairs": len(truth["pairs"]),
        "walk_status": None if status is None else status.value,
        "problems": problems,
        "detectors": {name: t.as_dict() for name, t in tallies.items()},
        "cases": cases,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "--media", type=Path, required=True, help="folder the sample paths are under"
    )
    parser.add_argument("--truth", type=Path, required=True, help="the ground-truth JSON file")
    parser.add_argument("--out", type=Path, help="write the full report here (JSON)")
    parser.add_argument(
        "--home", type=Path, help="throwaway library location (default: a temp dir)"
    )
    args = parser.parse_args(argv)
    home = args.home or Path(tempfile.mkdtemp(prefix="relationship-eval-"))
    try:
        report = run(args.media, args.truth, home)
    finally:
        if args.home is None:
            shutil.rmtree(home, ignore_errors=True)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    print(text)
    if report["ground_truth_source"] != "real":
        print(
            '\nNOTE: the ground truth is not marked "real": this is not real-world validation.',
            file=sys.stderr,
        )
    for name, stats in report["detectors"].items():  # type: ignore[attr-defined]
        if not stats["statistically_meaningful"]:
            print(
                f"NOTE: {name}: too few labelled cases for meaningful precision/recall.",
                file=sys.stderr,
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
