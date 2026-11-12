"""Classifying a file by what it contains, not by what it is called.

The evidence is whatever the analysis modules already stored (probe, transcript, vision tags,
OCR): every field may be unknown, and the classifier still answers, with less confidence. A file
name is a weak hint, a folder a weaker one, a file date no hint at all: cameras report 2016.
"""

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.numbers import fold
from media_house.modules.production_project.domain.values import Role


@dataclass(frozen=True, slots=True)
class FileEvidence:
    """Everything known about one file. ``None`` / empty = not known (never guessed)."""

    name: str
    kind: str  # video | image | audio | document | other
    sha256: str = ""
    size_bytes: int = 0
    folder: str = ""
    width: int = 0
    height: int = 0
    fps: float = 0.0
    duration: float = 0.0
    has_audio: bool | None = None
    has_alpha: bool | None = None
    is_vector: bool = False
    speech_words: int | None = None
    transcript_excerpt: str = ""
    faces: int | None = None
    ocr_text: str = ""
    #: Vision tags: night_sky, fireworks, browser_ui, cursor, product, crowd, brochure,
    #: white_background, logo, illegal_pyro, ``brand:<name>`` ...
    tags: tuple[str, ...] = ()
    peak_dbfs: float | None = None
    longest_silence: float | None = None


type Adder = Callable[[Role, float, str], None]


@dataclass(frozen=True, slots=True)
class Classification:
    role: Role | None
    confidence: float
    reasons: tuple[str, ...]
    alternatives: tuple[tuple[Role, float], ...]


_NAME_HINTS: tuple[tuple[re.Pattern[str], Role, float], ...] = (
    (re.compile(r"talk|sprech|cam(?!era)|moderation|a-?roll"), Role.TALKING, 0.2),
    (re.compile(r"gag|reaktion|react|wecker|blooper"), Role.GAGS, 0.3),
    (re.compile(r"screen|scr\b|bildschirm|aufnahme-?pc|rec-?pc"), Role.SCREENREC, 0.25),
    (re.compile(r"pack|produkt|product|freisteller"), Role.PACKSHOT, 0.25),
    (re.compile(r"screenshot|fullpage|ganze-?seite"), Role.SCREENSHOT, 0.3),
    (re.compile(r"archiv|prospekt|alt\b|letztes"), Role.ARCHIVE, 0.25),
    (re.compile(r"bg\b|background|hintergrund|nachthimmel|feuerwerk"), Role.BACKGROUND, 0.2),
    (re.compile(r"logo"), Role.LOGO, 0.35),
    (re.compile(r"intro|outro|endscreen"), Role.BRAND, 0.4),
)
_FOLDER_ROLE: dict[str, tuple[Role, ...]] = {
    "cam": (Role.TALKING, Role.GAGS),
    "scr": (Role.SCREENREC, Role.SCREENSHOT),
    "pck": (Role.PACKSHOT, Role.DOCUMENT),
    "arc": (Role.ARCHIVE,),
    "bg": (Role.BACKGROUND,),
    "gfx": (Role.LOGO, Role.BRAND),
}
_URL = re.compile(r"https?://|www\.|\.de/|\.com/")
_MIN_ROLE = 0.3


def classify(e: FileEvidence) -> Classification:
    scores: dict[Role, float] = {}
    reasons: dict[Role, list[str]] = {}

    def add(role: Role, value: float, why: str) -> None:
        scores[role] = scores.get(role, 0.0) + value
        reasons.setdefault(role, []).append(why)

    name = fold(e.name)
    for pattern, role, weight in _NAME_HINTS:
        if pattern.search(name):
            add(role, weight, f"Name erinnert an {role.value}")
    tail = e.folder.casefold().rpartition("_")[2]
    for role in _FOLDER_ROLE.get(tail, ()):
        add(role, 0.12, f"liegt im Ordner {e.folder}")
    tags = set(e.tags)
    if e.kind == "document":
        add(Role.DOCUMENT, 0.9, "Dokument")
    elif e.kind == "video":
        _video(e, tags, add)
    elif e.kind == "image":
        _image(e, tags, add)
    ranked = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0].value))
    if not ranked or ranked[0][1] < _MIN_ROLE:
        return Classification(None, 0.0, ("zu wenig Hinweise",), tuple(ranked[:3]))
    best, top = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    confidence = round(max(0.05, min(0.99, top - 0.25 * second)), 2)
    return Classification(
        best, confidence, tuple(reasons[best]), tuple((r, round(s, 2)) for r, s in ranked[1:4])
    )


def _video(e: FileEvidence, tags: set[str], add: Adder) -> None:
    words = e.speech_words
    per_second = (words / e.duration) if words is not None and e.duration > 0 else None
    faces = e.faces or 0
    if per_second is not None and per_second >= 1.0 and e.duration >= 20 and faces >= 1:
        add(Role.TALKING, 0.7, "Gesicht und durchgehende Sprache")
    elif per_second is not None and per_second >= 1.5 and e.duration >= 20:
        add(Role.TALKING, 0.45, "durchgehende Sprache")
    if tags & {"browser_ui", "cursor"}:
        add(Role.SCREENREC, 0.7, "Bedienoberfläche mit Cursor")
    if _URL.search(e.ocr_text):
        add(Role.SCREENREC, 0.15, "Adresse im Bild")
    scenic = bool(tags & {"night_sky", "fireworks"}) and faces == 0
    if scenic:
        add(Role.BACKGROUND, 0.65, "Nachthimmel oder Feuerwerk ohne Personen")
        if e.duration >= 60:
            add(Role.BACKGROUND, 0.1, "lange Einstellung")
    if tags & {"crowd", "brochure", "prospect"} or faces >= 3:
        add(Role.ARCHIVE, 0.5, "Menschenmenge oder Prospekt")
    if e.duration and e.duration <= 20 and (words is None or words <= 12):
        add(Role.GAGS, 0.5, "kurzer Clip ohne viel Sprache")
        if faces >= 1:
            add(Role.GAGS, 0.1, "Person im Bild")
    if e.duration and e.duration <= 25 and Role.BRAND in _hinted(e):
        add(Role.BRAND, 0.3, "kurzer Clip mit Intro/Outro im Namen")


def _hinted(e: FileEvidence) -> set[Role]:
    name = fold(e.name)
    return {role for pattern, role, _ in _NAME_HINTS if pattern.search(name)}


def _image(e: FileEvidence, tags: set[str], add: Adder) -> None:
    longest = max(e.width, e.height)
    if e.is_vector:
        add(Role.LOGO, 0.6, "Vektorgrafik")
    if "logo" in tags:
        add(Role.LOGO, 0.4, "als Logo erkannt")
    if e.has_alpha and 0 < longest <= 1200 and "product" not in tags:
        add(Role.LOGO, 0.45, "kleines Bild mit Transparenz")
    if tags & {"product", "white_background"}:
        add(Role.PACKSHOT, 0.5, "Produktbild")
    if e.has_alpha and "product" in tags:
        add(Role.PACKSHOT, 0.15, "freigestellt")
    tall = e.width > 0 and e.height / e.width >= 2.2
    if "browser_ui" in tags or tall or _URL.search(e.ocr_text):
        add(Role.SCREENSHOT, 0.6, "Screenshot einer Seite")
    if "brochure" in tags:
        add(Role.ARCHIVE, 0.45, "Prospektseite")


def assign_slot(
    e: FileEvidence, role: Role, requirements: Sequence[AssetRequirement]
) -> tuple[str, str]:
    """The requirement a classified file fills, and why. A packshot goes to the product its
    name or picture says; otherwise to the first requirement of the role that is still open."""
    candidates = [r for r in requirements if r.role is role]
    if not candidates:
        return "", f"keine Anforderung für {role.value}"
    if role is Role.PACKSHOT:
        haystack = fold(f"{e.name} {e.ocr_text} {' '.join(e.transcript_excerpt.split()[:20])}")
        compact = haystack.replace(" ", "")
        scored = sorted(
            (
                (
                    sum(
                        1
                        for t in fold(r.subject.replace("-", " ")).split()
                        if len(t) > 2 and t in compact
                    ),
                    r,
                )
                for r in candidates
            ),
            key=lambda x: (-x[0], x[1].requirement_id),
        )
        if scored[0][0] > 0:
            return scored[0][1].requirement_id, "Produktname im Dateinamen oder Bild"
        return "", "kein Produkt erkannt: bitte einem Slot zuordnen"
    if len(candidates) > 1:
        name = fold(e.name).replace(" ", "")
        for r in candidates:
            tail = r.requirement_id.rpartition("-")[2]
            if tail and tail in name:
                return r.requirement_id, f"„{tail}“ im Dateinamen"
    return candidates[0].requirement_id, "erste passende Anforderung"
