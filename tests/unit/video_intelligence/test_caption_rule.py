"""Burned-in captions vs OCR misreads and screens full of text (synthetic strings only)."""

from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.observations import Overlay
from media_house.modules.video_intelligence.domain.signals import TextRow, TextSignals
from media_house.modules.video_intelligence.domain.values import AnalyzerId, OverlayKind
from tests.support.vi_signals import derived, shot_signals

FRAMES = list(range(0, 120, 10))
BOTTOM = BBox(0.2, 0.8, 0.8, 0.88)


def overlays(rows: list[TextRow]) -> tuple[Overlay, ...]:
    signals = TextSignals("fake", tuple(FRAMES), tuple(rows))
    return derived(shot_signals(120), signals={AnalyzerId.TEXT: signals}).overlays


def test_misreads_of_a_static_label_low_in_the_picture_are_no_caption() -> None:
    # the "9AX" case: one small static label, read differently from frame to frame
    reads = ["9AX", "AF", "AE", "9AK"]
    rows = [
        TextRow(f, reads[i % len(reads)], 0.7, BBox(0.6, 0.75, 0.66, 0.79))
        for i, f in enumerate(FRAMES)
    ]

    assert overlays(rows) == ()


def test_misreads_of_one_caption_line_are_not_changes() -> None:
    reads = ["wecker stellen", "wecker stel1en", "wecker stellen.", "wecker ste11en"]
    rows = [TextRow(f, reads[i % len(reads)], 0.8, BOTTOM) for i, f in enumerate(FRAMES)]

    assert not [o for o in overlays(rows) if o.kind is OverlayKind.BURNED_IN_CAPTION]


def test_changing_german_lines_at_one_bottom_position_are_a_burned_in_caption() -> None:
    lines = [
        "heute zeige ich euch",
        "wie man den wecker stellt",
        "und warum das so wichtig ist",
        "genau deshalb testen wir alles",
    ]
    rows = [TextRow(f, lines[i // 3], 0.9, BOTTOM) for i, f in enumerate(FRAMES)]
    rows[4] = TextRow(FRAMES[4], "wie man den weoker stellt", 0.6, BOTTOM)  # one misread

    found = overlays(rows)

    assert [o.kind for o in found] == [OverlayKind.BURNED_IN_CAPTION]
    assert (found[0].first.frame, found[0].last.frame) == (0, 110)
    assert found[0].text == "heute zeige ich euch"


def test_a_screen_full_of_changing_text_in_the_bottom_band_is_no_caption() -> None:
    # a web page or app: several strings side by side in every frame, changing as it scrolls
    words = ["preis", "lieferung", "rabatt", "versand", "kasse", "filiale", "gutschein"]
    rows = [
        TextRow(
            f, words[(i + n) % len(words)], 0.9, BBox(0.05 + 0.24 * n, 0.8, 0.25 + 0.24 * n, 0.85)
        )
        for i, f in enumerate(FRAMES)
        for n in range(4)
    ]

    assert not [o for o in overlays(rows) if o.kind is OverlayKind.BURNED_IN_CAPTION]
