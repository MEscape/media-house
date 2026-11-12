"""A short keeps the one stretch of kept sentences that stands on its own."""

from fractions import Fraction
from types import SimpleNamespace
from typing import cast

from media_house.modules.editing_intelligence.domain.facts import SegmentFact, UnitFact
from media_house.modules.editing_intelligence.domain.highlight import highlight_window
from media_house.modules.editing_intelligence.domain.model import Decision
from media_house.modules.editing_intelligence.domain.speech import Extent
from media_house.modules.editing_intelligence.domain.units import EditUnit
from media_house.modules.editing_intelligence.domain.values import Action, Precision


def unit(
    i: int,
    seconds: int,
    *,
    hook: float = 0.0,
    highlight: float = 0.0,
    needs: tuple[str, ...] = (),
    clean: bool = True,
    section: str = "s1",
) -> EditUnit:
    uid = f"u{i}"
    start = Fraction(i * 10)
    return EditUnit(
        asset_id="a",
        asset_index=0,
        unit=UnitFact(
            unit_id=uid,
            segment_id=f"g{i}",
            start=float(start),
            end=float(start + seconds),
            time_basis="words",
            text=f"Satz {i}.",
            words=5,
            starts_clean=clean,
            ends_clean=True,
        ),
        segment=SegmentFact(
            segment_id=f"g{i}",
            unit_ids=(uid,),
            start=None,
            end=None,
            importance=0.5,
            removal=None,
            highlight=highlight,
            hook=hook,
            requires_context=needs,
            depends_on=(),
            repeats=None,
        ),
        section_id=section,
        priority="supporting",
        function=None,
        beat_id=None,
        extent=Extent(start=start, end=start + seconds, precision=Precision.WORD_ALIGNED, words=()),
        order=i,
        source_pos=i,
    )


def keep_all(units: list[EditUnit]) -> dict[str, Decision]:
    return {u.decision_id: cast(Decision, SimpleNamespace(action=Action.KEEP)) for u in units}


def test_the_hook_led_stretch_of_the_wanted_length_wins() -> None:
    units = [
        unit(0, 10),
        unit(1, 10),
        unit(2, 12, hook=0.9, highlight=0.8),
        unit(3, 12, highlight=0.9),
        unit(4, 12, highlight=0.7),
        unit(5, 10),
    ]

    chosen = highlight_window(units, keep_all(units), 30, 40)

    assert [u.unit.unit_id for u in chosen] == ["u2", "u3", "u4"]


def test_a_stretch_needing_earlier_context_or_a_dirty_start_loses() -> None:
    units = [
        unit(0, 12, highlight=0.5),
        unit(1, 12, highlight=0.5),
        unit(2, 12, hook=0.9, highlight=0.9, clean=False),  # broken-off start
        unit(3, 12, highlight=0.9, needs=("u0",)),  # needs a sentence from before it
        unit(4, 12, highlight=0.5),
    ]

    chosen = highlight_window(units, keep_all(units), 30, 40)

    assert [u.unit.unit_id for u in chosen] == ["u0", "u1", "u2"]


def test_nothing_fits_gives_nothing() -> None:
    units = [unit(0, 5), unit(1, 5)]

    assert highlight_window(units, keep_all(units), 30, 40) == ()


def test_a_stretch_opening_its_section_beats_one_starting_mid_section() -> None:
    # E2E regression: the Short began mid-product ("Nur 80 Schuss ...") without its introduction
    units = [
        unit(0, 12, section="s1"),
        unit(1, 12, section="s2"),
        unit(2, 12, section="s2"),
        unit(3, 12, section="s2"),
        unit(4, 12, section="s2"),
    ]

    chosen = highlight_window(units, keep_all(units), 30, 40)

    assert [u.unit.unit_id for u in chosen] == ["u1", "u2", "u3"]
