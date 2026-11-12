"""Run the media pipeline on real files and print what happened.

    uv run python scratch/run_pipeline.py --input clip.mp4
    uv run python scratch/run_pipeline.py --input footage_dir --stages inspect,audio_analysis
    uv run python scratch/run_pipeline.py --input clip.mp4 --dry-run
    uv run python scratch/run_pipeline.py --input clip.mp4 --all --output out/ -v
    uv run python scratch/run_pipeline.py --input clip.mp4 --config pipeline.toml --cpu-only
    uv run python scratch/run_pipeline.py --input clip.mp4 --stages edit_plan,layer4         --output-format 9:16 --format-profile shorts_haul --report-json out/report.json

Everything runs through the real modules and the real Media Library under ``--home`` (default
``~/.media_house_scratch/pipeline``). Running the same input again reports ``reused``: the owners
stored their results in the library the first time. A dry run imports nothing and writes nothing.

Exit codes: 0 ok; 1 a task or input failed; 2 bad arguments or configuration; 130 cancelled
(Ctrl+C once = finish cleanly, twice = stop now).

Not offered on purpose: ``--force`` / ``--no-cache``. The owning modules expose no common way to
recompute, so a flag here could only pretend. Use a fresh ``--home`` to recompute everything.

A configuration file (``--config``, TOML or JSON) may set: ``stages``, ``content_uses_video``,
``cpu_only``, ``allow_model_download``, ``transcription_language``, ``gpu_slots``, ``cpu_slots``, ``light_slots``, ``video_analysis_profile``,
``proxy_profile``, ``audio_improvement_profile``, ``video_source_profile``,
``video_processing_profile``, ``story_format``, ``story_llm``, ``edit_profile``,
``edit_target_seconds``, ``edit_target_hard``, ``pacing_profile``, ``pacing_strategy``,
``reframing_target``, ``reframing_resolution``, ``reframing_profile``, ``reframing_safe_areas``,
and for Layer 4: ``output_format``, ``output_resolution``, ``format_profile``, ``sponsored``,
``graphics_profile``, ``caption_profile``, ``transition_profile``, ``vfx_profile``,
``sound_profile``, ``layer4_render``, and for Layer 5: ``timeline_derivatives``,
``timeline_conflict_policy``, ``timeline_fit``, ``timeline_full_rebuild``, and for DaVinci Resolve:
``resolve_policy``, ``resolve_project``, ``resolve_adopt_project``, ``resolve_allow_switch``,
``resolve_captions``, ``resolve_render_profile``, and for the audio mix: ``mix_profile``, ``mix_delivery``,
and for hosted AI: ``llm_provider``, ``llm_model``, ``transcription_engine``, ``transcription_model``,
``allow_remote_ai``. Command-line options win over the file.

Hosted AI (for machines without a usable GPU; see docs/overhaul/ai-providers.md):

    *** WARNING: remote engines UPLOAD CLIENT AUDIO / TRANSCRIPT TEXT to a third party. ***
    *** Do not use them for NDA material unless the client agreed and the provider's    ***
    *** terms fit (no training on API data). Never use Gemini's free tier for it.       ***

``--transcription-engine local|groq`` (default ``local`` = WhisperX on this machine): ``groq``
sends the prepared audio to Groq's whisper-large-v3-turbo (``--transcription-model`` to change)
in 10-minute FLAC windows. ``--llm-provider local|ollama|groq|cerebras|openrouter|gemini`` with
``--llm-model ID`` picks the model behind ``--story-llm`` (default ``local`` = the in-process
Qwen model; hosted model ids change, so there is no default: check the provider's model list).
Every remote choice also needs ``--allow-remote-ai`` (``allow_remote_ai = true``), else the run
stops with exit code 2. Keys come from the environment only: ``MEDIA_HOUSE_SECRET_GROQ_API_KEY``,
``MEDIA_HOUSE_SECRET_CEREBRAS_API_KEY``, ``MEDIA_HOUSE_SECRET_OPENROUTER_API_KEY``,
``MEDIA_HOUSE_SECRET_GEMINI_API_KEY``. Remote transcripts get their own cache key (engine,
host, model), so they never replace or reuse a local WhisperX transcript.

    uv run python scratch/run_pipeline.py --input clip.mp4 --stages audio_analysis,story
        --story-llm --transcription-engine groq --llm-provider groq
        --llm-model openai/gpt-oss-120b --allow-remote-ai

Layer 4 (``--stages layer4`` = visual_style, graphics, captions, transitions, vfx, sound_design):
add ``edit_plan`` to work on the edit; without it (or for a recording without speech) every
Layer 4 plan is made for the whole recording. The format comes from ONE place: ``--format-profile``
/ ``--output-format`` resolve the channel style, and each module maps the style's format profile
to its own unless its ``--<module>-profile`` is given. ``--report-json`` lists per asset the
"layer4" package: every plan document id and rendered asset id. Import the sound library first
with ``scratch/import_sound_library.py`` (same ``--home``).

Layer 5 (``--stages timeline`` or ``--stages layer4,timeline``): compile the edit and every Layer 4
plan of the run (the stored ones for plans the run does not make) into ONE validated
``timeline_specification``, stored as a document of the clip. ``--report-json`` lists per asset the
"timeline" package. A Layer 4 task of the run that failed blocks it; ``--timeline-derivatives
prefer_improved`` points clips at the stored improved audio / video where one exists.

``--stages timeline_build`` (implies ``timeline``) CONSTRUCTS the timeline from that specification:
tracks that hold their clips in order, one source table (original, derivative, proxy), each
picture clip's processing chain, transitions that join two named clips, mix instructions, markers,
a dependency manifest and findings, stored as a document of the clip. ``--report-json`` lists the
"built_timeline" package. A person's earlier changes to the stored build survive a rebuild
(``--timeline-conflict-policy preserve|replace|review``); ``--timeline-full-rebuild`` rebuilds
every element; ``--timeline-fit`` says how a source of another shape meets the frame.

``--stages resolve_execute`` (implies ``timeline_build``) CARRIES THE TIMELINE OUT IN DAVINCI RESOLVE:
a project, the media pool, tracks, clips, markers, then reads it back to verify it. Resolve must be
running with External scripting set to Local (``--resolve-lib`` / ``--resolve-api`` if it is not in
the default place); otherwise the task is ``skipped_unavailable``. What Resolve's scripting cannot
do (transitions, retime, native effects, moving crops) is reported and marked in the timeline for a
person to finish in Resolve; fixes made there are read back with ``scratch/resolve_sync.py``.
``--stages audio_mix`` (implies ``timeline_build``; needs no Resolve) MIXES the timeline's sound: a
versioned mix specification, one stem per bus (dialogue, production audio, music, effects, ambience), the
final mix, loudness measured against the delivery (``--mix-delivery youtube|shorts|social|broadcast_r128``)
and the mix profile (``--mix-profile generic|talking_head|tutorial|product_demo|cinematic``; default from the
creator identity's format profile). A stem or mix whose inputs did not change is reused.
``--stages deliver`` (needs ``resolve_execute`` with ``--resolve-render`` and ``audio_mix``) puts the final mix
under the rendered picture (video copied, sound replaced) and measures that file: the delivered
loudness is a measurement. It is a review copy: nothing is approved, exported or uploaded.
The run brief (``graphics``, ``cover``, ``screens``, ``background_footage``, ``intro``, ``outro``,
``screen_style``, ``focus_blur``, ``vfx_overrides``) is AUTOMATIC for a project folder in the owner's
convention (``project_folder``; default: the folder above ``--script`` when the script is in a
``00_*`` folder): every key the config does not set is derived from the folder's files and what the
library already knows (``media_pipeline`` ``AutoBriefer``); a key the config sets wins whole (an
empty list means "none"); ``vfx_overrides`` merge per path. ``auto_brief = false`` turns it off.
Every automatic decision and its reason is printed and written to ``<--output>/auto_brief.json``.
Without ``--product-images`` the folder's ``*_pck`` folder is used.
Brand pictures (``wolf_mascot``, ``fire_plate`` ...) are imported with ``scratch/import_brand_assets.py``
(same ``--home``) and bound with ``--brand-asset SLOT=ASSET_ID`` (the import script prints the flags).
``--resolve-policy`` says what may be done to a project that already has work (default
``require_conflict_review``); ``--resolve-render youtube|master`` renders and checks the file.
"""

import argparse
import dataclasses
import json
import os
import re
import signal
import sys
import threading
import tomllib
from collections.abc import Sequence
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, PositiveInt, ValidationError

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.modules import installed_modules
from media_house.core.modules import ApplicationModule
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_intelligence.infrastructure.groq_transcription import (
    RemoteTranscriptionSettings,
)
from media_house.modules.audio_intelligence.module import AudioIntelligenceModule
from media_house.modules.content_intelligence.domain.config import (
    LLM_PROVIDERS,
    LOOPBACK_HOSTS,
    LlmSettings,
)
from media_house.modules.content_intelligence.module import ContentIntelligenceModule
from media_house.modules.media_inspection.application.contracts import InspectionCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_pipeline.application.contracts import (
    AUTO_KEYS,
    DEFAULT_STAGES,
    LAYER4_STAGES,
    AssetReport,
    AutoBriefCommand,
    AutoBriefer,
    GraphicWish,
    Lane,
    MediaPipeline,
    PipelineReport,
    RunPipelineCommand,
    StageSettings,
    TaskKind,
    TaskRecord,
    TaskReport,
    TaskStatus,
    apply_auto_brief,
    decisions,
    next_attempt,
)
from media_house.modules.motion_graphics_engine.application.contracts import SlotValue
from media_house.modules.production_project.application.contracts import (
    TranscriptLine,
    conform,
    fact_rows,
    parse_script,
    sentence_lines,
)
from media_house.modules.reframing_intelligence.application.contracts import (
    KEEP_OUT_PRESETS,
    KeepOut,
    SafeArea,
)
from media_house.modules.resolve_adapter.infrastructure.install import ResolveInstall
from media_house.modules.resolve_adapter.module import ResolveAdapterModule
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.video_intelligence.application.contracts import VideoCatalog
from media_house.modules.visual_asset_generation.application.contracts import (
    AdjustImageCommand,
    ImageAdjuster,
)
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


class CoverEntry(BaseModel):
    """Footage to show over named speech (needs ``--script``): from the line that says ``over``
    to the end of the line that says ``until`` (else that line), at most ``seconds``."""

    model_config = ConfigDict(extra="forbid")

    over: str
    footage: Path
    until: str | None = None
    start: float = 0.0
    seconds: float = 8.0
    #: Start this far (source seconds) into the line, and cover at most ``upto`` of it.
    skip: float = 0.0
    upto: float | None = None


class ScreenEntry(BaseModel):
    """A screen recording shown as its page only: ``window`` = x, y, w, h (normalised)."""

    model_config = ConfigDict(extra="forbid")

    footage: Path
    window: tuple[float, float, float, float]


type _Slot = str | int | list[str] | list[list[str]]


class GraphicEntry(BaseModel):
    """A graphic over named speech (``say``). A slot value ``file:PATH`` is imported and passed as
    its library asset id; ``brand:SLOT`` is the asset bound to that brand slot."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    say: str
    slots: dict[str, _Slot] = {}
    variant: str = ""
    occurrence: int = 1
    offset: float = 0.0
    duration: float | None = None
    zone: str = ""
    emphasis: int | None = None


class ThumbnailEntry(BaseModel):
    """The ``[thumbnail]`` table: the video's A/B/C thumbnails (stage ``thumbnail_design``).

    Files are imported into the library (stored once); ``products`` and ``device_product`` are
    names of the ``--product-images`` pictures. Without ``reaction`` the newest picture tagged
    ``reaction`` is used, else the best moment of the analysed footage.
    """

    model_config = ConfigDict(extra="forbid")

    headline: list[str]
    alt_headline: list[str] | None = None
    reaction: Path | None = None
    flip_reaction: bool = False
    products: list[str] | None = None
    logo: Path | None = None
    device_product: str | None = None
    device_price: str | None = None
    device_button: str | None = None
    device_screen: Path | None = None
    badge: str | None = None
    plate: Path | None = None


class RunConfig(BaseModel):
    """The configuration file. Unknown keys are errors, so a typo cannot silently do nothing."""

    model_config = ConfigDict(extra="forbid")

    stages: list[TaskKind] | None = None
    content_uses_video: bool | None = None
    cpu_only: bool | None = None
    allow_model_download: bool | None = None
    transcription_language: str | None = None
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
    #: The text of the per-video context file (``--context``), background for the story model.
    story_context: str | None = None
    #: Brand slot -> library asset id (``scratch/import_brand_assets.py`` prints them).
    brand_assets: dict[str, str] | None = None
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
    reframing_keep_out: list[str] | None = None
    output_format: str | None = None
    output_resolution: str | None = None
    format_profile: str | None = None
    sponsored: bool | None = None
    graphics_profile: str | None = None
    caption_profile: str | None = None
    transition_profile: str | None = None
    vfx_profile: str | None = None
    sound_profile: str | None = None
    #: Dotted-path overrides, e.g. ``{"music.max_tracks": 1}`` / ``{"punch_in.apply": "never"}``.
    sound_overrides: dict[str, str | int | float | bool | list[str]] | None = None
    vfx_overrides: dict[str, str | int | float | bool | list[str]] | None = None
    layer4_render: bool | None = None
    jump_cut_zoom: float | None = None
    camera_beat: float | None = None
    #: A Short from the long edit: the best (low, high) seconds highlight only.
    edit_excerpt: tuple[float, float] | None = None
    #: ``condensed`` (default: hook, every item, verdict) or ``contiguous`` (one stretch).
    edit_excerpt_mode: Literal["condensed", "contiguous"] | None = None
    cover: list[CoverEntry] | None = None
    screens: list[ScreenEntry] | None = None
    graphics: list[GraphicEntry] | None = None
    thumbnail: ThumbnailEntry | None = None
    #: Lines to leave out (a slip that cannot be cut around): the line that says the phrase.
    drop: list[str] | None = None
    #: The channel's intro / outro clips (played whole: intro before the greeting).
    intro: Path | None = None
    outro: Path | None = None
    screen_style: str | None = None
    focus_blur: float | None = None
    #: Moving footage under full-frame cards (never shown as B-roll).
    background_footage: Path | None = None
    #: The owner's project folder (``00_kontext`` ... ``06_gfx``) the automatic brief reads.
    project_folder: Path | None = None
    #: ``false``: no automatic brief (only the hand entries above).
    auto_brief: bool | None = None
    #: Attempts at a Short: after one fails the quality gate, its remedies plan the next.
    gate_iterations: int | None = None
    resolve_auto_grade: bool | None = None
    timeline_derivatives: str | None = None
    timeline_conflict_policy: str | None = None
    timeline_fit: str | None = None
    timeline_full_rebuild: bool | None = None
    resolve_policy: str | None = None
    resolve_project: str | None = None
    resolve_adopt_project: bool | None = None
    resolve_allow_switch: bool | None = None
    resolve_captions: str | None = None
    resolve_render_profile: str | None = None
    mix_profile: str | None = None
    mix_delivery: str | None = None
    #: ``local`` (in-process model) or a key of ``LLM_PROVIDERS``; used with ``story_llm``.
    llm_provider: str | None = None
    llm_model: str | None = None
    #: ``local`` (WhisperX) or ``groq`` (uploads the audio).
    transcription_engine: str | None = None
    transcription_model: str | None = None
    #: Consent that audio/text may leave this machine. Required by every remote engine.
    allow_remote_ai: bool | None = None


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
    """Stage names, comma separated; ``layer4`` stands for every Layer 4 stage."""
    names = [n.strip() for n in text.split(",") if n.strip()]
    try:
        return [
            kind
            for n in names
            for kind in (sorted(LAYER4_STAGES) if n == "layer4" else [TaskKind(n)])
        ]
    except ValueError as exc:
        valid = ", ".join(k.value for k in TaskKind)
        raise argparse.ArgumentTypeError(f"{exc}. Valid stages: {valid}, layer4") from exc


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
        "--allow-model-download",
        action="store_true",
        default=None,
        help="video analysis may download a missing model once (pinned checksum); off by default",
    )
    add(
        "--language",
        dest="transcription_language",
        help="pin the transcription language (ISO code such as de); default: auto-detect",
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
        "--context",
        type=Path,
        help="the per-video context (pipeline-context.md of a Brief-to-Cut project): background "
        "text the story model reads; with --story-llm only",
    )
    add(
        "--llm-provider",
        choices=["local", *LLM_PROVIDERS],
        help="model behind --story-llm: local (default, in-process) or a served/hosted provider",
    )
    add("--llm-model", help="model id at --llm-provider (required for every provider but local)")
    add(
        "--transcription-engine",
        choices=["local", "groq"],
        help="local = WhisperX on this machine (default); groq = UPLOADS the audio to Groq",
    )
    add("--transcription-model", help="model for --transcription-engine groq")
    add(
        "--allow-remote-ai",
        action="store_true",
        default=None,
        help="consent that client audio/text may be sent to the chosen remote AI provider (NDA!)",
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
        "--reframe-keep-out",
        dest="reframing_keep_out",
        action="append",
        help="a colour of the SOURCE picture that must not be visible (a set's wall): green, blue, "
        "red, or NAME=hue_min-hue_max[,min_saturation,min_value] (repeatable). The reframing "
        "crops in until it is gone and keeps the subject inside; with no --reframe-target the "
        "output format is the target",
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
    add(
        "--output-format",
        dest="output_format",
        choices=["16:9", "9:16", "1:1"],
        help="delivery shape of every Layer 4 plan (default: the reframing target, else the "
        "format profile's own shape)",
    )
    add(
        "--output-resolution",
        dest="output_resolution",
        help="delivery size in pixels, WIDTHxHEIGHT (default: the standard size of the shape)",
    )
    add(
        "--format-profile",
        dest="format_profile",
        help="channel format profile (generic, ranking, brochure_review, shopping_tour, unboxing, "
        "compilation, nye_uncut, shorts_meme, shorts_haul, shorts_product_test, shorts_versus); "
        "default: by story format, then output shape",
    )
    add(
        "--sponsored",
        action="store_true",
        default=None,
        help='a sponsored video: the mandatory "Werbung" label is planned',
    )
    for module in ("graphics", "caption", "transition", "vfx", "sound"):
        add(
            f"--{module}-profile",
            dest=f"{module}_profile",
            help=f"{module} profile (default: mapped from the format profile)",
        )
    add(
        "--brand-asset",
        dest="brand_asset",
        action="append",
        default=[],
        metavar="SLOT=ASSET_ID",
        help="bind a brand slot (wolf_mascot, fire_plate ...) to a library asset (repeatable)",
    )
    add(
        "--no-render",
        dest="layer4_render",
        action="store_false",
        default=None,
        help="Layer 4 plans only: render no graphics, captions, overlays, VFX clips or stem",
    )
    add(
        "--timeline-derivatives",
        dest="timeline_derivatives",
        choices=("original", "prefer_improved"),
        help="which version of a recording the timeline points at (default: original)",
    )
    add(
        "--timeline-conflict-policy",
        dest="timeline_conflict_policy",
        choices=("preserve", "replace", "review"),
        help="when a new specification changes what a person changed (default: review)",
    )
    add(
        "--timeline-fit",
        dest="timeline_fit",
        choices=("contain", "cover", "stretch", "none"),
        help="how a source of another shape meets the frame (default: contain)",
    )
    add(
        "--resolve-policy",
        dest="resolve_policy",
        choices=(
            "create_new",
            "update_managed_elements",
            "preserve_manual_edits",
            "require_conflict_review",
            "full_rebuild_with_backup",
        ),
        help="what may be done to a Resolve project that already has work (default: "
        "require_conflict_review)",
    )
    add(
        "--mix-profile",
        dest="mix_profile",
        choices=("generic", "talking_head", "tutorial", "product_demo", "cinematic"),
        help="how the audio buses are balanced and processed (default: from the creator identity)",
    )
    add(
        "--mix-delivery",
        dest="mix_delivery",
        choices=("youtube", "shorts", "social", "broadcast_r128"),
        help="loudness, true peak and format the mix must meet (default: youtube)",
    )
    add("--resolve-project", dest="resolve_project", help="name of the Resolve project")
    add("--resolve-lib", dest="resolve_lib", type=Path, help="Resolve's fusionscript library")
    add("--resolve-api", dest="resolve_api", type=Path, help="Resolve's Developer/Scripting folder")
    add(
        "--resolve-adopt-project",
        dest="resolve_adopt_project",
        action="store_true",
        default=None,
        help="use a Resolve project of that name that Media House did not make",
    )
    add(
        "--resolve-allow-switch",
        dest="resolve_allow_switch",
        action="store_true",
        default=None,
        help="save and leave the project that is open in Resolve if it is another one",
    )
    add(
        "--resolve-captions",
        dest="resolve_captions",
        choices=("rendered", "rendered_with_editable_cues"),
        help="also import the caption cues as a subtitle file, to correct words in Resolve",
    )
    add(
        "--resolve-render",
        dest="resolve_render_profile",
        choices=("youtube", "master"),
        help="render the verified timeline with Resolve and check the file",
    )
    add(
        "--timeline-full-rebuild",
        dest="timeline_full_rebuild",
        action="store_true",
        default=None,
        help="rebuild every element of the timeline instead of carrying over unchanged ones",
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
    add(
        "--script",
        type=Path,
        help="with --project: a scripted transcript (## S01 ... SPRECHTEXT:). The recordings are "
        "ordered by the scene they speak (stored transcripts needed: run the analysis first); "
        "recordings that speak no script line stay out of the story (B-roll still finds them); "
        "speech the script excludes (approval-gated, unfilled {SLOT}) is cut",
    )
    add(
        "--product-images",
        type=Path,
        help="a folder of product pictures named after the product (pure-impact.png -> 'Pure "
        "Impact'): a section that speaks the name shows that picture on its product card/sticker",
    )
    add(
        "--project",
        action="store_true",
        help="all inputs, in the order given (a folder: sorted by name), make ONE video; the first "
        "recording is the anchor the story, edit and every later stage are planned on",
    )
    add("--verbose", "-v", action="store_true", help="show per-task progress messages and facts")
    return parser


def parse_resolution(
    text: str | None, flag: str = "--reframe-resolution"
) -> tuple[int, int] | None:
    if text is None:
        return None
    width, _, height = text.lower().partition("x")
    if not (width.isdigit() and height.isdigit()):
        raise SystemExit(f"{flag} wants WIDTHxHEIGHT, not {text!r}")
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


def parse_keep_out(texts: Sequence[str] | None) -> tuple[KeepOut, ...]:
    rules = []
    for text in texts or ():
        if text in KEEP_OUT_PRESETS:
            rules.append(KEEP_OUT_PRESETS[text])
            continue
        name, _, numbers = text.partition("=")
        parts = numbers.replace("-", ",").split(",")
        try:
            hue_min, hue_max, *rest = (float(p) for p in parts)
            rules.append(KeepOut(name, hue_min, hue_max, *rest[:2]))
        except (ValueError, TypeError):
            presets = ", ".join(KEEP_OUT_PRESETS)
            raise SystemExit(
                f"--reframe-keep-out wants {presets} or NAME=hue_min-hue_max[,sat,val], not {text!r}"
            ) from None
    return tuple(rules)


def import_product_images(
    app: Application, folder: Path, settings: StageSettings, script: Path | None = None
) -> StageSettings:
    """Import every picture of the folder (stored once) and name it after its file. With a
    script, every product its fact tables name gets a full-screen intro and data card (its
    picture cut out by the library's own cut-out operation, reused when stored)."""
    library = app.container.resolve(MediaLibrary)
    named: list[tuple[str, str]] = []
    for path in sorted(folder.iterdir()):
        if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        imported = library.import_file(path)
        if isinstance(imported, Err):
            print(f"  ! {path.name}: {imported.error.user_message}")
            continue
        name = " ".join(w.capitalize() for w in path.stem.replace("_", "-").split("-") if w)
        named.append((name, imported.value.asset.id))
    print(f"product pictures: {', '.join(n for n, _ in named) or 'none'}")
    cards: list[tuple[str, str, tuple[tuple[str, str], ...]]] = []
    if script is not None:
        rows = fact_rows(script.read_text(encoding="utf-8"), [n for n, _ in named])
        adjuster = app.container.resolve(ImageAdjuster)
        for name, asset_id in named:
            if name not in rows:
                continue
            cut = adjuster.execute(
                AdjustImageCommand(source_asset_id=asset_id), JobContext.detached()
            )
            if isinstance(cut, Err):
                print(f"  ! {name}: no cut-out ({cut.error.user_message})")
                continue
            cards.append((name, cut.value.asset.id, rows[name]))
        print(f"product cards: {', '.join(n for n, _, _ in cards) or 'none'}")
    print()
    return dataclasses.replace(settings, product_images=tuple(named), product_cards=tuple(cards))


def conform_to_script(
    app: Application,
    files: list[Path],
    script_path: Path,
    settings: StageSettings,
    cover: list[CoverEntry],
    drop: list[str] | None = None,
) -> tuple[list[Path], StageSettings]:
    """Order the recordings by the script scene they speak and collect the excluded speech. Reads
    stored transcripts only; a recording without one is left out and named."""
    script = parse_script(script_path.read_text(encoding="utf-8"))
    library = app.container.resolve(MediaLibrary)
    catalog = app.container.resolve(AudioCatalog)
    by_id: dict[str, Path] = {}
    lines: list[TranscriptLine] = []
    for path in files:
        imported = library.import_file(path)
        if isinstance(imported, Err):
            print(f"  ! {path.name}: {imported.error.user_message}")
            continue
        asset_id = imported.value.asset.id
        stored = catalog.latest(asset_id)
        if isinstance(stored, Err) or stored.value is None:
            print(f"  ! {path.name}: no stored transcript (run the analysis first)")
            continue
        by_id[asset_id] = path
        lines += sentence_lines(
            asset_id,
            [(w.raw_word, w.start, w.end) for w in stored.value[0].transcript.words],
        )
    result = conform(script, lines, list(by_id))
    print(f"script: {len(script.scenes)} scenes, {len(script.sentences)} sentences")
    for asset_id in result.order:
        scenes = sorted(
            {m.scene_id for m in result.matches if m.line.asset_id == asset_id and m.scene_id},
            key=lambda sid: int(sid[1:]),
        )
        print(f"  {by_id[asset_id].name}: {', '.join(scenes) or 'speaks no script line'}")
    for m in result.excluded:
        print(
            f"  - cut {by_id[m.line.asset_id].name} {m.line.start:.1f}-{m.line.end:.1f}s "
            f"({m.exclusion.value}, match {m.score})"
        )
    for take in result.rejected:
        print(
            f"  - cut {by_id[take.line.asset_id].name} {take.line.start:.1f}-{take.line.end:.1f}s "
            f"({take.reason}): {take.line.text[:70]}"
        )
    if result.missing_scenes:
        print(f"  ! no recording speaks: {', '.join(result.missing_scenes)}")
    print()
    placed = [by_id[a] for a in result.order if a not in result.unplaced]
    excluded = tuple(
        (m.line.asset_id, m.line.start, m.line.end, f"script: {m.exclusion.value}")
        for m in result.excluded
    ) + tuple((t.line.asset_id, t.line.start, t.line.end, t.reason) for t in result.rejected)
    for phrase in drop or []:
        line = _line_saying(lines, phrase)
        if line is None:
            print(f"  ! drop: nobody says {phrase!r}")
            continue
        print(
            f"  - cut {by_id[line.asset_id].name} {line.start:.1f}-{line.end:.1f}s (dropped): {line.text[:70]}"
        )
        excluded += ((line.asset_id, line.start, line.end, f"owner: drop {phrase!r}"),)
    wishes = cover_wishes(library, lines, cover, by_id)
    return placed, dataclasses.replace(settings, excluded_speech=excluded, cover_wishes=wishes)


def thumbnail_settings(
    app: Application, settings: StageSettings, entry: ThumbnailEntry | None
) -> StageSettings:
    """Import the thumbnail pictures and hand the brief to the ``thumbnail_design`` stage."""
    if entry is None:
        return settings
    library = app.container.resolve(MediaLibrary)

    def picture(path: Path | None, tags: tuple[str, ...] = ()) -> str | None:
        if path is None:
            return None
        imported = library.import_file(path, tags=tags)
        if isinstance(imported, Err):
            sys.exit(f"thumbnail: {path}: {imported.error.user_message}")
        if tags:
            library.add_tags(imported.value.asset.id, tags)  # a stored duplicate joins too
        return imported.value.asset.id

    print(f"thumbnail: {' / '.join(entry.headline)}")
    return dataclasses.replace(
        settings,
        thumbnail_headline=tuple(entry.headline),
        thumbnail_alt_headline=tuple(entry.alt_headline or ()),
        thumbnail_reaction=picture(entry.reaction, ("reaction",)),
        thumbnail_flip_reaction=entry.flip_reaction,
        thumbnail_products=tuple(entry.products or ()),
        thumbnail_logo=picture(entry.logo),
        thumbnail_device_product=entry.device_product,
        thumbnail_device_price=entry.device_price,
        thumbnail_device_button=entry.device_button,
        thumbnail_device_screen=picture(entry.device_screen),
        thumbnail_badge=entry.badge,
        thumbnail_plate=picture(entry.plate),
    )


def brief_graphics(
    app: Application,
    settings: StageSettings,
    screens: list[ScreenEntry],
    graphics: list[GraphicEntry],
    intro: Path | None = None,
    outro: Path | None = None,
    background: Path | None = None,
) -> StageSettings:
    """Screen windows and the brief's graphics, with their files as library asset ids."""
    library = app.container.resolve(MediaLibrary)
    brand = dict(settings.brand_assets)

    def asset(path: Path) -> str | None:
        imported = library.import_file(path.expanduser())
        if isinstance(imported, Err):
            print(f"  ! {path}: {imported.error.user_message}")
            return None
        return imported.value.asset.id

    windows = []
    for screen in screens:
        if (asset_id := asset(screen.footage)) is not None:
            windows.append((asset_id, *screen.window))

    def slot(value: _Slot) -> SlotValue:
        if isinstance(value, list):
            return tuple(  # type: ignore[return-value]
                tuple(v) if isinstance(v, list) else slot(v) for v in value
            )
        if isinstance(value, str) and value.startswith("file:"):
            return asset(Path(value[5:])) or value
        if isinstance(value, str) and value.startswith("brand:"):
            return brand.get(value[6:], value)
        return value

    wishes = tuple(
        GraphicWish(
            kind=g.kind,
            say=g.say,
            slots=tuple((k, slot(v)) for k, v in sorted(g.slots.items())),
            variant=g.variant,
            occurrence=g.occurrence,
            offset=g.offset,
            duration=g.duration,
            zone=g.zone,
            emphasis=g.emphasis,
        )
        for g in graphics
    )
    if windows or wishes:
        print(f"brief: {len(windows)} screen window(s), {len(wishes)} graphic(s)")
    return dataclasses.replace(
        settings,
        screen_windows=tuple(sorted(windows)),
        graphic_wishes=wishes,
        intro_asset_id=asset(intro) if intro is not None else None,
        outro_asset_id=asset(outro) if outro is not None else None,
        background_footage=asset(background) if background is not None else None,
    )


def _said(text: str) -> list[str]:
    return re.findall(r"\w+", text.casefold())


def _line_saying(lines: list[TranscriptLine], phrase: str) -> TranscriptLine | None:
    wanted = _said(phrase)
    for line in lines:
        words = _said(line.text)
        if any(words[i : i + len(wanted)] == wanted for i in range(len(words))):
            return line
    return None


def cover_wishes(
    library: MediaLibrary,
    lines: list[TranscriptLine],
    cover: list[CoverEntry],
    by_id: dict[str, Path],
) -> tuple[tuple[str, str, float, float, float, float, str], ...]:
    """Each cover entry as a wish over the transcript line that says its phrase."""
    out: list[tuple[str, str, float, float, float, float, str]] = []
    for entry in cover:
        line = _line_saying(lines, entry.over)
        if line is None:
            print(f"  ! cover: nobody says {entry.over!r}")
            continue
        end = line.end
        start = line.start + entry.skip
        if entry.upto is not None:
            end = min(end, start + entry.upto)
        if entry.until is not None:
            later = [x for x in lines if x.asset_id == line.asset_id and x.start >= line.start]
            last = _line_saying(later, entry.until)
            if last is None:
                print(f"  ! cover: {entry.until!r} is not said after {entry.over!r}")
                continue
            end = last.end
        imported = library.import_file(entry.footage.expanduser())
        if isinstance(imported, Err):
            print(f"  ! cover: {entry.footage}: {imported.error.user_message}")
            continue
        footage = imported.value.asset.id
        print(
            f"  cover {entry.footage.name} over {by_id[line.asset_id].name} "
            f"{start:.1f}-{end:.1f}s ({entry.over!r})"
        )
        out.append((footage, line.asset_id, start, end, entry.start, entry.seconds, entry.over))
    return tuple(out)


def project_folder(config: RunConfig, script: Path | None) -> Path | None:
    """The configured project folder, else the one the script's ``00_*`` folder sits in."""
    if config.project_folder is not None:
        return config.project_folder.expanduser()
    if script is not None and script.parent.name.startswith("00_"):
        return script.parent.parent
    return None


def auto_brief(
    app: Application,
    settings: StageSettings,
    config: RunConfig,
    folder: Path,
    files: list[Path],
    script: Path | None,
    output: Path | None,
) -> StageSettings:
    """The automatic brief of the project folder for every brief key the config leaves out."""
    library = app.container.resolve(MediaLibrary)
    recordings = []
    for path in files:
        imported = library.import_file(path)
        if not isinstance(imported, Err):
            recordings.append(imported.value.asset.id)
    briefer = AutoBriefer(
        library,
        app.container.resolve(AudioCatalog),
        app.container.resolve(VideoCatalog),
        app.container.resolve(InspectionCatalog),
        app.container.resolve(StoryCatalog),
    )
    result = briefer.execute(
        AutoBriefCommand(
            files=tuple(p for p in sorted(folder.glob("*/*")) if p.is_file()),
            recordings=tuple(recordings),
            script=script.read_text(encoding="utf-8") if script is not None else None,
            settings=settings,
        )
    )
    given = frozenset(k for k in AUTO_KEYS if getattr(config, k) is not None)
    log = decisions(result, given)
    print(f"auto brief ({result.brief.format_profile}) of {folder}:")
    for d in log:
        mark = "+" if d["used"] else "-"
        print(f"  {mark} {d['key']}: {d['what']}  [{d['source']}]")
    for name, why in result.skipped:
        print(f"  ! {name}: {why}")
    if given:
        print(f"  (hand entries win: {', '.join(sorted(given))})")
    print()
    if output is not None:
        write_json_atomically(output / "auto_brief.json", log)
    return apply_auto_brief(settings, result.brief, given)


def _overrides(
    given: dict[str, str | int | float | bool | list[str]] | None,
) -> tuple[tuple[str, str | int | float | bool | tuple[str, ...]], ...]:
    return tuple(
        sorted((k, tuple(v) if isinstance(v, list) else v) for k, v in (given or {}).items())
    )


def settings_from(config: RunConfig) -> StageSettings:
    return StageSettings(
        video_analysis_profile=config.video_analysis_profile,
        proxy_profile=config.proxy_profile,
        audio_improvement_profile=config.audio_improvement_profile,
        video_source_profile=config.video_source_profile,
        video_processing_profile=config.video_processing_profile,
        story_format=config.story_format,
        story_llm=bool(config.story_llm),
        story_context=config.story_context or "",
        brand_assets=tuple(sorted((config.brand_assets or {}).items())),
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
        reframing_keep_out=parse_keep_out(config.reframing_keep_out),
        cpu_only=bool(config.cpu_only),
        allow_model_download=bool(config.allow_model_download),
        transcription_language=config.transcription_language,
        output_format=config.output_format,
        output_resolution=parse_resolution(config.output_resolution, "--output-resolution"),
        format_profile=config.format_profile,
        sponsored=bool(config.sponsored),
        graphics_profile=config.graphics_profile,
        caption_profile=config.caption_profile,
        transition_profile=config.transition_profile,
        vfx_profile=config.vfx_profile,
        sound_profile=config.sound_profile,
        sound_overrides=_overrides(config.sound_overrides),
        jump_cut_zoom=config.jump_cut_zoom or 1.0,
        camera_beat=config.camera_beat,
        edit_excerpt=config.edit_excerpt,
        edit_excerpt_mode=config.edit_excerpt_mode or "condensed",
        screen_style=config.screen_style or "crop",
        focus_blur=config.focus_blur or 0.0,
        resolve_auto_grade=bool(config.resolve_auto_grade),
        vfx_overrides=_overrides(config.vfx_overrides),
        layer4_render=config.layer4_render is not False,
        timeline_derivatives=config.timeline_derivatives,
        timeline_conflict_policy=config.timeline_conflict_policy,
        timeline_fit=config.timeline_fit,
        timeline_full_rebuild=config.timeline_full_rebuild is True,
        resolve_policy=config.resolve_policy,
        resolve_project=config.resolve_project or "",
        resolve_adopt_project=config.resolve_adopt_project is True,
        resolve_allow_switch=config.resolve_allow_switch is True,
        resolve_captions=config.resolve_captions,
        resolve_render_profile=config.resolve_render_profile,
        mix_profile=config.mix_profile,
        mix_delivery=config.mix_delivery,
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
    package = asset.layer4_package()
    if package and isinstance(plans := package["plans"], dict):
        rendered = package["rendered"]
        lists = rendered.values() if isinstance(rendered, dict) else ()
        count = sum(len(v) for v in lists if isinstance(v, list))
        print(f"  layer4 ({package['path']}): {len(plans)} plans, {count} rendered assets")
        for kind, plan in plans.items():
            if isinstance(plan, dict):
                print(f"        {kind:<13} {plan.get('plan_asset_id')}")
    print_timeline(asset)
    print_built_timeline(asset)
    print_resolve(asset)
    print_mix(asset)


def print_built_timeline(asset: AssetReport) -> None:
    package = asset.build_package()
    if package is None:
        return
    print(
        f"  built timeline: {package['status']}  {package.get('plan_asset_id')}  "
        f"{package.get('video_tracks')}V+{package.get('audio_tracks')}A tracks, "
        f"{package.get('video_clips')} picture + {package.get('audio_clips')} sound clips, "
        f"{package.get('transitions')} transitions, {package.get('markers')} markers; "
        f"{package.get('unresolved')} unresolved, {package.get('unsupported')} unsupported, "
        f"{package.get('reviews')} to review ({package.get('mode')}, "
        f"{package.get('elements_reused')}/{package.get('elements_total')} carried over)"
    )


def with_resolve_install(lib: Path | None, api: Path | None) -> list[ApplicationModule]:
    """The product's modules, with Resolve's scripting library where ``--resolve-lib`` /
    ``--resolve-api`` say (Blackmagic's documented install location otherwise)."""
    overrides = {
        **({"RESOLVE_SCRIPT_LIB": str(lib)} if lib else {}),
        **({"RESOLVE_SCRIPT_API": str(api)} if api else {}),
    }
    install = ResolveInstall.defaults(sys.platform, overrides)
    return [
        *(m for m in installed_modules() if m.name != "resolve_adapter"),
        ResolveAdapterModule(install=install),
    ]


def with_ai_engines(modules: list[ApplicationModule], config: RunConfig) -> list[ApplicationModule]:
    """Swap in the hosted engines the configuration asks for; ``ValueError`` explains a refusal."""
    remote_ok = config.allow_remote_ai is True
    replace: dict[str, ApplicationModule] = {}
    engine = config.transcription_engine or "local"
    if engine not in {"local", "groq"}:
        raise ValueError(f"transcription_engine must be local or groq, not {engine!r}")
    if engine == "groq":
        if not remote_ok:
            raise ValueError(
                "transcription_engine groq uploads client audio: add --allow-remote-ai"
            )
        audio = RemoteTranscriptionSettings()
        if config.transcription_model:
            audio = dataclasses.replace(audio, model=config.transcription_model)
        replace["audio_intelligence"] = AudioIntelligenceModule(remote_transcription=audio)
        print(f"! transcription: audio is UPLOADED to {audio.endpoint} ({audio.model})")
    provider = config.llm_provider or "local"
    if provider != "local":
        preset = LLM_PROVIDERS.get(provider)
        if preset is None:
            raise ValueError(
                f"unknown llm_provider {provider!r}; use local or {sorted(LLM_PROVIDERS)}"
            )
        if not config.llm_model:
            raise ValueError(f"llm_provider {provider} needs llm_model (the provider's model id)")
        remote = urlsplit(preset.endpoint).hostname not in LOOPBACK_HOSTS
        if remote and not remote_ok:
            raise ValueError(
                f"llm_provider {provider} sends transcript text: add --allow-remote-ai"
            )
        settings = LlmSettings(
            model=config.llm_model,
            endpoint=preset.endpoint,
            api_key_secret=preset.api_key_secret,
            allow_remote_endpoint=remote,
        )
        replace["content_intelligence"] = ContentIntelligenceModule(generation=settings)
        where = "SENT to" if remote else "served by"
        print(f"! story wording: text is {where} {preset.endpoint} ({config.llm_model})")
    return [replace.get(m.name, m) for m in modules]


def print_mix(asset: AssetReport) -> None:
    package = asset.mix_package()
    if package is None:
        return
    if "state" not in package:
        print(f"  audio mix: {package['status']}  {package.get('detail', '')}")
        return
    print(
        f"  audio mix: {package['status']}  {package.get('state')}  profile {package.get('profile')}, "
        f"delivery {package.get('delivery')}: {package.get('integrated_lufs')} LUFS, true peak "
        f"{package.get('true_peak_dbtp')} dBTP, {package.get('stems')} stems "
        f"({package.get('stems_reused')} reused); {package.get('errors')} errors, "
        f"{package.get('warnings')} warnings  -> {package.get('mix_asset_id')}"
    )


def print_resolve(asset: AssetReport) -> None:
    package = asset.resolve_package()
    if package is None:
        return
    if "outcome" not in package:  # Resolve was not reached: nothing was done
        print(f"  resolve: {package['status']}  {package.get('detail', '')}")
        return
    print(
        f"  resolve: {package['status']}  {package.get('outcome', '')}  project "
        f"{package.get('project', '')!r} ({package.get('resolve_edition', '')} "
        f"{package.get('resolve_version', '')}): {package.get('executed')} executed, "
        f"{package.get('partial')} left for a person, {package.get('unsupported')} unsupported, "
        f"{package.get('failed')} failed, {package.get('verified')} verified; "
        f"{package.get('open_conflicts')} open conflicts, {package.get('human_modified')} "
        "changed by a person"
    )
    if package.get("render_status"):
        print(
            f"  resolve render: {package['render_status']} {package.get('render_outcome')} "
            f"-> {package.get('render_asset_id')}"
        )


def print_timeline(asset: AssetReport) -> None:
    package = asset.timeline_package()
    if package is None:
        return
    print(
        f"  timeline: {package['status']}  {package.get('plan_asset_id')}  "
        f"{package.get('video_items')} picture, {package.get('audio_items')} sound, "
        f"{package.get('overlays')} overlays, {package.get('warning_items')} warnings, "
        f"{package.get('conflicts')} conflicts"
    )


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
                "allow_model_download",
                "transcription_language",
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
                "reframing_keep_out",
                "output_format",
                "output_resolution",
                "format_profile",
                "sponsored",
                "graphics_profile",
                "caption_profile",
                "transition_profile",
                "vfx_profile",
                "sound_profile",
                "layer4_render",
                "timeline_derivatives",
                "timeline_conflict_policy",
                "timeline_fit",
                "timeline_full_rebuild",
                "resolve_policy",
                "resolve_project",
                "resolve_adopt_project",
                "resolve_allow_switch",
                "resolve_captions",
                "resolve_render_profile",
                "mix_profile",
                "mix_delivery",
                "llm_provider",
                "llm_model",
                "transcription_engine",
                "transcription_model",
                "allow_remote_ai",
            )
            if getattr(args, key) is not None
        }
    )
    if args.context is not None:
        try:
            context_text = args.context.expanduser().read_text(encoding="utf-8")
        except OSError as exc:
            parser.error(f"--context {args.context}: {exc.strerror or exc}")
        merged = merged.model_copy(update={"story_context": context_text})
    if args.brand_asset:
        bound = dict(merged.brand_assets or {})
        for text in args.brand_asset:
            slot, sep, asset_id = text.partition("=")
            if not sep or not slot or not asset_id:
                parser.error(f"--brand-asset expects SLOT=ASSET_ID, got {text!r}")
            bound[slot] = asset_id
        merged = merged.model_copy(update={"brand_assets": bound})
    capacity = {
        lane: slots
        for lane, slots in (
            (Lane.GPU, merged.gpu_slots),
            (Lane.CPU, merged.cpu_slots),
            (Lane.LIGHT, merged.light_slots),
        )
        if slots is not None
    }
    try:
        modules = with_ai_engines(with_resolve_install(args.resolve_lib, args.resolve_api), merged)
    except ValueError as exc:
        parser.error(str(exc))
    token = CancellationToken()
    install_interrupt(token)

    app = Application.start(
        StartupOptions(
            # Only secrets (API keys) come from the environment; they are held as SecretStr.
            env={k: v for k, v in os.environ.items() if k.startswith("MEDIA_HOUSE_SECRET_")},  # noqa: TID251 - CLI entry point
            home=args.home,
            environment=Environment.DEVELOPMENT,
            shared_installs=True,  # models and HyperFrames are installed once per machine
            modules=modules,
        )
    )
    try:
        pipeline = app.container.resolve(MediaPipeline)
        print(f"Media-House pipeline  (home: {args.home})")
        print(f"stages: {', '.join(sorted(k.value for k in stages))}")
        if merged.cpu_only:
            print("cpu-only: models run on the CPU")
        print()
        settings = settings_from(merged)
        if args.script is not None:
            if not args.project:
                parser.error("--script needs --project")
            files, settings = conform_to_script(
                app, files, args.script, settings, merged.cover or [], merged.drop
            )
        settings = brief_graphics(
            app,
            settings,
            merged.screens or [],
            merged.graphics or [],
            merged.intro,
            merged.outro,
            merged.background_footage,
        )
        folder = project_folder(merged, args.script)
        auto = folder is not None and merged.auto_brief is not False
        pictures = args.product_images
        if pictures is None and auto and folder is not None:
            pictures = next((d for d in sorted(folder.iterdir()) if d.name.endswith("_pck")), None)
        if pictures is not None:
            settings = import_product_images(app, pictures, settings, args.script)
        if auto and folder is not None:
            settings = auto_brief(app, settings, merged, folder, files, args.script, args.output)
        settings = thumbnail_settings(app, settings, merged.thumbnail)
        attempts = max(1, merged.gate_iterations or 3)
        for attempt in range(1, attempts + 1):
            result = pipeline.execute(
                RunPipelineCommand(
                    paths=files,
                    asset_ids=args.asset_id,
                    stages=stages,
                    content_uses_video=bool(merged.content_uses_video),
                    capacity=capacity,
                    dry_run=args.dry_run,
                    project=args.project,
                    max_running=args.max_workers,
                    settings=settings,
                ),
                JobContext("cli", token, JobContext.detached().progress),
                ConsoleObserver(args.verbose),
            )
            if isinstance(result, Err):
                print(f"x {result.error.user_message}", file=sys.stderr)
                return EXIT_USAGE
            report = result.value
            gate = next(
                (t.facts for a in report.assets if (t := a.task(TaskKind.DELIVER)) is not None),
                None,
            )
            if not gate or "gate_passed" not in gate:
                break
            print(
                f"quality gate, attempt {attempt}: {'passed' if gate['gate_passed'] else 'failed'}"
            )
            for finding in gate.get("gate_findings") or []:  # type: ignore[union-attr]
                print(f"  - {finding}")
            if gate["gate_passed"] or attempt == attempts or report.cancelled:
                break
            remedies = tuple(str(r) for r in gate.get("gate_remedies") or [])  # type: ignore[union-attr]
            following = next_attempt(settings, remedies)
            if following is None:
                print("  no remedy can change this attempt: a person decides")
                break
            settings = following
            print(f"  next attempt with: {', '.join(remedies)}")
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
