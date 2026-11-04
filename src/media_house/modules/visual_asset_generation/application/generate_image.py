"""Use case: generate an illustrative picture, only when policy and availability both allow it.

Order: the policy refuses what must never be generated (logos, product photos, evidence,
realistic footage) and anything an existing library image already covers (the selection runs
here, it is not taken on trust); then a generator that is not available fails explicitly. On
this installation the generator is ``UnavailableImageGenerator``: every allowed request ends in
``GenerationNotAvailable``. Nothing is ever faked.
"""

from dataclasses import dataclass, field

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    ImportResultDto,
    MediaError,
    MediaLibrary,
    SourceType,
)
from media_house.modules.visual_asset_generation.application.ports import (
    ImageGenerator,
    ImageMeasurer,
)
from media_house.modules.visual_asset_generation.application.select_images import (
    SelectImages,
    SelectImagesCommand,
)
from media_house.modules.visual_asset_generation.domain.errors import (
    GenerationNotAvailable,
    GenerationRefused,
    VisualAssetError,
)
from media_house.modules.visual_asset_generation.domain.generation import (
    GENERATED_TAG,
    GenerationRecord,
    GenerationRequest,
    generation_problems,
)
from media_house.modules.visual_asset_generation.domain.profile import VisualAssetConfig
from media_house.modules.visual_asset_generation.domain.qc import blocking, check_file
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Result
from media_house.shared.filesystem import AppPaths

type GenerateError = MediaError | VisualAssetError


@dataclass(frozen=True, slots=True, kw_only=True)
class GenerateImageCommand:
    request: GenerationRequest
    project_group_ids: tuple[str, ...] = ()
    config: VisualAssetConfig = field(default_factory=VisualAssetConfig)


class GenerateImage:
    def __init__(
        self,
        library: MediaLibrary,
        paths: AppPaths,
        clock: Clock,
        selector: SelectImages,
        generator: ImageGenerator,
        measurer: ImageMeasurer,
    ) -> None:
        self._library = library
        self._paths = paths
        self._clock = clock
        self._selector = selector
        self._generator = generator
        self._measurer = measurer

    def execute(
        self, command: GenerateImageCommand, ctx: JobContext
    ) -> Result[ImportResultDto, GenerateError]:
        request = command.request
        existing = self._selector.execute(
            SelectImagesCommand(
                concept=request.concept,
                project_group_ids=command.project_group_ids,
                config=command.config,
            )
        )
        problems = generation_problems(request, len(existing.candidates))
        if problems:
            return Err(GenerationRefused(problems))
        reason = self._generator.unavailable()
        if reason is not None:
            return Err(GenerationNotAvailable(reason))
        ctx.raise_if_cancelled()
        with self._paths.temporary_directory(prefix="generate-") as tmp:
            output = tmp / "generated.png"
            made = self._generator.generate(request, output, ctx.cancellation)
            issues = check_file(
                self._measurer.measure(output), request.request_id, expected_format="PNG"
            )
            if blocking(issues):
                return Err(GenerationNotAvailable("; ".join(i.message for i in issues)))
            record = GenerationRecord(
                request=request,
                generator=self._generator.identity(),
                model=made.model,
                model_version=made.model_version,
                seed=made.seed,
                created_at=self._clock.now(),
            )
            return self._library.import_file(
                output,
                display_name=f"{request.concept} (generated)",
                tags=(GENERATED_TAG,),
                metadata=record.to_metadata(),
                source_type=SourceType.AI_GENERATED,
                source_provider=self._generator.identity(),
            )
