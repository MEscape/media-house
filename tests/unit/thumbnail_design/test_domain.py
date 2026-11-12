import pytest

from media_house.modules.thumbnail_design.domain.brief import (
    DeviceCard,
    Headline,
    StoryBadge,
    ThumbnailBrief,
)
from media_house.modules.thumbnail_design.domain.layout import (
    HEADLINE_ACCENT_PX,
    HEADLINE_PLAIN_PX,
    HEADLINE_SINGLE_PX,
    TIMESTAMP_ZONE,
    lay_out,
    unplaced_products,
)
from media_house.modules.thumbnail_design.domain.qc import Severity, blocking, judge
from media_house.modules.thumbnail_design.domain.reactions import ShotCue, rank
from media_house.modules.thumbnail_design.domain.variants import variants_of
from media_house.shared.errors import InvariantViolation


def brief(**changes: object) -> ThumbnailBrief:
    values: dict[str, object] = {
        "headline": Headline(("Erstmals", "online!")),
        "reaction_asset_id": "me",
        "products": ("seven", "arma"),
        "logo_asset_id": "lidl",
    }
    values.update(changes)
    return ThumbnailBrief(**values)  # type: ignore[arg-type]


class TestBrief:
    def test_headline_caps_write_sharp_s_as_ss(self) -> None:
        assert Headline(("Größte", "Böller")).caps == ("GRÖSSTE", "BÖLLER")

    @pytest.mark.parametrize(
        "lines", [(), ("a", "b", "c"), ("viel zu viele wörter",), ("Feuerwerksbatterien",)]
    )
    def test_headline_refuses_what_does_not_read_in_the_feed(self, lines: tuple[str, ...]) -> None:
        with pytest.raises(InvariantViolation):
            Headline(lines)

    def test_accent_line_out_of_range_means_no_accent(self) -> None:
        assert Headline(("Online!",), accent_line=1).accent_line is None

    @pytest.mark.parametrize("price", ["79.99 €", "79,9 €", "79,99", "1299,00 €"])
    def test_price_needs_the_channel_format(self, price: str) -> None:
        with pytest.raises(InvariantViolation):
            DeviceCard(product_asset_id="p", product_name="Pure Impact", price=price)

    def test_thousands_dot_price_is_accepted(self) -> None:
        assert DeviceCard(product_asset_id="p", product_name="X", price="1.299,00 €").price

    def test_the_phone_product_is_not_repeated_as_a_sticker(self) -> None:
        device = DeviceCard(product_asset_id="seven", product_name="Seven Kings", price="134,99 €")
        assert brief(device=device, products=("seven", "arma", "arma")).products == ("arma",)

    def test_too_many_products_are_refused(self) -> None:
        with pytest.raises(InvariantViolation):
            brief(products=("a", "b", "c", "d"))

    def test_a_reaction_is_required(self) -> None:
        with pytest.raises(InvariantViolation):
            brief(reaction_asset_id="")

    def test_config_is_stable_and_complete(self) -> None:
        config = brief(badge=StoryBadge("04:30")).to_config()
        assert config["headline"] == {"lines": ["ERSTMALS", "ONLINE!"], "accent_line": 1}
        assert config["badge"] == {"text": "04:30", "struck": True}
        assert config == brief(badge=StoryBadge(" 04:30 ")).to_config()


class TestVariants:
    def test_only_a_clean_variant_without_badge_or_alternate(self) -> None:
        assert [v.name for v in variants_of(brief())] == ["a"]

    def test_each_variant_changes_one_thing(self) -> None:
        full = brief(badge=StoryBadge("04:30"), alternate_headline=Headline(("Nie", "anstehen")))
        a, b, c = variants_of(full)
        assert a.brief.badge is None and a.brief.headline == full.headline
        assert b.brief.badge == full.badge and b.brief.headline == full.headline
        assert c.brief.headline.caps == ("NIE", "ANSTEHEN")


class TestLayout:
    def test_the_badge_takes_the_top_slot(self) -> None:
        three = ("a", "b", "c")
        assert len(lay_out(brief(products=three)).products) == 3
        with_badge = brief(products=three, badge=StoryBadge("04:30"))
        assert len(lay_out(with_badge).products) == 2
        assert unplaced_products(with_badge) == ("c",)

    def test_most_important_product_is_drawn_last(self) -> None:
        assert [p.asset_id for p in lay_out(brief()).products] == ["arma", "seven"]

    def test_headline_sizes_favour_the_accent_line(self) -> None:
        assert lay_out(brief()).headline_sizes == (HEADLINE_PLAIN_PX, HEADLINE_ACCENT_PX)
        single = brief(headline=Headline(("Online!",)))
        assert lay_out(single).headline_sizes == (HEADLINE_SINGLE_PX,)

    def test_same_brief_same_layout(self) -> None:
        assert lay_out(brief()) == lay_out(brief())


class TestChecks:
    BOXES = {"headline": (50, 20, 900, 340), "reaction": (-330, 300, 1050, 1300)}

    def test_a_clean_render_has_no_issue(self) -> None:
        assert judge(width=1920, height=1080, boxes=self.BOXES, headline_px=(168, 206)) == ()

    def test_wrong_size_blocks(self) -> None:
        issues = judge(width=1280, height=720, boxes=self.BOXES, headline_px=(168,))
        assert blocking(issues)

    def test_text_under_the_duration_chip_and_cut_off_are_flagged(self) -> None:
        x0, y0, _, _ = TIMESTAMP_ZONE
        boxes = self.BOXES | {"button": (x0 + 10, y0 + 10, x0 + 300, 1120)}
        codes = {i.code for i in judge(width=1920, height=1080, boxes=boxes, headline_px=(168,))}
        assert codes == {"thumbnail.under_timestamp", "thumbnail.cut_off"}

    def test_small_headline_and_small_reaction_warn(self) -> None:
        boxes = {"headline": (0, 0, 10, 10), "reaction": (0, 0, 300, 300)}
        issues = judge(width=1920, height=1080, boxes=boxes, headline_px=(90,))
        assert {i.code for i in issues} == {"thumbnail.headline_small", "thumbnail.reaction_small"}
        assert all(i.severity is Severity.WARNING for i in issues)


def cue(**changes: object) -> ShotCue:
    values: dict[str, object] = {
        "asset_id": "cam",
        "asset_name": "talking_1",
        "shot_id": "s1",
        "start_seconds": 1.0,
        "end_seconds": 3.0,
        "face_frames": 10,
        "face_height": 0.4,
        "face_sharpness": 0.8,
        "eyes_closed": 0.0,
        "smile": 0.1,
        "mouth_activity": 0.6,
    }
    values.update(changes)
    return ShotCue(**values)  # type: ignore[arg-type]


class TestReactions:
    def test_unusable_shots_are_never_offered(self) -> None:
        cues = [cue(face_frames=0), cue(face_height=0.05), cue(end_seconds=1.2)]
        assert rank(cues, 5) == ()

    def test_big_sharp_open_eyed_faces_win_and_reaction_recordings_are_preferred(self) -> None:
        cues = [
            cue(shot_id="blink", eyes_closed=0.9),
            cue(shot_id="good"),
            cue(shot_id="named", asset_id="r", asset_name="lidlnico_reaktionen"),
        ]
        moments = rank(cues, 3)
        assert [m.shot_id for m in moments] == ["named", "good", "blink"]
        assert moments[0].seconds == 2.0
        assert "reaction recording" in moments[0].reasons

    def test_order_is_stable_for_equal_scores(self) -> None:
        cues = [cue(asset_id="b"), cue(asset_id="a")]
        assert [m.asset_id for m in rank(cues, 2)] == ["a", "b"]
