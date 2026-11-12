"""Checks of a rendered thumbnail, from what the renderer reports it drew (never by guessing).

Thumbnails are judged in the feed, where they are about 320 px wide: a headline that is not
readable there, or text under YouTube's duration chip, costs clicks.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from media_house.modules.thumbnail_design.domain.brief import JsonValue
from media_house.modules.thumbnail_design.domain.layout import HEIGHT, TIMESTAMP_ZONE, WIDTH

type Box = tuple[int, int, int, int]

#: The feed width a thumbnail is judged at.
FEED_WIDTH: Final = 320
#: Smallest headline glyph height (px) that still reads in the feed.
MIN_FEED_HEADLINE_PX: Final = 20
#: The creator's picture should be a large part of the frame (faces drive clicks).
MIN_REACTION_SHARE: Final = 0.18
#: Elements that must stay clear of the duration chip.
_KEEP_CLEAR: Final = ("headline", "price", "button", "badge")


class Severity(StrEnum):
    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class QcIssue:
    code: str
    severity: Severity
    message: str

    def to_json(self) -> dict[str, JsonValue]:
        return {"code": self.code, "severity": self.severity.value, "message": self.message}

    @classmethod
    def from_json(cls, data: Mapping[str, JsonValue]) -> "QcIssue":
        return cls(str(data["code"]), Severity(str(data["severity"])), str(data["message"]))


def _area(box: Box) -> int:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


def _overlap(a: Box, b: Box) -> int:
    return _area((max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])))


def judge(
    *,
    width: int,
    height: int,
    boxes: Mapping[str, Box],
    headline_px: Sequence[int],
    skipped_products: int = 0,
) -> tuple[QcIssue, ...]:
    issues: list[QcIssue] = []
    if (width, height) != (WIDTH, HEIGHT):
        issues.append(
            QcIssue("thumbnail.size", Severity.ERROR, f"Rendered {width}x{height}, not 1920x1080.")
        )
    feed = min(headline_px, default=0) * FEED_WIDTH / WIDTH
    if feed < MIN_FEED_HEADLINE_PX:
        issues.append(
            QcIssue(
                "thumbnail.headline_small",
                Severity.WARNING,
                f"The headline is {feed:.0f} px tall in the feed (want {MIN_FEED_HEADLINE_PX}+): "
                "use fewer or shorter words.",
            )
        )
    for name in _KEEP_CLEAR:
        box = boxes.get(name)
        if box is not None and _overlap(box, TIMESTAMP_ZONE) > 0:
            issues.append(
                QcIssue(
                    "thumbnail.under_timestamp",
                    Severity.WARNING,
                    f"The {name} reaches under YouTube's duration chip (bottom right).",
                )
            )
        if box is not None and (box[0] < 0 or box[1] < 0 or box[2] > WIDTH or box[3] > HEIGHT):
            issues.append(
                QcIssue(
                    "thumbnail.cut_off",
                    Severity.WARNING,
                    f"The {name} is cut off at the edge of the picture.",
                )
            )
    reaction = boxes.get("reaction")
    if reaction is not None:
        visible = (max(0, reaction[0]), max(0, reaction[1]), min(WIDTH, reaction[2]), reaction[3])
        share = _area((*visible[:3], min(HEIGHT, visible[3]))) / (WIDTH * HEIGHT)
        if share < MIN_REACTION_SHARE:
            issues.append(
                QcIssue(
                    "thumbnail.reaction_small",
                    Severity.WARNING,
                    f"The reaction covers {share:.0%} of the picture: choose a closer shot.",
                )
            )
    headline, logo = boxes.get("headline"), boxes.get("logo")
    if headline is not None and logo is not None and _overlap(headline, logo) > 0:
        issues.append(
            QcIssue("thumbnail.headline_logo", Severity.WARNING, "The headline runs into the logo.")
        )
    if skipped_products:
        issues.append(
            QcIssue(
                "thumbnail.products_skipped",
                Severity.WARNING,
                f"{skipped_products} product(s) had no free place (the badge takes one).",
            )
        )
    return tuple(issues)


def blocking(issues: Sequence[QcIssue]) -> bool:
    return any(i.severity is Severity.ERROR for i in issues)
