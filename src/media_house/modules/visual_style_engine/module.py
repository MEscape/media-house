"""Module registration: how the Visual Style Engine plugs into the application.

It owns no storage and no UI: resolved styles are ``visual_style`` JSON documents derived from
the project's anchor asset in the Media Library; brand assets are library assets bound by id;
the only files it ships are the bundled OFL fonts. Layer 4 modules resolve through
``StyleResolver``, read through ``VisualStyleCatalog`` and validate through ``StyleValidator``.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.media_library.application.contracts import MediaLibrary
from media_house.modules.visual_style_engine.application.contracts import (
    FontFiles,
    StyleResolver,
    StyleValidator,
    UsageValidator,
    VisualStyleCatalog,
)
from media_house.modules.visual_style_engine.application.resolve_style import ResolveStyle
from media_house.modules.visual_style_engine.application.results import VisualStyleResults
from media_house.modules.visual_style_engine.infrastructure.font_files import PackagedFontFiles
from media_house.shared.filesystem import AppPaths


class VisualStyleEngineModule:
    name: str = "visual_style_engine"

    def register(self, container: Container) -> None:
        container.register_factory(PackagedFontFiles, lambda c: PackagedFontFiles())
        container.register_factory(FontFiles, lambda c: c.resolve(PackagedFontFiles))
        container.register_factory(
            ResolveStyle,
            lambda c: ResolveStyle(
                c.resolve(MediaLibrary), c.resolve(FontFiles), c.resolve(AppPaths), c.resolve(Clock)
            ),
        )
        container.register_factory(StyleResolver, lambda c: c.resolve(ResolveStyle))
        container.register_factory(
            VisualStyleResults, lambda c: VisualStyleResults(c.resolve(MediaLibrary))
        )
        container.register_factory(VisualStyleCatalog, lambda c: c.resolve(VisualStyleResults))
        container.register_factory(StyleValidator, lambda c: UsageValidator())
