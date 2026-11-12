"""A short shows each item's own footage, and its hook the gag, over the kept speech."""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import Piece, Timeline
from media_house.modules.editing_intelligence.domain.facts import (
    AssetEvidence,
    SourceFacts,
    StoryFacts,
    UnitFact,
)
from media_house.modules.editing_intelligence.domain.inserts import plan_inserts
from media_house.modules.editing_intelligence.domain.profile import PROFILES, CoverWish
from media_house.modules.editing_intelligence.domain.speech import Extent
from media_house.modules.editing_intelligence.domain.timing import to_fraction
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import Precision

RATE = Rational(25, 1)
STORY = StoryFacts(
    story_plan_id="sp",
    plan_version=1,
    stored_as=None,
    target_format="generic",
    asset_ids=("talk",),
    sections=(),
    beats=(),
    key_info=(),
    constraints=(),
    contexts=(),
    links=(),
    visuals=(),
    openings=(),
    recommended_opening=None,
    order_changes=(),
    relations=(),
    target_low=None,
    target_high=None,
)


def source(asset_id: str, duration: float) -> AssetEvidence:
    return AssetEvidence(
        source=SourceFacts(
            asset_id=asset_id,
            name=asset_id,
            duration=duration,
            frame_rate=RATE,
            variable_frame_rate=False,
            has_video=True,
            has_audio=True,
            sample_rate=48_000,
            usable=True,
            identity=asset_id,
        )
    )


def unit(n: int, section: str, start: int, end: int) -> EditUnit:
    return EditUnit(
        asset_id="talk",
        asset_index=0,
        unit=UnitFact(
            unit_id=f"u{n}",
            segment_id=f"g{n}",
            start=start,
            end=end,
            time_basis="words",
            text="Ein erfundener Satz.",
            words=3,
            starts_clean=True,
            ends_clean=True,
        ),
        segment=None,
        section_id=section,
        priority="essential",
        function=None,
        beat_id=None,
        extent=Extent(Fraction(start), Fraction(end), Precision.WORD_ALIGNED, ()),
        order=n,
        source_pos=n,
    )


# The long story: hook 0-3 and 4-5 s, item one 10-16 and 20-23 s, item two 30-37 s. The short
# keeps the hook's first line, item one's first line and item two.
UNITS = [
    unit(0, "open", 0, 3),
    unit(1, "open", 4, 5),
    unit(2, "one", 10, 16),
    unit(3, "one", 20, 23),
    unit(4, "two", 30, 37),
]


def piece(u: EditUnit) -> Piece:
    assert u.extent is not None
    return Piece(
        asset_id="talk",
        start=u.extent.start,
        end=u.extent.end,
        start_precision=Precision.WORD_ALIGNED,
        end_precision=Precision.WORD_ALIGNED,
        cut_in=None,
        cut_out=None,
        units=(u,),
    )


PIECES = [piece(UNITS[0]), piece(UNITS[2]), piece(UNITS[4])]
TIMELINE = Timeline(
    placements=(), total=Fraction(16), starts=(Fraction(0), Fraction(3), Fraction(9))
)
EVIDENCE = {"talk": source("talk", 60), "gag": source("gag", 8), "rush": source("rush", 30)}
# Asked for over sentences the short cut: the gag over the hook's second line, the rush over
# item one's second line.
WISHES = (
    CoverWish(
        footage_asset_id="gag", over_asset_id="talk", over_start=4, over_end=5, max_seconds=4
    ),
    CoverWish(footage_asset_id="rush", over_asset_id="talk", over_start=20, over_end=23),
)


def spans() -> list[tuple[str | None, Fraction, Fraction]]:
    inserts = plan_inserts(
        "open",
        ["one", "two"],
        UNITS,
        PIECES,
        TIMELINE,
        STORY,
        WISHES,
        None,
        EVIDENCE,
        [],
        PROFILES["short"],
        RATE,
    )
    return [
        (
            p.section_id,
            to_fraction(p.timeline_start),
            to_fraction(p.timeline_start) + to_fraction(p.timeline_duration),
        )
        for p in inserts.placements
    ]


def test_the_hook_opens_on_its_gag_and_an_item_shows_its_footage_after_its_name() -> None:
    assert spans() == [
        ("open", Fraction(0), Fraction(3)),  # the whole hook line
        ("one", Fraction(9, 2), Fraction(69, 10)),  # 1.5 s in, 40 % of the 6 s item
    ]


def test_an_item_without_footage_of_its_own_gets_none() -> None:
    assert "two" not in [s for s, _, _ in spans()]


def test_inserts_keep_four_seconds_apart() -> None:
    tight = [piece(UNITS[0]), piece(UNITS[2]), piece(unit(5, "two", 40, 47))]
    wishes = (
        *WISHES,
        CoverWish(footage_asset_id="rush", over_asset_id="talk", over_start=40, over_end=41,
                  footage_start=10),
    )  # fmt: skip
    timeline = Timeline(
        placements=(), total=Fraction(13), starts=(Fraction(0), Fraction(3), Fraction(6))
    )
    inserts = plan_inserts(
        "open",
        ["one", "two"],
        [*UNITS, unit(5, "two", 40, 47)],
        tight,
        timeline,
        STORY,
        wishes,
        None,
        EVIDENCE,
        [],
        PROFILES["short"],
        RATE,
    )
    # item two's slot (7.5 s) is only 3 s after item one's insert (4.5 s): it moves to 8.5 s
    starts = [to_fraction(p.timeline_start) for p in inserts.placements]
    assert starts == [0, Fraction(9, 2), Fraction(17, 2)]
