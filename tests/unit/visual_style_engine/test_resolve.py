"""Resolution: precedence, locking, determinism, zones, no-speech fallback, round trip."""

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from media_house.modules.visual_style_engine.domain.errors import (
    InvalidStyleConfig,
    InvalidStyleDocument,
    UnsupportedOutputFormat,
)
from media_house.modules.visual_style_engine.domain.identity import MESC_IDENTITY_V1
from media_house.modules.visual_style_engine.domain.model import ResolvedStyle
from media_house.modules.visual_style_engine.domain.profile import (
    Override,
    SectionOverride,
    StyleConfig,
)
from media_house.modules.visual_style_engine.domain.resolve import (
    compare_identity,
    config_of,
    resolve_style,
)
from media_house.modules.visual_style_engine.domain.serialization import (
    style_from_json,
    style_to_json,
)
from media_house.modules.visual_style_engine.domain.values import OverrideLayer, ZoneKind

T0 = datetime(2026, 11, 2, 12, tzinfo=UTC)


def style(config: StyleConfig | None = None, **kwargs: object) -> ResolvedStyle:
    return resolve_style(MESC_IDENTITY_V1, config or StyleConfig(), created_at=T0, **kwargs)  # type: ignore[arg-type]


def test_the_identity_resolves_unchanged_without_profile_overrides() -> None:
    s = style(StyleConfig(profile="generic"), width=1920, height=1080)

    assert s.tokens == MESC_IDENTITY_V1.tokens
    assert s.overrides == ()
    assert (s.identity_id, s.identity_version, s.profile) == ("mesc", 1, "generic")
    assert s.hex("highlight") == "#F4DE19"
    assert s.hex("brand_red") == "#C4131A"


def test_precedence_identity_then_profile_then_project_then_section() -> None:
    config = StyleConfig(
        profile="ranking",
        overrides=(Override("motion.intensity_ceiling", 4, "finale episode"),),
        section_overrides=(
            SectionOverride("sec-1", (Override("motion.intensity_ceiling", 1, "calm intro"),)),
        ),
    )
    s = style(config, width=1920, height=1080)

    assert s.tokens.graphics.max_simultaneous == 3  # profile
    assert s.tokens.motion.intensity_ceiling == 4  # project beats profile
    assert s.tokens_for("sec-1").motion.intensity_ceiling == 1  # section beats project
    assert s.tokens_for("sec-1").graphics.max_simultaneous == 3  # untouched paths inherit
    assert s.tokens_for("unknown") is s.tokens
    layers = [(o.layer, o.path, o.previous_json, o.value_json) for o in s.overrides]
    assert layers == [
        (OverrideLayer.FORMAT_PROFILE, "motion.intensity_ceiling", "2", "3"),
        (OverrideLayer.FORMAT_PROFILE, "graphics.max_simultaneous", "2", "3"),
        (OverrideLayer.PROJECT, "motion.intensity_ceiling", "3", "4"),
        (OverrideLayer.SECTION, "motion.intensity_ceiling", "4", "1"),
    ]


def test_a_locked_token_cannot_be_overridden() -> None:
    for path, value in (
        ("palette.brand_red", "#FF0000"),
        ("typography.caption.face", "poppins_700"),
    ):
        with pytest.raises(InvalidStyleConfig, match="locked"):
            style(StyleConfig(overrides=(Override(path, value, "the client wants it"),)))


def test_an_override_needs_a_reason_and_a_known_path() -> None:
    with pytest.raises(InvalidStyleConfig, match="reason"):
        style(StyleConfig(overrides=(Override("motion.stagger", 0.1, " "),)))
    with pytest.raises(InvalidStyleConfig, match="unknown token"):
        style(StyleConfig(overrides=(Override("motion.wobble", 1, "why not"),)))
    with pytest.raises(InvalidStyleConfig, match="twice"):
        style(
            StyleConfig(
                overrides=(
                    Override("motion.stagger", 0.1, "a"),
                    Override("motion.stagger", 0.2, "b"),
                )
            )
        )


def test_an_override_that_makes_text_illegible_is_refused() -> None:
    bad = Override("titles.price_tag.fill", "accent_yellow", "match the tag")

    with pytest.raises(InvalidStyleConfig, match="contrast"):
        style(StyleConfig(overrides=(bad,)))


def test_an_override_that_moves_captions_out_of_the_caption_band_is_refused() -> None:
    bad = Override("captions.word_group.zone", "callout_left", "try something")

    with pytest.raises(InvalidStyleConfig, match="zone"):
        style(StyleConfig(overrides=(bad,)))


def test_resolution_is_deterministic_and_the_fingerprint_tracks_the_look() -> None:
    a = style(StyleConfig(profile="ranking"), width=1920, height=1080, project_id="p1")
    b = resolve_style(
        MESC_IDENTITY_V1,
        StyleConfig(profile="ranking"),
        width=1920,
        height=1080,
        created_at=datetime(2030, 1, 1, tzinfo=UTC),
        project_id="p2",
        style_version=7,
    )
    louder = style(
        StyleConfig(profile="ranking", overrides=(Override("motion.stagger", 0.1, "x"),)),
        width=1920,
        height=1080,
    )

    assert a.fingerprint == b.fingerprint  # bookkeeping never changes the look
    assert len(a.fingerprint) == 64
    assert louder.fingerprint != a.fingerprint
    assert style(width=3840, height=2160).fingerprint != style(width=1920, height=1080).fingerprint


@pytest.mark.parametrize(
    ("width", "height", "caption"),
    [
        (1920, 1080, (192, 886, 1536, 140)),
        (1080, 1920, (54, 1114, 853, 249)),
        (1080, 1080, (86, 821, 908, 194)),
    ],
)
def test_zones_are_resolved_in_output_pixels(
    width: int, height: int, caption: tuple[int, int, int, int]
) -> None:
    s = style(width=width, height=height)
    zone = s.zone("caption_band")

    assert (zone.left, zone.top, zone.width, zone.height) == caption
    assert zone.kind is ZoneKind.CAPTION
    assert "card_safe" not in {z.name for z in s.safe_areas()}


def test_sizes_scale_with_the_short_side() -> None:
    hd, uhd = style(width=1920, height=1080), style(width=3840, height=2160)
    vertical = style(width=1080, height=1920)

    assert hd.type_style("caption").size_px == 72
    assert uhd.type_style("caption").size_px == 144
    assert vertical.type_style("caption").size_px == 72
    assert uhd.px(hd.tokens.graphics.sticker_glow) == 16


def test_no_speech_path_needs_only_an_output_or_a_profile() -> None:
    vertical = style(width=1080, height=1920)  # no profile, no story format, no project
    native = style(StyleConfig(profile="shorts_versus"))  # no size

    assert vertical.profile == "shorts_meme"
    assert vertical.project_id is None
    assert vertical.tokens.captions.default_look == "line"
    assert style().profile == "generic"
    assert (style().output.width, style().output.height) == (1920, 1080)
    assert (native.output.width, native.output.height) == (1080, 1920)
    assert style(width=1920, height=1080, story_format="top_n").profile == "ranking"


def test_an_unknown_profile_or_aspect_is_refused() -> None:
    with pytest.raises(InvalidStyleConfig):
        style(StyleConfig(profile="cinematic"))
    with pytest.raises(UnsupportedOutputFormat):
        style(width=2560, height=1080)
    with pytest.raises(InvalidStyleConfig):
        style(width=1920)


def test_brand_assets_bind_slots_and_missing_ones_are_reported() -> None:
    s = style(brand_assets={"fire_plate": ("asset-1", "abc")})

    assert s.missing_brand_assets
    assert "fire_plate" not in {a.slot for a in s.missing_brand_assets}
    missing = {i.item_id: i for i in s.checks if i.code == "brand.asset_missing"}
    assert "logo" in missing and "wolf_mascot" in missing
    with pytest.raises(InvalidStyleConfig):
        style(brand_assets={"banner": ("asset-2", "def")})


def test_a_german_price_uses_a_decimal_comma() -> None:
    s = style()

    assert s.format_price(1599) == "15,99 €"
    assert s.format_price(129900) == "1.299,00 €"
    eur = style(
        StyleConfig(overrides=(Override("graphics.price_format", "comma_eur", "2021 look"),))
    )
    assert eur.format_price(1599) == "15,99 EUR"


def test_mandatory_rules_and_preferences_are_separated() -> None:
    s = style()

    assert "sponsor.label" in {r.code for r in s.mandatory_rules}
    assert "rank.card" in {r.code for r in s.preferences}
    assert "palette" in s.locked_tokens


def test_a_stored_style_round_trips_exactly() -> None:
    s = style(
        StyleConfig(
            profile="ranking",
            sponsored=True,
            section_overrides=(SectionOverride("s", (Override("motion.stagger", 0.1, "x"),)),),
        ),
        width=1080,
        height=1920,
        project_id="p",
    )

    assert style_from_json(style_to_json(s)) == s
    with pytest.raises(InvalidStyleDocument):
        style_from_json(style_to_json(s).replace('"schema_version": 1', '"schema_version": 99'))


def test_the_request_is_recovered_and_the_same_identity_affects_nothing() -> None:
    s = style(
        StyleConfig(profile="ranking", overrides=(Override("motion.stagger", 0.1, "x"),)),
        width=1920,
        height=1080,
    )

    assert config_of(s).overrides == (Override("motion.stagger", 0.1, "x"),)
    same = compare_identity(s, MESC_IDENTITY_V1, "doc-1")
    assert (same.affected, same.new_fingerprint) == (False, s.fingerprint)


def test_a_new_identity_version_that_changes_the_look_is_detected() -> None:
    s = style(width=1920, height=1080)
    tokens = replace(
        MESC_IDENTITY_V1.tokens,
        palette=replace(MESC_IDENTITY_V1.tokens.palette, brand_red="#C0101A"),
    )
    v2 = replace(MESC_IDENTITY_V1, version=2, tokens=tokens)

    found = compare_identity(s, v2, "doc-1")

    assert found.affected and found.new_fingerprint != s.fingerprint
    assert found.document_id == "doc-1"


def test_an_outline_heavier_than_the_face_allows_is_refused() -> None:
    heavy = Override("captions.word_group.stroke_width", 7.0, "bolder look")

    with pytest.raises(InvalidStyleConfig, match="closes letter gaps"):
        style(StyleConfig(overrides=(heavy,)))
