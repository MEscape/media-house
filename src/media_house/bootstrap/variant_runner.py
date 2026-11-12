"""One pipeline run per context variant, through the pipeline's public contract.

Lives in the composition root because only the composition root may know the pipeline: the
production project asks for a ``VariantRunner`` (its own port) and this wires the pipeline in.

The stages are the ones that make a built timeline (story, edit, graphics, timeline); the context
text goes in as ``story_context``, so a run with the 60-word variant and one with the 460-word
variant differ in exactly that. No render: the measurements come from the built timeline.
"""

from collections.abc import Sequence
from pathlib import Path

from media_house.core.modules import Container
from media_house.modules.media_pipeline.application.contracts import (
    LAYER4_STAGES,
    LAYER5_STAGES,
    MediaPipeline,
    RunPipelineCommand,
    StageSettings,
)
from media_house.modules.production_project.application.ports import VariantRunner
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result, ValidationError


class PipelineVariantRunner:
    """Structurally implements ``VariantRunner``."""

    def __init__(self, pipeline: MediaPipeline) -> None:
        self._pipeline = pipeline

    def run(
        self, paths: Sequence[Path], context_text: str, output_format: str, ctx: JobContext
    ) -> Result[str, ValidationError]:
        if not paths:
            return Err(
                ValidationError(
                    "no media",
                    user_message="Für den Testlauf braucht es mindestens eine Aufnahme.",
                )
            )
        ran = self._pipeline.execute(
            RunPipelineCommand(
                paths=list(paths),
                stages=LAYER4_STAGES | LAYER5_STAGES,
                project=len(paths) > 1,
                settings=StageSettings(
                    output_format=output_format, layer4_render=False, story_context=context_text
                ),
            ),
            ctx,
        )
        if isinstance(ran, Err):
            return Err(
                ValidationError(
                    str(ran.error), user_message="Der Testlauf der Pipeline ist fehlgeschlagen."
                )
            )
        report = ran.value
        if not report.ok or not report.assets:
            return Err(
                ValidationError(
                    "pipeline run not ok",
                    user_message="Der Testlauf ist nicht vollständig durchgelaufen.",
                )
            )
        return Ok(report.assets[0].asset_id)


class VariantRunnerModule:
    """Binds the production project's ``VariantRunner`` port to the pipeline."""

    name: str = "variant_runner"

    def register(self, container: Container) -> None:
        container.register_factory(
            VariantRunner, lambda c: PipelineVariantRunner(c.resolve(MediaPipeline))
        )
