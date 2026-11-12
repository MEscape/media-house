"""What a thumbnail shows: the owner's brief, validated against the channel's thumbnail rules.

The rules come from the channel's own thumbnails (docs/layer4/channel-discovery.md 6.8): one or two
huge words, a creator reaction, a retailer logo, at most a few product cut-outs. Everything here
is decided by the owner (or the project settings); nothing is guessed.
"""

import re
from dataclasses import dataclass, replace
from typing import Final

from media_house.shared.errors import InvariantViolation

type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None

MAX_LINES: Final = 2
MAX_WORDS_PER_LINE: Final = 3
#: A line wider than this no longer fits the headline area at a size readable in the feed.
MAX_CHARS_PER_LINE: Final = 14
MAX_PRODUCTS: Final = 3
MAX_BADGE_CHARS: Final = 6
MAX_BUTTON_CHARS: Final = 18
MAX_NAME_CHARS: Final = 20
#: German price, decimal comma, optional thousands dot: ``79,99 €``, ``1.299,00 €``.
_PRICE = re.compile(r"^\d{1,3}(\.\d{3})*,\d{2} €$")


def upper(text: str) -> str:
    """Thumbnail caps: ß becomes SS (the bundled faces have no capital sharp s)."""
    return text.replace("ß", "SS").upper()


@dataclass(frozen=True, slots=True)
class Headline:
    """One or two lines; ``accent_line`` (an index or ``None``) is drawn in the highlight colour."""

    lines: tuple[str, ...]
    accent_line: int | None = 1

    def __post_init__(self) -> None:
        lines = tuple(" ".join(line.split()) for line in self.lines)
        lines = tuple(line for line in lines if line)
        object.__setattr__(self, "lines", lines)
        if not 1 <= len(lines) <= MAX_LINES:
            raise InvariantViolation(
                "A headline needs one or two lines",
                user_message=f"Write the headline in 1 to {MAX_LINES} lines.",
            )
        for line in lines:
            if len(line.split()) > MAX_WORDS_PER_LINE or len(line) > MAX_CHARS_PER_LINE:
                raise InvariantViolation(
                    f"Headline line too long: {line!r}",
                    user_message=(
                        f"“{line}” is too long for a thumbnail: at most {MAX_WORDS_PER_LINE} "
                        f"words and {MAX_CHARS_PER_LINE} characters per line."
                    ),
                )
        if self.accent_line is not None and not 0 <= self.accent_line < len(lines):
            object.__setattr__(self, "accent_line", None)

    @property
    def caps(self) -> tuple[str, ...]:
        return tuple(upper(line) for line in self.lines)

    def to_config(self) -> dict[str, JsonValue]:
        return {"lines": list(self.caps), "accent_line": self.accent_line}


@dataclass(frozen=True, slots=True, kw_only=True)
class DeviceCard:
    """A phone showing the shop: product card, price and a cart button ("you order this online")."""

    product_asset_id: str
    product_name: str
    price: str
    button: str = "In den Warenkorb"
    #: A picture of the real shop page (screenshot or a screen-recording frame); ``None`` = plain.
    screen_asset_id: str | None = None

    def __post_init__(self) -> None:
        if not self.product_asset_id:
            raise InvariantViolation("A device card needs a product picture")
        if not _PRICE.match(self.price):
            raise InvariantViolation(
                f"Price {self.price!r} is not in the channel format",
                user_message=f"Write the price like 79,99 € (decimal comma), not “{self.price}”.",
            )
        for label, text, limit in (
            ("product name", self.product_name, MAX_NAME_CHARS),
            ("button", self.button, MAX_BUTTON_CHARS),
        ):
            if not text.strip() or len(text) > limit:
                raise InvariantViolation(
                    f"Device {label} must be 1-{limit} characters",
                    user_message=f"The {label} must be 1 to {limit} characters.",
                )

    def to_config(self) -> dict[str, JsonValue]:
        return {
            "product": self.product_asset_id,
            "name": self.product_name,
            "price": self.price,
            "button": self.button,
            "screen": self.screen_asset_id,
        }


@dataclass(frozen=True, slots=True)
class StoryBadge:
    """A short story element, struck through in red: the thing the video makes unnecessary."""

    text: str
    struck: bool = True

    def __post_init__(self) -> None:
        text = self.text.strip()
        object.__setattr__(self, "text", text)
        if not 1 <= len(text) <= MAX_BADGE_CHARS:
            raise InvariantViolation(
                "A badge needs 1-6 characters",
                user_message=f"The badge must be 1 to {MAX_BADGE_CHARS} characters (like 04:30).",
            )

    def to_config(self) -> dict[str, JsonValue]:
        return {"text": upper(self.text), "struck": self.struck}


@dataclass(frozen=True, slots=True, kw_only=True)
class ThumbnailBrief:
    headline: Headline
    #: The creator reaction (a library picture; a cut-out is made from it).
    reaction_asset_id: str
    #: Product pictures shown as stickers, most important first.
    products: tuple[str, ...] = ()
    logo_asset_id: str | None = None
    device: DeviceCard | None = None
    badge: StoryBadge | None = None
    #: A real fireworks still blended behind the products (image or video; a video gives a frame).
    plate_asset_id: str | None = None
    #: A second headline for an A/B variant.
    alternate_headline: Headline | None = None
    #: Mirror the reaction so the creator looks into the picture (towards the products).
    flip_reaction: bool = False

    def __post_init__(self) -> None:
        if not self.reaction_asset_id:
            raise InvariantViolation(
                "A thumbnail needs a reaction picture",
                user_message="Choose a reaction picture of you (upload, library or footage).",
            )
        products = tuple(dict.fromkeys(p for p in self.products if p))
        if self.device is not None:  # the phone already shows this product
            products = tuple(p for p in products if p != self.device.product_asset_id)
        object.__setattr__(self, "products", products)
        if len(products) > MAX_PRODUCTS:
            raise InvariantViolation(
                "Too many products",
                user_message=f"Use at most {MAX_PRODUCTS} products: fewer elements read better.",
            )

    def without_badge(self) -> "ThumbnailBrief":
        return replace(self, badge=None)

    def to_config(self) -> dict[str, JsonValue]:
        """Canonical description: everything that changes the pixels, nothing else."""
        return {
            "headline": self.headline.to_config(),
            "reaction": self.reaction_asset_id,
            "flip_reaction": self.flip_reaction,
            "products": list(self.products),
            "logo": self.logo_asset_id,
            "device": None if self.device is None else self.device.to_config(),
            "badge": None if self.badge is None else self.badge.to_config(),
            "plate": self.plate_asset_id,
        }
