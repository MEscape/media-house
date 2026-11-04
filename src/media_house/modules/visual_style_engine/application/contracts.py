"""The Visual Style Engine PUBLIC API for other modules.

Other modules may import this file (and only this file) from ``visual_style_engine``.

The engine is the single source of truth for how the MEsc channel looks: colours, fonts, type
scale, graphic language, layout zones per output format, caption and title looks, motion feel,
logo rules and format profiles. It resolves them into an immutable ``ResolvedStyle`` per project
and output, stores it as a ``visual_style`` document of the project's anchor asset (pinned:
later identity versions are used only when asked) and validates what consumers drew. It never
renders, animates, grades footage or decides captions/transitions.

    result = resolver.execute(ResolveStyleCommand(anchor_asset_id=anchor, project_id=pid,
                                                  width=1080, height=1920,
                                                  config=StyleConfig(profile="shorts_haul")))
    style, ref = result.value.style, result.value.ref       # keep ``ref`` in your plan
    style.hex("highlight")                  # '#F4DE19' (palette name or semantic meaning)
    style.zone("caption_band")              # Zone(left, top, width, height) in output pixels
    style.type_style("caption")             # ResolvedType(face=FontFace(...), size_px=...)
    style.px(style.tokens.graphics.sticker_glow)               # reference px -> output px
    style.easing(style.tokens.motion.entry_easing).css()      # 'cubic-bezier(0.34, 1.56, ...)'
    style.format_price(1599)                # '15,99 €'
    issues = validator.validate(style, StyleUsage(module="caption_engine", ...))

Consumers put ``style.fingerprint`` in their cache key, read stored styles through
``VisualStyleCatalog`` (``load_ref`` for a ``StyleRef``) and never define colours, fonts, easings
or zones of their own. Font files: ``FontFiles.locate(face)`` (tokens carry the resource name
and sha256, never a path).
"""

from typing import Protocol

from media_house.modules.media_library.application.contracts import MediaAssetDto, MediaError
from media_house.modules.visual_style_engine.application.ports import FontFile, FontFiles
from media_house.modules.visual_style_engine.application.resolve_style import (
    BrandAssetBinding,
    ResolveStyleCommand,
    ResolveStyleError,
    ResolveStyleResult,
)
from media_house.modules.visual_style_engine.domain.color import contrast_ratio
from media_house.modules.visual_style_engine.domain.drift import (
    ColorUse,
    MotionUse,
    PlacementUse,
    StyleUsage,
    TextUse,
    validate_usage,
)
from media_house.modules.visual_style_engine.domain.errors import (
    FontUnavailable,
    InvalidStyle,
    InvalidStyleConfig,
    InvalidStyleDocument,
    StyleRefMismatch,
    UnknownIdentityVersion,
    UnsupportedOutputFormat,
    VisualStyleError,
)
from media_house.modules.visual_style_engine.domain.identity import (
    CURRENT_IDENTITY_VERSION,
    EMOJI_GLYPHS,
    IDENTITIES,
    MESC_IDENTITY_V1,
    MESC_IDENTITY_V2,
)
from media_house.modules.visual_style_engine.domain.model import (
    AffectedStyle,
    AppliedOverride,
    BrandAsset,
    BrandRule,
    ChannelIdentity,
    ColorToken,
    ContrastRules,
    Easing,
    FontFace,
    OutputFormat,
    ResolvedPair,
    ResolvedStyle,
    ResolvedType,
    SectionStyle,
    StyleRef,
    TokenRule,
    Zone,
)
from media_house.modules.visual_style_engine.domain.profile import (
    ASPECT_PROFILE,
    DEFAULT_OUTPUT,
    DEFAULT_PROFILE,
    PROFILES,
    STORY_FORMAT_PROFILE,
    TYPE_ROLES,
    ZONE_NAMES,
    Branding,
    CaptionLook,
    Captions,
    Emoji,
    EndScreen,
    FormatProfile,
    Graphics,
    Motion,
    Override,
    Palette,
    SectionOverride,
    Semantic,
    StyleConfig,
    StyleTokens,
    TextStyle,
    Titles,
    Transitions,
    TypeRole,
    Typography,
    knob_fields,
)
from media_house.modules.visual_style_engine.domain.resolve import resolve_style
from media_house.modules.visual_style_engine.domain.values import (
    PROFILE_VERSION,
    SCHEMA_VERSION,
    ElementKind,
    MotionPhase,
    QcCategory,
    QcIssue,
    RuleKind,
    Severity,
    Surface,
    TextCase,
    ZoneKind,
)
from media_house.shared.errors import Result


class StyleResolver(Protocol):
    def execute(
        self, command: ResolveStyleCommand
    ) -> Result[ResolveStyleResult, ResolveStyleError]:
        """Resolve, check and store (or reuse) the style. Blocking library I/O: run in a job."""
        ...


class VisualStyleCatalog(Protocol):
    """Stored styles; nothing is resolved here (except ``affected_by``'s comparison)."""

    def latest(
        self,
        anchor_asset_id: str,
        *,
        project_id: str | None = None,
        profile: str | None = None,
        output: str | None = None,
    ) -> Result[tuple[ResolvedStyle, MediaAssetDto] | None, MediaError]:
        """The pinned (newest stored) style; ``output`` as ``"1920x1080"``."""
        ...

    def history(self, anchor_asset_id: str) -> list[MediaAssetDto]: ...

    def load(
        self, document_id: str
    ) -> Result[ResolvedStyle, MediaError | InvalidStyleDocument]: ...

    def load_ref(
        self, ref: StyleRef
    ) -> Result[ResolvedStyle, MediaError | InvalidStyleDocument | StyleRefMismatch]: ...

    def affected_by(
        self, identity_version: int
    ) -> Result[list[AffectedStyle], UnknownIdentityVersion]: ...


class StyleValidator(Protocol):
    def validate(self, style: ResolvedStyle, usage: StyleUsage) -> tuple[QcIssue, ...]:
        """Drift check of what a consumer drew (ERROR = locked rule broken, WARNING/INFO =
        preference deviation without/with a reason)."""
        ...


class UsageValidator:
    """The ``StyleValidator`` (pure; safe to construct anywhere)."""

    def validate(self, style: ResolvedStyle, usage: StyleUsage) -> tuple[QcIssue, ...]:
        return validate_usage(style, usage)


__all__ = [
    "ASPECT_PROFILE",
    "CURRENT_IDENTITY_VERSION",
    "DEFAULT_OUTPUT",
    "DEFAULT_PROFILE",
    "EMOJI_GLYPHS",
    "IDENTITIES",
    "MESC_IDENTITY_V1",
    "MESC_IDENTITY_V2",
    "PROFILES",
    "PROFILE_VERSION",
    "SCHEMA_VERSION",
    "STORY_FORMAT_PROFILE",
    "TYPE_ROLES",
    "ZONE_NAMES",
    "AffectedStyle",
    "AppliedOverride",
    "BrandAsset",
    "BrandAssetBinding",
    "BrandRule",
    "Branding",
    "CaptionLook",
    "Captions",
    "ChannelIdentity",
    "ColorToken",
    "ColorUse",
    "ContrastRules",
    "Easing",
    "ElementKind",
    "Emoji",
    "EndScreen",
    "FontFace",
    "FontFile",
    "FontFiles",
    "FontUnavailable",
    "FormatProfile",
    "Graphics",
    "InvalidStyle",
    "InvalidStyleConfig",
    "InvalidStyleDocument",
    "Motion",
    "MotionPhase",
    "MotionUse",
    "OutputFormat",
    "Override",
    "Palette",
    "PlacementUse",
    "QcCategory",
    "QcIssue",
    "ResolveStyleCommand",
    "ResolveStyleError",
    "ResolveStyleResult",
    "ResolvedPair",
    "ResolvedStyle",
    "ResolvedType",
    "RuleKind",
    "SectionOverride",
    "SectionStyle",
    "Semantic",
    "Severity",
    "StyleConfig",
    "StyleRef",
    "StyleRefMismatch",
    "StyleResolver",
    "StyleTokens",
    "StyleUsage",
    "StyleValidator",
    "Surface",
    "TextCase",
    "TextStyle",
    "TextUse",
    "Titles",
    "TokenRule",
    "Transitions",
    "TypeRole",
    "Typography",
    "UnknownIdentityVersion",
    "UnsupportedOutputFormat",
    "UsageValidator",
    "VisualStyleCatalog",
    "VisualStyleError",
    "Zone",
    "ZoneKind",
    "contrast_ratio",
    "knob_fields",
    "resolve_style",
    "validate_usage",
]
