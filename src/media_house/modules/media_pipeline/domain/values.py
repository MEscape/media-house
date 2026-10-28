"""The vocabulary of a pipeline run: what a task is, where it runs, how it ended."""

from dataclasses import dataclass
from enum import StrEnum

from media_house.shared.errors import InvariantViolation


class TaskKind(StrEnum):
    """One schedulable unit of work on one asset. The owner of each is a different module."""

    INSPECT = "inspect"
    THUMBNAIL = "thumbnail"
    PROXY = "proxy"
    AUDIO_IMPROVEMENT = "audio_improvement"
    VIDEO_IMPROVEMENT = "video_improvement"
    AUDIO_ANALYSIS = "audio_analysis"
    VIDEO_ANALYSIS = "video_analysis"
    CONTENT_ANALYSIS = "content_analysis"
    ALIGNMENT = "alignment"
    STORY = "story"
    PACING = "pacing"
    EDIT_PLAN = "edit_plan"
    RELATIONSHIPS = "relationships"


class Lane(StrEnum):
    """A class of hardware a task occupies. Each lane has a capacity; a task takes one slot."""

    LIGHT = "light"  # short probes and lookups
    CPU = "cpu"  # FFmpeg encodes/decodes, signal processing
    GPU = "gpu"  # model inference that wants the whole accelerator


class TaskStatus(StrEnum):
    SUCCEEDED = "succeeded"  # the owner computed something new
    REUSED = "reused"  # a valid stored result was served; nothing was computed
    PARTIAL = "partial"  # a result exists but some part of it is missing
    FAILED = "failed"
    SKIPPED_DISABLED = "skipped_disabled"
    SKIPPED_DEPENDENCY = "skipped_dependency"  # a required upstream task did not deliver
    SKIPPED_UNAVAILABLE = (
        "skipped_unavailable"  # does not apply (no audio track) or a capability is missing
    )
    CANCELLED = "cancelled"

    @property
    def delivered(self) -> bool:
        """Whether downstream tasks that REQUIRE this one may run."""
        return self in (TaskStatus.SUCCEEDED, TaskStatus.REUSED, TaskStatus.PARTIAL)


@dataclass(frozen=True, slots=True, order=True)
class TaskId:
    asset_id: str
    kind: TaskKind

    def __post_init__(self) -> None:
        if not self.asset_id:
            raise InvariantViolation("TaskId needs an asset id")

    def __str__(self) -> str:
        return f"{self.kind.value}[{self.asset_id}]"
