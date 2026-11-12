"""The Media Pipeline PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``media_pipeline``.

``MediaPipeline.execute`` imports inputs, plans every asset, runs one dependency graph and returns
a ``PipelineReport``. It is blocking: run it as a job. It computes nothing itself; every result
comes from the module that owns it and is stored where that owner stores it, so running the same
inputs again reports ``reused`` instead of doing the work twice.
"""

from typing import Protocol

from media_house.modules.media_pipeline.application.auto_brief import (
    AUTO_KEYS,
    AutoBriefCommand,
    AutoBriefer,
    AutoBriefResult,
    apply_auto_brief,
    decisions,
)
from media_house.modules.media_pipeline.application.executors import StageSettings
from media_house.modules.media_pipeline.application.layer4_inputs import GraphicWish
from media_house.modules.media_pipeline.application.report import (
    AssetReport,
    InputFailure,
    PipelineReport,
    TaskReport,
)
from media_house.modules.media_pipeline.application.run_pipeline import (
    DEFAULT_STAGES,
    LAYER4_STAGES,
    LAYER5_STAGES,
    RunPipelineCommand,
)
from media_house.modules.media_pipeline.application.short_iterate import next_attempt
from media_house.modules.media_pipeline.application.tasks import RunObserver, TaskRecord
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskKind, TaskStatus
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Result, ValidationError


class MediaPipeline(Protocol):
    def execute(
        self,
        command: RunPipelineCommand,
        ctx: JobContext,
        observer: RunObserver | None = None,
    ) -> Result[PipelineReport, ValidationError]: ...


__all__ = [
    "AUTO_KEYS",
    "DEFAULT_STAGES",
    "LAYER4_STAGES",
    "LAYER5_STAGES",
    "AssetReport",
    "AutoBriefCommand",
    "AutoBriefResult",
    "AutoBriefer",
    "GraphicWish",
    "InputFailure",
    "Lane",
    "MediaPipeline",
    "PipelineReport",
    "RunObserver",
    "RunPipelineCommand",
    "StageSettings",
    "TaskId",
    "TaskKind",
    "TaskRecord",
    "TaskReport",
    "TaskStatus",
    "apply_auto_brief",
    "decisions",
    "next_attempt",
]
