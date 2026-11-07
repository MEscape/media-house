"""Module registration: how the revision engine plugs into the application.

No storage of its own: revision plans are documents of the recording's anchor asset in the Media
Library. Every change is carried out through the owner's public contract (the timeline builder, the
audio mix engine, the Resolve adapter); a person's edits in Resolve are read back through the
adapter. No render is made here: a ``PreviewProvider`` (default: none) supplies the preview that
revalidation looks at.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_mix_engine.application.contracts import AudioMixer, MixCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.quality_control.application.contracts import (
    QualityAnalyzer,
    QualityCatalog,
)
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveCatalog,
    ResolveExecutor,
    ResolveSynchronizer,
)
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSessions,
)
from media_house.modules.revision_engine.application.contracts import (
    RevisionCatalog,
    RevisionDecider,
    RevisionExecutorService,
    RevisionPlanner,
    RollbackPlanner,
)
from media_house.modules.revision_engine.application.decide_revision import DecideRevisions
from media_house.modules.revision_engine.application.execute_revision import ExecuteRevisions
from media_house.modules.revision_engine.application.executors import (
    AudioMixChanges,
    Handoff,
    ResolveChanges,
    RevisionExecutor,
    TimelineEdits,
    TimelineRebuild,
)
from media_house.modules.revision_engine.application.plan_revisions import PlanRevisions
from media_house.modules.revision_engine.application.ports import (
    ManualChangeSync,
    NoPreview,
    PreviewProvider,
)
from media_house.modules.revision_engine.application.rollback import PlanRollback, RestoreCheckpoint
from media_house.modules.revision_engine.application.store import RevisionStore
from media_house.modules.revision_engine.infrastructure.resolve_sync import ResolveReadBack
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimelineCatalog,
    TimelineBuilder,
    TimelineEditor,
)
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class RevisionEngineModule:
    name: str = "revision_engine"

    def __init__(self, previews: PreviewProvider | None = None) -> None:
        self._previews = previews

    def register(self, container: Container) -> None:
        container.register_instance(PreviewProvider, self._previews or NoPreview())
        container.register_factory(
            ManualChangeSync, lambda c: ResolveReadBack(c.resolve(ResolveSynchronizer))
        )
        container.register_factory(
            RevisionStore, lambda c: RevisionStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            AudioMixChanges, lambda c: AudioMixChanges(c.resolve(AudioMixer), c.resolve(MixCatalog))
        )
        container.register_factory(
            PlanRevisions,
            lambda c: PlanRevisions(
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(QualityCatalog),
                c.resolve(MixCatalog),
                c.resolve(ReviewSessions),
                c.resolve(ReviewCatalog),
                c.resolve(ManualChangeSync),
                c.resolve(RevisionStore),
                c.resolve(EventPublisher),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            DecideRevisions,
            lambda c: DecideRevisions(
                c.resolve(MediaLibrary),
                c.resolve(ReviewSessions),
                c.resolve(ReviewCatalog),
                c.resolve(RevisionStore),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            ExecuteRevisions,
            lambda c: ExecuteRevisions(
                c.resolve(MediaLibrary),
                c.resolve(ReviewSessions),
                c.resolve(ReviewCatalog),
                c.resolve(RevisionStore),
                _executors(c),
                c.resolve(ManualChangeSync),
                c.resolve(PreviewProvider),
                c.resolve(QualityAnalyzer),
                c.resolve(QualityCatalog),
                c.resolve(MixCatalog),
                c.resolve(ResolveCatalog),
                c.resolve(EventPublisher),
                c.resolve(Clock),
            ),
        )
        container.register_factory(
            PlanRollback,
            lambda c: PlanRollback(
                c.resolve(MediaLibrary),
                c.resolve(ReviewSessions),
                c.resolve(ReviewCatalog),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(RevisionStore),
                c.resolve(Clock),
            ),
        )
        container.register_factory(RevisionPlanner, lambda c: c.resolve(PlanRevisions))
        container.register_factory(RevisionDecider, lambda c: c.resolve(DecideRevisions))
        container.register_factory(RevisionExecutorService, lambda c: c.resolve(ExecuteRevisions))
        container.register_factory(RollbackPlanner, lambda c: c.resolve(PlanRollback))
        container.register_factory(RevisionCatalog, lambda c: c.resolve(RevisionStore))


def _executors(c: Container) -> tuple[RevisionExecutor, ...]:
    mixes = c.resolve(AudioMixChanges)
    return (
        TimelineEdits(c.resolve(TimelineEditor)),
        TimelineRebuild(c.resolve(TimelineBuilder)),
        mixes,
        ResolveChanges(c.resolve(ResolveSynchronizer), c.resolve(ResolveExecutor)),
        RestoreCheckpoint(c.resolve(BuiltTimelineCatalog), c.resolve(TimelineEditor), mixes),
        Handoff(c.resolve(EventPublisher), c.resolve(Clock)),
    )
