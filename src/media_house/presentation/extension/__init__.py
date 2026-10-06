"""Extension points through which modules contribute UI without the shell knowing them."""

from media_house.presentation.extension.registry import (
    ActionContribution,
    DockContribution,
    UiContributor,
    UiRegistry,
    ViewContribution,
)

__all__ = [
    "ActionContribution",
    "DockContribution",
    "UiContributor",
    "UiRegistry",
    "ViewContribution",
]
