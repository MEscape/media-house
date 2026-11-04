"""Shared builders: a real resolved MEsc style, its facts and the bundled fonts' metrics."""

from datetime import UTC, datetime
from functools import cache

from media_house.core.domain import Rational
from media_house.modules.motion_graphics_engine.application.style import style_facts
from media_house.modules.motion_graphics_engine.domain.animation import CaptionMotion
from media_house.modules.motion_graphics_engine.domain.engine import BuildSettings
from media_house.modules.motion_graphics_engine.domain.layout import LayoutInputs
from media_house.modules.motion_graphics_engine.domain.model import (
    FaceMetrics,
    GraphicContent,
    GraphicRequest,
    StyleFacts,
    Timing,
)
from media_house.modules.motion_graphics_engine.domain.values import GraphicKind
from media_house.modules.motion_graphics_engine.infrastructure.font_metrics import read_metrics
from media_house.modules.visual_style_engine.domain.identity import MESC_IDENTITY_V1
from media_house.modules.visual_style_engine.domain.model import ResolvedStyle
from media_house.modules.visual_style_engine.domain.profile import StyleConfig
from media_house.modules.visual_style_engine.domain.resolve import resolve_style
from media_house.modules.visual_style_engine.infrastructure.font_files import FONT_DIR

T0 = datetime(2026, 11, 2, tzinfo=UTC)
FPS = Rational(25, 1)
START = Rational(10, 1)


@cache
def style(width: int = 1920, height: int = 1080, profile: str | None = None) -> ResolvedStyle:
    return resolve_style(
        MESC_IDENTITY_V1,
        StyleConfig(profile=profile),
        created_at=T0,
        width=width,
        height=height,
    )


@cache
def facts(width: int = 1920, height: int = 1080, profile: str | None = None) -> StyleFacts:
    return style_facts(style(width, height, profile))


@cache
def metrics() -> dict[str, FaceMetrics]:
    return {
        f.key: read_metrics(f.key, (FONT_DIR / f.resource).read_bytes())
        for f in MESC_IDENTITY_V1.faces
    }


def inputs(width: int = 1920, height: int = 1080, **images: tuple[int, int]) -> LayoutInputs:
    return LayoutInputs(style=facts(width, height), metrics=metrics(), images=images)


def settings(fps: Rational = FPS) -> BuildSettings:
    return BuildSettings(
        fps=fps, captions=CaptionMotion(active_word=True, word_reveal=True), rank_label="Platz"
    )


def request(
    kind: GraphicKind,
    content: GraphicContent | None = None,
    *,
    request_id: str = "g1",
    seconds: int = 4,
    start: Rational | None = START,
    **extra: object,
) -> GraphicRequest:
    return GraphicRequest(
        request_id=request_id,
        kind=kind,
        content=content or GraphicContent(),
        timing=Timing(local_duration=Rational(seconds, 1), timeline_start=start),
        **extra,  # type: ignore[arg-type]
    )
