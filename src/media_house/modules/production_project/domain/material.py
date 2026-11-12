"""The Material Planner: from the approved script to the fewest files that still work.

The plan is a hypothesis: real uploads will differ and ``reconcile`` adjusts to them. Principles,
in order: the fewest files (one talking take, one screen recording, one gag file); everything the
video needs and nothing it does not (every requirement names the scenes that use it, and an
optional block switched off removes its requirements); order matters (``depends_on`` and
``timing``); exact instructions; raw footage and pictures only, never music or sound effects.
"""

import re
from collections.abc import Callable, Collection, Sequence
from dataclasses import dataclass, replace

from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.compliance import RuleKind, RuleSet
from media_house.modules.production_project.domain.facts import ProductRow
from media_house.modules.production_project.domain.naming import target_name
from media_house.modules.production_project.domain.numbers import slugify
from media_house.modules.production_project.domain.script import Scene, Script
from media_house.modules.production_project.domain.values import (
    FOLDER_OF,
    ConstraintKind,
    MultiFile,
    Priority,
    Role,
    SlotStatus,
    Timing,
)

FACT_CHECK = "fact-check"
SLOTS_FILLED = "slots-filled"

_GAG = re.compile(r"gag|reaktion|ritual|wecker", re.IGNORECASE)
_SCREEN = re.compile(r"bildschirm|screen|webseite|bestellseite|onlineshop|shop", re.IGNORECASE)
_ARCHIVE = re.compile(r"prospekt|archiv|früher|letztes jahr|älter", re.IGNORECASE)

_VIDEO = ("mp4", "mov", "mkv", "mxf", "avi", "m4v")
_IMAGE = ("png", "jpg", "jpeg", "webp", "svg", "tif", "tiff")
_DOC = ("pdf", "docx", "png", "jpg")


@dataclass(frozen=True, slots=True)
class AssetSpec:
    resolution: str = ""
    fps: int = 0
    #: ``required``, ``none`` (a silent clip) or ``any``.
    audio: str = "any"
    transparent: bool = False


@dataclass(frozen=True, slots=True)
class AssetRequirement:
    requirement_id: str
    folder: str
    role: Role
    title: str
    #: What to record or capture, exactly (German).
    instruction: str
    #: The "So nimmst du es auf" checklist.
    howto: tuple[str, ...]
    duration_hint: str
    spec: AssetSpec
    priority: Priority
    scene_ids: tuple[str, ...]
    #: Other requirements, ``fact-check``, ``slots-filled`` or ``approval:<constraint>``.
    depends_on: tuple[str, ...]
    #: The optional block or conditional claim that switches the requirement on.
    switched_by: str
    target_name_pattern: str
    accepts: tuple[str, ...]
    multi_file: MultiFile
    validators: tuple[str, ...]
    #: What must not be visible or audible (visible third-party brands, private data ...).
    forbidden: tuple[str, ...]
    timing: Timing
    #: The recommended recording order (1 = first).
    order: int = 0
    status: SlotStatus = SlotStatus.OPEN
    #: A product, for a packshot.
    subject: str = ""


@dataclass(frozen=True, slots=True)
class PlanInputs:
    slug: str
    script: Script
    brief: BriefModel
    rules: RuleSet
    products: tuple[ProductRow, ...]
    #: Named optional blocks that are switched off.
    off_blocks: frozenset[str] = frozenset()
    #: Approved conditional constraints (by id), e.g. the archive material.
    approvals: frozenset[str] = frozenset()
    #: Brand slots the library already holds (``intro``, ``outro``): they are not requested again.
    known_brand_assets: frozenset[str] = frozenset()
    client_logo_known: bool = False


def plan_materials(inputs: PlanInputs) -> tuple[AssetRequirement, ...]:
    scenes = [s for s in inputs.script.scenes if _live(s, inputs.off_blocks)]
    forbidden = _forbidden(inputs.rules, inputs.brief)
    slug = slugify(inputs.slug)
    out: list[AssetRequirement] = []

    spoken = tuple(s.scene_id for s in scenes if any(x.usable for x in s.sentences))
    out.append(_talking(slug, spoken, forbidden))
    if gag := _scenes(scenes, lambda s: s.function == "callback" or bool(_GAG.search(_blob(s)))):
        out.append(_gags(slug, gag, forbidden))
    if screen := _scenes(scenes, lambda s: bool(_SCREEN.search(s.graphics_intent))):
        out.append(_screenrec(slug, screen))
        out.append(_screenshot(slug, screen))
    for row in inputs.products:
        product_scenes = tuple(s.scene_id for s in scenes if _about(s, row))
        if product_scenes:
            out.append(_packshot(slug, row, product_scenes))
    archive = _scenes(scenes, lambda s: bool(_ARCHIVE.search(_blob(s))))
    cond = [
        c
        for c in inputs.brief.constraints_of(ConstraintKind.CONDITIONAL)
        if _ARCHIVE.search(c.text)
    ]
    if archive:
        out.append(_archive(slug, archive, cond[0].constraint_id if cond else "", forbidden))
    background_scenes = tuple(s.scene_id for s in scenes if s.graphics_intent)
    if background_scenes:
        out.append(replace(_background(slug), scene_ids=background_scenes))
    if not inputs.client_logo_known and inputs.brief.client:
        out.append(_logo(slug, inputs.brief.client, tuple(s.scene_id for s in scenes)))
    for kind in ("intro", "outro"):
        if kind not in inputs.known_brand_assets:
            consumers = tuple(s.scene_id for s in scenes if s.function == kind)
            if kind == "intro" and not consumers and scenes:
                consumers = (scenes[0].scene_id,)
            if consumers:
                out.append(replace(_brand(slug, kind), scene_ids=consumers))
    for c in inputs.brief.constraints_of(ConstraintKind.CONFIDENTIAL):
        if c.constraint_id in inputs.approvals and _ARCHIVE.search(c.text + " prospekt"):
            out.append(_document(slug, c.constraint_id, c.text))
    return _order(out)


def _live(scene: Scene, off: Collection[str]) -> bool:
    """A scene stays unless every sentence it speaks sits in a switched-off optional block."""
    spoken = [s for s in scene.sentences if s.usable]
    if not spoken:
        return True
    return any(not (s.optional and s.block in off) for s in spoken)


def _scenes(scenes: Sequence[Scene], pick: Callable[[Scene], bool]) -> tuple[str, ...]:
    return tuple(s.scene_id for s in scenes if pick(s))


def _blob(scene: Scene) -> str:
    return f"{scene.heading} {scene.intent} {scene.graphics_intent}"


def _about(scene: Scene, row: ProductRow) -> bool:
    return (
        scene.function == "product"
        and row.name.casefold() in (scene.heading + " " + scene.intent).casefold()
    )


def _forbidden(rules: RuleSet, brief: BriefModel) -> tuple[str, ...]:
    items: list[str] = []
    if rules.has(RuleKind.THIRD_PARTY_BRAND):
        items.append("sichtbare Fremdmarken (Logos auf Kleidung und Verpackungen abkleben)")
    if rules.has(RuleKind.ILLEGAL_PYRO):
        items.append("illegale oder nicht zugelassene Pyrotechnik")
    items.extend(
        f"vertraulich: {c.text[:80]}" for c in brief.constraints_of(ConstraintKind.CONFIDENTIAL)
    )
    return tuple(items)


def _req(
    slug: str,
    key: str,
    role: Role,
    title: str,
    instruction: str,
    howto: tuple[str, ...],
    *,
    accepts: tuple[str, ...],
    multi: MultiFile,
    scene_ids: tuple[str, ...],
    priority: Priority = Priority.REQUIRED,
    duration: str = "",
    spec: AssetSpec | None = None,
    depends: tuple[str, ...] = (),
    switched: str = "",
    validators: tuple[str, ...] = (),
    forbidden: tuple[str, ...] = (),
    timing: Timing = Timing.ANYTIME,
    label: str = "",
    index: bool = False,
    subject: str = "",
    ext: str = "mp4",
) -> AssetRequirement:
    pattern = target_name(slug, role, index=1 if index else None, label=label, ext=ext).replace(
        "_01", "_{nn}"
    )
    return AssetRequirement(
        requirement_id=key,
        folder=FOLDER_OF[role].value,
        role=role,
        title=title,
        instruction=instruction,
        howto=howto,
        duration_hint=duration,
        spec=spec or AssetSpec(),
        priority=priority,
        scene_ids=scene_ids,
        depends_on=depends,
        switched_by=switched,
        target_name_pattern=pattern,
        accepts=accepts,
        multi_file=multi,
        validators=validators,
        forbidden=forbidden,
        timing=timing,
        subject=subject,
    )


def _talking(slug: str, scenes: tuple[str, ...], forbidden: tuple[str, ...]) -> AssetRequirement:
    return _req(
        slug,
        "talking",
        Role.TALKING,
        "Sprechtext in einem durchgehenden Take",
        "Nimm das ganze Skript an einem Stück auf. Die Engine schneidet alles: "
        "du brauchst keine Datei pro Szene.",
        (
            "Zuerst fünf Sekunden Raumton aufnehmen, ohne zu sprechen.",
            "Das Skript Szene für Szene sprechen, bei einem Versprecher den Satz neu ansetzen.",
            "Vor jeder Szene laut „Szene …“ und die Nummer sagen (als Marker).",
            "Kameralicht und Ton prüfen, bevor du startest.",
            *(forbidden or ()),
        ),
        accepts=_VIDEO,
        multi=MultiFile.ORDERED_CHUNKS,
        scene_ids=scenes,
        duration="So lang wie dein freigegebenes Script; die Schätzung steht im Script-Tab.",
        spec=AssetSpec("3840x2160", 0, "required"),
        depends=(FACT_CHECK, SLOTS_FILLED),
        validators=("no-third-party-brands", "audio-present", "no-confidential", "resolution-ok"),
        forbidden=forbidden,
        index=True,
    )


def _gags(slug: str, scenes: tuple[str, ...], forbidden: tuple[str, ...]) -> AssetRequirement:
    return _req(
        slug,
        "gags",
        Role.GAGS,
        "Gags und Reaktionen in einer Datei",
        "Alle kurzen Gag- und Reaktionsclips hintereinander in einer Aufnahme.",
        (
            "Vor jedem Clip das Stichwort laut sagen (z. B. „Wecker eins“).",
            "Jeden Clip nur ein paar Sekunden, danach kurz Pause.",
            "Ruhig mehrere Varianten pro Gag, die Engine wählt.",
        ),
        accepts=_VIDEO,
        multi=MultiFile.ORDERED_CHUNKS,
        scene_ids=scenes,
        priority=Priority.RECOMMENDED,
        duration="30 bis 60 Sekunden gesamt",
        spec=AssetSpec("", 0, "any"),
        depends=(),
        validators=("no-third-party-brands", "no-bystanders"),
        forbidden=forbidden,
        index=True,
    )


def _screenrec(slug: str, scenes: tuple[str, ...]) -> AssetRequirement:
    return _req(
        slug,
        "screenrec",
        Role.SCREENREC,
        "Bildschirmaufnahme der Bestellseite",
        "Eine einzige Aufnahme, in der du die Seite von oben nach unten durchgehst.",
        (
            "Browserleiste und Lesezeichen leeren, Benachrichtigungen ausschalten.",
            "Nicht einloggen: keine Konto- oder Zahlungsdaten im Bild.",
            "Langsam scrollen, an jedem Produkt kurz anhalten.",
        ),
        accepts=_VIDEO,
        multi=MultiFile.SINGLE,
        scene_ids=scenes,
        duration="1 bis 2 Minuten",
        spec=AssetSpec("1920x1080", 30, "none"),
        depends=(FACT_CHECK,),
        validators=("no-private-data", "no-confidential"),
        forbidden=("private Daten (Adresse, Login, Zahlungsdaten)",),
        timing=Timing.AFTER_SOURCE_LIVE,
        index=True,
    )


def _screenshot(slug: str, scenes: tuple[str, ...]) -> AssetRequirement:
    return _req(
        slug,
        "screenshot",
        Role.SCREENSHOT,
        "Komplette Seite als Screenshot",
        "Ein Screenshot der gesamten Seite in voller Länge.",
        ("Browserfunktion „ganze Seite“ nutzen.", "Keine Konto- oder Zahlungsdaten im Bild."),
        accepts=("png", "jpg", "jpeg", "webp"),
        multi=MultiFile.SINGLE,
        scene_ids=scenes,
        priority=Priority.RECOMMENDED,
        spec=AssetSpec("", 0, "none"),
        validators=("no-private-data", "no-confidential"),
        forbidden=("private Daten (Adresse, Login, Zahlungsdaten)",),
        timing=Timing.AFTER_SOURCE_LIVE,
        label="shop",
        ext="png",
    )


def _packshot(slug: str, row: ProductRow, scenes: tuple[str, ...]) -> AssetRequirement:
    return _req(
        slug,
        f"packshot-{row.subject}",
        Role.PACKSHOT,
        f"Packshot {row.name}",
        f"Ein freigestelltes Bild von {row.name}.",
        ("Von vorn, gerade, gleichmäßig ausgeleuchtet.", "Heller, ruhiger Hintergrund."),
        accepts=_IMAGE,
        multi=MultiFile.SET,
        scene_ids=scenes,
        spec=AssetSpec("", 0, "none", True),
        validators=("no-third-party-brands", "no-confidential"),
        label=row.subject,
        subject=row.subject,
        ext="png",
    )


def _archive(
    slug: str, scenes: tuple[str, ...], constraint: str, forbidden: tuple[str, ...]
) -> AssetRequirement:
    return _req(
        slug,
        "archive",
        Role.ARCHIVE,
        "Archivmaterial älterer Prospekte",
        "Fotos oder Aufnahmen älterer Prospekte, nur nach Freigabe des Auftraggebers.",
        ("Erst die Freigabe einholen.", "Seiten gerade und vollständig abfotografieren."),
        accepts=_VIDEO + _IMAGE,
        multi=MultiFile.SET,
        scene_ids=scenes,
        priority=Priority.OPTIONAL,
        depends=(f"approval:{constraint}",) if constraint else (),
        switched=constraint,
        validators=("no-third-party-brands", "no-bystanders", "no-confidential"),
        forbidden=forbidden,
        label="archiv",
        ext="jpg",
    )


def _background(slug: str) -> AssetRequirement:
    return _req(
        slug,
        "background",
        Role.BACKGROUND,
        "Feuerwerk im Hintergrund",
        "Ein langes Video von Feuerwerk am Nachthimmel, ohne Personen.",
        (
            "Mindestens fünf Minuten am Stück.",
            "Nur eigenes Material oder mit Lizenznachweis.",
            "Keine Gesichter, keine Logos im Bild.",
        ),
        accepts=_VIDEO,
        multi=MultiFile.SINGLE,
        scene_ids=(),
        priority=Priority.RECOMMENDED,
        duration="5 bis 10 Minuten",
        spec=AssetSpec("1920x1080", 0, "none"),
        validators=("license-known", "no-bystanders", "no-third-party-brands"),
        index=True,
    )


def _logo(slug: str, client: str, scenes: tuple[str, ...]) -> AssetRequirement:
    return _req(
        slug,
        "logo",
        Role.LOGO,
        f"Logo {client}",
        f"Das Logo von {client} als PNG mit Transparenz (oder als SVG).",
        ("Vom Auftraggeber oder aus dem Presseportal.", "Nicht abfotografieren."),
        accepts=("png", "svg", "webp"),
        multi=MultiFile.SINGLE,
        scene_ids=scenes[:1],
        priority=Priority.RECOMMENDED,
        spec=AssetSpec("", 0, "none", True),
        label="logo",
        ext="png",
    )


def _brand(slug: str, kind: str) -> AssetRequirement:
    return _req(
        slug,
        f"brand-{kind}",
        Role.BRAND,
        f"Kanal-{kind.title()}",
        f"Dein {kind} des Kanals als Videoclip.",
        ("Das fertige Intro oder Outro, wie es sonst in deinen Videos läuft.",),
        accepts=_VIDEO,
        multi=MultiFile.SINGLE,
        scene_ids=(),
        priority=Priority.RECOMMENDED,
        spec=AssetSpec("", 0, "any"),
        label=kind,
    )


def _document(slug: str, constraint: str, text: str) -> AssetRequirement:
    return _req(
        slug,
        f"document-{constraint}",
        Role.DOCUMENT,
        "Freigegebenes Dokument",
        f"Nur mit schriftlicher Freigabe: {text[:80]}",
        ("Die Freigabe zuerst im Projekt eintragen.",),
        accepts=_DOC,
        multi=MultiFile.SINGLE,
        scene_ids=(),
        priority=Priority.OPTIONAL,
        depends=(f"approval:{constraint}",),
        switched=constraint,
        validators=("no-confidential",),
        label="freigabe",
        ext="pdf",
    )


_ROLE_ORDER = {
    Role.LOGO: 1,
    Role.BRAND: 1,
    Role.PACKSHOT: 2,
    Role.BACKGROUND: 3,
    Role.ARCHIVE: 4,
    Role.DOCUMENT: 4,
    Role.TALKING: 5,
    Role.GAGS: 6,
    Role.SCREENREC: 7,
    Role.SCREENSHOT: 7,
}


def _order(items: list[AssetRequirement]) -> tuple[AssetRequirement, ...]:
    """Recommended order: what needs no source first, then the talking take (after the fact
    check and the Technik-Felder), then what can only be captured once the source is live."""
    live = {Timing.ANYTIME: 0, Timing.BEFORE_SOURCE_LIVE: 0, Timing.AFTER_SOURCE_LIVE: 1}
    ranked = sorted(items, key=lambda r: (live[r.timing], _ROLE_ORDER[r.role], r.requirement_id))
    return tuple(replace(r, order=i + 1) for i, r in enumerate(ranked))


def switch_off(
    requirements: Sequence[AssetRequirement], dropped: Collection[str]
) -> tuple[AssetRequirement, ...]:
    return tuple(r for r in requirements if r.requirement_id not in dropped)
