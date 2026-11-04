"""Reuse before generation: rank existing library images for a concept, by words only.

Source priority (owner decision): existing uploads > approved project images > brand assets >
earlier generated images. A new image may be generated only when nothing here matches. Matching
is the share of the concept's words found in an image's name, file name or tags (case-folded,
umlauts kept; a word of four or more letters also matches inside a German compound); no model is
involved, so the ranking is deterministic.
"""

import re
from dataclasses import dataclass
from enum import StrEnum

from media_house.modules.visual_asset_generation.domain.profile import SelectionSettings

_WORD = re.compile(r"[^\W_]+", re.UNICODE)
#: Words shorter than this carry no meaning on their own ("a", "3", "de").
_MIN_WORD = 3
#: From this length a word also matches inside a compound ("rakete" in "raketenbatterie").
_MIN_PART = 4


class SourceTier(StrEnum):
    UPLOAD = "upload"
    APPROVED = "approved"
    BRAND = "brand"
    GENERATED = "generated"


TIER_ORDER: tuple[SourceTier, ...] = (
    SourceTier.UPLOAD,
    SourceTier.APPROVED,
    SourceTier.BRAND,
    SourceTier.GENERATED,
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ImageEntry:
    """What the library says about one image (filled by the application)."""

    asset_id: str
    name: str
    filename: str
    tags: tuple[str, ...]
    tier: SourceTier


@dataclass(frozen=True, slots=True, kw_only=True)
class Candidate:
    asset_id: str
    name: str
    tier: SourceTier
    #: Share of the concept's words this image carries (0-1).
    score: float
    matched: tuple[str, ...]


def words(text: str) -> tuple[str, ...]:
    """Distinct meaningful words of ``text``, case-folded, in order."""
    found = (w.casefold() for w in _WORD.findall(text))
    return tuple(dict.fromkeys(w for w in found if len(w) >= _MIN_WORD))


def tier_of(
    tags: tuple[str, ...],
    *,
    generated: bool,
    in_project: bool,
    settings: SelectionSettings,
) -> SourceTier:
    folded = {t.casefold() for t in tags}
    if generated:
        return SourceTier.GENERATED
    if settings.brand_tag.casefold() in folded:
        return SourceTier.BRAND
    if in_project or settings.approved_tag.casefold() in folded:
        return SourceTier.APPROVED
    return SourceTier.UPLOAD


def rank(
    concept: str,
    entries: list[ImageEntry],
    settings: SelectionSettings,
) -> tuple[Candidate, ...]:
    """Matching images, best source tier first, then best match, then name and id."""
    wanted = words(concept)
    if not wanted:
        return ()
    found: list[Candidate] = []
    for entry in entries:
        carried = set(words(f"{entry.name} {entry.filename} {' '.join(entry.tags)}"))
        matched = tuple(w for w in wanted if _carries(carried, w))
        score = len(matched) / len(wanted)
        if score >= settings.min_score:
            found.append(
                Candidate(
                    asset_id=entry.asset_id,
                    name=entry.name,
                    tier=entry.tier,
                    score=round(score, 6),
                    matched=matched,
                )
            )
    found.sort(key=lambda c: (TIER_ORDER.index(c.tier), -c.score, c.name, c.asset_id))
    return tuple(found[: settings.max_candidates])


def _carries(carried: set[str], word: str) -> bool:
    return word in carried or (len(word) >= _MIN_PART and any(word in c for c in carried))
