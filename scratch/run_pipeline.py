"""Run the media pipeline on real files and print what happened.

    uv run python scratch/run_pipeline.py --input clip.mp4
    uv run python scratch/run_pipeline.py --input footage_dir --stages inspect,audio_analysis
    uv run python scratch/run_pipeline.py --input clip.mp4 --dry-run
    uv run python scratch/run_pipeline.py --input clip.mp4 --all --output out/ -v
    uv run python scratch/run_pipeline.py --input clip.mp4 --config pipeline.toml --cpu-only

Everything runs through the real modules and the real Media Library under ``--home`` (default
``~/.media_house_scratch/pipeline``). Running the same input again reports ``reused``: the owners
stored their results in the library the first time. A dry run imports nothing and writes nothing.

Exit codes: 0 ok; 1 a task or input failed; 2 bad arguments or configuration; 130 cancelled
(Ctrl+C once = finish cleanly, twice = stop now).

Not offered on purpose: ``--force`` / ``--no-cache``. The owning modules expose no common way to
recompute, so a flag here could only pretend. Use a fresh ``--home`` to recompute everything.

A configuration file (``--config``, TOML or JSON) may set: ``stages``, ``content_uses_video``,
``cpu_only``, ``gpu_slots``, ``cpu_slots``, ``light_slots``, ``video_analysis_profile``,
``proxy_profile``, ``audio_improvement_profile``, ``video_source_profile``,
``video_processing_profile``, ``story_format``, ``story_llm``, ``edit_profile``,
``edit_target_seconds``, ``edit_target_hard``, ``pacing_profile``, ``pacing_strategy``,
``reframing_target``, ``reframing_resolution``, ``reframing_profile``, ``reframing_safe_areas``.
Command-line options win over the file.
"""

import argparse
import json
import os
import signal
import sys
import threading
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError

from media_house.bootstrap.application import Application, StartupOptions
from media_house.modules.media_pipeline.application.contracts import (
    DEFAULT_STAGES,
    AssetReport,
    Lane,
    MediaPipeline,
    PipelineReport,
    RunPipelineCommand,
    StageSettings,
    TaskKind,
    TaskRecord,
    TaskReport,
    TaskStatus,
)
from media_house.modules.reframing_intelligence.application.contracts import SafeArea
from media_house.shared.concurrency import CancellationToken, JobContext, JobProgress
from media_house.shared.configuration import Environment
from media_house.shared.errors import Err

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_CANCELLED = 0, 1, 2, 130
MEDIA_SUFFIXES = frozenset(
    {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".mxf", ".mts", ".wav", ".mp3", ".flac"}
    | {".m4a", ".aac", ".ogg", ".opus"}
)
SYMBOL = {
    TaskStatus.SUCCEEDED: "+",
    TaskStatus.REUSED: "=",
    TaskStatus.PARTIAL: "~",
    TaskStatus.FAILED: "x",
    TaskStatus.SKIPPED_DISABLED: "-",
    TaskStatus.SKIPPED_DEPENDENCY: "!",
    TaskStatus.SKIPPED_UNAVAILABLE: "o",
    TaskStatus.CANCELLED: "#",
}
LEGEND = (
    "+ computed   = reused from library   ~ partial   x failed   ! skipped (dependency failed)\n"
    "  o not applicable / capability missing   - not requested   # cancelled"
)


class RunConfig(BaseModel):
    """The configuration file. Unknown keys are errors, so a typo cannot silently do nothing."""

    model_config = ConfigDict(extra="forbid")

    stages: list[TaskKind] | None = None
    content_uses_video: bool | None = None
    cpu_only: bool | None = None
    gpu_slots: PositiveInt | None = None
    cpu_slots: PositiveInt | None = None
    light_slots: PositiveInt | None = None
    video_analysis_profile: str | None = None
    proxy_profile: str | None = None
    audio_improvement_profile: str | None = None
    video_source_profile: str | None = None
    video_processing_profile: str | None = None
    story_format: str | None = None
    story_llm: bool | None = None
    edit_profile: str | None = None
    edit_target_seconds: float | None = None
    edit_target_hard: bool | None = None
    pacing_profile: str | None = None
    pacing_strategy: str | None = None
    broll_profile: str | None = None
    continuity_profile: str | None = None
    continuity_strictness: float | None = None
    reframing_target: str | None = None
    reframing_resolution: str | None = None
    reframing_profile: str | None = None
    reframing_safe_areas: list[str] | None = None


class ConsoleObserver:
    """Live progress lines. Calls arrive from several threads, so printing is serialised."""

    def __init__(self, verbose: bool) -> None:
        self._verbose = verbose
        self._lock = threading.Lock()

    def task_started(self, task_id: object, lane: Lane) -> None:
        self._say(f"  > start   {task_id}  [{lane.value}]")

    def task_progress(self, task_id: object, progress: JobProgress) -> None:
        if self._verbose and progress.message:
            self._say(f"      {task_id}: {progress.message}")

    def task_finished(self, record: TaskRecord) -> None:
        status = record.result.status
        via = " (pre-flight)" if record.preflight_hit else ""
        self._say(
            f"  {SYMBOL[status]} {status.value:<19} {record.id}  {record.run_seconds:6.1f}s{via}"
            + (f"  {record.result.detail}" if record.result.detail else "")
        )

    def _say(self, text: str) -> None:
        with self._lock:
            print(text, flush=True)


def parse_stages(text: str) -> list[TaskKind]:
    names = [n.strip() for n in text.split(",") if n.strip()]
    try:
        return [TaskKind(n) for n in names]
    except ValueError as exc:
        valid = ", ".join(k.value for k in TaskKind)
        raise argparse.ArgumentTypeError(f"{exc}. Valid stages: {valid}") from exc


def positive(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{text!r} is not a whole number") from exc
    if value < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return value


def load_config(path: Path | None) -> RunConfig:
    if path is None:
        return RunConfig()
    try:
        raw = path.read_bytes()
        data: Any = tomllib.loads(raw.decode()) if path.suffix == ".toml" else json.loads(raw)
        return RunConfig.model_validate(data)
    except (OSError, ValueError, ValidationError) as exc:  # tomllib/json errors are ValueErrors
        raise argparse.ArgumentTypeError(f"configuration {path}: {exc}") from exc


def collect_inputs(inputs: Sequence[Path]) -> tuple[list[Path], list[str]]:
    files: list[Path] = []
    problems: list[str] = []
    for item in inputs:
        if item.is_dir():
            found = sorted(p for p in item.rglob("*") if p.suffix.lower() in MEDIA_SUFFIXES)
            if not found:
                problems.append(f"{item}: no media files found")
            files.extend(found)
        elif item.is_file():
            files.append(item)
        else:
            problems.append(f"{item}: does not exist")
    return files, problems


def write_json_atomically(path: Path, data: object) -> None:
    """Write beside the target, then rename: a reader never sees half a report."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the Media-House analysis pipeline.")
    add = parser.add_argument
    add(
        "--input",
        "-i",
        action="append",
        type=Path,
        default=[],
        help="file or directory (repeatable)",
    )
    add("--asset-id", action="append", default=[], help="asset already in the library (repeatable)")
    add(
        "--home",
        type=Path,
        default=Path.home() / ".media_house_scratch" / "pipeline",
        help="application home: library, caches, logs",
    )
    add("--output", "-o", type=Path, help="directory for the JSON report (report.json)")
    add("--report-json", type=Path, help="exact path of the JSON report (wins over --output)")
    add("--config", type=Path, help="TOML or JSON settings file")
    add(
        "--stages",
        type=parse_stages,
        help="comma separated; default: " + ",".join(sorted(k.value for k in DEFAULT_STAGES)),
    )
    add("--all", action="store_true", help="every stage, including proxy and improvement")
    add(
        "--content-uses-video",
        action="store_true",
        default=None,
        help="content analysis waits for video analysis and uses it",
    )
    add(
        "--cpu-only",
        action="store_true",
        default=None,
        help="never use the GPU (audio, video and content models run on the CPU)",
    )
    add(
        "--story-format",
        help="story format to plan for (generic, review, tutorial, interview, short ...)",
    )
    add(
        "--story-llm",
        action="store_true",
        default=None,
        help="let a local language model reword the story plan (grounded; off = deterministic)",
    )
    add(
        "--edit-profile",
        help="editing profile for --stages edit_plan (talking_head, tutorial, documentary, short ...)",
    )
    add(
        "--edit-target",
        dest="edit_target_seconds",
        type=float,
        help="target length of the edit in seconds (an upper bound; a preference unless --edit-target-hard)",
    )
    add(
        "--pacing-profile",
        help="pacing profile (talking_head, tutorial, documentary, shorts_reels ...); default: by story format",
    )
    add(
        "--broll-profile",
        help="B-roll profile for --stages broll (tutorial, documentary, product_review ...); default: by story format",
    )
    add(
        "--continuity-profile",
        help="continuity profile for --stages continuity (cinematic_dialogue, talking_head ...)",
    )
    add(
        "--continuity-strictness",
        type=float,
        help="0.5 lenient .. 1.5 strict (default 1.0)",
    )
    add(
        "--reframe-target",
        dest="reframing_target",
        help="output shape for --stages reframing: 9:16, 16:9, 1:1 or any W:H (no default: a crop "
        "means nothing without one)",
    )
    add(
        "--reframe-resolution",
        dest="reframing_resolution",
        help="output size in pixels, WIDTHxHEIGHT (default: the standard size of the target)",
    )
    add(
        "--reframe-profile",
        dest="reframing_profile",
        help="reframing profile (talking_head, interview_podcast, shorts_reels, cinematic ...); "
        "default: by story format",
    )
    add(
        "--reframe-safe-area",
        dest="reframing_safe_areas",
        action="append",
        help="a reserved rectangle of the OUTPUT picture, NAME=x0,y0,x1,y1 in 0-1 (repeatable; "
        "none is assumed)",
    )
    add(
        "--pacing-strategy",
        choices=["concise", "balanced", "detailed", "high_energy", "cinematic"],
        help="how tight the pacing recommendations are (default balanced)",
    )
    add(
        "--edit-target-hard",
        action="store_true",
        default=None,
        help="treat --edit-target as a hard constraint",
    )
    add("--gpu-slots", type=positive, help="tasks that may use the GPU lane at once (default 1)")
    add("--cpu-slots", type=positive, help="tasks that may use the CPU lane at once (default 2)")
    add(
        "--light-slots", type=positive, help="tasks that may use the light lane at once (default 4)"
    )
    add(
        "--max-workers",
        type=positive,
        help="most tasks running at once across all lanes (1 = one at a time)",
    )
    add("--dry-run", action="store_true", help="show the execution graph; import and run nothing")
    add("--verbose", "-v", action="store_true", help="show per-task progress messages and facts")
    return parser


def parse_resolution(text: str | None) -> tuple[int, int] | None:
    if text is None:
        return None
    width, _, height = text.lower().partition("x")
    if not (width.isdigit() and height.isdigit()):
        raise SystemExit(f"--reframe-resolution wants WIDTHxHEIGHT, not {text!r}")
    return int(width), int(height)


def parse_safe_areas(texts: Sequence[str] | None) -> tuple[SafeArea, ...]:
    areas = []
    for text in texts or ():
        name, _, numbers = text.partition("=")
        try:
            x0, y0, x1, y1 = (float(n) for n in numbers.split(","))
        except ValueError:
            raise SystemExit(f"--reframe-safe-area wants NAME=x0,y0,x1,y1, not {text!r}") from None
        areas.append(SafeArea(name or "area", x0, y0, x1, y1))
    return tuple(areas)


def settings_from(config: RunConfig) -> StageSettings:
    return StageSettings(
        video_analysis_profile=config.video_analysis_profile,
        proxy_profile=config.proxy_profile,
        audio_improvement_profile=config.audio_improvement_profile,
        video_source_profile=config.video_source_profile,
        video_processing_profile=config.video_processing_profile,
        story_format=config.story_format,
        story_llm=bool(config.story_llm),
        edit_profile=config.edit_profile,
        edit_target_seconds=config.edit_target_seconds,
        edit_target_hard=bool(config.edit_target_hard),
        pacing_profile=config.pacing_profile,
        pacing_strategy=config.pacing_strategy,
        broll_profile=config.broll_profile,
        continuity_profile=config.continuity_profile,
        continuity_strictness=config.continuity_strictness or 1.0,
        reframing_target=config.reframing_target,
        reframing_resolution=parse_resolution(config.reframing_resolution),
        reframing_profile=config.reframing_profile,
        reframing_safe_areas=parse_safe_areas(config.reframing_safe_areas),
        cpu_only=bool(config.cpu_only),
    )


def print_report(report: PipelineReport, verbose: bool) -> None:
    print("\n=== Result " + "=" * 60)
    for asset in report.assets:
        print_asset(asset, verbose)
    for failure in report.failures:
        print(f"\n  x input {failure.source}: {failure.reason}")
    counts = ", ".join(f"{n} {k}" for k, n in sorted(report.counts().items()))
    print(f"\n  tasks: {counts or 'none'}")
    caches = ", ".join(f"{n} {k}" for k, n in sorted(report.cache_counts().items()))
    if caches:
        print(f"  cache: {caches}")
    if not report.dry_run:
        peaks = ", ".join(f"{lane.value}={n}" for lane, n in report.peak_running.items() if n)
        print(
            f"  wall {report.wall_seconds:.1f}s; most tasks at once: {report.peak_total} ({peaks})"
        )
    print("  " + LEGEND)
    verdict = "CANCELLED" if report.cancelled else "OK" if report.ok else "FAILED"
    print(f"\n  => {verdict}")


def print_asset(asset: AssetReport, verbose: bool) -> None:
    print(f"\n{asset.name}  [{asset.media_type}, {asset.origin}]  {asset.asset_id}")
    for note in asset.notes:
        print(f"    note: {note}")
    for task in asset.tasks:
        print_task(task, verbose)


def print_task(task: TaskReport, verbose: bool) -> None:
    if task.status is None:
        after = ", ".join(k.value for k in (*task.needs, *task.wants)) or "-"
        mark = " (implied)" if task.implied else ""
        lane = task.lane.value if task.lane else ""
        print(f"  . planned {task.kind.value:<18}{mark} [{lane}] after: {after}")
        return
    timing = ""
    if task.started_at is not None:
        timing = f"{task.run_seconds:6.1f}s (queued {task.queued_seconds:.1f}s)"
    via = f" [{task.cache}]" if task.cache else ""
    detail = f"  {task.detail}" if task.detail else ""
    print(
        f"  {SYMBOL[task.status]} {task.kind.value:<18} {task.status.value:<19} {timing}{via}{detail}"
    )
    if verbose or task.status in (TaskStatus.PARTIAL, TaskStatus.FAILED):
        for warning in task.warnings:
            print(f"        warning: {warning}")
    if verbose:
        for key, value in task.facts.items():
            print(f"        {key}: {value}")


def install_interrupt(token: CancellationToken) -> None:
    """First Ctrl+C asks the run to stop cleanly; the second one stops the process."""

    def handler(_signum: int, _frame: object) -> None:
        if token.is_cancelled:
            raise KeyboardInterrupt
        print(
            "\ninterrupt: finishing running tasks, starting nothing new (Ctrl+C again to stop now)",
            flush=True,
        )
        token.cancel()

    signal.signal(signal.SIGINT, handler)


def main(argv: Sequence[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.input and not args.asset_id:
        parser.error("give --input or --asset-id")
    if args.all and args.stages:
        parser.error("--all and --stages exclude each other")
    try:
        config = load_config(args.config)
    except argparse.ArgumentTypeError as exc:
        parser.error(str(exc))

    files, problems = collect_inputs(args.input)
    for problem in problems:
        print(f"x {problem}", file=sys.stderr)
    if args.stages:
        stages = frozenset(args.stages)
    elif args.all:
        stages = frozenset(TaskKind)
    else:
        stages = frozenset(config.stages) if config.stages else DEFAULT_STAGES
    merged = config.model_copy(
        update={
            key: getattr(args, key)
            for key in (
                "cpu_only",
                "content_uses_video",
                "gpu_slots",
                "cpu_slots",
                "light_slots",
                "story_format",
                "story_llm",
                "edit_profile",
                "edit_target_seconds",
                "edit_target_hard",
                "pacing_profile",
                "pacing_strategy",
                "broll_profile",
                "continuity_profile",
                "continuity_strictness",
                "reframing_target",
                "reframing_resolution",
                "reframing_profile",
                "reframing_safe_areas",
            )
            if getattr(args, key) is not None
        }
    )
    capacity = {
        lane: slots
        for lane, slots in (
            (Lane.GPU, merged.gpu_slots),
            (Lane.CPU, merged.cpu_slots),
            (Lane.LIGHT, merged.light_slots),
        )
        if slots is not None
    }
    token = CancellationToken()
    install_interrupt(token)

    app = Application.start(
        StartupOptions(env={}, home=args.home, environment=Environment.DEVELOPMENT)
    )
    try:
        pipeline = app.container.resolve(MediaPipeline)
        print(f"Media-House pipeline  (home: {args.home})")
        print(f"stages: {', '.join(sorted(k.value for k in stages))}")
        if merged.cpu_only:
            print("cpu-only: models run on the CPU")
        print()
        result = pipeline.execute(
            RunPipelineCommand(
                paths=files,
                asset_ids=args.asset_id,
                stages=stages,
                content_uses_video=bool(merged.content_uses_video),
                capacity=capacity,
                dry_run=args.dry_run,
                max_running=args.max_workers,
                settings=settings_from(merged),
            ),
            JobContext("cli", token, JobContext.detached().progress),
            ConsoleObserver(args.verbose),
        )
        if isinstance(result, Err):
            print(f"x {result.error.user_message}", file=sys.stderr)
            return EXIT_USAGE
        report = result.value
        if report.dry_run:
            print("Execution graph (tasks in one row are independent):")
            for number, level in enumerate(report.levels, 1):
                print(f"  {number}. " + "  |  ".join(level))
        print_report(report, args.verbose)
        target = args.report_json or (args.output / "report.json" if args.output else None)
        if target:
            write_json_atomically(target, report.to_dict())
            print(f"  report: {target}")
        if report.cancelled:
            return EXIT_CANCELLED
        return EXIT_FAILED if problems else report.exit_code
    finally:
        app.shutdown()


if __name__ == "__main__":
    raise SystemExit(main())
