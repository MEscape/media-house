"""Module registration: how the final export plugs into the application.

No storage of its own: export reports are documents of the recording's anchor asset in the Media
Library; the delivered masters go to one folder next to the application's data, under names that
are never reused. The render is the render manager's, the check of the file is export validation's,
every decision is the review's: this module only joins them in the one order that keeps a person in
control.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.export_validation.application.contracts import ExportValidator
from media_house.modules.final_polish.application.contracts import PolishCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.quality_control.application.contracts import QualityCatalog
from media_house.modules.render_manager.application.contracts import (
    RenderCatalog,
    RenderManager,
    RenderProfiles,
)
from media_house.modules.resolve_adapter.application.contracts import (
    ResolveCatalog,
    ResolveExecutor,
)
from media_house.modules.resolve_export.application.approvals import ExportApprovals
from media_house.modules.resolve_export.application.contracts import (
    ExportApprovalService,
    ExportCatalog,
    FinalExporter,
)
from media_house.modules.resolve_export.application.inputs import InputsReader
from media_house.modules.resolve_export.application.ports import DeliveryStore
from media_house.modules.resolve_export.application.run_export import ExportRunner
from media_house.modules.resolve_export.application.store import ExportStore
from media_house.modules.resolve_export.infrastructure.delivery_store import (
    FilesystemDeliveryStore,
)
from media_house.modules.review_orchestrator.application.contracts import (
    ReviewCatalog,
    ReviewSessions,
)
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class ResolveExportModule:
    name: str = "resolve_export"

    def __init__(self, delivery: DeliveryStore | None = None) -> None:
        self._delivery = delivery

    def register(self, container: Container) -> None:
        if self._delivery is not None:
            container.register_instance(DeliveryStore, self._delivery)
        else:
            container.register_factory(
                DeliveryStore,
                lambda c: FilesystemDeliveryStore(c.resolve(AppPaths).data_dir / "deliveries"),
            )
        container.register_factory(
            ExportStore, lambda c: ExportStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            InputsReader,
            lambda c: InputsReader(
                c.resolve(ReviewCatalog),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(QualityCatalog),
                c.resolve(PolishCatalog),
                c.resolve(MixCatalog),
                c.resolve(ResolveCatalog),
                c.resolve(RenderProfiles),
            ),
        )
        container.register_factory(
            ExportApprovals,
            lambda c: ExportApprovals(
                c.resolve(InputsReader), c.resolve(ReviewSessions), c.resolve(ResolveExecutor)
            ),
        )
        container.register_factory(
            ExportRunner,
            lambda c: ExportRunner(
                c.resolve(MediaLibrary),
                c.resolve(InputsReader),
                c.resolve(ReviewSessions),
                c.resolve(RenderManager),
                c.resolve(RenderCatalog),
                c.resolve(ExportValidator),
                c.resolve(ExportStore),
                c.resolve(DeliveryStore),
                c.resolve(EventPublisher),
                c.resolve(Clock),
            ),
        )
        container.register_factory(ExportApprovalService, lambda c: c.resolve(ExportApprovals))
        container.register_factory(FinalExporter, lambda c: c.resolve(ExportRunner))
        container.register_factory(ExportCatalog, lambda c: c.resolve(ExportStore))
