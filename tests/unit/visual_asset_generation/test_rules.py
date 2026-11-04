"""Pure rules: frame addressing, QC, selection ranking, generation policy, stored form."""

from datetime import UTC, datetime

import pytest

from media_house.core.domain import Rational
from media_house.modules.visual_asset_generation.domain.engine import digest, state_of
from media_house.modules.visual_asset_generation.domain.errors import InvalidVisualAssetDocument
from media_house.modules.visual_asset_generation.domain.frames import (
    StreamClock,
    decimal_seconds,
    frame_position,
)
from media_house.modules.visual_asset_generation.domain.generation import (
    GenerationKind,
    GenerationRequest,
    generation_problems,
)
from media_house.modules.visual_asset_generation.domain.model import (
    AssetKind,
    FrameGrab,
    InputHow,
    InputRecord,
    ItemStatus,
    PreparedAsset,
    Provenance,
    ResultState,
    StageReport,
    StageState,
    StyleRef,
    VisualAssetSet,
)
from media_house.modules.visual_asset_generation.domain.profile import (
    QcThresholds,
    SelectionSettings,
)
from media_house.modules.visual_asset_generation.domain.qc import (
    CutoutFacts,
    ImageFacts,
    QcCategory,
    QcIssue,
    Severity,
    blocking,
    check_cutout,
    check_file,
    check_transparency,
)
from media_house.modules.visual_asset_generation.domain.selection import (
    ImageEntry,
    SourceTier,
    rank,
    tier_of,
    words,
)
from media_house.modules.visual_asset_generation.domain.serialization import (
    set_from_json,
    set_to_json,
)

NTSC = Rational(30000, 1001)


# --- frames ---------------------------------------------------------------------------------
def test_a_time_inside_a_frame_addresses_the_frame_on_screen() -> None:
    clock = StreamClock(frame_rate=Rational(25, 1), video_offset=Rational(0, 1))
    position = frame_position(Rational(1003, 1000), clock)  # 1.003 s -> frame 25 (1.000-1.040)
    assert (position.frame_index, position.frame_time, position.exact) == (
        25,
        Rational(1, 1),
        True,
    )
    assert position.seek_time == Rational(49, 50)  # half a frame early: 0.98 s


def test_frame_math_is_exact_for_ntsc_rates_and_stream_offsets() -> None:
    clock = StreamClock(frame_rate=NTSC, video_offset=Rational(1, 10))
    frame_100 = Rational(3000 + 100 * 1001, 30000)
    position = frame_position(frame_100, clock)
    assert position.frame_index == 100
    assert position.frame_time == frame_100
    before = frame_position(Rational(1, 20), clock)  # before the first picture
    assert before.frame_index == 0 and before.frame_time == Rational(1, 10)


def test_without_a_clock_the_time_is_used_and_marked_inexact() -> None:
    position = frame_position(Rational(3, 2), None)
    assert position.exact is False and position.frame_index is None
    assert position.seek_time == Rational(3, 2)


def test_decimal_seconds_is_exact_to_the_nanosecond() -> None:
    assert decimal_seconds(Rational(1001, 30000)) == "0.033366667"
    assert decimal_seconds(Rational(5, 1)) == "5.000000000"


# --- QC -------------------------------------------------------------------------------------
GOOD = ImageFacts(
    format="PNG",
    mode="RGBA",
    width=1080,
    height=1080,
    has_alpha=True,
    transparent_ratio=0.7,
    opaque_ratio=0.25,
    border_alpha=0.0,
    rim_deviation=0.01,
    residue_ratio=0.0,
)
CUT = CutoutFacts(mask_box=(10, 10, 200, 200), source_size=(800, 600), scale=1.0)


def test_a_good_cutout_passes() -> None:
    issues = (
        check_file(GOOD, "x", expected_format="PNG")
        + check_transparency(GOOD, "x", clipped_is_error=False)
        + check_cutout(GOOD, CUT, QcThresholds(), "x")
    )
    assert issues == []


def test_an_unreadable_file_is_an_error_and_nothing_else_is_judged() -> None:
    broken = ImageFacts(error="truncated")
    issues = check_file(broken, "x", expected_format="PNG")
    assert [i.code for i in issues] == ["file_unreadable"] and blocking(issues)
    assert check_transparency(broken, "x", clipped_is_error=True) == []


@pytest.mark.parametrize(
    ("facts", "code"),
    [
        (ImageFacts(format="PNG", width=8, height=8, has_alpha=False), "no_alpha"),
        (
            ImageFacts(format="PNG", width=8, height=8, has_alpha=True, transparent_ratio=0.0),
            "no_transparency",
        ),
        (
            ImageFacts(
                format="PNG",
                width=8,
                height=8,
                has_alpha=True,
                transparent_ratio=0.5,
                opaque_ratio=0.0,
            ),
            "no_solid_subject",
        ),
    ],
)
def test_alpha_problems_are_errors(facts: ImageFacts, code: str) -> None:
    issues = check_transparency(facts, "x", clipped_is_error=False)
    assert code in [i.code for i in issues] and blocking(issues)


def test_touching_the_canvas_edge_is_an_error_only_for_effects() -> None:
    edge = ImageFacts(
        format="PNG", width=8, height=8, has_alpha=True, transparent_ratio=0.5, border_alpha=0.4
    )
    assert not blocking(check_transparency(edge, "x", clipped_is_error=False))
    assert blocking(check_transparency(edge, "x", clipped_is_error=True))


def test_halo_residue_upscale_and_source_edge_are_warnings() -> None:
    facts = ImageFacts(
        format="PNG",
        width=8,
        height=8,
        has_alpha=True,
        transparent_ratio=0.5,
        rim_deviation=0.5,
        residue_ratio=0.01,
    )
    cut = CutoutFacts(mask_box=(0, 5, 100, 100), source_size=(100, 200), scale=3.0)
    issues = check_cutout(facts, cut, QcThresholds(), "x")
    assert {i.code for i in issues} == {
        "edge_halo",
        "background_residue",
        "upscaled",
        "small_source",
        "subject_at_source_edge",
    }
    assert all(i.severity is Severity.WARNING for i in issues)


# --- selection ------------------------------------------------------------------------------
def entry(asset_id: str, name: str, tier: SourceTier, tags: tuple[str, ...] = ()) -> ImageEntry:
    return ImageEntry(asset_id=asset_id, name=name, filename=f"{name}.png", tags=tags, tier=tier)


def test_ranking_prefers_uploads_then_approved_then_brand_then_generated() -> None:
    entries = [
        entry("g", "Titan Rakete", SourceTier.GENERATED),
        entry("b", "Titan Rakete", SourceTier.BRAND),
        entry("a", "Titan Rakete", SourceTier.APPROVED),
        entry("u", "Titan Rakete", SourceTier.UPLOAD),
        entry("x", "Wolf", SourceTier.UPLOAD),
    ]
    ranked = rank("Rakete Titan", entries, SelectionSettings())
    assert [c.asset_id for c in ranked] == ["u", "a", "b", "g"]


def test_words_match_inside_german_compounds_and_tags() -> None:
    entries = [entry("c", "Raketenbatterie 2025", SourceTier.UPLOAD, tags=("feuerwerk",))]
    ranked = rank("Rakete Feuerwerk", entries, SelectionSettings())
    assert ranked and ranked[0].score == 1.0
    assert words("Die Ü-Rakete!") == ("die", "rakete")


def test_a_weak_match_is_not_a_candidate() -> None:
    entries = [entry("c", "Titan", SourceTier.UPLOAD)]
    assert rank("Titan Rakete Batterie", entries, SelectionSettings()) == ()


def test_tier_follows_generated_brand_approved_tags_and_project_groups() -> None:
    settings = SelectionSettings()
    assert tier_of((), generated=True, in_project=True, settings=settings) is SourceTier.GENERATED
    assert (
        tier_of(("Brand",), generated=False, in_project=False, settings=settings)
        is SourceTier.BRAND
    )
    assert tier_of((), generated=False, in_project=True, settings=settings) is SourceTier.APPROVED
    assert tier_of(("approved",), generated=False, in_project=False, settings=settings) is (
        SourceTier.APPROVED
    )
    assert tier_of((), generated=False, in_project=False, settings=settings) is SourceTier.UPLOAD


# --- generation policy ----------------------------------------------------------------------
def generation(
    kind: GenerationKind, reason: str = "the b-roll gap has no footage"
) -> GenerationRequest:
    return GenerationRequest(
        request_id="g1",
        concept="Feuerwerk Sicherheit",
        kind=kind,
        prompt="flat icon",
        reason=reason,
    )


@pytest.mark.parametrize(
    "kind",
    [
        GenerationKind.LOGO,
        GenerationKind.PRODUCT_PHOTO,
        GenerationKind.EVIDENCE,
        GenerationKind.REALISTIC_FOOTAGE,
    ],
)
def test_logos_products_and_evidence_are_never_generated(kind: GenerationKind) -> None:
    assert generation_problems(generation(kind), existing_matches=0)


def test_generation_needs_a_reason_and_no_existing_match() -> None:
    assert generation_problems(generation(GenerationKind.ILLUSTRATION), existing_matches=0) == ()
    assert generation_problems(generation(GenerationKind.ILLUSTRATION), existing_matches=2)
    assert generation_problems(generation(GenerationKind.ILLUSTRATION, reason=" "), 0)


# --- stored form ----------------------------------------------------------------------------
def sample_set() -> VisualAssetSet:
    item = PreparedAsset(
        item_id="r1:clean",
        request_id="r1",
        purpose="rank 3 sticker",
        variant="clean",
        kind=AssetKind.CLEAN_CUTOUT,
        status=ItemStatus.OK,
        asset_id="a2",
        source_asset_id="a1",
        derived_from_asset_id="a1",
        clean_cutout_asset_id="a2",
        operation="image_adjustment",
        configuration_hash=digest({"x": 1}),
        width=1080,
        height=1080,
        format="png",
        has_alpha=True,
    )
    failed = PreparedAsset(
        item_id="r1:sticker",
        request_id="r1",
        purpose="",
        variant="sticker",
        kind=AssetKind.ENHANCED,
        status=ItemStatus.FAILED,
        asset_id=None,
        source_asset_id="a1",
        derived_from_asset_id=None,
        clean_cutout_asset_id=None,
        operation="image_enhancement",
        configuration_hash="",
        width=None,
        height=None,
        format=None,
        has_alpha=None,
        fallback_asset_id="a2",
    )
    items = (item, failed)
    return VisualAssetSet(
        plan_id="vas:a1:p:1",
        plan_version=1,
        supersedes=None,
        project_id="p",
        edit=None,
        style=StyleRef(document_id=None, fingerprint="f", identity_version=1, profile="long"),
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        state=state_of(items),
        items=items,
        frames=(
            FrameGrab(
                video_asset_id="v",
                source_time=Rational(1001, 30000),
                frame_index=1,
                frame_time=Rational(1001, 30000),
                exact=True,
                asset_id="a1",
            ),
        ),
        checks=(
            QcIssue(
                code="edge_halo",
                severity=Severity.WARNING,
                category=QcCategory.TECHNICAL,
                message="m",
                item_id="r1:clean",
            ),
        ),
        inputs=(InputRecord(kind="source", how=InputHow.SUPPLIED, asset_id="a1"),),
        stages=(StageReport(stage="cutout", state=StageState.OK),),
        provenance=Provenance(
            schema_version=1,
            planner_version=1,
            profile="generic",
            profile_version=1,
            configuration_fingerprint="c",
            engines=(("ffmpeg", "9.0.2"),),
        ),
        warnings=(),
        limitations=("l",),
    )


def test_a_set_round_trips_exactly_and_names_its_fallbacks() -> None:
    document = sample_set()
    assert document.state is ResultState.PARTIAL
    again = set_from_json(set_to_json(document))
    assert again == document
    assert again.asset_for("r1", "sticker") == "a2"
    assert again.asset_for("r1", "clean") == "a2"
    assert [i.variant for i in again.usable("r1")] == ["clean"]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda t: t.replace('"schema_version": 1', '"schema_version": 99'),
        lambda t: t.replace('"plan_version": 1', '"plan_version": 1, "surprise": 1'),
        lambda t: t.replace('"visual_asset_set"', '"other"'),
        lambda t: "{",
    ],
)
def test_a_damaged_document_is_refused(mutate: object) -> None:
    text = set_to_json(sample_set())
    with pytest.raises(InvalidVisualAssetDocument):
        set_from_json(mutate(text))  # type: ignore[operator]
