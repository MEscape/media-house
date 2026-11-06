"""What is known about the Resolve installation and the connection to it.

Facts only. A connection is ``CONNECTED`` when an actual call came back with a version and a
project manager, never because a process exists.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.resolve_adapter.domain.values import ConnectionState


@dataclass(frozen=True, slots=True, order=True)
class ResolveVersion:
    major: int
    minor: int = 0
    patch: int = 0
    build: int = 0
    suffix: str = ""

    @classmethod
    def parse(cls, parts: Sequence[object]) -> "ResolveVersion | None":
        """From ``Resolve.GetVersion()``: ``[19, 0, 3, 2, ""]`` (anything unusable is ``None``)."""
        numbers: list[int] = []
        suffix = ""
        for part in parts:
            if isinstance(part, bool):
                return None
            if isinstance(part, int):
                numbers.append(part)
            elif isinstance(part, str):
                suffix = part
        if not numbers:
            return None
        numbers = [*numbers, 0, 0, 0, 0][:4]
        return cls(numbers[0], numbers[1], numbers[2], numbers[3], suffix)

    @property
    def text(self) -> str:
        core = ".".join(str(n) for n in (self.major, self.minor, self.patch, self.build))
        return f"{core}{self.suffix}"

    def at_least(self, minimum: tuple[int, ...]) -> bool:
        return (self.major, self.minor, self.patch, self.build)[: len(minimum)] >= minimum


@dataclass(frozen=True, slots=True)
class ResolveEnvironment:
    product: str
    version: ResolveVersion | None
    #: ``studio``, ``free`` or ``unknown`` (read from the product name, never assumed).
    edition: str
    operating_system: str
    scripting_module: str
    database: str | None = None
    databases: tuple[str, ...] = ()
    current_project: str | None = None
    projects: tuple[str, ...] = ()
    #: The API classes whose methods were listed (a project must be open to list the pool's).
    probed: tuple[str, ...] = ()
    #: ``"MediaPool.AppendToTimeline"``: the methods those objects really have.
    methods: tuple[str, ...] = ()
    #: Installed plugins / components, or ``None`` when the API cannot list them.
    plugins: tuple[str, ...] | None = None

    def has_method(self, name: str) -> bool | None:
        """``True`` / ``False`` for a probed class, ``None`` when its class was not probed."""
        owner = name.split(".", 1)[0]
        if owner not in self.probed:
            return None
        return name in self.methods

    @property
    def key(self) -> str:
        """Which Resolve a verified capability was confirmed on."""
        v = self.version.text if self.version else "unknown"
        return f"{self.edition}:{v}:{self.operating_system}"


@dataclass(frozen=True, slots=True)
class ConnectionStatus:
    state: ConnectionState
    detail: str = ""
    installed: bool = False
    running: bool = False
    environment: ResolveEnvironment | None = None

    @property
    def connected(self) -> bool:
        return self.state is ConnectionState.CONNECTED
