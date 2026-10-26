"""What a claim rests on: the detector and version that made it and the inputs it read.

The basis defines "stale": a claim is stale when its detector version differs from the current one
or an input identity changed. A person's claim has the ``manual`` basis.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.shared.errors import InvariantViolation

MANUAL_DETECTOR = "manual"


@dataclass(frozen=True, slots=True)
class Basis:
    detector: str
    version: str
    #: Identities of the inputs read (for example an inspection asset id and its version).
    inputs: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.detector.strip() or not self.version.strip():
            raise InvariantViolation("A basis needs a detector and a version")
        object.__setattr__(self, "inputs", dict(self.inputs))

    @classmethod
    def manual(cls) -> "Basis":
        return cls(MANUAL_DETECTOR, "1")

    def canonical(self) -> str:
        """Stable text form: equal bases give equal text."""
        return json.dumps(
            {"detector": self.detector, "version": self.version, "inputs": dict(self.inputs)},
            sort_keys=True,
            separators=(",", ":"),
        )

    @classmethod
    def from_canonical(cls, text: str) -> "Basis":
        try:
            data = json.loads(text)
            return cls(str(data["detector"]), str(data["version"]), dict(data["inputs"]))
        except (ValueError, KeyError, TypeError) as exc:
            raise InvariantViolation("A stored basis is unreadable") from exc
