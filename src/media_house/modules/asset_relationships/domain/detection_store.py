"""The port that stores what detection derives: marks, indexed facts and the walk's cursors.

All of it is derived and disposable. A mark records that a detector looked at an asset on certain
inputs (so "found nothing" differs from "never looked" and an unchanged asset costs a comparison);
a fact is the few values copied from an inspection so candidates can be found with an indexed query
instead of reading every other asset. Deleting every row costs time and never changes a claim or a
decision: nothing here is read to decide a claim's status.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from media_house.modules.asset_relationships.domain.detection import Fact, Observed


class MarkOutcome(StrEnum):
    #: At least one candidate was proposed.
    FOUND = "found"
    #: Looked at, and nothing (or no usable fact) was found.
    NONE = "none"
    #: There was no inspection to read. Never "none": absence of an inspection is not absence of
    #: relationships, and the asset is looked at again.
    UNAVAILABLE = "unavailable"
    #: More candidates exist than the cap allows; the first ones in a fixed order were proposed.
    CAPPED = "capped"


@dataclass(frozen=True, slots=True)
class Mark:
    asset_id: str
    detector: str
    detector_version: str
    input_identity: str
    outcome: MarkOutcome
    candidates_found: int


class DetectionStore(Protocol):
    def get_mark(self, asset_id: str, detector: str) -> Mark | None: ...

    def put_mark(self, mark: Mark, *, analyzed_at: str) -> None:
        """Upsert. Written LAST for an asset, so it is the commit record of its analysis."""
        ...

    def replace_fact(
        self, asset_id: str, detector: str, fact: Fact | None, *, identity: str, inspection_id: str
    ) -> None:
        """Set this asset's fact for the detector, or remove it when ``fact`` is ``None``."""
        ...

    def overlapping(
        self, detector: str, start_us: int, end_us: int, *, exclude: str, limit: int
    ) -> Sequence[Observed]:
        """Other assets' spans intersecting ``[start, end)``, longest overlap first, then id."""
        ...

    def in_group(
        self, detector: str, group_key: str, *, exclude: str, limit: int
    ) -> Sequence[Observed]:
        """Other assets with the same group key, in id order."""
        ...

    def get_state(self, key: str) -> str | None: ...

    def cas_state(self, key: str, expected: str | None, new: str | None) -> bool:
        """Set ``key`` to ``new`` (``None`` removes it) only if it still equals ``expected``."""
        ...
