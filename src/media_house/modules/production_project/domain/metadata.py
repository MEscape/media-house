"""YouTube metadata: title options, description, tags, hashtags and chapters.

Everything the brief makes mandatory is inserted by rule, never by a model: the advertising
label line, the mandatory hashtag (first), the handle, the tracking-link placeholder, the safety
line. Chapters come from the final timeline's scene starts. No thumbnail output exists here.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from media_house.modules.production_project.domain.brief import BriefModel, Fact
from media_house.modules.production_project.domain.claims import ScanContext, scan_text
from media_house.modules.production_project.domain.compliance import tag_length
from media_house.modules.production_project.domain.concepts import StoryConcept
from media_house.modules.production_project.domain.facts import display, products
from media_house.modules.production_project.domain.numbers import fold
from media_house.modules.production_project.domain.script import Script
from media_house.modules.production_project.domain.script_tools import heading_title
from media_house.modules.production_project.domain.values import ElementKind, Severity

TRACKING_PLACEHOLDER = "{TRACKING_LINK}"
MAX_TITLE = 100
MAX_TAG_CHARS = 500
MAX_HASHTAGS = 15
MIN_CHAPTER_SECONDS = 10
_CHAPTER_FUNCTIONS = frozenset({"hook", "facts", "product", "comparison", "cta", "pick", "outro"})


@dataclass(frozen=True, slots=True)
class Chapter:
    at_seconds: int
    title: str


@dataclass(frozen=True, slots=True)
class YoutubeMetadata:
    titles: tuple[str, ...]
    description: str
    tags: tuple[str, ...]
    hashtags: tuple[str, ...]
    chapters: tuple[Chapter, ...]


@dataclass(frozen=True, slots=True)
class MetadataInputs:
    brief: BriefModel
    facts: tuple[Fact, ...]
    script: Script
    concepts: tuple[StoryConcept, ...]
    #: Start of each scene in the final timeline (seconds); estimated lengths before that exists.
    scene_starts: Mapping[str, float]
    scan: ScanContext
    #: The tracking link once it arrived ("" = still a placeholder).
    tracking_link: str = ""
    catchphrase: str = ""
    extra_tags: tuple[str, ...] = ()


def build_metadata(i: MetadataInputs) -> YoutubeMetadata:
    hashtags = _hashtags(i)
    chapters = _chapters(i)
    return YoutubeMetadata(
        titles=_titles(i),
        description=_description(i, hashtags, chapters),
        tags=_tags(i),
        hashtags=hashtags,
        chapters=chapters,
    )


def _titles(i: MetadataInputs) -> tuple[str, ...]:
    options = [t for c in i.concepts for t in c.titles]
    safe = [
        t
        for t in dict.fromkeys(options)
        if len(t) <= MAX_TITLE
        and not any(r.severity is Severity.BLOCKER for r in scan_text(t, "title", i.scan))
    ]
    n = len(products(i.facts))
    client = i.brief.client or "dem Kanal"
    fallback = f"Das gibt es bei {client}: {n} Feuerwerksartikel" if n else f"{client}: Feuerwerk"
    return tuple(safe[:5]) or (fallback[:MAX_TITLE],)


def _hashtags(i: MetadataInputs) -> tuple[str, ...]:
    mandatory = [e.text for e in i.brief.mandatory if e.kind is ElementKind.HASHTAG]
    extra = ["#Feuerwerk", "#Silvester"]
    seen: dict[str, str] = {}
    for tag in (*mandatory, *extra):
        seen.setdefault(tag.casefold(), tag)
    return tuple(list(seen.values())[:MAX_HASHTAGS])


def _chapters(i: MetadataInputs) -> tuple[Chapter, ...]:
    found: list[Chapter] = []
    for scene in i.script.scenes:
        if scene.function not in _CHAPTER_FUNCTIONS or scene.scene_id not in i.scene_starts:
            continue
        found.append(Chapter(int(i.scene_starts[scene.scene_id]), heading_title(scene.heading)))
    chapters: list[Chapter] = []
    for chapter in sorted(found, key=lambda c: c.at_seconds):
        if chapters and chapter.at_seconds - chapters[-1].at_seconds < MIN_CHAPTER_SECONDS:
            continue
        chapters.append(chapter)
    if chapters and chapters[0].at_seconds != 0:
        chapters[0] = Chapter(0, chapters[0].title)
    return tuple(chapters) if len(chapters) >= 3 else ()


def clock(seconds: int) -> str:
    h, rest = divmod(max(0, seconds), 3600)
    m, s = divmod(rest, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _description(
    i: MetadataInputs, hashtags: tuple[str, ...], chapters: tuple[Chapter, ...]
) -> str:
    lines: list[str] = []
    label = next((e.text for e in i.brief.mandatory if e.kind is ElementKind.LABEL), "")
    if label:
        partner = f" in Zusammenarbeit mit {i.brief.client}" if i.brief.client else ""
        lines.append(f"{label.upper()}: Dieses Video entsteht{partner}.")
    lead = i.concepts[0].logline if i.concepts else ""
    if lead:
        lines += ["", lead]
    product_rows = products(i.facts)
    if product_rows:
        by_key = {f.key: f for f in i.facts}
        lines += ["", "Die Produkte:"]
        lines += [
            f"- {r.name}: "
            + (
                display(price)
                if (price := by_key.get(f"price_{r.subject}"))
                else "Preis noch offen"
            )
            for r in product_rows
        ]
    window = next((f for f in i.facts if f.key == "order_window"), None)
    if window:
        lines += ["", f"Bestellzeitraum: {display(window)}"]
    for element in i.brief.mandatory:
        if element.kind is ElementKind.LINK:
            link = i.tracking_link or TRACKING_PLACEHOLDER
            lines += ["", f"Zur Aktion: {link}"]
        elif element.kind is ElementKind.LINE:
            lines += ["", element.text]
    handles = [e.text for e in i.brief.mandatory if e.kind is ElementKind.HANDLE]
    if handles:
        lines += ["", " ".join(handles)]
    if chapters:
        lines += ["", "Kapitel:"]
        lines += [f"{clock(c.at_seconds)} {c.title}" for c in chapters]
    if i.catchphrase:
        lines += ["", i.catchphrase.capitalize() + "."]
    lines += ["", " ".join(hashtags)]
    return "\n".join(lines)


def _tags(i: MetadataInputs) -> tuple[str, ...]:
    candidates = [
        *(r.name for r in products(i.facts)),
        i.brief.client,
        i.brief.campaign,
        "Feuerwerk",
        "Silvester",
        *i.extra_tags,
    ]
    tags: list[str] = []
    seen: set[str] = set()
    for tag in candidates:
        key = fold(tag)
        if not key or key in seen:
            continue
        if tag_length([*tags, tag]) > MAX_TAG_CHARS:
            continue
        tags.append(tag)
        seen.add(key)
    return tuple(tags)


def scene_starts_from_lengths(
    script: Script, lengths: Sequence[tuple[str, float]]
) -> dict[str, float]:
    """Estimated starts (before the final timeline exists): the running sum of the lengths."""
    starts: dict[str, float] = {}
    clock_s = 0.0
    seconds = dict(lengths)
    for scene in script.scenes:
        starts[scene.scene_id] = clock_s
        clock_s += seconds.get(scene.scene_id, 0.0)
    return starts
