"""The module's PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``workspace``.
Keep it small and stable: it is a promise.
"""

from collections.abc import Sequence
from typing import Protocol

from media_house.modules.workspace.application.dto import WorkspaceDto


class WorkspaceCatalog(Protocol):
    def list_workspaces(self) -> Sequence[WorkspaceDto]: ...
