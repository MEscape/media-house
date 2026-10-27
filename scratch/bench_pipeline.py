"""Measure the pipeline on one fixed workload under comparable conditions.

    uv run python scratch/bench_pipeline.py --media DIR --work DIR [--scenarios cold,warm,...] [--repeat-warm 3]

``--media`` holds the clips to process (the same files in every scenario). Each scenario gets its
own fresh application home under ``--work``, except where the point is reuse (warm, changed), which
continue the cold home. Cold scenarios run once: they take minutes and are dominated by speech
model loading, so a second run would mostly measure the OS file cache. Warm scenarios repeat and
report min / median / max.

What is measured, and only that: wall time and per-task queue/run time (from the pipeline report),
cache decisions, and how many times each model was loaded (counted in the application log).
CPU, RAM and GPU utilisation are NOT measured here: nothing in the application reports them.

Scenarios (same inputs and stages unless the name says otherwise):
  cold        fresh home, default lanes
  warm        the cold home again: everything stored
  changed     the cold home with one clip replaced by different content
  sequential  fresh home, ``--max-workers 1``: the dependency-respecting one-at-a-time baseline
  no_align    fresh home, content and alignment stages off (optional analysis disabled)
  cpu_only    fresh home, ``--cpu-only``
"""

import argparse
import importlib.util
import json
import shutil
import statistics
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

SCENARIOS = ("cold", "warm", "changed", "sequential", "no_align", "cpu_only")
MODEL_LOAD_MARKERS = ("Loading speech model", "Loading alignment model")


def load_runner() -> ModuleType:
    path = Path(__file__).with_name("run_pipeline.py")
    spec = importlib.util.spec_from_file_location("run_pipeline_script", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def count_model_loads(home: Path) -> dict[str, int]:
    counts = dict.fromkeys(MODEL_LOAD_MARKERS, 0)
    for log in (home / "logs").glob("*.log*"):
        text = log.read_text(encoding="utf-8", errors="replace")
        for marker in MODEL_LOAD_MARKERS:
            counts[marker] += text.count(marker)
    return counts


def run_once(
    runner: ModuleType, media: Path, home: Path, report: Path, *flags: str
) -> dict[str, Any]:
    before = count_model_loads(home)
    started = time.perf_counter()
    code = runner.main(
        ["--input", str(media), "--home", str(home), "--report-json", str(report), *flags]
    )
    elapsed = time.perf_counter() - started
    data: dict[str, Any] = json.loads(report.read_text())
    after = count_model_loads(home)
    tasks = [
        t
        for a in data["assets"]
        for t in a["tasks"]
        if t["status"] not in (None, "skipped_disabled")
    ]
    return {
        "exit_code": code,
        "process_seconds": round(elapsed, 2),  # includes application start-up and shutdown
        "wall_seconds": data["wall_seconds"],  # the graph run itself
        "counts": data["counts"],
        "cache": data["cache"],
        "peak_total": data["peak_total"],
        "queue_seconds_total": round(sum(t["queued_seconds"] for t in tasks), 2),
        "run_seconds_total": round(sum(t["run_seconds"] for t in tasks), 2),
        "slowest": sorted(((t["kind"], t["run_seconds"]) for t in tasks), key=lambda kv: -kv[1])[
            :3
        ],
        "model_loads": {k: after[k] - before[k] for k in MODEL_LOAD_MARKERS},
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--media", type=Path, required=True)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--scenarios", default=",".join(SCENARIOS))
    parser.add_argument("--repeat-warm", type=int, default=3)
    parser.add_argument("--json", type=Path, help="write all results here")
    args = parser.parse_args()
    wanted = [s.strip() for s in args.scenarios.split(",") if s.strip()]
    unknown = [s for s in wanted if s not in SCENARIOS]
    if unknown:
        parser.error(f"unknown scenarios {unknown}; choose from {list(SCENARIOS)}")

    runner = load_runner()
    results: dict[str, Any] = {}
    args.work.mkdir(parents=True, exist_ok=True)
    cold_home = args.work / "home_cold"
    media = args.work / "media"
    if media.exists():
        shutil.rmtree(media)
    shutil.copytree(args.media, media)  # scenarios may replace a clip; never touch the originals

    def fresh(name: str) -> Path:
        home: Path = args.work / f"home_{name}"
        shutil.rmtree(home, ignore_errors=True)
        return home

    for name in wanted:
        report = args.work / f"report_{name}.json"
        print(f"\n##### {name}", flush=True)
        if name == "cold":
            results[name] = run_once(runner, media, fresh("cold"), report)
        elif name == "warm":
            runs = [run_once(runner, media, cold_home, report) for _ in range(args.repeat_warm)]
            walls = [r["wall_seconds"] for r in runs]
            results[name] = {
                **runs[-1],
                "wall_seconds_runs": walls,
                "wall_min_median_max": [min(walls), statistics.median(walls), max(walls)],
            }
        elif name == "changed":
            victim = sorted(media.glob("*.mp4"))[0]
            data = victim.read_bytes()
            victim.write_bytes(data + b"\0" * 64)  # different content => a different asset
            results[name] = run_once(runner, media, cold_home, report)
            results[name]["changed_file"] = victim.name
        elif name == "sequential":
            results[name] = run_once(runner, media, fresh(name), report, "--max-workers", "1")
        elif name == "no_align":
            results[name] = run_once(
                runner,
                media,
                fresh(name),
                report,
                "--stages",
                "inspect,audio_analysis,video_analysis",
            )
        elif name == "cpu_only":
            results[name] = run_once(runner, media, fresh(name), report, "--cpu-only")
        print(json.dumps(results[name], indent=2), flush=True)

    if args.json:
        args.json.write_text(json.dumps(results, indent=2, sort_keys=True), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
