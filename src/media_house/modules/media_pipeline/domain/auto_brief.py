"""The automatic brief: what an owner's hand-written run brief says, derived from the project.

A project folder follows the owner's convention (``01_cam`` talking takes and gag clips,
``02_scr`` screen recordings, ``03_pck`` packshots, ``04_arc`` archive, ``05_bg`` background,
``06_gfx`` channel clips and logos). From those files, the transcript lines of the edit and the
product table this module decides, without I/O:

* graphics over named speech: the title card at the premise turn, a two-product teaser where the
  products are counted, brand logos where the brands are named (and on the sponsor disclosure), a
  gallery of an archive picture series where it is talked about (and this year's page after it),
  the comparison matrix of the product table where the products are compared, the subscribe CTA
  and, when products get no full-screen cards, a sticker where each is first named;
* cover footage: silent camera clips, archive and screen recordings over the sentence their file
  name (and the script scene heading) talks about;
* screen windows: the band of a screen recording without its static browser bar and taskbar (on-
  screen text that stays put across the shots of the stored video analysis);
* background footage, channel intro/outro and the format's screen/blur/VFX defaults.

Every decision carries ``source`` (why it is there) and the same inputs give the same brief.
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final


class Role(StrEnum):
    """A project folder's role, by the suffix of its numbered name (``01_cam``)."""

    CAMERA = "cam"
    SCREEN = "scr"
    PACKSHOT = "pck"
    ARCHIVE = "arc"
    BACKGROUND = "bg"
    CHANNEL = "gfx"


def role_of(folder: str) -> Role | None:
    tail = folder.casefold().rpartition("_")[2]
    return next((r for r in Role if r.value == tail), None)


@dataclass(frozen=True, slots=True, kw_only=True)
class Footage:
    """One file of the project folder (an asset of the library)."""

    asset_id: str
    name: str  # the file name without its extension
    role: Role
    video: bool
    duration: float = 0.0
    #: Words its own transcript holds (a gag clip without speech has none).
    spoken_words: int = 0
    #: Shot ranges (s) of its stored video analysis.
    shots: tuple[tuple[float, float], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Line:
    """A spoken sentence of the edit's recordings (source seconds of ``asset_id``)."""

    asset_id: str
    start: float
    end: float
    text: str
    #: The heading of the script scene its recording speaks ("" without a script).
    scene: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ScreenText:
    """On-screen text a stored video analysis read in one shot (normalised y of its box)."""

    text: str
    shot: int
    y0: float
    y1: float


@dataclass(frozen=True, slots=True, kw_only=True)
class Product:
    name: str
    image: str  # library asset id (the cut-out when there is one)
    facts: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class BriefInputs:
    format_profile: str
    sponsored: bool = False
    #: The video's title (the script's heading); no title, no title card.
    title: str = ""
    footage: tuple[Footage, ...] = ()
    #: In edit order.
    lines: tuple[Line, ...] = ()
    #: In table order.
    products: tuple[Product, ...] = ()
    #: The products already get full-screen intro/data cards (no name stickers then).
    product_cards: bool = False
    screen_text: Mapping[str, tuple[ScreenText, ...]] = field(default_factory=dict)


type Slot = str | int | tuple[str, ...] | tuple[tuple[str, ...], ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoGraphic:
    kind: str
    say: str
    slots: tuple[tuple[str, Slot], ...]
    source: str
    variant: str = ""
    zone: str = ""
    offset: float = 0.0
    duration: float | None = None
    emphasis: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoCover:
    """``footage`` over ``over`` (source seconds start..end), from ``footage_start`` for at most
    ``seconds``."""

    footage: str
    over: str
    start: float
    end: float
    footage_start: float
    seconds: float
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoWindow:
    asset_id: str
    window: tuple[float, float, float, float]  # x, y, w, h (normalised)
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class Pick:
    asset_id: str
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class FormatDefaults:
    screen_style: str = "crop"
    focus_blur: float = 0.0
    vfx_overrides: tuple[tuple[str, str], ...] = ()
    why: str = "no format default"


#: Per format profile (visual style names). A ranking shows its screens as cards and blurs the
#: talking picture under focus graphics; its camera moves (jump-cut zoom) already punch in.
FORMAT_DEFAULTS: Final = MappingProxyType(
    {
        "ranking": FormatDefaults(
            screen_style="card",
            focus_blur=5.0,
            vfx_overrides=(("punch_in.apply", "never"),),
            why="ranking: card screens, focus blur 5, camera moves replace punch-ins",
        ),
        "brochure_review": FormatDefaults(
            screen_style="card",
            focus_blur=3.0,
            vfx_overrides=(("punch_in.apply", "never"),),
            why="brochure review: card screens, focus blur 3, camera moves replace punch-ins",
        ),
        "unboxing": FormatDefaults(focus_blur=3.0, why="unboxing: full-frame screens, blur 3"),
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class AutoBrief:
    format_profile: str
    graphics: tuple[AutoGraphic, ...]
    cover: tuple[AutoCover, ...]
    screens: tuple[AutoWindow, ...]
    background: Pick | None
    intro: Pick | None
    outro: Pick | None
    style: FormatDefaults
    #: Footage no sentence talks about (named so the owner can place it by hand).
    unused: tuple[str, ...]


# --- words ---------------------------------------------------------------------------------------
_WORD = re.compile(r"\w+")
_LETTERS = re.compile(r"[^\W\d_]+")
_NUMBERS: Final = MappingProxyType(
    dict(enumerate(("zwei", "drei", "vier", "fünf", "sechs", "sieben", "acht", "neun", "zehn"), 2))
)
#: A sentence that turns the story ("Aber dieses Jahr ...") opens the premise.
_TURN: Final = ("aber", "doch", "jetzt", "heute", "dieses jahr", "diesmal")
_SHOW: Final = ("so sieht", "hier seht", "hier sieht", "schaut euch", "seht ihr hier")
_SCREEN_SCENE: Final = ("screen", "bildschirm", "website", "webseite", "onlineshop")
_COMPARE: Final = ("vergleich", "nebeneinander", "gegenüber")
#: File-name words that say what a clip is, not what it shows.
_STOP: Final = frozenset(
    {"gag", "clip", "take", "shot", "final", "reaktion", "reaktionen", "reaction", "talking"}
    | {"logo", "svg", "png", "jpg", "webp", "mov", "mp4", "seite", "neu", "full", "aktion"}
)
#: shortcut: a tiny fixed German synonym table for file-name words; upgrade to the multilingual
#: text encoder of content intelligence when file names stop matching what is said.
_ALIASES: Final = MappingProxyType(
    {
        "wecker": ("uhr", "aufstehen", "aufwachen", "morgens"),
        "ansturm": ("schlange", "andrang", "anstehen"),
        "handy": ("smartphone", "app"),
        "letztes": ("bisher", "früher"),
    }
)
_COVER_SECONDS: Final = MappingProxyType({Role.SCREEN: 20.0})
_CLIP_SECONDS: Final = 4.0


def _words(text: str) -> list[str]:
    return _WORD.findall(text.casefold())


def _hit(token: str, word: str) -> bool:
    """Same word, or the same stem (a long shared start: ``bestellen``/``bestellst``)."""
    if token == word:
        return True
    n = 0
    for a, b in zip(token, word, strict=False):
        if a != b:
            break
        n += 1
    return n >= 5 and n >= min(len(token), len(word)) - 2


def _phrase_at(words: Sequence[str], phrase: str) -> int | None:
    wanted = phrase.split()
    for i in range(len(words) - len(wanted) + 1):
        if words[i : i + len(wanted)] == wanted:
            return i
    return None


class _Text:
    """The lines as words, with unique phrases to find them again in the edit."""

    def __init__(self, lines: Sequence[Line]) -> None:
        self.lines = list(lines)
        self.words = [_words(line.text) for line in lines]

    def say(self, index: int) -> str:
        """The shortest opening of the line (3+ words) that no other line says."""
        own = self.words[index]
        raw = self.lines[index].text.split()
        for k in range(min(3, len(own)), len(own) + 1):
            head = own[:k]
            seen = sum(
                1
                for words in self.words
                for i in range(len(words) - k + 1)
                if words[i : i + k] == head
            )
            if seen <= 1:
                return " ".join(raw[: min(k, len(raw))])
        return self.lines[index].text

    def hits(self, tokens: Sequence[tuple[str, ...]], index: int) -> list[int]:
        """Word positions of the line hit by each token group (a file word and its aliases)."""
        out = []
        for group in tokens:
            at = next(
                (i for i, w in enumerate(self.words[index]) if any(_hit(t, w) for t in group)),
                None,
            )
            if at is not None:
                out.append(at)
        return out


def _stem(name: str) -> str:
    """The series a file belongs to (``wecker_gag_0`` and ``wecker_gag_3``: ``wecker_gag``)."""
    return re.sub(r"[\W_]*\d+$", "", name.casefold())


def _brands(footage: Sequence[Footage]) -> list[tuple[tuple[str, ...], Footage]]:
    """Channel pictures (``06_gfx``) are brand logos, named by their file words (the first word
    is the brand: ``Lidl-Logo``, ``NICO-Feuerwerk``)."""
    out = []
    for f in footage:
        if f.role is Role.CHANNEL and not f.video:
            words = tuple(w for w in _LETTERS.findall(f.name.casefold()) if w not in _STOP)
            if words:
                out.append((words, f))
    return sorted(out, key=lambda b: b[1].name)


def _tokens(name: str, ignore: frozenset[str]) -> tuple[tuple[str, ...], ...]:
    words = [w for w in _LETTERS.findall(name.casefold()) if len(w) >= 3]
    kept = [w for w in dict.fromkeys(words) if w not in _STOP and w not in ignore]
    return tuple((w, *_ALIASES.get(w, ())) for w in kept)


def _common(footage: Sequence[Footage]) -> frozenset[str]:
    """Words in many file names (the project prefix) name no subject."""
    counts: dict[str, int] = {}
    for f in footage:
        for w in set(_LETTERS.findall(f.name.casefold())):
            counts[w] = counts.get(w, 0) + 1
    return frozenset(w for w, n in counts.items() if n * 3 >= len(footage) and len(footage) > 3)


def _turn(words: Sequence[str]) -> bool:
    head = " ".join(words[:2])
    return any(head == t or head.startswith(t + " ") for t in _TURN)


def _count_noun(line: Line, n: int) -> bool:
    """The products counted with their noun ("Fünf brandneue Batterien")."""
    raw = line.text.split()
    said = [w.strip(".,!?;:").casefold() for w in raw]
    for i, w in enumerate(said):
        if w in (_NUMBERS.get(n, ""), str(n)) and any(r[:1].isupper() for r in raw[i + 1 : i + 3]):
            return True
    return False


# --- the brief ------------------------------------------------------------------------------------
def auto_brief(inputs: BriefInputs) -> AutoBrief:
    text = _Text(inputs.lines)
    style = FORMAT_DEFAULTS.get(inputs.format_profile, FormatDefaults())
    cover, unused = _cover(inputs, text)
    return AutoBrief(
        format_profile=inputs.format_profile,
        graphics=_graphics(inputs, text),
        cover=cover,
        screens=_screens(inputs),
        background=_first(inputs.footage, Role.BACKGROUND, "", "the first clip of the bg folder"),
        intro=_first(inputs.footage, Role.CHANNEL, "intro", "the plainest intro of the gfx folder"),
        outro=_first(inputs.footage, Role.CHANNEL, "outro", "the plainest outro of the gfx folder"),
        style=style,
        unused=unused,
    )


def _first(footage: Sequence[Footage], role: Role, word: str, why: str) -> Pick | None:
    """The video of the role (whose name has ``word``) with the fewest name words."""
    found = sorted(
        (len(_LETTERS.findall(f.name)), f.name, f.asset_id)
        for f in footage
        if f.role is role and f.video and (not word or word in _LETTERS.findall(f.name.casefold()))
    )
    if not found:
        return None
    return Pick(asset_id=found[0][2], source=f"{why}: {found[0][1]}")


# --- graphics -------------------------------------------------------------------------------------
class _Placer:
    """One graphic group per sentence: a taken sentence passes it on to the next one of the same
    recording."""

    def __init__(self, text: _Text) -> None:
        self.text = text
        self.taken: set[int] = set()
        self.out: list[AutoGraphic] = []

    def free(self, index: int) -> int | None:
        lines = self.text.lines
        for i in range(index, len(lines)):
            if lines[i].asset_id != lines[index].asset_id:
                return None
            if i not in self.taken:
                return i
        return None

    def put(self, index: int, graphics: Sequence[AutoGraphic]) -> None:
        self.taken.add(index)
        self.out.extend(graphics)


def _graphics(inputs: BriefInputs, text: _Text) -> tuple[AutoGraphic, ...]:
    place = _Placer(text)
    lines = text.lines
    n = len(lines)
    named = _first_named(text, [p.name for p in inputs.products])

    # The title at the premise turn: the first sentence after the opening that turns the story.
    if inputs.title:
        turn = next((i for i in range(1, max(1, n * 3 // 10)) if _turn(text.words[i])), None)
        if turn is not None:
            place.put(
                turn,
                [
                    _g(
                        "title",
                        text,
                        turn,
                        (("title", inputs.title),),
                        "the premise turns here",
                        duration=2.6,
                    )
                ],
            )

    # The products counted before any is named: a teaser of the first two.
    products = inputs.products
    first_name = min(named.values(), default=n)
    if len(products) >= 2:
        count = next((i for i in range(first_name) if _count_noun(lines[i], len(products))), None)
        if count is not None and (i := place.free(count)) is not None:
            why = f"the {len(products)} products are counted before one is named"
            place.put(
                i,
                [
                    _g(
                        "product_sticker",
                        text,
                        i,
                        (("image", p.image),),
                        f"{why}: {p.name}",
                        zone=zone,
                        offset=offset,
                        duration=1.6,
                        emphasis=2,
                    )
                    for p, zone, offset in (
                        (products[0], "callout_left", 0.0),
                        (products[1], "callout_right", 0.5),
                    )
                ],
            )

    # Brand logos: both where the brands are first named together, the sponsor's on the
    # disclosure.
    brands = _brands(inputs.footage)
    if len(brands) >= 2:
        both = next(
            (i for i in range(n) if sum(bool(text.hits([b[:1]], i)) for b, _ in brands) >= 2), None
        )
        if both is not None and (i := place.free(both)) is not None:
            place.put(
                i,
                [
                    _g(
                        "product_sticker",
                        text,
                        i,
                        (("image", f.asset_id),),
                        f"the brands are named together: {f.name}",
                        zone=zone,
                        offset=offset,
                        duration=2.4,
                        emphasis=2,
                    )
                    for (_, f), zone, offset in zip(
                        brands[:2], ("callout_left", "callout_right"), (0.0, 0.4), strict=True
                    )
                ],
            )
    if inputs.sponsored:
        for i in range(n):
            if "werbung" not in text.words[i]:
                continue
            sponsor = next(((b, f) for b, f in brands if text.hits([b[:1]], i)), None)
            if sponsor is not None and (j := place.free(i)) is not None:
                place.put(
                    j,
                    [
                        _g(
                            "product_sticker",
                            text,
                            j,
                            (("image", sponsor[1].asset_id),),
                            f"the sponsor disclosure names {sponsor[1].name}",
                            zone="callout_right",
                            duration=2.8,
                            emphasis=2,
                        )
                    ],
                )
            break

    _galleries(inputs, text, place)

    # The product table side by side where the products are compared.
    matrix = _matrix(products)
    if matrix is not None:
        last = max(named.values(), default=-1)
        at = next(
            (
                i
                for i in range(last + 1, n)
                if any(w.startswith(c) for w in text.words[i] for c in _COMPARE)
                and _count_noun(lines[i], len(products))
            ),
            None,
        )
        if at is not None and (i := place.free(at)) is not None:
            place.put(
                i,
                [
                    _g(
                        "comparison_matrix",
                        text,
                        i,
                        matrix,
                        "the products are compared side by side (product table)",
                        duration=6.5,
                    )
                ],
            )

    _deadlines(text, place)

    cta = next(
        (i for i in range(n) if any(w == "abo" or w.startswith("abonnier") for w in text.words[i])),
        None,
    )
    if cta is not None and (i := place.free(cta)) is not None:
        place.put(
            i,
            [
                _g(
                    "cta",
                    text,
                    i,
                    (("text", "Abonnieren"),),
                    "a subscription is asked for",
                    duration=2.6,
                )
            ],
        )

    if not inputs.product_cards:
        for p in products:
            if (at := named.get(p.name)) is not None and (i := place.free(at)) is not None:
                place.put(
                    i,
                    [
                        _g(
                            "product_sticker",
                            text,
                            i,
                            (("image", p.image),),
                            f"{p.name} is first named",
                            zone="callout_right",
                            duration=2.4,
                            emphasis=2,
                        )
                    ],
                )
    return tuple(place.out)


_MONTHS: Final = (
    "januar", "februar", "märz", "april", "mai", "juni",
    "juli", "august", "september", "oktober", "november", "dezember",
)  # fmt: skip


def _deadlines(text: _Text, place: _Placer) -> None:
    """A date said with a deadline ("bis zum 22. November") or a start ("startet am 29.
    Dezember") as a date chip when the date is said, each date once."""
    seen: set[str] = set()
    for i, words in enumerate(text.words):
        for k in range(len(words) - 1):
            day, month = words[k], words[k + 1]
            if not (day.isdigit() and 1 <= int(day) <= 31 and month in _MONTHS):
                continue
            cue = words[max(0, k - 3) : k]
            if "bis" in cue:
                label = "NUR BIS"
            elif "ab" in cue or any(w.startswith("start") for w in cue):
                label = "AB"
            else:
                continue
            date = f"{int(day):02d}.{_MONTHS.index(month) + 1:02d}."
            if date in seen or (j := place.free(i)) is None:
                continue
            seen.add(date)
            line = text.lines[j]
            offset = round((line.end - line.start) * k / len(words), 1) if j == i else 0.0
            place.put(
                j,
                [
                    _g(
                        "deadline_chip",
                        text,
                        j,
                        (("date", date), ("label", label)),
                        f"the date {date} is said with '{' '.join(cue)}'",
                        offset=offset,
                        duration=2.6,
                    )
                ],
            )
            break


def _g(
    kind: str,
    text: _Text,
    index: int,
    slots: tuple[tuple[str, Slot], ...],
    why: str,
    *,
    variant: str = "",
    zone: str = "",
    offset: float = 0.0,
    duration: float | None = None,
    emphasis: int | None = None,
) -> AutoGraphic:
    return AutoGraphic(
        kind=kind,
        say=text.say(index),
        slots=slots,
        source=f"auto brief: {why}",
        variant=variant,
        zone=zone,
        offset=offset,
        duration=duration,
        emphasis=emphasis,
    )


def _first_named(text: _Text, names: Sequence[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for name in names:
        phrase = " ".join(_words(name))
        at = next((i for i, w in enumerate(text.words) if _phrase_at(w, phrase) is not None), None)
        if at is not None:
            out[name] = at
    return out


def _years(name: str) -> str:
    """``prospekt_2122`` -> ``2021/22``, ``prospekt_2024`` -> ``2024``."""
    m = re.search(r"(\d{4})$", name)
    if not m:
        return ""
    digits = m.group(1)
    if digits.startswith(("19", "20")):
        return digits
    return f"20{digits[:2]}/{digits[2:]}"


def _galleries(inputs: BriefInputs, text: _Text, place: _Placer) -> None:
    """An archive picture series where it is talked about; a packshot page of the same subject
    (this year's) at the next sentence that turns to now."""
    lines = text.lines
    ignore = _common(inputs.footage) | {w for b, _ in _brands(inputs.footage) for w in b}
    product_words = {w for p in inputs.products for w in _words(p.name)}
    series: dict[str, list[Footage]] = {}
    for f in inputs.footage:
        if f.role is Role.ARCHIVE and not f.video:
            series.setdefault(_stem(f.name), []).append(f)
    shown: dict[str, int] = {}
    for stem, pictures in sorted(series.items()):
        if len(pictures) < 2:
            continue
        tokens = _tokens(stem, frozenset(ignore))
        at = next((i for i in range(len(lines)) if text.hits(tokens, i)), None)
        if at is None or (i := place.free(at)) is None:
            continue
        pictures.sort(key=lambda f: f.name)
        noun = tokens[0][0].capitalize() if tokens else stem
        place.put(
            i,
            [
                _g(
                    "product_sticker",
                    text,
                    i,
                    (
                        ("captions", tuple(_years(f.name) for f in pictures)),
                        ("images", tuple(f.asset_id for f in pictures)),
                        ("title", noun),
                    ),
                    f"the archive series {stem} ({len(pictures)} pictures) is talked about",
                    variant="gallery",
                    duration=min(5.0, 3.0 + 0.4 * len(pictures)),
                )
            ],
        )
        for group in tokens:
            shown[group[0]] = i
    for f in sorted(inputs.footage, key=lambda f: f.name):
        if f.role is not Role.PACKSHOT or f.video:
            continue
        words = set(_LETTERS.findall(f.name.casefold()))
        if words & product_words:
            continue  # a product picture: its own cards or stickers
        linked = next((shown[w] for w in sorted(words) if w in shown), None)
        if linked is None:
            continue
        now = next(
            (
                i
                for i in range(linked + 1, min(len(lines), linked + 12))
                if _turn(text.words[i]) and i not in place.taken
            ),
            None,
        )
        if now is None:
            continue
        place.put(
            now,
            [
                _g(
                    "product_sticker",
                    text,
                    now,
                    (
                        ("captions", ("Neu" if "neu" in words else "",)),
                        ("images", (f.asset_id,)),
                        ("title", " ".join(text.words[now][:2]).title()),
                    ),
                    f"{f.name} is the new page after the archive series",
                    variant="gallery",
                    duration=4.0,
                )
            ],
        )


def _matrix(products: Sequence[Product]) -> tuple[tuple[str, Slot], ...] | None:
    """Up to three short facts every product has (price and shots first)."""
    if len(products) < 3 or not all(p.facts for p in products):
        return None
    tables = [{k.casefold(): v.replace("ca.", "").strip() for k, v in p.facts} for p in products]
    labels = [k for k, _ in products[0].facts]
    keys = sorted(
        (k for k in labels if k.casefold() not in {"nr.", "nr", "rang", "platz"}),
        key=lambda k: (
            not k.casefold().startswith("preis"),
            k.casefold() != "schuss",
            labels.index(k),
        ),
    )
    columns = [k for k in keys if all(0 < len(t.get(k.casefold(), "")) <= 10 for t in tables)][:3]
    if len(columns) < 2:
        return None
    rows = tuple(
        (p.name, *(t[k.casefold()] for k in columns)) for p, t in zip(products, tables, strict=True)
    )
    return (
        ("columns", tuple(columns)),
        ("rows", rows),
        ("title", f"Alle {len(products)} im Vergleich"),
    )


# --- cover ----------------------------------------------------------------------------------------
@dataclass(slots=True)
class _Placed:
    footage: Footage
    line: int
    anchor: float  # 0..1 into the line
    why: str
    until: int | None = None  # the last line it covers (screen walkthroughs)


def _cover(inputs: BriefInputs, text: _Text) -> tuple[tuple[AutoCover, ...], tuple[str, ...]]:
    lines = text.lines
    speaking = {line.asset_id for line in lines}
    ignore = _common(inputs.footage) | {w for b, _ in _brands(inputs.footage) for w in b}
    candidates = sorted(
        (
            f
            for f in inputs.footage
            if f.video
            and f.asset_id not in speaking
            and (
                (f.role is Role.CAMERA and f.spoken_words < 5)
                or f.role in (Role.ARCHIVE, Role.SCREEN)
            )
        ),
        key=lambda f: f.name,
    )
    placed: list[_Placed] = []
    used: set[str] = set()

    # The longest screen recording walks through where the speaker says "so sieht das aus".
    screens = sorted(
        (f for f in candidates if f.role is Role.SCREEN), key=lambda f: (-f.duration, f.name)
    )
    if screens:
        shows = [
            i
            for i in range(len(lines))
            if any(_phrase_at(text.words[i], s) is not None for s in _SHOW)
        ]
        on_screen = [i for i in shows if any(s in lines[i].scene.casefold() for s in _SCREEN_SCENE)]
        if on_screen or shows:
            at = (on_screen or shows)[0]
            last = max(j for j in range(at, len(lines)) if lines[j].asset_id == lines[at].asset_id)
            why = "the speaker shows the screen" + (" (screen scene)" if on_screen else "")
            placed.append(_Placed(screens[0], at, 0.0, why, until=last))
            used.add(screens[0].asset_id)

    # Every other clip (a series of numbered clips together) over the sentence it names.
    series: dict[str, list[Footage]] = {}
    for f in candidates:
        if f.asset_id not in used:
            series.setdefault(_stem(f.name), []).append(f)
    covered = {p.line for p in placed if p.until is None} | {
        j for p in placed if p.until is not None for j in range(p.line, p.until + 1)
    }
    for stem, clips in sorted(series.items()):
        tokens = _tokens(stem, ignore)
        if not tokens:
            continue
        need = 1 if len(tokens) <= 2 else 2
        scored = [
            (len(h), i, h)
            for i in range(len(lines))
            if i not in covered and len(h := text.hits(tokens, i)) >= need
        ]
        if not scored:
            continue
        best = max(scored, key=lambda s: (s[0], -s[1]))
        why = f"its name ({stem}) is said"
        if len(clips) == 1:
            placed.append(
                _Placed(clips[0], best[1], min(best[2]) / max(1, len(text.words[best[1]])), why)
            )
            used.add(clips[0].asset_id)
            continue
        # A series spreads over the matching sentences of the same recording, in order.
        own = [
            i
            for _, i, _ in sorted(scored, key=lambda s: s[1])
            if lines[i].asset_id == lines[best[1]].asset_id
        ]
        share, extra = divmod(len(clips), len(own))
        k = 0
        for n, i in enumerate(own[: len(clips)]):
            for _ in range(share + (1 if n < extra else 0)):
                placed.append(_Placed(clips[k], i, 0.0, f"{why} (clip {k + 1} of {len(clips)})"))
                used.add(clips[k].asset_id)
                k += 1

    out: list[AutoCover] = []
    by_line: dict[int, list[_Placed]] = {}
    for p in placed:
        by_line.setdefault(p.line, []).append(p)
    for index, group in sorted(by_line.items()):
        line = lines[index]
        group.sort(key=lambda p: (p.anchor, p.footage.name))
        length = line.end - line.start
        starts = _spread([line.start + length * p.anchor for p in group], line.end)
        for p, start, stop in zip(group, starts, [*starts[1:], line.end], strict=True):
            end = lines[p.until].end if p.until is not None else stop
            cap = _COVER_SECONDS.get(p.footage.role, _CLIP_SECONDS)
            footage_start = _in_point(p.footage, cap)
            room = max(0.0, p.footage.duration - footage_start) if p.footage.duration else cap
            seconds = round(min(cap, end - start, room), 3)
            if seconds <= 0:
                continue
            out.append(
                AutoCover(
                    footage=p.footage.asset_id,
                    over=line.asset_id,
                    start=round(start, 3),
                    end=round(end if p.until is not None else min(end, start + seconds), 3),
                    footage_start=footage_start,
                    seconds=seconds,
                    source=f"auto brief: {p.footage.name}: {p.why}",
                )
            )
    unused = tuple(f.name for f in candidates if f.asset_id not in used)
    return tuple(out), unused


def _spread(starts: Sequence[float], end: float) -> list[float]:
    """Equal starts share the time up to the next start (or the end) evenly."""
    out = list(starts)
    i = 0
    while i < len(out):
        j = i
        while j + 1 < len(out) and out[j + 1] == out[i]:
            j += 1
        if j > i:
            stop = out[j + 1] if j + 1 < len(out) else end
            step = (stop - out[i]) / (j - i + 1)
            for k in range(i, j + 1):
                out[k] = out[i] + step * (k - i)
        i = j + 1
    return out


def _in_point(footage: Footage, seconds: float) -> float:
    """Where to start: past a short opening shot of a screen recording (the recorder's own
    window), else into the longest shot (settled for half a second when it is long enough)."""
    shots = footage.shots
    if not shots:
        return 0.0
    if footage.role is Role.SCREEN:
        first = shots[0]
        return round(first[1], 3) if len(shots) > 1 and first[1] - first[0] < 5.0 else 0.0
    start, end = max(shots, key=lambda s: (s[1] - s[0], -s[0]))
    return round(start + 0.5 if end - start >= seconds + 0.5 else start, 3)


# --- screen windows ------------------------------------------------------------------------------
_BAND: Final = 0.25
_MARGIN: Final = 0.01


def _screens(inputs: BriefInputs) -> tuple[AutoWindow, ...]:
    out = []
    for f in sorted(inputs.footage, key=lambda f: f.name):
        if f.role is not Role.SCREEN or not f.video:
            continue
        window = static_bands(inputs.screen_text.get(f.asset_id, ()))
        if window is None:
            continue
        out.append(
            AutoWindow(
                asset_id=f.asset_id,
                window=window,
                source=f"auto brief: {f.name}: text that stays put in the top/bottom band of "
                "its analysed shots (browser bar, taskbar) is cropped off",
            )
        )
    return tuple(out)


def static_bands(items: Sequence[ScreenText]) -> tuple[float, float, float, float] | None:
    """The page between the static top and bottom bands: text read at the same height in most
    shots (a URL bar, a clock) is interface, text that moves is the page."""
    shots = {t.shot for t in items}
    if len(shots) < 3:
        return None
    seen: dict[tuple[str, float, float], set[int]] = {}
    for t in items:
        key = re.sub(r"\W+", "", t.text.casefold())
        if len(key) >= 3:
            seen.setdefault((key, round(t.y0, 2), round(t.y1, 2)), set()).add(t.shot)
    static = [k for k, s in seen.items() if len(s) * 5 >= len(shots) * 3]
    top = max((y1 for _, _, y1 in static if y1 <= _BAND), default=0.0)
    bottom = min((y0 for _, y0, _ in static if y0 >= 1 - _BAND), default=1.0)
    if top == 0.0 and bottom == 1.0:
        return None
    y = round(top + _MARGIN, 2) if top else 0.0
    h = round((bottom - _MARGIN if bottom < 1.0 else 1.0) - y, 2)
    return (0.0, y, 1.0, h)
