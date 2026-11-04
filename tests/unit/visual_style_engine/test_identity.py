"""The MEsc identity itself: integrity, rule coverage, contrast, zones, profiles."""

from dataclasses import replace

import pytest

from media_house.modules.visual_style_engine.domain.color import contrast_ratio, hsl, rgb
from media_house.modules.visual_style_engine.domain.identity import (
    CURRENT_IDENTITY_VERSION,
    EMOJI_GLYPHS,
    IDENTITIES,
    IDENTITY_DIGESTS,
    MESC_IDENTITY_V1,
    MESC_IDENTITY_V2,
)
from media_house.modules.visual_style_engine.domain.model import ZoneSpec
from media_house.modules.visual_style_engine.domain.profile import (
    PROFILES,
    STORY_FORMAT_PROFILE,
    ZONE_NAMES,
    FormatProfile,
    Override,
    Palette,
    StyleTokens,
    Transitions,
    leaf_paths,
    present,
)
from media_house.modules.visual_style_engine.domain.validation import (
    identity_digest,
    profile_problems,
    validate_identity,
)
from media_house.modules.visual_style_engine.domain.values import (
    Confidence,
    RuleKind,
    Severity,
    ZoneKind,
)


@pytest.mark.parametrize("version", sorted(IDENTITIES))
def test_every_released_identity_has_no_problem(version: int) -> None:
    assert validate_identity(IDENTITIES[version]) == []


def test_every_released_version_is_pinned_by_its_digest() -> None:
    assert set(IDENTITY_DIGESTS) == set(IDENTITIES)
    for version, identity in IDENTITIES.items():
        assert identity.version == version
        assert identity_digest(identity) == IDENTITY_DIGESTS[version]
    assert max(IDENTITIES) == CURRENT_IDENTITY_VERSION


def test_an_identity_edited_in_place_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    from media_house.modules.visual_style_engine.domain import validation

    edited = replace(MESC_IDENTITY_V1, name="Somebody else")
    monkeypatch.setattr(validation, "IDENTITIES", {1: edited})

    issues = validate_identity(edited)

    assert [i.code for i in issues] == ["identity.digest_mismatch"]
    assert issues[0].severity is Severity.CRITICAL


def test_every_token_has_a_rule_with_evidence() -> None:
    from media_house.modules.visual_style_engine.domain.profile import leaf_paths

    for path in leaf_paths(MESC_IDENTITY_V1.tokens):
        rule = MESC_IDENTITY_V1.rule_for(path)
        assert rule is not None, path
        assert rule.source


def test_the_brand_palette_is_locked_and_measured() -> None:
    rule = MESC_IDENTITY_V1.rule_for("palette.brand_red")

    assert rule is not None and rule.rule is RuleKind.MANDATORY
    assert MESC_IDENTITY_V1.tokens.palette.brand_red == "#C4131A"
    assert rgb("#C4131A") == (196, 19, 26)
    assert hsl("#FFFFFF") == (0.0, 0.0, 1.0)


@pytest.mark.parametrize(
    ("fg", "bg", "expected"),
    [("#FDFEFE", "#000000", 20.78), ("#FDFEFE", "#C4131A", 6.02), ("#000000", "#F4DE19", 15.31)],
)
def test_contrast_ratios_are_computed_with_wcag(fg: str, bg: str, expected: float) -> None:
    assert contrast_ratio(fg, bg) == expected
    assert contrast_ratio(bg, fg) == expected


def test_a_failing_approved_combination_is_reported() -> None:
    from media_house.modules.visual_style_engine.domain.model import ColorPair

    broken = replace(
        MESC_IDENTITY_V1,
        combinations=(ColorPair(fg="accent_yellow", bg="white", use="text"),),
    )

    assert "identity.contrast" in {i.code for i in validate_identity(broken)}


@pytest.mark.parametrize("aspect", ["16:9", "9:16", "1:1"])
def test_every_format_has_the_same_named_zones(aspect: str) -> None:
    layout = MESC_IDENTITY_V1.layout_for(aspect)

    assert layout is not None
    names = {z.name for z in layout.zones if z.kind is not ZoneKind.KEEP_OUT}
    assert names == set(ZONE_NAMES)


def test_vertical_output_has_platform_keep_outs() -> None:
    layout = MESC_IDENTITY_V1.layout_for("9:16")

    assert layout is not None
    assert {z.name for z in layout.zones if z.kind is ZoneKind.KEEP_OUT} == {
        "platform_top",
        "platform_right",
        "platform_bottom",
    }


def test_overlapping_zones_are_refused() -> None:
    layout = MESC_IDENTITY_V1.layouts[0]
    clash = ZoneSpec(name="caption_band", kind=ZoneKind.CAPTION, x0=0.1, y0=0.7, x1=0.9, y1=0.95)
    zones = (*(z for z in layout.zones if z.name != "caption_band"), clash)
    broken = replace(MESC_IDENTITY_V1, layouts=(replace(layout, zones=zones),))

    messages = [i.message for i in validate_identity(broken) if i.code == "identity.layout_invalid"]

    assert any("overlap" in m for m in messages)


@pytest.mark.parametrize("version", sorted(IDENTITIES))
def test_every_profile_inherits_and_overrides_only_preferences(version: int) -> None:
    identity = IDENTITIES[version]
    for profile in PROFILES.values():
        assert profile_problems(identity, profile) == [], profile.name
        for override in profile.overrides:
            assert override.reason
            if not present(identity.tokens, override.path):
                continue  # a token group this version does not have (skipped when resolving)
            rule = identity.rule_for(override.path)
            assert rule is not None and rule.rule is RuleKind.PREFERENCE


def test_v2_adds_emoji_and_transitions_and_leaves_v1_untouched() -> None:
    assert MESC_IDENTITY_V1.tokens.emoji is None and MESC_IDENTITY_V1.tokens.transitions is None
    assert identity_digest(MESC_IDENTITY_V1) == IDENTITY_DIGESTS[1]  # v1 content unchanged
    assert "transitions" not in leaf_paths(MESC_IDENTITY_V1.tokens)
    v2 = MESC_IDENTITY_V2
    assert v2.tokens.emoji is not None and v2.tokens.transitions is not None
    face = next(f for f in v2.faces if f.key == v2.tokens.emoji.face)
    assert face.coverage == EMOJI_GLYPHS and face.license == "OFL-1.1"
    assert v2.rule_for("transitions.prohibited").rule is RuleKind.MANDATORY  # type: ignore[union-attr]
    assert v2.rule_for("transitions.approved").rule is RuleKind.PREFERENCE  # type: ignore[union-attr]
    assert CURRENT_IDENTITY_VERSION == 2


def test_transition_tokens_refuse_a_name_both_approved_and_prohibited() -> None:
    tokens = replace(
        MESC_IDENTITY_V2.tokens,
        transitions=Transitions(approved=("hard_cut", "glitch"), prohibited=("glitch",)),
    )

    assert any("both approved and prohibited" in p for p in tokens.problems())
    bad = replace(MESC_IDENTITY_V2.tokens, transitions=Transitions(approved=("teleport",)))
    assert any("transitions.approved" in p for p in bad.problems())


def test_a_profile_that_touches_a_locked_token_is_inconsistent() -> None:
    rogue = FormatProfile(
        name="rogue",
        description="x",
        native_aspect="16:9",
        evidence="none",
        confidence=Confidence.LOW,
        overrides=(Override("palette.brand_red", "#FF0000", "looks nicer"),),
    )

    assert any("locked" in p for p in profile_problems(MESC_IDENTITY_V1, rogue))


def test_story_formats_map_to_known_profiles() -> None:
    assert set(STORY_FORMAT_PROFILE.values()) <= set(PROFILES)
    for story in ("generic", "top_n", "review", "unboxing", "short", "talking_head"):
        assert story in STORY_FORMAT_PROFILE


def test_the_default_tokens_are_the_identity_tokens() -> None:
    assert StyleTokens() == MESC_IDENTITY_V1.tokens
    assert Palette().accent_yellow == "#F4DE19"
