"""What a feature module must provide to be plugged into the application."""

from collections.abc import Sequence
from typing import Protocol

from media_house.core.modules.container import Container, DependencyError


class ApplicationModule(Protocol):
    """A module registers its services (and optional UI contributors) in the container.

    Modules never know about each other: cross-module collaboration goes through
    the public contracts in ``modules.<name>.application.contracts``.
    """

    name: str

    def register(self, container: Container) -> None: ...


def install_modules(modules: Sequence[ApplicationModule], container: Container) -> None:
    seen: set[str] = set()
    for module in modules:
        if module.name in seen:
            raise DependencyError(f"Module '{module.name}' is installed twice")
        seen.add(module.name)
        module.register(container)
