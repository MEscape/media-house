"""The manifest: every file the project holds, where it came from and what is known about it.

``manifest.json`` is the one file the edit engine reads to know what each file is for. It keeps
the original name (the file itself is renamed), the hash, the role and slot, the scenes served,
the probe data, a speech excerpt, flags, licence and verification state.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from media_house.modules.production_project.domain.assets import AssetFlag, usable
from media_house.modules.production_project.domain.values import Resolution


@dataclass(frozen=True, slots=True)
class ManifestEntry:
    #: The project name (``lidlnico_talking_01.mp4``), relative to the project folder.
    file: str
    original_name: str
    sha256: str
    role: str
    slot: str
    scenes: tuple[str, ...]
    #: Probe data as (key, value) text pairs: ``width``, ``height``, ``fps``, ``duration`` ...
    probe: tuple[tuple[str, str], ...]
    asr_excerpt: str
    flags: tuple[AssetFlag, ...]
    #: ``own``, ``licensed``, ``unknown``.
    license_state: str
    #: ``unchecked``, ``checked``.
    verification_state: str
    #: The library asset this file is (so nothing is probed or analysed twice).
    asset_id: str = ""
    #: Notes on what was done to it (converted from ``.svg.webp`` to ``.png`` ...).
    normalization: str = ""
    #: Where the original lies (it is never moved or changed).
    source_path: str = ""

    @property
    def usable(self) -> bool:
        return usable(self.flags)


@dataclass(frozen=True, slots=True)
class Manifest:
    project_id: str
    slug: str
    entries: tuple[ManifestEntry, ...]
    #: Folders and files that were looked at and left alone (an empty ``07_thumb``).
    ignored: tuple[str, ...] = ()

    def usable_entries(self) -> tuple[ManifestEntry, ...]:
        """What the edit engine may use: no file with an open flag, ever."""
        return tuple(e for e in self.entries if e.usable)

    def flagged(self) -> tuple[ManifestEntry, ...]:
        return tuple(e for e in self.entries if not e.usable)

    def by_slot(self, slot: str) -> tuple[ManifestEntry, ...]:
        return tuple(e for e in self.entries if e.slot == slot)


def resolution_counts(entries: Sequence[ManifestEntry]) -> dict[str, int]:
    counts = {r.value: 0 for r in Resolution}
    for e in entries:
        for f in e.flags:
            counts[f.resolution.value] += 1
    return counts
