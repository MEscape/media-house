"""What the Control Center adds to the shell: four pages and one menu action.

Background work shows in the shell's existing "Background Jobs" dock (every job goes through
``UiJobRunner``); no second job monitor is contributed.
"""

from collections.abc import Callable

from media_house.modules.control_center.presentation import strings
from media_house.modules.control_center.presentation.viewmodels.pipeline_viewmodel import (
    PipelineViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.resolve_viewmodel import (
    ResolveViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.review_viewmodel import (
    ReviewViewModel,
)
from media_house.modules.control_center.presentation.viewmodels.sound_library_viewmodel import (
    SoundLibraryViewModel,
)
from media_house.modules.control_center.presentation.views.pipeline_view import PipelineView
from media_house.modules.control_center.presentation.views.resolve_view import ResolveView
from media_house.modules.control_center.presentation.views.review_view import ReviewView
from media_house.modules.control_center.presentation.views.sound_library_view import (
    SoundLibraryView,
)
from media_house.presentation.extension import ActionContribution, UiRegistry, ViewContribution


class ControlCenterUiContributor:
    def __init__(
        self,
        *,
        resolve: Callable[[], ResolveViewModel],
        sounds: Callable[[], SoundLibraryViewModel],
        review: Callable[[], ReviewViewModel],
    ) -> None:
        self._resolve_factory = resolve
        self._sounds_factory = sounds
        self._review_factory = review
        self._resolve: ResolveViewModel | None = None

    def contribute(self, registry: UiRegistry) -> None:
        registry.add_view(
            ViewContribution(
                "control_center.pipeline",
                strings.NAV_PIPELINE,
                lambda: PipelineView(PipelineViewModel()),
            )
        )
        registry.add_view(
            ViewContribution(
                "control_center.resolve",
                strings.NAV_RESOLVE,
                lambda: ResolveView(self._resolve_vm()),
            )
        )
        registry.add_view(
            ViewContribution("control_center.sounds", strings.NAV_SOUNDS, self._sounds_view)
        )
        registry.add_view(
            ViewContribution("control_center.review", strings.NAV_REVIEW, self._review_view)
        )
        registry.add_action(
            ActionContribution(
                "control_center.resolve.test",
                strings.ACTION_TEST_CONNECTION,
                strings.MENU,
                lambda: self._resolve_vm().test_connection(),
            )
        )

    def _resolve_vm(self) -> ResolveViewModel:
        if self._resolve is None:
            self._resolve = self._resolve_factory()
        return self._resolve

    def _sounds_view(self) -> SoundLibraryView:
        viewmodel = self._sounds_factory()
        view = SoundLibraryView(viewmodel)
        viewmodel.refresh()
        return view

    def _review_view(self) -> ReviewView:
        viewmodel = self._review_factory()
        view = ReviewView(viewmodel)
        viewmodel.load()
        return view
