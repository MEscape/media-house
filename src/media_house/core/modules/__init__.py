"""Module contract and composition container."""

from media_house.core.modules.container import Container, DependencyError
from media_house.core.modules.contract import ApplicationModule, install_modules

__all__ = ["ApplicationModule", "Container", "DependencyError", "install_modules"]
