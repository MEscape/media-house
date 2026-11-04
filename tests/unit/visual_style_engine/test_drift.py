"""Drift validation: what a consumer drew, judged against the resolved style."""

from dataclasses import replace
from datetime import UTC, datetime

from media_house.core.domain import Rational
from media_house.modules.visual_style_engine.domain.drift import (
    ColorUse,
    MotionUse,
    PlacementUse,
    StyleUsage,
    TextUse,
    validate_usage,
)
from media_house.modules.visual_style_engine.domain.identity import (
    MESC_IDENTITY_V1,
    MESC_IDENTITY_V2,
)
from media_house.modules.visual_style_engine.domain.model import ResolvedStyle
from media_house.modules.visual_style_engine.domain.profile import StyleConfig
from media_house.modules.visual_style_engine.domain.resolve import resolve_style
from media_house.modules.visual_style_engine.domain.values import (
    ElementKind,
    MotionPhase,
    QcCategory,
    QcIssue,
    Severity,
    Surface,
    TextCase,
)

STYLE = resolve_style(
    MESC_IDENTITY_V1,
    StyleConfig(profile="shorts_haul"),
    created_at=datetime(2026, 11, 2, tzinfo=UTC),
    width=1080,
    height=1920,
)


def usage(style: ResolvedStyle = STYLE, **items: object) -> StyleUsage:
    return StyleUsage(
        module="caption_engine",
        fingerprint=style.fingerprint,
        width=style.output.width,
        height=style.output.height,
        **items,  # type: ignore[arg-type]
    )


def caption_text(**changes: object) -> TextUse:
    base = TextUse(
        element_id="cap-1",
        role="caption",
        family="Anton",
        weight=400,
        size_px=84,
        fill_hex="#FDFEFE",
        stroke_hex="#000000",
        stroke_px=3,
        case=TextCase.UPPER,
        text="NUR 15,99 €",
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def codes(issues: tuple[QcIssue, ...]) -> list[tuple[str, Severity]]:
    return [(i.code, i.severity) for i in issues]


def test_a_conforming_caption_has_no_finding() -> None:
    band = STYLE.zone("caption_band")
    issues = validate_usage(
        STYLE,
        usage(
            texts=(caption_text(),),
            colors=(ColorUse(element_id="cap-1", hex="#f4de19", token="highlight"),),
            placements=(
                PlacementUse(
                    element_id="cap-1",
                    kind=ElementKind.CAPTION,
                    zone="caption_band",
                    left=band.left + 10,
                    top=band.top,
                    width=band.width - 20,
                    height=band.height,
                ),
            ),
            motions=(
                MotionUse(
                    element_id="cap-1",
                    phase=MotionPhase.ENTRY,
                    easing="pop",
                    duration=Rational(3, 10),
                    intensity=2,
                ),
            ),
        ),
    )

    assert issues == ()


def test_a_stale_style_or_another_output_size_is_an_error() -> None:
    stale = replace(usage(), fingerprint="0" * 64, width=1920, height=1080)

    assert codes(validate_usage(STYLE, stale)) == [
        ("style.stale", Severity.ERROR),
        ("style.output_mismatch", Severity.ERROR),
    ]


def test_an_off_palette_or_thumbnail_colour_breaks_a_mandatory_rule() -> None:
    issues = validate_usage(
        STYLE,
        usage(
            colors=(
                ColorUse(element_id="a", hex="#123456"),
                ColorUse(element_id="b", hex="#245FA7"),
                ColorUse(element_id="c", hex="#245FA7", surface=Surface.THUMBNAIL),
            )
        ),
    )

    assert codes(issues) == [
        ("color.off_palette", Severity.ERROR),
        ("color.thumbnail_only", Severity.ERROR),
    ]
    assert all(i.category is QcCategory.TECHNICAL for i in issues)


def test_a_semantic_colour_mismatch_is_a_creative_warning() -> None:
    issues = validate_usage(
        STYLE, usage(colors=(ColorUse(element_id="a", hex="#D5A50A", token="highlight"),))
    )

    assert codes(issues) == [("color.token_mismatch", Severity.WARNING)]
    assert issues[0].category is QcCategory.CREATIVE


def test_a_wrong_font_is_an_error_and_a_reported_fallback_is_info() -> None:
    issues = validate_usage(
        STYLE,
        usage(
            texts=(
                caption_text(element_id="x", family="Arial", weight=900),
                caption_text(element_id="y", family="Poppins", weight=800),
            )
        ),
    )

    assert codes(issues) == [("font.mismatch", Severity.ERROR), ("font.substituted", Severity.INFO)]


def test_small_text_no_outline_low_contrast_and_dot_prices_are_errors() -> None:
    issues = validate_usage(
        STYLE,
        usage(
            texts=(
                caption_text(element_id="small", size_px=20, stroke_px=1),
                caption_text(element_id="bare", stroke_hex=None, stroke_px=0),
                caption_text(element_id="pale", fill_hex="#ECECEC", stroke_hex="#FDFEFE"),
                caption_text(element_id="price", text="NUR 15.99 €"),
            )
        ),
    )

    assert codes(issues) == [
        ("text.too_small", Severity.ERROR),
        ("text.no_outline", Severity.ERROR),
        ("text.contrast", Severity.ERROR),
        ("price.format", Severity.ERROR),
    ]


def test_a_case_mismatch_on_a_locked_case_is_an_error_even_with_a_reason() -> None:
    issues = validate_usage(
        STYLE, usage(texts=(caption_text(case=TextCase.SENTENCE, reason="softer"),))
    )

    assert codes(issues) == [("text.case", Severity.ERROR)]
    assert "locked" in issues[0].message


def test_placements_must_stay_in_a_zone_that_takes_them() -> None:
    band, ui = STYLE.zone("caption_band"), STYLE.zone("platform_right")
    issues = validate_usage(
        STYLE,
        usage(
            placements=(
                PlacementUse(
                    element_id="a",
                    kind=ElementKind.LOWER_THIRD,
                    zone="caption_band",
                    left=band.left,
                    top=band.top,
                    width=band.width,
                    height=band.height,
                ),
                PlacementUse(
                    element_id="b",
                    kind=ElementKind.CAPTION,
                    zone="caption_band",
                    left=band.left,
                    top=band.top - 50,
                    width=band.width,
                    height=band.height,
                ),
                PlacementUse(
                    element_id="c",
                    kind=ElementKind.CALLOUT,
                    zone="platform_right",
                    left=ui.left,
                    top=ui.top,
                    width=ui.width,
                    height=ui.height,
                ),
                PlacementUse(
                    element_id="d",
                    kind=ElementKind.CALLOUT,
                    zone="nowhere",
                    left=0,
                    top=0,
                    width=1,
                    height=1,
                ),
            )
        ),
    )

    assert codes(issues) == [
        ("zone.wrong_kind", Severity.ERROR),
        ("zone.outside", Severity.ERROR),
        ("zone.keep_out", Severity.ERROR),
        ("zone.unknown", Severity.ERROR),
    ]


def test_a_caption_reaching_into_the_platform_ui_is_flagged() -> None:
    band = STYLE.zone("caption_band")
    issues = validate_usage(
        STYLE,
        usage(
            placements=(
                PlacementUse(
                    element_id="a",
                    kind=ElementKind.CAPTION,
                    zone="caption_band",
                    left=band.left,
                    top=band.top,
                    width=band.width + 100,
                    height=band.height,
                ),
            )
        ),
    )

    assert ("zone.keep_out", Severity.ERROR) in codes(issues)


def test_motion_outside_the_tokens_warns_and_a_reason_downgrades_to_info() -> None:
    slow = MotionUse(element_id="m", phase=MotionPhase.ENTRY, easing="pop", duration=Rational(2, 1))
    issues = validate_usage(
        STYLE,
        usage(
            motions=(
                slow,
                replace(slow, element_id="n", reason="slow reveal of Platz 1"),
                MotionUse(
                    element_id="o", phase=MotionPhase.EXIT, easing="bounce", duration=Rational(1, 4)
                ),
            )
        ),
    )

    assert codes(issues) == [
        ("motion.duration", Severity.WARNING),
        ("motion.duration", Severity.INFO),
        ("motion.easing_unknown", Severity.ERROR),
    ]


def test_a_transition_is_checked_against_the_v2_duration_range_and_never_under_v1() -> None:
    v2 = resolve_style(
        MESC_IDENTITY_V2,
        StyleConfig(profile="ranking"),
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        width=1920,
        height=1080,
    )
    long = MotionUse(
        element_id="t", phase=MotionPhase.TRANSITION, easing="move", duration=Rational(2, 1)
    )
    ok = replace(long, element_id="u", duration=Rational(1, 2))

    assert codes(validate_usage(v2, usage(v2, motions=(long, ok)))) == [
        ("motion.duration", Severity.WARNING)
    ]
    assert validate_usage(STYLE, usage(motions=(long,))) == ()  # v1: no transition range


def test_the_intensity_ceiling_follows_the_format_profile() -> None:
    calm = resolve_style(
        MESC_IDENTITY_V1,
        StyleConfig(profile="brochure_review"),
        created_at=datetime(2026, 11, 2, tzinfo=UTC),
        width=1920,
        height=1080,
    )
    move = MotionUse(
        element_id="m", phase=MotionPhase.ENTRY, easing="pop", duration=Rational(3, 10), intensity=3
    )

    assert validate_usage(STYLE, usage(motions=(move,))) == ()
    assert codes(validate_usage(calm, usage(calm, motions=(move,)))) == [
        ("motion.intensity", Severity.WARNING)
    ]


def test_an_outline_that_closes_letter_gaps_is_an_error() -> None:
    # Anton: at most 5% of the size (a 7 px outline on 84 px merged the letters into a box)
    issues = validate_usage(STYLE, usage(texts=(caption_text(stroke_px=7),)))

    assert codes(issues) == [("text.stroke_heavy", Severity.ERROR)]
