"""Module registration: how export validation plugs into the application.

No storage of its own: reports are documents of the recording's anchor asset in the Media Library.
Every measurement of the file goes through the shared ``MediaAnalyzer`` port; this module names no
media tool.
"""

from media_house.core.application.ports import Clock, MediaAnalyzer
from media_house.core.modules import Container
from media_house.modules.audio_mix_engine.application.contracts import MixCatalog
from media_house.modules.export_validation.application.catalog import ValidationHistory
from media_house.modules.export_validation.application.contracts import (
    ExportValidationCatalog,
    ExportValidator,
)
from media_house.modules.export_validation.application.store import ValidationStore
from media_house.modules.export_validation.application.validate import ValidateExport
from media_house.modules.media_inspection.application.contracts import MediaInspector
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.render_manager.application.contracts import RenderCatalog, RenderProfiles
from media_house.modules.review_orchestrator.application.contracts import ReviewSessions
from media_house.modules.timeline_builder.application.contracts import BuiltTimelineCatalog
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths


class ExportValidationModule:
    name: str = "export_validation"

    def register(self, container: Container) -> None:
        container.register_factory(
            ValidationStore, lambda c: ValidationStore(c.resolve(MediaLibrary), c.resolve(AppPaths))
        )
        container.register_factory(
            ValidateExport,
            lambda c: ValidateExport(
                c.resolve(MediaLibrary),
                c.resolve(BuiltTimelineCatalog),
                c.resolve(MediaInspector),
                c.resolve(MediaAnalyzer),
                c.resolve(RenderCatalog),
                c.resolve(RenderProfiles),
                c.resolve(MixCatalog),
                c.resolve(ReviewSessions),
                c.resolve(ValidationStore),
                c.resolve(AppPaths),
                c.resolve(Clock),
                c.resolve(EventPublisher),
            ),
        )
        container.register_factory(ExportValidator, lambda c: c.resolve(ValidateExport))
        container.register_factory(
            ValidationHistory, lambda c: ValidationHistory(c.resolve(ValidationStore))
        )
        container.register_factory(ExportValidationCatalog, lambda c: c.resolve(ValidationHistory))
