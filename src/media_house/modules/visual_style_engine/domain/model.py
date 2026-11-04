"""The identity (versioned data) and the resolved style every Layer 4 module consumes."""

from dataclasses import dataclass, field, fields
from datetime import datetime
from fractions import Fraction

from media_house.modules.visual_style_engine.domain.color import hsl, rgb
from media_house.modules.visual_style_engine.domain.profile import Semantic, StyleTokens
from media_house.modules.visual_style_engine.domain.values import (
    ADDED_IN,
    REFERENCE_SHORT_SIDE,
    Confidence,
    ElementKind,
    Evidence,
    OverrideLayer,
    QcIssue,
    RuleKind,
    Severity,
    ZoneKind,
)

# --- identity building blocks ------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class FontFace:
    """One bundled font file, referenced by ``resource`` (relative to the font package) and
    pinned by ``sha256``. Never an absolute path."""

    key: str
    family: str
    weight: int
    style: str
    resource: str
    sha256: str
    license: str
    version: str
    fallback: str | None = None  # another bundled face key; a substitution is always reported
    #: Thickest outline (outside the glyph) as a share of the font size that keeps letters and
    #: counters apart in this face (condensed faces have narrow gaps). Measured by rendering.
    max_stroke_ratio: float = 0.11
    #: The glyphs this face must cover INSTEAD of the identity's text glyphs (the emoji face
    #: covers emoji, not letters). Identity v2+; ``None`` = ``ChannelIdentity.required_glyphs``.
    coverage: str | None = field(default=None, metadata={ADDED_IN: 2})


@dataclass(frozen=True, slots=True, kw_only=True)
class Easing:
    """A named CSS-style cubic bezier (control points P1, P2; P0 = (0,0), P3 = (1,1))."""

    name: str
    x1: float
    y1: float
    x2: float
    y2: float
    use: str

    def css(self) -> str:
        return f"cubic-bezier({self.x1}, {self.y1}, {self.x2}, {self.y2})"


@dataclass(frozen=True, slots=True, kw_only=True)
class ZoneSpec:
    """A named rectangle of the output as fractions (top-left origin)."""

    name: str
    kind: ZoneKind
    x0: float
    y0: float
    x1: float
    y1: float
    accepts: tuple[ElementKind, ...] = ()


@dataclass(frozen=True, slots=True, kw_only=True)
class Layout:
    aspect: str  # "16:9", "9:16", "1:1"
    zones: tuple[ZoneSpec, ...]


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorPair:
    """An approved foreground/background combination (palette names) and what it is for."""

    fg: str
    bg: str
    use: str  # "text" (needs the text minimum) or "large_text" (needs the large-text minimum)


@dataclass(frozen=True, slots=True, kw_only=True)
class ContrastRules:
    min_text: float = 4.5  # WCAG AA normal text
    min_large_text: float = 3.0  # WCAG AA large text
    large_text_size: float = 48.0  # ref px from which text counts as large


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenRule:
    """Rule kind and evidence of every token under ``path`` (longest prefix wins)."""

    path: str
    rule: RuleKind
    evidence: Evidence
    confidence: Confidence
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandRule:
    """A rule that is not a single token (e.g. "stickers never over flat colour")."""

    code: str
    text: str
    rule: RuleKind
    evidence: Evidence
    confidence: Confidence
    source: str


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandAssetSlot:
    """A recurring brand asset the identity needs. Files live in the Media Library."""

    name: str
    purpose: str
    required: bool
    evidence: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ChannelIdentity:
    """One immutable version of the channel identity. Old versions are kept forever."""

    id: str
    version: int
    name: str
    handle: str
    description: str
    personality: tuple[str, ...]
    audience: str
    emotions: tuple[str, ...]
    language: str
    required_glyphs: str
    references: tuple[str, ...]
    tokens: StyleTokens
    faces: tuple[FontFace, ...]
    easings: tuple[Easing, ...]
    layouts: tuple[Layout, ...]
    combinations: tuple[ColorPair, ...]
    contrast: ContrastRules
    thumbnail_only: tuple[str, ...]
    token_rules: tuple[TokenRule, ...]
    brand_rules: tuple[BrandRule, ...]
    prohibited: tuple[str, ...]
    brand_assets: tuple[BrandAssetSlot, ...]

    def rule_for(self, path: str) -> TokenRule | None:
        return rule_for(self.token_rules, path)

    def layout_for(self, aspect: str) -> Layout | None:
        return next((layout for layout in self.layouts if layout.aspect == aspect), None)


def rule_for(rules: tuple[TokenRule, ...], path: str) -> TokenRule | None:
    matches = [r for r in rules if path == r.path or path.startswith(f"{r.path}.")]
    return max(matches, key=lambda r: len(r.path), default=None)


# --- the resolved style ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True, kw_only=True)
class OutputFormat:
    width: int
    height: int

    @property
    def aspect(self) -> str:
        ratio = Fraction(self.width, self.height)
        return f"{ratio.numerator}:{ratio.denominator}"

    @property
    def short_side(self) -> int:
        return min(self.width, self.height)


@dataclass(frozen=True, slots=True, kw_only=True)
class Zone:
    """A zone of the requested output: fractions and the pixel rectangle (left, top, w, h)."""

    name: str
    kind: ZoneKind
    accepts: tuple[ElementKind, ...]
    x0: float
    y0: float
    x1: float
    y1: float
    left: int
    top: int
    width: int
    height: int

    def contains(self, left: int, top: int, width: int, height: int) -> bool:
        return (
            left >= self.left
            and top >= self.top
            and left + width <= self.left + self.width
            and top + height <= self.top + self.height
        )


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedType:
    """A type role for this output: the face and the size in output pixels."""

    role: str
    face: FontFace
    size_px: int
    tracking_em: float
    leading: float
    case: str


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedPair:
    fg: str
    bg: str
    use: str
    ratio: float
    required: float


@dataclass(frozen=True, slots=True, kw_only=True)
class AppliedOverride:
    layer: OverrideLayer
    path: str
    value_json: str  # canonical JSON of the new value
    previous_json: str
    reason: str
    section_id: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class SectionStyle:
    section_id: str
    overrides: tuple[AppliedOverride, ...]
    tokens: StyleTokens


@dataclass(frozen=True, slots=True, kw_only=True)
class BrandAsset:
    """A brand asset slot bound (or not yet bound) to a Media Library asset."""

    slot: str
    purpose: str
    required: bool
    asset_id: str | None = None
    checksum: str | None = None

    @property
    def missing(self) -> bool:
        return self.asset_id is None


@dataclass(frozen=True, slots=True, kw_only=True)
class ColorToken:
    name: str
    hex: str

    @property
    def rgb(self) -> tuple[int, int, int]:
        return rgb(self.hex)

    @property
    def hsl(self) -> tuple[float, float, float]:
        return hsl(self.hex)


@dataclass(frozen=True, slots=True, kw_only=True)
class StyleRef:
    """What a consumer keeps in its plan (and its cache key): the stored style document."""

    document_id: str
    fingerprint: str
    identity_version: int
    profile: str


@dataclass(frozen=True, slots=True, kw_only=True)
class AffectedStyle:
    """A stored style compared with the same request resolved under another identity version."""

    document_id: str
    anchor_asset_id: str
    project_id: str | None
    profile: str
    identity_version: int
    fingerprint: str
    new_fingerprint: str | None  # None: the request is not valid under the new identity
    affected: bool
    detail: str = ""


@dataclass(frozen=True, slots=True, kw_only=True)
class ResolvedStyle:
    """The channel identity resolved for one project, format and output size. Immutable.

    ``tokens`` is the resolved token tree (sizes in reference pixels, colours as palette names);
    ``px()``, ``hex()``, ``zone()`` and ``type_style()`` turn them into output values.
    ``fingerprint`` is the sha256 of the canonical visual content: put it in your cache key.
    """

    schema_version: int
    style_version: int
    supersedes: str | None
    project_id: str | None  # None on the no-speech path (no edit plan)
    anchor_asset_id: str
    created_at: datetime
    identity_id: str
    identity_version: int
    profile: str
    profile_version: int
    output: OutputFormat
    sponsored: bool
    tokens: StyleTokens
    faces: tuple[FontFace, ...]
    types: tuple[ResolvedType, ...]
    easings: tuple[Easing, ...]
    zones: tuple[Zone, ...]
    combinations: tuple[ResolvedPair, ...]
    contrast: ContrastRules
    thumbnail_only: tuple[str, ...]
    brand_assets: tuple[BrandAsset, ...]
    token_rules: tuple[TokenRule, ...]
    brand_rules: tuple[BrandRule, ...]
    prohibited: tuple[str, ...]
    overrides: tuple[AppliedOverride, ...]
    sections: tuple[SectionStyle, ...]
    checks: tuple[QcIssue, ...]
    fingerprint: str

    # --- lookups ---

    def px(self, ref_px: float) -> int:
        """Reference pixels -> output pixels (scaled by the short side)."""
        return round(ref_px * self.output.short_side / REFERENCE_SHORT_SIDE)

    def hex(self, name: str, *, section_id: str | None = None) -> str:
        """A palette name (``brand_red``) or a semantic meaning (``highlight``) as ``#RRGGBB``."""
        tokens = self.tokens_for(section_id)
        if name in {f.name for f in fields(Semantic)}:
            name = getattr(tokens.semantic, name)
        value = getattr(tokens.palette, name, None)
        if not isinstance(value, str):
            raise KeyError(f"unknown colour {name!r}")
        return value

    def palette(self) -> tuple[ColorToken, ...]:
        return tuple(
            ColorToken(name=f.name, hex=getattr(self.tokens.palette, f.name))
            for f in fields(self.tokens.palette)
        )

    def zone(self, name: str) -> Zone:
        found = next((z for z in self.zones if z.name == name), None)
        if found is None:
            raise KeyError(f"unknown zone {name!r}")
        return found

    def type_style(self, role: str) -> ResolvedType:
        found = next((t for t in self.types if t.role == role), None)
        if found is None:
            raise KeyError(f"unknown type role {role!r}")
        return found

    def face(self, key: str) -> FontFace:
        found = next((f for f in self.faces if f.key == key), None)
        if found is None:
            raise KeyError(f"unknown font face {key!r}")
        return found

    def easing(self, name: str) -> Easing:
        found = next((e for e in self.easings if e.name == name), None)
        if found is None:
            raise KeyError(f"unknown easing {name!r}")
        return found

    def tokens_for(self, section_id: str | None) -> StyleTokens:
        """The tokens of one section (its overrides applied), else the project tokens."""
        if section_id is not None:
            for section in self.sections:
                if section.section_id == section_id:
                    return section.tokens
        return self.tokens

    def safe_areas(self) -> tuple[Zone, ...]:
        """Zones reframing must keep free of the subject (graphics, captions, platform UI)."""
        return tuple(z for z in self.zones if z.name != "card_safe")

    @property
    def mandatory_rules(self) -> tuple[BrandRule, ...]:
        return tuple(r for r in self.brand_rules if r.rule is RuleKind.MANDATORY)

    @property
    def preferences(self) -> tuple[BrandRule, ...]:
        return tuple(r for r in self.brand_rules if r.rule is RuleKind.PREFERENCE)

    @property
    def locked_tokens(self) -> tuple[str, ...]:
        return tuple(r.path for r in self.token_rules if r.rule is RuleKind.MANDATORY)

    @property
    def missing_brand_assets(self) -> tuple[BrandAsset, ...]:
        return tuple(a for a in self.brand_assets if a.missing)

    @property
    def errors(self) -> tuple[QcIssue, ...]:
        return tuple(i for i in self.checks if i.severity in (Severity.CRITICAL, Severity.ERROR))

    def format_price(self, cents: int) -> str:
        """German price text: ``1599`` -> ``15,99 €`` (or ``15,99 EUR``), thousands with a dot."""
        if cents < 0:
            raise ValueError("a price is never negative")
        euros, rest = divmod(cents, 100)
        whole = f"{euros:,}".replace(",", ".")
        unit = "EUR" if self.tokens.graphics.price_format == "comma_eur" else "€"
        return f"{whole},{rest:02d} {unit}"
