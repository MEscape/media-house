"""A/B/C variants of one brief, for YouTube's "Test & Compare" (up to three thumbnails).

Each variant changes ONE thing, so a test result says which idea won:

* ``a`` clean: the brief without the story badge.
* ``b`` story: with the struck-through badge (only when the brief has one).
* ``c`` headline: the alternate headline (with the badge when there is one).
"""

from dataclasses import dataclass, replace
from typing import Final

from media_house.modules.thumbnail_design.domain.brief import ThumbnailBrief

MAX_VARIANTS: Final = 3


@dataclass(frozen=True, slots=True)
class Variant:
    name: str
    label: str
    brief: ThumbnailBrief


def variants_of(brief: ThumbnailBrief) -> tuple[Variant, ...]:
    variants = [Variant("a", "A · clean", brief.without_badge())]
    if brief.badge is not None:
        variants.append(Variant("b", f"B · {brief.badge.text}", brief))
    if brief.alternate_headline is not None:
        variants.append(
            Variant(
                "c",
                "C · " + " ".join(brief.alternate_headline.caps),
                replace(brief, headline=brief.alternate_headline),
            )
        )
    return tuple(variants[:MAX_VARIANTS])
