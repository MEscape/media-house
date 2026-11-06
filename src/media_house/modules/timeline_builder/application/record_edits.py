"""Record what a person changed in a built timeline, as a new version of it.

The changes (``HumanEdit``) come from whatever synchronises the timeline with an application
(later: ``resolve_adapter``). Nothing is rebuilt: the stored build gets the changes, marked with
who made them and which AI version they were made on, and the next ``BuildTimeline`` applies the
conflict policy instead of overwriting them. A change that leaves the timeline invalid is refused
whole.
"""

from dataclasses import dataclass, field

from media_house.core.application.ports import Clock
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    MediaError,
    MediaLibrary,
)
from media_house.modules.timeline_builder.application import store
from media_house.modules.timeline_builder.application.build_timeline import BuildResult
from media_house.modules.timeline_builder.application.results import BuiltTimelineResults
from media_house.modules.timeline_builder.domain.engine import BuildMeta, cache_key
from media_house.modules.timeline_builder.domain.errors import (
    InvalidBuiltDocument,
    InvalidHumanEdit,
    NothingBuilt,
    TimelineBuilderError,
)
from media_house.modules.timeline_builder.domain.human import HumanEdit, apply_edits
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type RecordError = MediaError | TimelineBuilderError | InvalidBuiltDocument


@dataclass(frozen=True, slots=True)
class RecordEditsCommand:
    anchor_asset_id: str
    edits: tuple[HumanEdit, ...]
    timeline_id: str | None = None
    config: BuilderConfig = field(default_factory=BuilderConfig)
    capabilities: Capabilities = field(default_factory=Capabilities)


class RecordHumanEdits:
    def __init__(self, library: MediaLibrary, paths: AppPaths, clock: Clock) -> None:
        self._library = library
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = BuiltTimelineResults(library)

    def execute(
        self, command: RecordEditsCommand, ctx: JobContext
    ) -> Result[BuildResult, RecordError]:
        latest = self._results.latest(command.anchor_asset_id, timeline_id=command.timeline_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(NothingBuilt())
        timeline, asset = latest.value
        ctx.raise_if_cancelled()
        meta = BuildMeta(
            build_version=timeline.build.build_version + 1,
            supersedes=asset.id,
            built_at=self._clock.now(),
            spec_asset_id=timeline.build.spec_asset_id,
        )
        try:
            edited = apply_edits(
                timeline,
                command.edits,
                meta=meta,
                config=command.config,
                caps=command.capabilities,
            )
        except InvalidHumanEdit as exc:
            return Err(exc)
        anchor = self._library.get(command.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor
        # The key a later build of the SAME inputs will compute: it then finds this document.
        key = cache_key(timeline.build.input_hash, edited)
        existing = store.find(self._documents, command.anchor_asset_id, key)
        if isinstance(existing, Err):
            return existing
        if existing.value is not None:
            return Ok(BuildResult(existing.value[0], existing.value[1], created=False))
        stored = store.save(self._documents, anchor.value, edited, key)
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Human edits recorded",
            asset_id=stored.value.id,
            version=meta.build_version,
            edits=len(command.edits),
        )
        return Ok(BuildResult(edited, stored.value, created=True))
