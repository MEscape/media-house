"""Identity -> format profile -> project overrides -> section overrides = ``ResolvedStyle``.

Deterministic: the same identity version, profile, overrides, output size and brand-asset
bindings always give the same tokens and the same ``fingerprint``. Each step changes only the
paths it names; a locked (mandatory) token is never changed by any step.
"""

import json
from collections.abc import Mapping, Sequence
from dataclasses import replace
from datetime import datetime

from media_house.modules.visual_style_engine.domain.color import contrast_ratio
from media_house.modules.visual_style_engine.domain.errors import (
    InvalidStyleConfig,
    UnsupportedOutputFormat,
    VisualStyleError,
)
from media_house.modules.visual_style_engine.domain.model import (
    AffectedStyle,
    AppliedOverride,
    BrandAsset,
    ChannelIdentity,
    OutputFormat,
    ResolvedPair,
    ResolvedStyle,
    ResolvedType,
    SectionStyle,
    Zone,
)
from media_house.modules.visual_style_engine.domain.profile import (
    DEFAULT_OUTPUT,
    PROFILES,
    TYPE_ROLES,
    Override,
    SectionOverride,
    StyleConfig,
    StyleTokens,
    present,
    value_at,
)
from media_house.modules.visual_style_engine.domain.serialization import canonical, digest
from media_house.modules.visual_style_engine.domain.validation import (
    apply_override,
    token_checks,
    validate_identity,
)
from media_house.modules.visual_style_engine.domain.values import (
    SCHEMA_VERSION,
    OverrideLayer,
    QcCategory,
    QcIssue,
    Severity,
)

#: Fields of ``ResolvedStyle`` that are bookkeeping, not look: they never change the fingerprint.
_NOT_VISUAL = frozenset(
    {
        "schema_version",
        "style_version",
        "supersedes",
        "project_id",
        "anchor_asset_id",
        "created_at",
        "identity_id",
        "identity_version",
        "profile",
        "profile_version",
        "overrides",
        "checks",
        "fingerprint",
    }
)


def resolve_style(
    identity: ChannelIdentity,
    config: StyleConfig,
    *,
    created_at: datetime,
    width: int | None = None,
    height: int | None = None,
    story_format: str | None = None,
    project_id: str | None = None,
    anchor_asset_id: str = "",
    brand_assets: Mapping[str, tuple[str, str]] | None = None,
    style_version: int = 1,
    supersedes: str | None = None,
    extra_checks: Sequence[QcIssue] = (),
) -> ResolvedStyle:
    """The style for one project and output. ``brand_assets`` binds slot -> (asset id, checksum).

    Raises ``InvalidStyleConfig`` (unknown profile/token, missing reason, locked token, a value
    that breaks a range or legibility) and ``UnsupportedOutputFormat``. Identity problems are
    returned as CRITICAL ``checks`` (the caller refuses to store such a style)."""
    if (width is None) != (height is None):
        raise InvalidStyleConfig("give both output width and height, or neither")
    if width is None or height is None:  # no size: the profile's native aspect at 1080
        profile = PROFILES[config.profile_name(story_format)]
        width, height = DEFAULT_OUTPUT[profile.native_aspect]
    if width <= 0 or height <= 0:
        raise InvalidStyleConfig(f"output size {width}x{height} is not a picture")
    output = OutputFormat(width=width, height=height)
    layout = identity.layout_for(output.aspect)
    if layout is None:
        known = ", ".join(layout.aspect for layout in identity.layouts)
        raise UnsupportedOutputFormat(width, height, known)
    profile = PROFILES[config.profile_name(story_format, output.aspect)]

    applied: list[AppliedOverride] = []
    # a format profile may name token groups a newer identity added: skipped for older versions
    format_overrides = [o for o in profile.overrides if present(identity.tokens, o.path)]
    tokens = _apply(
        identity, identity.tokens, format_overrides, OverrideLayer.FORMAT_PROFILE, None, applied
    )
    tokens = _apply(identity, tokens, config.overrides, OverrideLayer.PROJECT, None, applied)
    _refuse(token_checks(tokens, identity))
    sections: list[SectionStyle] = []
    for section in config.section_overrides:
        if any(s.section_id == section.section_id for s in sections):
            raise InvalidStyleConfig(f"section {section.section_id!r} is overridden twice")
        own: list[AppliedOverride] = []
        section_tokens = _apply(
            identity, tokens, section.overrides, OverrideLayer.SECTION, section.section_id, own
        )
        _refuse(token_checks(section_tokens, identity))
        sections.append(
            SectionStyle(section_id=section.section_id, overrides=tuple(own), tokens=section_tokens)
        )
        applied += own

    bound = dict(brand_assets or {})
    unknown = set(bound) - {slot.name for slot in identity.brand_assets}
    if unknown:
        raise InvalidStyleConfig(f"unknown brand asset slots {sorted(unknown)}")
    assets = tuple(
        BrandAsset(
            slot=slot.name,
            purpose=slot.purpose,
            required=slot.required,
            asset_id=bound[slot.name][0] if slot.name in bound else None,
            checksum=bound[slot.name][1] if slot.name in bound else None,
        )
        for slot in identity.brand_assets
    )
    faces = {f.key: f for f in identity.faces}
    short = output.short_side
    types = tuple(
        ResolvedType(
            role=role,
            face=faces[getattr(tokens.typography, role).face],
            size_px=round(getattr(tokens.typography, role).size * short / 1080),
            tracking_em=getattr(tokens.typography, role).tracking,
            leading=getattr(tokens.typography, role).leading,
            case=getattr(tokens.typography, role).case,
        )
        for role in TYPE_ROLES
    )
    zones = tuple(
        Zone(
            name=z.name,
            kind=z.kind,
            accepts=z.accepts,
            x0=z.x0,
            y0=z.y0,
            x1=z.x1,
            y1=z.y1,
            left=round(z.x0 * width),
            top=round(z.y0 * height),
            width=round(z.x1 * width) - round(z.x0 * width),
            height=round(z.y1 * height) - round(z.y0 * height),
        )
        for z in layout.zones
    )
    pairs = tuple(
        ResolvedPair(
            fg=p.fg,
            bg=p.bg,
            use=p.use,
            ratio=contrast_ratio(getattr(tokens.palette, p.fg), getattr(tokens.palette, p.bg)),
            required=(
                identity.contrast.min_large_text
                if p.use == "large_text"
                else identity.contrast.min_text
            ),
        )
        for p in identity.combinations
    )
    checks = [*validate_identity(identity), *extra_checks]
    checks += [
        QcIssue(
            code="brand.asset_missing",
            severity=Severity.WARNING if a.required else Severity.INFO,
            category=QcCategory.TECHNICAL,
            message=f"brand asset {a.slot!r} ({a.purpose}) is not in the Media Library yet",
            item_id=a.slot,
        )
        for a in assets
        if a.missing
    ]
    if profile.native_aspect != output.aspect:
        checks.append(
            QcIssue(
                code="profile.aspect_differs",
                severity=Severity.INFO,
                category=QcCategory.CREATIVE,
                message=f"profile {profile.name} is made for {profile.native_aspect},"
                f" the output is {output.aspect}",
            )
        )
    style = ResolvedStyle(
        schema_version=SCHEMA_VERSION,
        style_version=style_version,
        supersedes=supersedes,
        project_id=project_id,
        anchor_asset_id=anchor_asset_id,
        created_at=created_at,
        identity_id=identity.id,
        identity_version=identity.version,
        profile=profile.name,
        profile_version=profile.version,
        output=output,
        sponsored=config.sponsored,
        tokens=tokens,
        faces=identity.faces,
        types=types,
        easings=identity.easings,
        zones=zones,
        combinations=pairs,
        contrast=identity.contrast,
        thumbnail_only=identity.thumbnail_only,
        brand_assets=assets,
        token_rules=identity.token_rules,
        brand_rules=identity.brand_rules,
        prohibited=identity.prohibited,
        overrides=tuple(applied),
        sections=tuple(sections),
        checks=tuple(checks),
        fingerprint="",
    )
    return _with_fingerprint(style)


def style_fingerprint(style: ResolvedStyle) -> str:
    """sha256 of the canonical visual content (bookkeeping fields excluded)."""
    content = json.loads(canonical(style))
    return digest({k: v for k, v in content.items() if k not in _NOT_VISUAL})


def _with_fingerprint(style: ResolvedStyle) -> ResolvedStyle:
    return replace(style, fingerprint=style_fingerprint(style))


def _apply(
    identity: ChannelIdentity,
    tokens: StyleTokens,
    overrides: Sequence[Override],
    layer: OverrideLayer,
    section_id: str | None,
    applied: list[AppliedOverride],
) -> StyleTokens:
    seen: set[str] = set()
    for override in overrides:
        if override.path in seen:
            raise InvalidStyleConfig(f"{override.path} is overridden twice in one {layer}")
        seen.add(override.path)
        previous = value_at(tokens, override.path)
        tokens = apply_override(identity, tokens, override.path, override.value, override.reason)
        applied.append(
            AppliedOverride(
                layer=layer,
                path=override.path,
                value_json=canonical(value_at(tokens, override.path)),
                previous_json=canonical(previous),
                reason=override.reason,
                section_id=section_id,
            )
        )
    return tokens


def _refuse(problems: list[str]) -> None:
    if problems:
        raise InvalidStyleConfig("; ".join(problems[:5]))


def config_of(style: ResolvedStyle) -> StyleConfig:
    """The request a stored style was resolved from (profile, project and section overrides)."""
    project = tuple(
        Override(o.path, json.loads(o.value_json), o.reason)
        for o in style.overrides
        if o.layer is OverrideLayer.PROJECT
    )
    sections = tuple(
        SectionOverride(
            s.section_id,
            tuple(Override(o.path, json.loads(o.value_json), o.reason) for o in s.overrides),
        )
        for s in style.sections
    )
    return StyleConfig(
        profile=style.profile,
        overrides=project,
        section_overrides=sections,
        sponsored=style.sponsored,
    )


def compare_identity(
    style: ResolvedStyle, identity: ChannelIdentity, document_id: str
) -> AffectedStyle:
    """Would the stored ``style`` look different under ``identity``? (Same request and output.)"""
    try:
        again = resolve_style(
            identity,
            config_of(style),
            created_at=style.created_at,
            width=style.output.width,
            height=style.output.height,
            brand_assets={
                a.slot: (a.asset_id, a.checksum or "")
                for a in style.brand_assets
                if a.asset_id is not None
            },
        )
        new: str | None = again.fingerprint
        detail = "the look changes" if new != style.fingerprint else "identical look"
    except VisualStyleError as exc:
        new, detail = None, exc.user_message
    return AffectedStyle(
        document_id=document_id,
        anchor_asset_id=style.anchor_asset_id,
        project_id=style.project_id,
        profile=style.profile,
        identity_version=style.identity_version,
        fingerprint=style.fingerprint,
        new_fingerprint=new,
        affected=new != style.fingerprint,
        detail=detail,
    )
