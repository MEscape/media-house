"""The real application for every caption engine integration test.

The caption engine (and motion graphics, while the orchestrator has not installed it yet) is
registered in this fixture's container; bootstrap is the orchestrator's. Rendering tests wire
motion graphics with the user's real HyperFrames install and skip when it is absent.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from media_house.bootstrap.application import Application, StartupOptions
from media_house.bootstrap.modules import installed_modules
from media_house.core.domain import Rational
from media_house.modules.audio_intelligence.application.contracts import timeline_to_json
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.caption_engine.application.contracts import (
    CaptionClip,
    CaptionTimeline,
    EditRef,
)
from media_house.modules.caption_engine.module import CaptionEngineModule
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.motion_graphics_engine.module import MotionGraphicsEngineModule
from media_house.modules.visual_style_engine.application.contracts import (
    ResolvedStyle,
    ResolveStyleCommand,
    StyleConfig,
    StyleRef,
    StyleResolver,
)
from media_house.shared.configuration import Environment
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths

pytestmark = pytest.mark.integration


@pytest.fixture
def app(tmp_path: Path) -> Iterator[Application]:
    base = installed_modules()
    names = {m.name for m in base}
    extra = tuple(
        m for m in (MotionGraphicsEngineModule(), CaptionEngineModule()) if m.name not in names
    )
    application = Application.start(
        StartupOptions(
            env={},
            home=tmp_path / "home",
            environment=Environment.TEST,
            modules=(*base, *extra),
        )
    )
    yield application
    application.shutdown()


def import_anchor(app: Application, tmp_path: Path, name: str = "clip") -> MediaAssetDto:
    """A picture stands in for the recording (the library needs no FFprobe for it)."""
    path = tmp_path / f"{name}.png"
    Image.new("RGB", (64, 36), (10, 10, 20)).save(path)
    result = app.container.resolve(MediaLibrary).import_file(path)
    assert isinstance(result, Ok), result
    return result.value.asset


def store_timeline(
    app: Application, asset: MediaAssetDto, timeline: AudioIntelligenceTimeline
) -> None:
    """A stored audio_intelligence timeline, as its catalog reads it."""
    documents = DerivedDocuments(
        app.container.resolve(MediaLibrary), app.container.resolve(AppPaths)
    )
    stored = documents.store(
        asset,
        timeline_to_json(timeline),
        filename="audio_intelligence.json",
        spec=DocumentSpec("audio_intelligence", 1, {"case": asset.id}),
        display_name="audio timeline",
        metadata={},
    )
    assert isinstance(stored, Ok), stored


def resolve_style(
    app: Application, anchor: str, profile: str, width: int = 1080, height: int = 1920
) -> tuple[ResolvedStyle, StyleRef]:
    result = app.container.resolve(StyleResolver).execute(
        ResolveStyleCommand(
            anchor_asset_id=anchor,
            project_id="p1",
            width=width,
            height=height,
            config=StyleConfig(profile=profile),
        )
    )
    assert isinstance(result, Ok), result
    return result.value.style, result.value.ref


def caption_timeline(
    anchor: str,
    seconds: int = 30,
    *,
    edit: EditRef | None = EditRef(edit_plan_id="ep1", plan_version=1),  # noqa: B008
    width: int = 1080,
    height: int = 1920,
) -> CaptionTimeline:
    return CaptionTimeline(
        edit=edit,
        project_id="p1" if edit is not None else None,
        frame_rate=Rational(30, 1),
        width=width,
        height=height,
        clips=(
            CaptionClip(
                clip_id="c1",
                asset_id=anchor,
                source_start=Rational(0, 1),
                source_end=Rational(seconds, 1),
                timeline_start=Rational(0, 1),
            ),
        ),
    )
