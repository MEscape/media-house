"""Build the timeline of a compiled specification and store it (or reuse the stored one).

The specification arrives in memory from the pipeline, or is read through the compiler's catalog;
the files are looked up in the Media Library and the proxy catalog, never probed. The same
inputs (and the same work by people) reuse the stored build (``created=False``); changed inputs
make the next version, which names the one it supersedes, carries over what is unchanged and
applies the conflict policy to what a person changed.
"""

from dataclasses import dataclass, field

from media_house.core.application.ports import Clock
from media_house.modules.edit_plan_compiler.application.contracts import (
    TimelineCatalog,
    TimelineSpecification,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.proxy_manager.application.contracts import DEFAULT_PROFILE
from media_house.modules.timeline_builder.application import store
from media_house.modules.timeline_builder.application.results import BuiltTimelineResults
from media_house.modules.timeline_builder.application.sources import LiveSourceReader
from media_house.modules.timeline_builder.application.views import asset_ids, spec_facts
from media_house.modules.timeline_builder.domain.engine import (
    BuildMeta,
    build_timeline,
    cache_key,
    input_hash,
)
from media_house.modules.timeline_builder.domain.errors import (
    InvalidBuiltDocument,
    SpecificationRequired,
    TimelineBuilderError,
    TimelineNotBuildable,
)
from media_house.modules.timeline_builder.domain.model import BuiltTimeline, ExternalCheck
from media_house.modules.timeline_builder.domain.preflight import IdentityExpectation
from media_house.modules.timeline_builder.domain.profile import BuilderConfig, Capabilities
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)
_STEPS = 5

type BuildError = MediaError | TimelineBuilderError | InvalidBuiltDocument


@dataclass(frozen=True, slots=True)
class BuildTimelineCommand:
    #: The compiled specification in memory (the pipeline hands it over), with the library
    #: document it was stored as when known. Without it, the newest stored specification of
    #: ``anchor_asset_id`` is read through the compiler's catalog.
    spec: TimelineSpecification | None = None
    spec_asset_id: str | None = None
    anchor_asset_id: str | None = None
    edit_plan_id: str | None = None
    config: BuilderConfig = field(default_factory=BuilderConfig)
    #: What the timeline application that will carry it out can do (default: the model's own).
    capabilities: Capabilities = field(default_factory=Capabilities)
    #: The creator identity the caller requires (a channel's, the same across its videos).
    expect_identity: IdentityExpectation | None = None
    #: Results of checks made on a rendered preview, if there are any.
    checks: tuple[ExternalCheck, ...] = ()
    #: Rebuild every element instead of carrying over the unchanged ones (people's work stays).
    full_rebuild: bool = False
    proxy_profile: str = DEFAULT_PROFILE


@dataclass(frozen=True, slots=True)
class BuildResult:
    timeline: BuiltTimeline
    asset: MediaAssetDto
    #: ``False``: an identical build was already stored and is returned as it is.
    created: bool


class BuildTimeline:
    def __init__(
        self,
        library: MediaLibrary,
        specifications: TimelineCatalog,
        sources: LiveSourceReader,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._specs = specifications
        self._sources = sources
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = BuiltTimelineResults(library)

    def execute(
        self, command: BuildTimelineCommand, ctx: JobContext
    ) -> Result[BuildResult, BuildError]:
        try:
            command.config.check()
        except TimelineBuilderError as exc:
            return Err(exc)
        ctx.progress.report(1, _STEPS, "Reading the compiled specification")
        found = self._specification(command)
        if isinstance(found, Err):
            return found
        spec, spec_asset_id = found.value
        facts = spec_facts(spec)

        ctx.progress.report(2, _STEPS, "Checking the files the timeline points at")
        live = self._sources.read(asset_ids(spec), proxy_profile=command.proxy_profile)
        anchor = self._library.get(spec.anchor_asset_id)
        if isinstance(anchor, Err):
            return anchor

        ctx.progress.report(3, _STEPS, "Looking for an identical build")
        latest = self._results.latest(spec.anchor_asset_id, timeline_id=spec.timeline_id)
        if isinstance(latest, Err):
            return latest
        previous = latest.value[0] if latest.value else None
        inputs = input_hash(
            facts,
            live,
            command.config,
            command.capabilities,
            command.expect_identity,
            command.checks,
            full_rebuild=command.full_rebuild,
        )
        key = cache_key(inputs, previous)
        cached = store.find(self._documents, spec.anchor_asset_id, key)
        if isinstance(cached, Err):
            return cached
        if cached.value is not None:
            _log.info("Built timeline reused", asset_id=cached.value[1].id, cache_hit=True)
            return Ok(BuildResult(cached.value[0], cached.value[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(4, _STEPS, "Building the timeline")
        history = self._results.history(spec.anchor_asset_id, timeline_id=spec.timeline_id)
        meta = BuildMeta(
            build_version=len(history) + 1,
            supersedes=history[0].id if history else None,
            built_at=self._clock.now(),
            spec_asset_id=spec_asset_id,
        )
        built = build_timeline(
            facts,
            live,
            command.config,
            meta=meta,
            previous=previous,
            caps=command.capabilities,
            expect=command.expect_identity,
            checks=command.checks,
            full_rebuild=command.full_rebuild,
        )
        if built.timeline is None:
            problems = [i.message for i in built.errors]
            _log.error("Timeline not buildable", problems=problems[:5], count=len(problems))
            return Err(TimelineNotBuildable(problems))

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        timeline = built.timeline
        stored = store.save(self._documents, anchor.value, timeline, key)
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Built timeline created",
            asset_id=stored.value.id,
            version=meta.build_version,
            mode=timeline.build.mode.value,
            elements=timeline.build.elements_total,
            reused=timeline.build.elements_reused,
            status=timeline.status.value,
        )
        return Ok(BuildResult(timeline, stored.value, created=True))

    def _specification(
        self, command: BuildTimelineCommand
    ) -> Result[tuple[TimelineSpecification, str | None], BuildError]:
        if command.spec is not None:
            return Ok((command.spec, command.spec_asset_id))
        if command.anchor_asset_id is None:
            return Err(SpecificationRequired())
        latest = self._specs.latest(command.anchor_asset_id, edit_plan_id=command.edit_plan_id)
        if isinstance(latest, Err):
            return latest
        if latest.value is None:
            return Err(SpecificationRequired())
        spec, asset = latest.value
        return Ok((spec, asset.id))
