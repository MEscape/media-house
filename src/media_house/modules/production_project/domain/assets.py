"""The asset validators: what is wrong with a file before the edit engine may use it.

A flagged file is never used until a person settles the flag (``approved``, ``blurred``,
``replaced``). The validators read the stored evidence only; they never open a file.
"""

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

from media_house.modules.production_project.domain.brief import BriefModel
from media_house.modules.production_project.domain.classify import FileEvidence
from media_house.modules.production_project.domain.compliance import RuleKind, RuleSet
from media_house.modules.production_project.domain.lexicon import (
    COMPETITORS,
    ILLEGAL_PYRO_TERMS,
    PRIVATE_DATA_PATTERNS,
    PYRO_BRANDS,
    STOCK_MARKERS,
)
from media_house.modules.production_project.domain.material import AssetRequirement
from media_house.modules.production_project.domain.numbers import fold
from media_house.modules.production_project.domain.values import (
    ConstraintKind,
    FlagKind,
    Resolution,
    Role,
    Severity,
)

_CAMERA_NAME = re.compile(
    r"^(gopr|gx\d|gh\d|dsc|img_|mvi_|pxl_|dji_|c\d{4}|mov_|vid_|clip\d)", re.I
)
_SITE_NAME = re.compile(r"[a-z0-9]+(?:[-_][a-z0-9]+){5,}")


@dataclass(frozen=True, slots=True)
class AssetFlag:
    kind: FlagKind
    message: str
    #: What the validator saw (an OCR snippet, a tag).
    evidence: str
    severity: Severity
    #: How it can be settled: ``approved`` (Freigabe liegt vor), ``blurred``, ``replaced``.
    options: tuple[Resolution, ...]
    resolution: Resolution = Resolution.OPEN
    note: str = ""

    @property
    def open(self) -> bool:
        return self.resolution is Resolution.OPEN


@dataclass(frozen=True, slots=True)
class ValidationContext:
    rules: RuleSet
    brief: BriefModel
    #: Brands the brief permits (the client, the products' own brand ...), lowercase.
    allowed_brands: frozenset[str] = frozenset()
    #: Approved constraints (by id): a confidential item with an approval is no flag.
    approvals: frozenset[str] = frozenset()
    #: Licence proof recorded per file hash.
    licensed: frozenset[str] = frozenset()


def validate_asset(
    e: FileEvidence, role: Role, requirement: AssetRequirement | None, ctx: ValidationContext
) -> tuple[AssetFlag, ...]:
    ids = set(requirement.validators) if requirement else set()
    flags: list[AssetFlag] = []
    flags += (
        _brands(e, role, ctx)
        if _wants(ids, "no-third-party-brands", ctx, RuleKind.THIRD_PARTY_BRAND)
        else []
    )
    flags += (
        _private(e, role)
        if role in {Role.SCREENREC, Role.SCREENSHOT} or "no-private-data" in ids
        else []
    )
    flags += (
        _confidential(e, ctx)
        if "no-confidential" in ids or ctx.rules.has(RuleKind.CONFIDENTIAL)
        else []
    )
    flags += (
        _bystanders(e, role)
        if "no-bystanders" in ids or role in {Role.ARCHIVE, Role.BACKGROUND}
        else []
    )
    flags += _pyro(e) if ctx.rules.has(RuleKind.ILLEGAL_PYRO) else []
    flags += _licence(e, role, ctx) if role is Role.BACKGROUND or "license-known" in ids else []
    flags += _technical(e, requirement)
    return tuple(flags)


def _wants(ids: set[str], validator: str, ctx: ValidationContext, kind: RuleKind) -> bool:
    return validator in ids or ctx.rules.has(kind)


def _brands(e: FileEvidence, role: Role, ctx: ValidationContext) -> list[AssetFlag]:
    seen = {t.split(":", 1)[1].lower() for t in e.tags if t.startswith("brand:")}
    text = fold(e.ocr_text)
    seen |= {
        b
        for b in (PYRO_BRANDS | COMPETITORS)
        if re.search(rf"(?<![a-z]){re.escape(fold(b))}(?![a-z])", text)
    }
    seen -= {b.lower() for b in ctx.allowed_brands} | {ctx.brief.client.lower()}
    if not seen:
        return []
    names = ", ".join(sorted(seen))
    archive = role is Role.ARCHIVE
    return [
        AssetFlag(
            FlagKind.THIRD_PARTY_BRAND,
            f"Datei prüfen: sichtbare Fremdmarke ({names})"
            + (". Auf alten Prospekten kannst du das bewusst so lassen." if archive else "."),
            names,
            Severity.WARNING if archive else Severity.BLOCKER,
            (Resolution.APPROVED, Resolution.BLURRED, Resolution.REPLACED),
        )
    ]


def _private(e: FileEvidence, role: Role) -> list[AssetFlag]:
    hits = [
        f"{label}: {m.group(0)[:4]}…"
        for label, pattern in PRIVATE_DATA_PATTERNS
        if (m := re.search(pattern, e.ocr_text))
    ]
    if not hits:
        return []
    return [
        AssetFlag(
            FlagKind.PRIVATE_DATA,
            "Datei prüfen: private Daten im Bild ("
            + ", ".join(h.split(":")[0] for h in hits)
            + ").",
            "; ".join(hits),
            Severity.BLOCKER,
            (Resolution.BLURRED, Resolution.REPLACED),
        )
    ]


def _confidential(e: FileEvidence, ctx: ValidationContext) -> list[AssetFlag]:
    compact = fold(f"{e.name} {e.ocr_text}").replace(" ", "")
    out: list[AssetFlag] = []
    for c in ctx.brief.constraints_of(ConstraintKind.CONFIDENTIAL):
        if c.constraint_id in ctx.approvals:
            continue
        hits = [k for k in c.keywords if k.replace(" ", "") in compact]
        if len(hits) >= 2 or any(len(k) >= 8 for k in hits):
            out.append(
                AssetFlag(
                    FlagKind.CONFIDENTIAL,
                    f"Datei prüfen: sieht nach vertraulichem Material aus („{c.text[:70]}“).",
                    ", ".join(hits),
                    Severity.BLOCKER,
                    (Resolution.APPROVED, Resolution.REPLACED),
                )
            )
    return out


def _bystanders(e: FileEvidence, role: Role) -> list[AssetFlag]:
    if not e.faces or role not in {Role.ARCHIVE, Role.BACKGROUND, Role.GAGS}:
        return []
    if role is Role.GAGS and e.faces < 2:
        return []
    return [
        AssetFlag(
            FlagKind.BYSTANDER_FACES,
            "Datei prüfen: fremde Gesichter im Bild. Verpixeln ist am sichersten.",
            f"{e.faces} Gesicht(er)",
            Severity.WARNING,
            (Resolution.BLURRED, Resolution.APPROVED, Resolution.REPLACED),
        )
    ]


def _pyro(e: FileEvidence) -> list[AssetFlag]:
    text = fold(e.ocr_text + " " + e.name)
    hit = "illegal_pyro" in e.tags or any(fold(t) in text for t in ILLEGAL_PYRO_TERMS if len(t) > 3)
    if not hit:
        return []
    return [
        AssetFlag(
            FlagKind.ILLEGAL_PYRO,
            "Datei prüfen: möglicherweise nicht zugelassene Pyrotechnik.",
            "Tag oder Text",
            Severity.BLOCKER,
            (Resolution.REPLACED,),
        )
    ]


def _licence(e: FileEvidence, role: Role, ctx: ValidationContext) -> list[AssetFlag]:
    if e.sha256 and e.sha256 in ctx.licensed:
        return []
    name = fold(e.name).replace(" ", "")
    stock = any(fold(m).replace(" ", "") in name for m in STOCK_MARKERS)
    site_style = bool(
        _SITE_NAME.fullmatch(e.name.rsplit(".", 1)[0].lower())
    ) and not _CAMERA_NAME.match(e.name)
    if not (stock or site_style):
        return []
    return [
        AssetFlag(
            FlagKind.LICENSE_UNKNOWN,
            "Datei prüfen: sieht nach heruntergeladenem Stockmaterial aus. "
            "Bitte den Lizenznachweis bereithalten.",
            e.name,
            Severity.WARNING,
            (Resolution.APPROVED, Resolution.REPLACED),
        )
    ]


def _technical(e: FileEvidence, r: AssetRequirement | None) -> list[AssetFlag]:
    out: list[AssetFlag] = []
    if r is not None and e.kind == "video":
        spec = r.spec
        if spec.resolution and e.width and e.height:
            want_w, want_h = (int(x) for x in spec.resolution.split("x"))
            if e.width * e.height < want_w * want_h * 0.9:
                out.append(
                    AssetFlag(
                        FlagKind.RESOLUTION,
                        f"Die Auflösung {e.width}x{e.height} liegt unter {spec.resolution}.",
                        f"{e.width}x{e.height}",
                        Severity.WARNING,
                        (Resolution.APPROVED, Resolution.REPLACED),
                    )
                )
        if spec.fps and e.fps and abs(e.fps - spec.fps) > 0.5:
            out.append(
                AssetFlag(
                    FlagKind.FPS,
                    f"{e.fps:g} Bilder/s statt {spec.fps}.",
                    f"{e.fps:g}",
                    Severity.WARNING,
                    (Resolution.APPROVED, Resolution.REPLACED),
                )
            )
        if spec.audio == "required" and e.has_audio is False:
            out.append(
                AssetFlag(
                    FlagKind.NO_AUDIO,
                    "Die Datei hat keine Tonspur.",
                    "kein Ton",
                    Severity.BLOCKER,
                    (Resolution.REPLACED,),
                )
            )
    if e.peak_dbfs is not None and e.peak_dbfs >= -0.1:
        out.append(
            AssetFlag(
                FlagKind.CLIPPING,
                f"Der Ton übersteuert (Spitze {e.peak_dbfs:.1f} dBFS).",
                f"{e.peak_dbfs:.1f} dBFS",
                Severity.WARNING,
                (Resolution.APPROVED, Resolution.REPLACED),
            )
        )
    if e.longest_silence is not None and e.longest_silence >= 8.0 and e.kind == "video":
        out.append(
            AssetFlag(
                FlagKind.LONG_SILENCE,
                f"Eine Pause von {e.longest_silence:.0f} s im Ton.",
                f"{e.longest_silence:.0f} s",
                Severity.WARNING,
                (Resolution.APPROVED,),
            )
        )
    return out


def settle(
    flags: Iterable[AssetFlag], kind: FlagKind, how: Resolution, note: str
) -> tuple[AssetFlag, ...]:
    """A person settles every flag of a kind on a file ("Freigabe liegt vor", "verpixeln")."""
    return tuple(replace(f, resolution=how, note=note) if f.kind is kind else f for f in flags)


def usable(flags: Sequence[AssetFlag]) -> bool:
    """The edit engine may use a file only when no flag of it is still open."""
    return not any(f.open for f in flags)
