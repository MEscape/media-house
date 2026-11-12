"""Module registration: the style memory owns ``style_memory.db`` and exposes its contracts.

It reads no media and calls no model. Its cold start (the MEsc profile and the first session's
events) is stored the first time anything asks for guidance.
"""

from media_house.core.application.ports import Clock
from media_house.core.modules import Container
from media_house.modules.style_memory.application.contracts import (
    PreferenceRecorder,
    StyleGuidance,
    StyleReview,
)
from media_house.modules.style_memory.application.guide import GuideStyle
from media_house.modules.style_memory.application.library import (
    ExemplarLibrary,
    ManageProfile,
    ReferenceStyles,
    SeedStyleMemory,
    TransferStyleMemory,
)
from media_house.modules.style_memory.application.memory import (
    DistillPreferences,
    RecordPreference,
    ReviewRules,
)
from media_house.modules.style_memory.application.ports import StyleStore
from media_house.modules.style_memory.infrastructure.sqlite_store import SqliteStyleStore
from media_house.shared.filesystem import AppPaths


class StyleMemoryModule:
    name: str = "style_memory"

    def register(self, container: Container) -> None:
        container.register_factory(
            StyleStore, lambda c: SqliteStyleStore(c.resolve(AppPaths).data_dir / "style_memory.db")
        )
        container.register_factory(
            SeedStyleMemory, lambda c: SeedStyleMemory(c.resolve(StyleStore), c.resolve(Clock))
        )
        container.register_factory(GuideStyle, lambda c: _seeded_guide(c))
        container.register_factory(StyleGuidance, lambda c: c.resolve(GuideStyle))
        container.register_factory(
            RecordPreference, lambda c: RecordPreference(c.resolve(StyleStore), c.resolve(Clock))
        )
        container.register_factory(PreferenceRecorder, lambda c: c.resolve(RecordPreference))
        container.register_factory(
            DistillPreferences,
            lambda c: DistillPreferences(c.resolve(StyleStore), c.resolve(Clock)),
        )
        container.register_factory(
            ReviewRules, lambda c: ReviewRules(c.resolve(StyleStore), c.resolve(Clock))
        )
        container.register_factory(StyleReview, lambda c: c.resolve(ReviewRules))
        container.register_factory(
            ManageProfile, lambda c: ManageProfile(c.resolve(StyleStore), c.resolve(Clock))
        )
        container.register_factory(
            ExemplarLibrary, lambda c: ExemplarLibrary(c.resolve(StyleStore), c.resolve(Clock))
        )
        container.register_factory(
            ReferenceStyles, lambda c: ReferenceStyles(c.resolve(StyleStore), c.resolve(Clock))
        )
        container.register_factory(
            TransferStyleMemory, lambda c: TransferStyleMemory(c.resolve(StyleStore))
        )


def _seeded_guide(c: Container) -> GuideStyle:
    c.resolve(SeedStyleMemory).execute()
    return GuideStyle(c.resolve(StyleStore))
