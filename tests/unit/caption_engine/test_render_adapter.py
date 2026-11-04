"""Caption groups -> motion requests (emoji, emphasis levels) and the render state back."""

from media_house.modules.caption_engine.application.render import (
    apply_renders,
    not_rendered,
    request_for,
)
from media_house.modules.caption_engine.domain.engine import EMOJI_NOT_RENDERED
from media_house.modules.caption_engine.domain.values import LEVEL_RANK, RenderStatus
from media_house.modules.motion_graphics_engine.application.contracts import (
    ItemStatus,
    QcCategory,
    QcIssue,
    Severity,
    StyleBinding,
)
from media_house.modules.motion_graphics_engine.application.render_graphics import (
    RenderedGraphic,
    RenderedGraphics,
)
from tests.support.caption_fakes import inputs, plan, transcript

HAUL = plan(inputs(transcript("Das ist krass. Wirklich krass. Total krass.")), "shorts_haul")
SEGMENT = next(s for s in HAUL.segments if s.emojis)


def test_the_request_carries_the_planned_emoji_after_its_word_and_the_levels() -> None:
    request = request_for(SEGMENT)
    (emoji,) = request.content.emojis
    planned = SEGMENT.emojis[0]
    words = request.content.words

    assert emoji.emoji == planned.emoji == "🤯"
    assert emoji.local_start == planned.local_start and emoji.entry == planned.animation
    word = next(w for w in SEGMENT.words if w.word_id == planned.word_id)
    assert words[emoji.after_word].text == word.display.split()[-1]
    assert [w.emphasis_level for w in words] == [LEVEL_RANK[w.emphasis] for w in SEGMENT.words]


def _rendered(*qc: QcIssue) -> RenderedGraphics:
    item = RenderedGraphic(
        request_id=SEGMENT.segment_id,
        status=ItemStatus.OK,
        asset_id="mov-1",
        created=True,
        frames=None,
        events=(),
        qc=qc,
        render=None,
    )
    return RenderedGraphics(
        items=(item,),
        style=StyleBinding(document_id=None, fingerprint="f", identity_version=2, profile="p"),
        renderer=("hyperframes", "0.9.5"),
    )


def test_a_rendered_group_marks_its_emoji_rendered() -> None:
    (segment,), _ = apply_renders([SEGMENT], _rendered())

    assert segment.render.status is RenderStatus.RENDERED
    assert [(e.render, e.detail) for e in segment.emojis] == [(RenderStatus.RENDERED, "")]


def test_an_emoji_the_style_cannot_draw_stays_planned() -> None:
    v1 = QcIssue(
        code="emoji.not_available",
        severity=Severity.WARNING,
        category=QcCategory.CREATIVE,
        message="the style has no emoji face",
    )
    (segment,), _ = apply_renders([SEGMENT], _rendered(v1))

    assert segment.render.status is RenderStatus.RENDERED  # the words are drawn
    assert [(e.render, e.detail) for e in segment.emojis] == [
        (RenderStatus.NOT_AVAILABLE, EMOJI_NOT_RENDERED)
    ]


def test_without_a_renderer_the_emoji_share_the_groups_state() -> None:
    (segment,) = not_rendered([SEGMENT], RenderStatus.NOT_AVAILABLE, "HyperFrames missing")

    assert [(e.render, e.detail) for e in segment.emojis] == [
        (RenderStatus.NOT_AVAILABLE, "HyperFrames missing")
    ]
