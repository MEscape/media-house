"""Process once, check, persist once: the one path every produced picture takes.

1. The library fingerprint (source content + operation + version + canonical config) is the
   cache: a hit is re-measured and judged, never re-rendered.
2. A new picture is rendered into a temporary folder and MEASURED there. An ERROR finding stops
   it: nothing is registered, so the library never holds a picture known to be broken.
3. Otherwise it becomes a derived asset of its source (the library's derivation record is the
   relationship; there is no second one).
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from media_house.modules.media_library.application.contracts import (
    DerivedAssetResultDto,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.visual_asset_generation.application.ports import ImageMeasurer
from media_house.modules.visual_asset_generation.domain.errors import AdjustmentFailed
from media_house.modules.visual_asset_generation.domain.qc import ImageFacts, QcIssue, blocking
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, ExternalSystemError, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)


@dataclass(frozen=True, slots=True, kw_only=True)
class Derivation:
    source: MediaAssetDto
    operation: str
    version: int
    config: dict[str, JsonValue]
    filename: str
    display_name: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Derived[R]:
    #: ``None`` when QC refused the new picture (it was not registered).
    result: DerivedAssetResultDto | None
    #: The renderer's report; ``None`` on a cache hit.
    report: R | None
    facts: ImageFacts
    issues: tuple[QcIssue, ...]


#: Library failure, a refusal for this input, or an unusable environment (model, tool).
type DeriveError = MediaError | AdjustmentFailed | ExternalSystemError
type Judge[R] = Callable[[ImageFacts, R | None, MediaAssetDto | None], list[QcIssue]]


class Deriver:
    def __init__(self, library: MediaLibrary, paths: AppPaths, measurer: ImageMeasurer) -> None:
        self._library = library
        self._paths = paths
        self._measurer = measurer

    def derive[R](
        self,
        spec: Derivation,
        render: Callable[[Path], R],
        metadata: Callable[[R], dict[str, JsonValue]],
        judge: Judge[R],
        ctx: JobContext,
    ) -> Result[Derived[R], DeriveError]:
        fingerprint = self._library.fingerprint(
            spec.source.id, spec.operation, spec.config, spec.version
        )
        if isinstance(fingerprint, Err):
            return fingerprint
        existing = self._library.find_derived_asset(spec.source.id, fingerprint.value)
        if existing is not None:
            path = self._library.local_path(existing.id)
            if isinstance(path, Err):
                return path
            facts = self._measurer.measure(path.value)
            hit = DerivedAssetResultDto(existing, created=False)
            return Ok(
                Derived(
                    result=hit, report=None, facts=facts, issues=tuple(judge(facts, None, existing))
                )
            )

        ctx.raise_if_cancelled()
        with self._paths.temporary_directory(prefix=f"{spec.operation}-") as tmp:
            output = tmp / spec.filename
            try:
                report = render(output)
            except AdjustmentFailed as exc:
                _log.warning("Rendering refused", reason=exc.code, asset_id=spec.source.id)
                return Err(exc)
            except (
                ExternalSystemError
            ) as exc:  # missing model or tool: the environment, not this input
                _log.warning("Renderer unavailable", reason=exc.code, asset_id=spec.source.id)
                return Err(exc)
            facts = self._measurer.measure(output)
            issues = tuple(judge(facts, report, None))
            if blocking(list(issues)):
                _log.warning(
                    "Output failed QC, not registered",
                    operation=spec.operation,
                    asset_id=spec.source.id,
                    codes=[i.code for i in issues],
                )
                return Ok(Derived(result=None, report=report, facts=facts, issues=issues))
            ctx.raise_if_cancelled()
            registered = self._library.register_derived(
                spec.source.id,
                output,
                operation=spec.operation,
                config=spec.config,
                version=spec.version,
                display_name=spec.display_name[:255],
                metadata={"processing_type": spec.operation, "source_asset_id": spec.source.id}
                | metadata(report),
            )
        if isinstance(registered, Err):
            return registered
        return Ok(Derived(result=registered.value, report=report, facts=facts, issues=issues))
