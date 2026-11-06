"""Module registration: how the Timeline Builder plugs into the application.

No storage of its own: built timelines are ``built_timeline`` documents of the edit's anchor asset
in the Media Library. The specification comes from ``edit_plan_compiler``, the files from the
Media Library and the proxy catalog; nothing is probed, planned, rendered or sent to an editing
application here.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.edit_plan_compiler.application.contracts import TimelineCatalog
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.proxy_manager.application.contracts import ProxyCatalog
from media_house.modules.timeline_builder.application.build_timeline import BuildTimeline
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimelineCatalog,
    TimelineBuilder,
    TimelineEditor,
)
from media_house.modules.timeline_builder.application.record_edits import RecordHumanEdits
from media_house.modules.timeline_builder.application.results import BuiltTimelineResults
from media_house.modules.timeline_builder.application.sources import LiveSourceReader
from media_house.shared.filesystem import AppPaths


class TimelineBuilderModule:
    name: str = "timeline_builder"

    def register(self, container: Container) -> None:
        container.register_factory(
            LiveSourceReader,
            lambda c: LiveSourceReader(c.resolve(MediaLibrary), c.resolve(ProxyCatalog)),
        )
        container.register_factory(
            BuildTimeline,
            lambda c: BuildTimeline(
                c.resolve(MediaLibrary),
                c.resolve(TimelineCatalog),
                c.resolve(LiveSourceReader),
                c.resolve(AppPaths),
                c.resolve(Clock),
            ),
        )
        container.register_factory(TimelineBuilder, lambda c: c.resolve(BuildTimeline))
        container.register_factory(
            RecordHumanEdits,
            lambda c: RecordHumanEdits(
                c.resolve(MediaLibrary), c.resolve(AppPaths), c.resolve(Clock)
            ),
        )
        container.register_factory(TimelineEditor, lambda c: c.resolve(RecordHumanEdits))
        container.register_factory(
            BuiltTimelineResults, lambda c: BuiltTimelineResults(c.resolve(MediaLibrary))
        )
        container.register_factory(BuiltTimelineCatalog, lambda c: c.resolve(BuiltTimelineResults))
