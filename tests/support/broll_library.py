"""A library of footage for B-roll integration tests: real assets, synthetic analyses.

The video files are tiny real MP4s imported through the real Media Library; what each "shows" is a
hand-made ``VideoAnalysis`` stored the way the video analyzer stores its own (a derived library
document), so the production path (library search, shot index, relationships, catalogs) runs
unchanged while no vision model is needed. Nothing here says how well B-roll works on real footage.
"""

from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from media_house.bootstrap.application import Application
from media_house.core.application.ports import Clock
from media_house.modules.asset_relationships.application.contracts import RelationshipCatalog
from media_house.modules.audio_intelligence.application.contracts import AudioCatalog
from media_house.modules.audio_visual_alignment.application.contracts import AlignmentCatalog
from media_house.modules.broll_intelligence.application.analyze_broll import AnalyzeBroll
from media_house.modules.broll_intelligence.application.pool import LibraryPool
from media_house.modules.broll_intelligence.application.retrieval import Retriever
from media_house.modules.broll_intelligence.infrastructure.numpy_vectors import NumpyVectorIndexer
from media_house.modules.content_intelligence.application.contracts import (
    AnalyzeContentCommand,
    ContentAnalyzer,
    ContentCatalog,
    ContentConfig,
    Profile,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    MediaAssetDto,
    MediaLibrary,
)
from media_house.modules.pacing_intelligence.application.contracts import PacingCatalog
from media_house.modules.story_architecture.application.contracts import StoryCatalog
from media_house.modules.video_intelligence.application.contracts import (
    Evidence,
    ShotIndexCatalog,
    VideoAnalysis,
    VideoCatalog,
    analysis_to_json,
)
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.observations import (
    Event,
    TextItem,
    TextObservation,
)
from media_house.modules.video_intelligence.domain.values import (
    RESULT_OPERATION,
    AnalyzerId,
    AnalyzerState,
    EventKind,
)
from media_house.shared.concurrency import CancellationToken, JobContext
from media_house.shared.errors import Ok
from media_house.shared.filesystem import AppPaths
from tests.support import vi_signals as vs
from tests.support.ci_texts import lines_to_transcript
from tests.support.media_factories import make_video, make_wav


def import_video(app: Application, folder: Path, name: str, seconds: int) -> MediaAssetDto:
    """A tiny real MP4 (``seconds`` makes the content, and so the asset, distinct)."""
    folder.mkdir(parents=True, exist_ok=True)
    imported = app.container.resolve(MediaLibrary).import_file(
        make_video(folder / f"{name}.mp4", seconds=seconds), display_name=name
    )
    assert isinstance(imported, Ok), imported
    return imported.value.asset


def footage_analysis(
    asset: MediaAssetDto,
    *,
    description: str | None = None,
    objects: Sequence[str] = (),
    texts: Sequence[str] = (),
    embedding: str | None = None,
    gestures: Sequence[float] = (),
) -> VideoAnalysis:
    """A one-shot, four-second analysis of ``asset`` that says what the shot shows."""
    signals = (
        {AnalyzerId.EMBEDDINGS: vs.embeddings([0, 40, 80], [vs.label_vector(embedding)] * 3)}
        if embedding
        else None
    )
    base = vs.analysis(vs.shot_signals(120), signals=signals)
    shot = base.shots[0]
    frame = shot.range.start
    meaning = shot.meaning
    if description or objects or embedding:
        meaning = replace(
            shot.meaning,
            state=AnalyzerState.OK,
            confidence=0.8,
            evidence=(Evidence(frames=(frame,), metric="description"),),
            reasons=(),
            description=description,
            objects=tuple(objects),
        )
    text = shot.text
    if texts:
        text = TextObservation(
            state=AnalyzerState.OK,
            confidence=0.9,
            evidence=(Evidence(frames=(frame,), metric="ocr"),),
            items=tuple(
                TextItem(
                    text=t,
                    first=shot.range.start,
                    last=shot.range.end,
                    box=BBox(0.1, 0.1, 0.9, 0.2),
                    confidence=0.9,
                )
                for t in texts
            ),
        )
    events = tuple(
        Event(
            state=AnalyzerState.OK,
            confidence=0.8,
            evidence=(Evidence(frames=(shot.range.start,), metric="gesture"),),
            event_id=f"ev{n}",
            kind=EventKind.GESTURE,
            shot_id=shot.shot_id,
            time=_at(shot, seconds),
            strength=0.7,
        )
        for n, seconds in enumerate(gestures)
    )
    return replace(
        base,
        asset_id=asset.id,
        asset_checksum=asset.checksum,
        shots=(replace(shot, meaning=meaning, text=text),),
        events=events,
    )


def _at(shot, seconds: float):  # type: ignore[no-untyped-def]
    start = shot.range.start
    rate = start.timebase
    pts = round(seconds / rate.value)
    return replace(start, frame=round(seconds * 30), pts=pts)


def store_analysis(app: Application, asset: MediaAssetDto, analysis: VideoAnalysis) -> None:
    """Store ``analysis`` where the video catalog looks for the asset's analyses."""
    library = app.container.resolve(MediaLibrary)
    stored = DerivedDocuments(library, app.container.resolve(AppPaths)).store(
        asset,
        analysis_to_json(analysis),
        filename="analysis.json",
        spec=DocumentSpec(RESULT_OPERATION, 1, {"case": "broll", "asset": asset.id}),
        display_name="video analysis",
        metadata={},
    )
    assert isinstance(stored, Ok), stored


LINES = [
    "Welcome to the show.",
    "First you boil the water, then you add the beans, after that you stir for a minute.",
    "In the city of Hamburg the old harbor is located near the station.",
    "Subscribe to the channel and click the link below.",
]
POT = "a pot of water boiling on a stove while beans are added"
HARBOR = "an old harbor in Hamburg with cranes and ships"


class ToyVisualEncoder:
    """Words into the toy embedding space the test footage was embedded in."""

    def identity(self) -> str:
        return "fake-clip"

    def unavailable(self) -> str | None:
        return None

    def embed(self, texts: list[str], cancellation: CancellationToken) -> list[tuple[float, ...]]:
        return [
            vs.label_vector("content:vlog" if "harbor" in t.casefold() else "content:event")
            for t in texts
        ]


class NoTextEncoder:
    def identity(self) -> str:
        return "none"

    def unavailable(self) -> str | None:
        return "not used in this test"

    def embed(self, texts: list[str], cancellation: CancellationToken) -> list[list[float]]:
        raise AssertionError("an unavailable encoder must not be called")


def import_talk(app: Application, tmp_path: Path) -> str:
    media = tmp_path / "audio"
    media.mkdir(exist_ok=True)
    imported = app.container.resolve(MediaLibrary).import_file(
        make_wav(media / "talk.wav", seconds=20)
    )
    assert isinstance(imported, Ok), imported
    asset_id = imported.value.asset.id
    analysed = app.container.resolve(ContentAnalyzer).execute(
        AnalyzeContentCommand(
            source_asset_id=asset_id,
            transcript=lines_to_transcript(LINES, seconds_per_word=0.3, gap=0.4),
            config=ContentConfig(profile=Profile.FAST),
        ),
        JobContext.detached(),
    )
    assert isinstance(analysed, Ok), analysed
    return asset_id


def analyzer(app: Application, visual: object | None = None) -> AnalyzeBroll:
    """The production wiring with the two encoders replaced by toy ones."""
    c = app.container
    return AnalyzeBroll(
        c.resolve(MediaLibrary),
        c.resolve(StoryCatalog),
        c.resolve(ContentCatalog),
        c.resolve(AudioCatalog),
        c.resolve(VideoCatalog),
        c.resolve(AlignmentCatalog),
        c.resolve(PacingCatalog),
        LibraryPool(
            c.resolve(MediaLibrary), c.resolve(ShotIndexCatalog), c.resolve(RelationshipCatalog)
        ),
        Retriever(visual or ToyVisualEncoder(), NoTextEncoder(), NumpyVectorIndexer()),  # type: ignore[arg-type]
        lambda: "fake-clip",
        lambda: None,
        c.resolve(AppPaths),
        c.resolve(Clock),
    )


def stock(app: Application, tmp_path: Path) -> dict[str, str]:
    """Three analysed clips and one clip nobody analysed."""
    folder = tmp_path / "clips"
    pot = import_video(app, folder, "kitchen", 5)
    harbor = import_video(app, folder, "harbor", 6)
    cat = import_video(app, folder, "cat", 7)
    raw = import_video(app, folder, "unanalysed", 8)
    store_analysis(
        app,
        pot,
        footage_analysis(pot, description=POT, gestures=(1.0, 2.0), embedding="content:event"),
    )
    store_analysis(
        app, harbor, footage_analysis(harbor, description=HARBOR, embedding="content:vlog")
    )
    store_analysis(
        app,
        cat,
        footage_analysis(
            cat, description="a cat sleeping on a sofa", embedding="content:talking_head"
        ),
    )
    return {"pot": pot.id, "harbor": harbor.id, "cat": cat.id, "raw": raw.id}
