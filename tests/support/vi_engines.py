"""Fake model engines: deterministic stand-ins that obey the ``SignalAnalyzer`` port.

They let the use case be tested end to end (one decode, dependencies, tiering, caching, cascade of
unavailability) without installing models. Each remembers what it was asked to do.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

import numpy as np

from media_house.modules.video_intelligence.application.ports import (
    DecodedVideo,
    MeasureRequest,
    Signals,
)
from media_house.modules.video_intelligence.domain.geometry import BBox
from media_house.modules.video_intelligence.domain.signals import (
    AppearanceRow,
    AppearanceSignals,
    DetectionRow,
    DetectionSignals,
    EmbeddingSignals,
    FaceRow,
    FaceSignals,
)
from media_house.modules.video_intelligence.domain.values import (
    AnalyzerId,
    DeviceKind,
    EntityKind,
)
from media_house.modules.video_intelligence.domain.vocabulary import label_prompts
from media_house.modules.video_intelligence.infrastructure.numpy_picture import (
    rgb_frame,
    usable_rgb_frames,
)
from media_house.modules.video_intelligence.infrastructure.numpy_signals import own
from media_house.shared.concurrency import CancellationToken

DIM = 32


@dataclass
class FakeEngine:
    """Base: records calls, can be made unavailable, reports a configurable device."""

    analyzer: AnalyzerId
    version: str = "1"
    why_unavailable: str | None = None
    on_device: str = "cpu"
    calls: list[tuple[int, ...]] = field(default_factory=list)

    def identity(self) -> Mapping[str, str]:
        return {"fake": f"{self.analyzer.value}-{self.version}"}

    def unavailable(self, allow_downloads: bool) -> str | None:
        return self.why_unavailable

    def device(self, requested: DeviceKind) -> str:
        return self.on_device if requested is not DeviceKind.CPU else "cpu"


class FakeDetector(FakeEngine):
    """Finds one person in the frames of ``people_until`` (a frame number), nothing after it."""

    def __init__(self, people_until: int = 10**9, **kw: object) -> None:
        super().__init__(AnalyzerId.ENTITIES, **kw)  # type: ignore[arg-type]
        self.people_until = people_until

    def measure(
        self, video: DecodedVideo, request: MeasureRequest, cancellation: CancellationToken
    ) -> Signals:
        frames = own(video)
        chosen = usable_rgb_frames(frames, request.rgb_plan)
        self.calls.append(tuple(chosen))
        rows = [
            DetectionRow(f, EntityKind.PERSON, "person", 0.9, BBox(0.3, 0.1, 0.7, 0.9))
            for f in chosen
            if f < self.people_until
        ]
        return DetectionSignals("fake-detector", tuple(chosen), tuple(rows))


class FakeFaces(FakeEngine):
    """A face in every frame it is given. Remembers which frames it was given (tiering)."""

    def __init__(self, **kw: object) -> None:
        super().__init__(AnalyzerId.FACES, **kw)  # type: ignore[arg-type]

    def measure(
        self, video: DecodedVideo, request: MeasureRequest, cancellation: CancellationToken
    ) -> Signals:
        frames = own(video)
        entities = request.dependencies.get(AnalyzerId.ENTITIES)
        chosen = usable_rgb_frames(frames, request.rgb_plan)
        if isinstance(entities, DetectionSignals):
            people = {r.frame for r in entities.rows}
            looked = set(entities.frames)
            chosen = [f for f in chosen if f not in looked or f in people]
        self.calls.append(tuple(chosen))
        rows = [
            FaceRow(f, BBox(0.4, 0.15, 0.6, 0.4), 0.95, 0, 0, 0, 0, 0, 0.95, 0.95, 0.1, 0.05, 0.4)
            for f in chosen
        ]
        return FaceSignals("fake-faces", tuple(chosen), tuple(rows))


class FakeEmbedder(FakeEngine):
    """Embeds a colour frame as its coarse brightness layout: equal pictures, equal vectors."""

    def __init__(self, **kw: object) -> None:
        super().__init__(AnalyzerId.EMBEDDINGS, **kw)  # type: ignore[arg-type]

    def measure(
        self, video: DecodedVideo, request: MeasureRequest, cancellation: CancellationToken
    ) -> Signals:
        frames = own(video)
        chosen = usable_rgb_frames(frames, request.rgb_plan)
        self.calls.append(tuple(chosen))
        vectors = tuple(_vector(rgb_frame(frames, f)) for f in chosen)
        names = tuple(n for n, _ in label_prompts())
        labels = tuple(
            tuple(1.0 if j == i % DIM else 0.0 for j in range(DIM)) for i in range(len(names))
        )
        return EmbeddingSignals("fake-clip", DIM, 1, tuple(chosen), vectors, names, labels)


class FakeAppearance(FakeEngine):
    def __init__(self, **kw: object) -> None:
        super().__init__(AnalyzerId.APPEARANCE, **kw)  # type: ignore[arg-type]

    def measure(
        self, video: DecodedVideo, request: MeasureRequest, cancellation: CancellationToken
    ) -> Signals:
        entities = request.dependencies[AnalyzerId.ENTITIES]
        assert isinstance(entities, DetectionSignals)
        self.calls.append(tuple(entities.frames))
        rows = tuple(AppearanceRow(r.frame, r.box, _basis(1)) for r in entities.rows)
        return AppearanceSignals("fake-appearance", tuple(entities.frames), rows)


def _basis(index: int) -> tuple[float, ...]:
    return tuple(1.0 if i == index else 0.0 for i in range(DIM))


def _vector(picture: np.ndarray) -> tuple[float, ...]:
    """A 4 x 8 grid of mean brightness, L2-normalised (pictures with the same layout agree)."""
    grey = picture.astype(np.float64).mean(axis=2)
    height, width = grey.shape
    cells = [
        grey[r * height // 4 : (r + 1) * height // 4, c * width // 8 : (c + 1) * width // 8].mean()
        for r in range(4)
        for c in range(8)
    ]
    vector = np.array(cells) - np.mean(cells)
    norm = float(np.linalg.norm(vector)) or 1.0
    return tuple(round(float(v), 4) for v in vector / norm)
