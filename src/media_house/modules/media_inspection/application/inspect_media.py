"""Use case: audio/video asset -> its technical ground truth, reusing what already exists.

The inspection is a derived JSON asset of the source, found by its processing fingerprint, so
the same media is probed once however many modules ask. A ``FULL`` inspection also answers a
``PROBE`` request (it contains everything a probe does).
"""

import time
from dataclasses import dataclass, field, replace

from media_house.core.application.ports import Clock
from media_house.modules.media_inspection.application.inspection_store import InspectionStore
from media_house.modules.media_inspection.application.ports import MediaProber
from media_house.modules.media_inspection.domain.assessment import assess
from media_house.modules.media_inspection.domain.config import InspectionConfig
from media_house.modules.media_inspection.domain.errors import MediaInspectionError
from media_house.modules.media_inspection.domain.model import MediaInspection
from media_house.modules.media_inspection.domain.values import Depth
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
    MediaType,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, InfrastructureError, Ok, Result, ValidationError
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type InspectError = MediaError | MediaInspectionError
_STEPS = 3


@dataclass(frozen=True, slots=True)
class InspectMediaCommand:
    source_asset_id: str
    config: InspectionConfig = field(default_factory=InspectionConfig)


@dataclass(frozen=True, slots=True)
class InspectionResult:
    """The inspection plus where it lives in the Media Library."""

    inspection: MediaInspection
    #: The stored inspection document (a derived asset of the source).
    asset: MediaAssetDto
    #: ``False`` when the identical inspection already existed and nothing was probed.
    created: bool


class InspectMedia:
    """Probe once, persist once, reuse everywhere. Never modifies the media."""

    def __init__(
        self,
        library: MediaLibrary,
        prober: MediaProber,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._prober = prober
        self._clock = clock
        self._store = InspectionStore(library, paths)

    def execute(
        self,
        command: InspectMediaCommand,
        ctx: JobContext,
    ) -> Result[InspectionResult, InspectError]:
        started = time.perf_counter()
        source_result = self._library.get(command.source_asset_id)
        if isinstance(source_result, Err):
            return source_result
        source = source_result.value
        if source.media_type not in {MediaType.AUDIO, MediaType.VIDEO}:
            return Err(
                ValidationError(
                    f"Asset {source.id} is {source.media_type.value}, not audio or video",
                    field="source_asset_id",
                    user_message="Only audio and video files can be inspected.",
                )
            )
        config = command.config

        existing = self._lookup(source.id, config)
        if isinstance(existing, Err):
            return existing
        if existing.value is not None:
            _log.info("Inspection reused", asset_id=existing.value.asset.id, cache_hit=True)
            return Ok(existing.value)

        path = self._library.local_path(source.id)
        if isinstance(path, Err):
            return path
        ctx.raise_if_cancelled()
        ctx.progress.report(1, _STEPS, "Reading the media")
        observed = self._prober.probe(path.value, config, ctx.cancellation)

        ctx.raise_if_cancelled()
        ctx.progress.report(2, _STEPS, "Assessing")
        inspection = assess(source.id, observed, config, now=self._clock.now())

        ctx.progress.report(3, _STEPS, "Saving the inspection")
        stored = self._store.save(source, inspection, config, self._prober.identity)
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Inspection created",
            asset_id=stored.value.id,
            cache_hit=False,
            verdict=inspection.status.verdict.value,
            findings=len(inspection.findings),
            depth=config.depth.value,
            seconds=round(time.perf_counter() - started, 2),
        )
        return Ok(InspectionResult(inspection, stored.value, created=True))

    def find(
        self, asset_id: str, config: InspectionConfig | None = None
    ) -> InspectionResult | None:
        """The stored inspection of ``asset_id``, or ``None``. Never probes and never fails.

        Without ``config`` the default configuration is meant (full depth first, then probe).
        """
        try:
            found = self._lookup(asset_id, config or InspectionConfig())
        except InfrastructureError as exc:  # e.g. FFmpeg is not installed: nothing can be cached
            _log.warning("Inspection lookup unavailable", reason=str(exc))
            return None
        return found.value if isinstance(found, Ok) else None

    # ------------------------------------------------------------------------------------------
    def _lookup(
        self, source_id: str, config: InspectionConfig
    ) -> Result[InspectionResult | None, MediaError]:
        """A stored inspection that satisfies ``config``: its own, or a deeper one."""
        identity = self._prober.identity
        wanted = [config]
        if config.depth is Depth.PROBE:
            wanted.insert(0, replace(config, depth=Depth.FULL))
        for candidate in wanted:
            fingerprint = self._store.fingerprint(source_id, candidate, identity)
            if isinstance(fingerprint, Err):
                return fingerprint
            loaded = self._store.load(source_id, fingerprint.value)
            if loaded is not None:
                return Ok(InspectionResult(*loaded, created=False))
        return Ok(None)
