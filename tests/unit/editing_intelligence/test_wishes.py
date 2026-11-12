"""Cover footage asked for over named speech lands where that speech ends up in the edit."""

from fractions import Fraction

from media_house.core.domain import Rational
from media_house.modules.editing_intelligence.domain.assemble import Piece, Timeline
from media_house.modules.editing_intelligence.domain.facts import AssetEvidence, SourceFacts
from media_house.modules.editing_intelligence.domain.model import Placement
from media_house.modules.editing_intelligence.domain.profile import CoverWish
from media_house.modules.editing_intelligence.domain.timing import to_fraction
from media_house.modules.editing_intelligence.domain.values import (
    AudioBehavior,
    Precision,
)
from media_house.modules.editing_intelligence.domain.wishes import place_wishes

RATE = Rational(25, 1)


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


def piece(asset_id: str, start: int, end: int) -> Piece:
    return Piece(
        asset_id=asset_id,
        start=Fraction(start),
        end=Fraction(end),
        start_precision=Precision.WORD_ALIGNED,
        end_precision=Precision.WORD_ALIGNED,
        cut_in=None,
        cut_out=None,
        units=(),
    )


# the talk 10-14 s and 20-26 s of "talk" (a pause between was trimmed), placed back to back
PIECES = (piece("talk", 10, 14), piece("talk", 20, 26))
TIMELINE = Timeline(placements=(), total=Fraction(10), starts=(Fraction(0), Fraction(4)))
EVIDENCE = {"talk": source("talk", 60), "rush": source("rush", 30)}


def ids() -> str:
    return "i1"


def test_the_footage_plays_on_over_every_kept_part_of_the_named_speech() -> None:
    wish = CoverWish(footage_asset_id="rush", over_asset_id="talk", over_start=12, over_end=24)

    result = place_wishes((wish,), PIECES, TIMELINE, EVIDENCE, (), RATE, ids)

    first, second = result.placements
    assert (to_fraction(first.timeline_start), to_fraction(first.timeline_duration)) == (
        Fraction(2),
        Fraction(2),
    )
    assert (to_fraction(second.timeline_start), to_fraction(second.timeline_duration)) == (
        Fraction(4),
        Fraction(4),
    )
    assert to_fraction(second.source.start) == to_fraction(first.source.end) == 2
    assert all(p.layer == 1 and p.audio is AudioBehavior.MUTED for p in result.placements)
    (decision,) = result.decisions
    assert decision.decision_id in first.decision_ids


def test_it_stops_at_its_length_and_says_so_when_the_speech_is_gone() -> None:
    short = CoverWish(
        footage_asset_id="rush", over_asset_id="talk", over_start=10, over_end=26, max_seconds=3
    )
    gone = CoverWish(footage_asset_id="rush", over_asset_id="talk", over_start=15, over_end=19)

    result = place_wishes((short, gone), PIECES, TIMELINE, EVIDENCE, (), RATE, ids)

    assert sum(to_fraction(p.timeline_duration) for p in result.placements) == 3
    (issue,) = result.issues
    assert "not in the edit" in issue.detail


def test_a_short_piece_inside_a_running_cover_is_covered_too() -> None:
    # 4 s, then a 0.3 s piece (shorter than any cover on its own), then 4 s, back to back
    pieces = (piece("talk", 10, 14), piece_of("talk", 20, Fraction(203, 10)), piece("talk", 30, 34))
    timeline = Timeline(
        placements=(),
        total=Fraction(83, 10),
        starts=(Fraction(0), Fraction(4), Fraction(43, 10)),
    )
    wish = CoverWish(
        footage_asset_id="rush", over_asset_id="talk", over_start=10, over_end=34, max_seconds=20
    )

    result = place_wishes((wish,), pieces, timeline, EVIDENCE, (), RATE, ids)

    assert sum(to_fraction(p.timeline_duration) for p in result.placements) == Fraction(83, 10)


def piece_of(asset_id: str, start: Fraction | int, end: Fraction) -> Piece:
    return Piece(
        asset_id=asset_id,
        start=Fraction(start),
        end=end,
        start_precision=Precision.WORD_ALIGNED,
        end_precision=Precision.WORD_ALIGNED,
        cut_in=None,
        cut_out=None,
        units=(),
    )


def test_a_glimpse_of_the_speaker_next_to_cover_is_closed() -> None:
    from media_house.modules.editing_intelligence.domain.wishes import seal_covers

    wish = CoverWish(footage_asset_id="rush", over_asset_id="talk", over_start=12.3, over_end=13.6)
    placed = place_wishes((wish,), PIECES, TIMELINE, EVIDENCE, (), RATE, ids).placements
    main = [
        piece_placement(0, 4),
        piece_placement(4, 10),
    ]
    (cover,) = seal_covers([*main, *placed], EVIDENCE, RATE)[2:]
    # 2.3 s after the cut at 0 s is a shot (kept); 0.4 s before the cut at 4 s is a flash:
    # the cover runs on to the cut
    assert to_fraction(cover.timeline_start) == Fraction(23, 10)
    assert to_fraction(cover.timeline_start) + to_fraction(cover.timeline_duration) == 4


def piece_placement(start: int, end: int) -> Placement:
    from media_house.modules.editing_intelligence.domain.ranges import make_range
    from media_house.modules.editing_intelligence.domain.timing import to_rational
    from media_house.modules.editing_intelligence.domain.values import (
        PlacementRole,
        TrackKind,
    )

    return Placement(
        placement_id=f"main:{start}",
        track=TrackKind.VIDEO,
        layer=0,
        role=PlacementRole.MAIN,
        source=make_range(
            EVIDENCE["talk"].source,
            Fraction(start),
            Fraction(end),
            Precision.WORD_ALIGNED,
            Precision.WORD_ALIGNED,
        ),
        timeline_start=to_rational(Fraction(start)),
        timeline_duration=to_rational(Fraction(end - start)),
        start_frame=start * 25,
        duration_frames=(end - start) * 25,
        section_id=None,
        beat_id=None,
        decision_ids=(),
        audio=AudioBehavior.SOURCE_SYNC,
        fade_in=Rational(0, 1),
        fade_out=Rational(0, 1),
    )
