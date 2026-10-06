"""Module registration: how the Media Library plugs into the application.

This file is the module's own composition root. There is no UI contributor yet: the GUI will
consume the ``MediaLibrary`` contract and contribute its own pages later.
"""

from media_house.core.application.ports import Clock, ProcessRunner
from media_house.core.modules import Container
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.media_library.application.delete_media import DeleteMedia
from media_house.modules.media_library.application.derived_assets import RegisterDerivedAsset
from media_house.modules.media_library.application.groups import ManageGroups
from media_house.modules.media_library.application.import_media import ImportMedia
from media_house.modules.media_library.application.ingest import AssetIngestor
from media_house.modules.media_library.application.library import MediaLibraryService
from media_house.modules.media_library.application.manage_assets import ManageAsset
from media_house.modules.media_library.application.mapping import AssetDtoMapper
from media_house.modules.media_library.application.ports import (
    MediaInspector,
    MediaStorage,
    ThumbnailGenerator,
)
from media_house.modules.media_library.application.queries import MediaQueries
from media_house.modules.media_library.application.thumbnails import Thumbnails
from media_house.modules.media_library.domain.repository import (
    MediaAssetRepository,
    MediaGroupRepository,
)
from media_house.modules.media_library.infrastructure.ffmpeg_thumbnails import (
    FfmpegThumbnailGenerator,
)
from media_house.modules.media_library.infrastructure.file_inspector import FileMediaInspector
from media_house.modules.media_library.infrastructure.local_storage import LocalMediaStorage
from media_house.modules.media_library.infrastructure.sqlite_asset_repository import (
    SqliteMediaAssetRepository,
)
from media_house.modules.media_library.infrastructure.sqlite_database import SqliteMediaDatabase
from media_house.modules.media_library.infrastructure.sqlite_group_repository import (
    SqliteMediaGroupRepository,
)
from media_house.modules.media_library.presentation.contributor import MediaLibraryUiContributor
from media_house.modules.media_library.presentation.viewmodels.media_library_viewmodel import (
    MediaLibraryViewModel,
)
from media_house.presentation.extension import UiContributor
from media_house.presentation.qt.job_bridge import UiJobRunner
from media_house.presentation.qt.status import StatusReporter
from media_house.shared.events import EventPublisher
from media_house.shared.filesystem import AppPaths

#: Database file and blob root, both below ``AppPaths.data_dir`` (back them up together).
DATABASE_FILE_NAME = "media_library.db"
STORAGE_DIRECTORY_NAME = "media"


class MediaLibraryModule:
    name: str = "media_library"

    def register(self, container: Container) -> None:
        container.register_factory(
            SqliteMediaDatabase,
            lambda c: SqliteMediaDatabase(c.resolve(AppPaths).data_dir / DATABASE_FILE_NAME),
        )
        container.register_factory(
            MediaAssetRepository,
            lambda c: SqliteMediaAssetRepository(c.resolve(SqliteMediaDatabase)),
        )
        container.register_factory(
            MediaGroupRepository,
            lambda c: SqliteMediaGroupRepository(c.resolve(SqliteMediaDatabase)),
        )
        container.register_factory(
            MediaStorage,
            lambda c: LocalMediaStorage(c.resolve(AppPaths).data_dir / STORAGE_DIRECTORY_NAME),
        )
        container.register_factory(MediaInspector, lambda _c: FileMediaInspector())
        container.register_factory(
            ThumbnailGenerator,
            lambda c: FfmpegThumbnailGenerator(c.resolve(ProcessRunner)),
        )
        container.register_factory(
            AssetIngestor,
            lambda c: AssetIngestor(
                c.resolve(MediaAssetRepository),
                c.resolve(MediaStorage),
                c.resolve(MediaInspector),
                c.resolve(Clock),
                c.resolve(EventPublisher),
            ),
        )
        container.register_factory(
            AssetDtoMapper,
            lambda c: AssetDtoMapper(c.resolve(MediaGroupRepository)),
        )
        container.register_factory(
            RegisterDerivedAsset,
            lambda c: RegisterDerivedAsset(
                c.resolve(AssetIngestor),
                c.resolve(MediaAssetRepository),
                c.resolve(AssetDtoMapper),
            ),
        )
        container.register_factory(
            MediaLibrary,
            lambda c: MediaLibraryService(
                importer=ImportMedia(
                    c.resolve(AssetIngestor),
                    c.resolve(MediaAssetRepository),
                    c.resolve(MediaGroupRepository),
                    c.resolve(AssetDtoMapper),
                ),
                derived=c.resolve(RegisterDerivedAsset),
                queries=MediaQueries(
                    c.resolve(MediaAssetRepository),
                    c.resolve(AssetDtoMapper),
                    c.resolve(MediaStorage),
                ),
                assets=ManageAsset(
                    c.resolve(MediaAssetRepository),
                    c.resolve(AssetDtoMapper),
                    c.resolve(Clock),
                ),
                groups=ManageGroups(
                    c.resolve(MediaGroupRepository),
                    c.resolve(MediaAssetRepository),
                    c.resolve(Clock),
                    c.resolve(EventPublisher),
                ),
                deleter=DeleteMedia(
                    c.resolve(MediaAssetRepository),
                    c.resolve(MediaStorage),
                    c.resolve(Clock),
                    c.resolve(EventPublisher),
                ),
                thumbnails=Thumbnails(
                    c.resolve(MediaAssetRepository),
                    c.resolve(MediaStorage),
                    c.resolve(ThumbnailGenerator),
                    c.resolve(RegisterDerivedAsset),
                    c.resolve(AssetDtoMapper),
                    c.resolve(AppPaths),
                ),
            ),
        )

        container.add_to_collection(
            UiContributor,
            lambda c: MediaLibraryUiContributor(
                lambda: MediaLibraryViewModel(
                    c.resolve(MediaLibrary),
                    c.resolve(UiJobRunner),
                    c.resolve(StatusReporter),
                )
            ),
        )
