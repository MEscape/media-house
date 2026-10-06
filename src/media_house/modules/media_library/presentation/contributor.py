"""What the Media Library module adds to the shell: a page, a menu and shortcuts."""

from collections.abc import Callable

from media_house.modules.media_library.presentation import strings
from media_house.modules.media_library.presentation.viewmodels.media_library_viewmodel import (
    MediaLibraryViewModel,
)
from media_house.modules.media_library.presentation.views.media_library_view import MediaLibraryView
from media_house.presentation.extension import ActionContribution, UiRegistry, ViewContribution


class MediaLibraryUiContributor:
    def __init__(self, viewmodel_factory: Callable[[], MediaLibraryViewModel]) -> None:
        self._viewmodel_factory = viewmodel_factory
        self._viewmodel: MediaLibraryViewModel | None = None

    def contribute(self, registry: UiRegistry) -> None:
        registry.add_view(
            ViewContribution("media_library.main", strings.NAV_TITLE, self._create_view)
        )
        registry.add_action(
            ActionContribution(
                "media_library.refresh",
                strings.ACTION_REFRESH,
                strings.MENU,
                lambda: self._vm().refresh(),
                shortcut="F5",
            ),
        )

    def _vm(self) -> MediaLibraryViewModel:
        if self._viewmodel is None:
            self._viewmodel = self._viewmodel_factory()
        return self._viewmodel

    def _create_view(self) -> MediaLibraryView:
        viewmodel = self._vm()
        view = MediaLibraryView(viewmodel)
        viewmodel.refresh()
        return view
