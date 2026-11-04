"""QC of the identity itself and of a resolved token tree (not of a consumer's usage).

``validate_identity`` is run on every resolve: a CRITICAL finding means the identity is broken
and nothing is stored. ``token_checks`` runs on resolved tokens too, so an override that makes
text illegible or moves a caption into a graphic zone is refused, never stored.
"""

from dataclasses import fields

from media_house.modules.visual_style_engine.domain.color import contrast_ratio
from media_house.modules.visual_style_engine.domain.errors import InvalidStyleConfig
from media_house.modules.visual_style_engine.domain.identity import IDENTITIES, IDENTITY_DIGESTS
from media_house.modules.visual_style_engine.domain.model import ChannelIdentity, ZoneSpec
from media_house.modules.visual_style_engine.domain.profile import (
    PROFILES,
    TYPE_ROLES,
    ZONE_NAMES,
    CaptionLook,
    FormatProfile,
    StyleTokens,
    TextStyle,
    leaf_paths,
    present,
    value_at,
    with_value,
)
from media_house.modules.visual_style_engine.domain.serialization import digest
from media_house.modules.visual_style_engine.domain.values import (
    ElementKind,
    QcCategory,
    QcIssue,
    RuleKind,
    Severity,
    ZoneKind,
)

#: What each title style draws (its zone must accept that kind in every layout).
TITLE_ELEMENTS = {
    "opening_title": ElementKind.TITLE,
    "chapter_title": ElementKind.RANK_CARD,
    "lower_third": ElementKind.LOWER_THIRD,
    "callout": ElementKind.CALLOUT,
    "price_tag": ElementKind.PRICE_TAG,
    "spec_card": ElementKind.FULL_SCREEN,
    "cta": ElementKind.CTA,
    "sponsor_label": ElementKind.SPONSOR_LABEL,
    "credit": ElementKind.CREDIT,
    "end_screen_label": ElementKind.FULL_SCREEN,
}


def identity_digest(identity: ChannelIdentity) -> str:
    return digest(identity)


def _critical(code: str, message: str, item_id: str | None = None) -> QcIssue:
    return QcIssue(
        code=code,
        severity=Severity.CRITICAL,
        category=QcCategory.TECHNICAL,
        message=message,
        item_id=item_id,
    )


def validate_identity(identity: ChannelIdentity) -> list[QcIssue]:
    """Every problem of one identity version (CRITICAL: the identity must not be used)."""
    issues: list[QcIssue] = []
    recorded = IDENTITY_DIGESTS.get(identity.version)
    if IDENTITIES.get(identity.version) is identity and recorded != identity_digest(identity):
        issues.append(
            _critical(
                "identity.digest_mismatch",
                f"released identity {identity.id} v{identity.version} was edited in place;"
                " add a new version instead",
            )
        )
    tokens = identity.tokens
    issues += [_critical("identity.token_invalid", p) for p in tokens.problems()]
    paths = leaf_paths(tokens)
    issues += [
        _critical("identity.rule_missing", f"token {p} has no rule", p)
        for p in paths
        if identity.rule_for(p) is None
    ]
    for rule in identity.token_rules:
        if not any(p == rule.path or p.startswith(f"{rule.path}.") for p in paths):
            issues.append(
                _critical("identity.rule_unknown_path", f"rule {rule.path} matches no token")
            )
    keys = {f.key for f in identity.faces}
    for face in identity.faces:
        if face.fallback is not None and face.fallback not in keys:
            issues.append(_critical("identity.font_unknown", f"fallback {face.fallback} unknown"))
        if len(face.sha256) != 64:
            issues.append(_critical("identity.font_unpinned", f"{face.key} has no sha256"))
    issues += [_critical(c, m) for c, m in _reference_problems(tokens, identity)]
    for layout in identity.layouts:
        issues += [
            _critical("identity.layout_invalid", f"{layout.aspect}: {m}")
            for m in _layout_problems(layout.zones)
        ]
    for pair in identity.combinations:
        ratio = contrast_ratio(getattr(tokens.palette, pair.fg), getattr(tokens.palette, pair.bg))
        need = (
            identity.contrast.min_large_text
            if pair.use == "large_text"
            else identity.contrast.min_text
        )
        if ratio < need:
            issues.append(
                _critical(
                    "identity.contrast",
                    f"{pair.fg} on {pair.bg}: {ratio} < {need}",
                    f"{pair.fg}/{pair.bg}",
                )
            )
    for profile in PROFILES.values():
        issues += [
            _critical("identity.profile_invalid", f"{profile.name}: {m}", profile.name)
            for m in profile_problems(identity, profile)
        ]
    return issues


def profile_problems(identity: ChannelIdentity, profile: FormatProfile) -> list[str]:
    found: list[str] = []
    if identity.layout_for(profile.native_aspect) is None:
        found.append(f"no layout for its native aspect {profile.native_aspect}")
    tokens = identity.tokens
    for override in profile.overrides:
        if not present(tokens, override.path):
            continue  # a token group this identity version does not have
        try:
            tokens = apply_override(
                identity, tokens, override.path, override.value, override.reason
            )
        except InvalidStyleConfig as exc:
            found.append(exc.user_message)
    found += tokens.problems()
    found += [m for _, m in _reference_problems(tokens, identity)]
    return found


def apply_override(
    identity: ChannelIdentity, tokens: StyleTokens, path: str, value: object, reason: str
) -> StyleTokens:
    """One override with the locking and reason rules (any layer). Raises ``InvalidStyleConfig``."""
    if not reason.strip():
        raise InvalidStyleConfig(f"the override of {path} must name a reason")
    value_at(tokens, path)  # unknown path -> InvalidStyleConfig
    rule = identity.rule_for(path)
    if rule is None or rule.rule is RuleKind.MANDATORY:
        raise InvalidStyleConfig(
            f"{path} is a locked brand token; change it with a new identity version"
        )
    return with_value(tokens, path, value)  # type: ignore[arg-type]


def token_checks(tokens: StyleTokens, identity: ChannelIdentity) -> list[str]:
    """Problems of a token tree that ranges alone cannot see (legibility, references, zones)."""
    return tokens.problems() + [m for _, m in _reference_problems(tokens, identity)]


def _reference_problems(tokens: StyleTokens, identity: ChannelIdentity) -> list[tuple[str, str]]:
    found: list[tuple[str, str]] = []
    faces = {f.key for f in identity.faces}
    easings = {e.name for e in identity.easings}
    for role in TYPE_ROLES:
        face = getattr(tokens.typography, role).face
        if face not in faces:
            found.append(("identity.font_unknown", f"typography.{role}: face {face} not bundled"))
    if tokens.emoji is not None and tokens.emoji.face not in faces:
        found.append(("identity.font_unknown", f"emoji.face: face {tokens.emoji.face} not bundled"))
    for name in ("entry_easing", "exit_easing", "move_easing"):
        if getattr(tokens.motion, name) not in easings:
            found.append(("identity.easing_unknown", f"motion.{name} is not a defined easing"))
    styles: list[tuple[str, TextStyle | CaptionLook, ElementKind]] = [
        (f"titles.{f.name}", getattr(tokens.titles, f.name), TITLE_ELEMENTS[f.name])
        for f in fields(tokens.titles)
    ]
    styles += [
        ("captions.word_group", tokens.captions.word_group, ElementKind.CAPTION),
        ("captions.line", tokens.captions.line, ElementKind.CAPTION),
    ]
    by_key = {f.key: f for f in identity.faces}
    strokes = [(path, style.size, style.stroke_width, style.role) for path, style, _ in styles]
    strokes.append(("graphics.rank_stroke", tokens.titles.chapter_title.size,
                    tokens.graphics.rank_stroke, tokens.titles.chapter_title.role))  # fmt: skip
    for path, size, stroke, role in strokes:
        face = by_key.get(getattr(tokens.typography, role).face)
        if face is not None and stroke > face.max_stroke_ratio * size:
            found.append((
                "identity.stroke_too_heavy",
                f"{path}: a {stroke} px outline on {size} px {face.family} closes letter gaps"
                f" (max {face.max_stroke_ratio:.0%} of the size)",
            ))  # fmt: skip
    for path, style, kind in styles:
        found += [
            ("identity.text_contrast", f"{path}: {m}") for m in _legibility(tokens, identity, style)
        ]
        for layout in identity.layouts:
            zone = next((z for z in layout.zones if z.name == style.zone), None)
            if zone is None or kind not in zone.accepts:
                found.append(
                    (
                        "identity.zone_mismatch",
                        f"{path}: zone {style.zone} does not take {kind} in {layout.aspect}",
                    )
                )
    if tokens.captions.word_group.zone != "caption_band":
        found.append(("identity.zone_mismatch", "speech captions must use caption_band"))
    return found


def _legibility(
    tokens: StyleTokens, identity: ChannelIdentity, style: TextStyle | CaptionLook
) -> list[str]:
    """Text over footage needs a panel or an outline, and enough contrast against it."""
    palette = tokens.palette
    panel = style.panel if isinstance(style, TextStyle) and style.background == "panel" else None
    if panel is None and style.stroke_width <= 0:
        return ["text over footage needs an outline or a panel"]
    against = panel or style.stroke
    large = style.size >= identity.contrast.large_text_size
    need = identity.contrast.min_large_text if large else identity.contrast.min_text
    found = []
    for colour in {style.fill, *([style.highlight] if isinstance(style, CaptionLook) else [])}:
        ratio = contrast_ratio(getattr(palette, colour), getattr(palette, against))
        if ratio < need:
            found.append(f"{colour} on {against} has contrast {ratio} < {need}")
    return found


def _layout_problems(zones: tuple[ZoneSpec, ...]) -> list[str]:
    found: list[str] = []
    names = [z.name for z in zones if z.kind is not ZoneKind.KEEP_OUT]
    if sorted(names) != sorted(ZONE_NAMES):
        found.append(f"zones {sorted(names)} differ from {sorted(ZONE_NAMES)}")
    for z in zones:
        if not (0 <= z.x0 < z.x1 <= 1 and 0 <= z.y0 < z.y1 <= 1):
            found.append(f"zone {z.name} is not a rectangle inside the frame")
    for i, a in enumerate(zones):
        for b in zones[i + 1 :]:
            if "card_safe" in (a.name, b.name) and ZoneKind.KEEP_OUT not in (a.kind, b.kind):
                continue  # full-screen cards replace the picture; only keep-outs bind them
            if _overlap(a, b):
                found.append(f"zones {a.name} and {b.name} overlap")
    return found


def _overlap(a: ZoneSpec, b: ZoneSpec) -> bool:
    return a.x0 < b.x1 and b.x0 < a.x1 and a.y0 < b.y1 and b.y0 < a.y1
