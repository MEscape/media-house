"""The analyzer registry: identity, version, cost tier and dependencies of each pixel analyzer.

An analyzer reads decoded frames and stores a series of raw measurements (its *signals*). The
result is derived from the signals afterwards, so the thresholds in a profile never need a decode.

* ``depends_on``: signals the analyzer cannot run without. If one is unavailable, so is this one.
* ``optional_deps``: signals that make it cheaper or better when present (for example the face
  model only looks at frames where a person was detected) but that it can do without.

Adding an analyzer: add its id to ``AnalyzerId``, its spec below, a signal type in ``signals``, a
codec entry in ``serialization``, an engine in infrastructure and a derivation in ``derive``.
"""

from dataclasses import dataclass

from media_house.modules.video_intelligence.domain.errors import InvalidProfile
from media_house.modules.video_intelligence.domain.values import AnalyzerId, CostTier


@dataclass(frozen=True, slots=True)
class AnalyzerSpec:
    id: AnalyzerId
    #: Bump when the measured values change meaning; it is part of the analyzer's cache key.
    version: int
    cost: CostTier
    depends_on: tuple[AnalyzerId, ...]
    description: str
    optional_deps: tuple[AnalyzerId, ...] = ()
    #: Which decoded frames it reads: ``dense`` (every frame, grey, small), ``gray`` (sampled, grey)
    #: or ``rgb`` (sampled, colour).
    stream: str = "gray"

    @property
    def inputs(self) -> tuple[AnalyzerId, ...]:
        return (*self.depends_on, *self.optional_deps)


_S = AnalyzerId.SHOTS

ANALYZERS: dict[AnalyzerId, AnalyzerSpec] = {
    spec.id: spec
    for spec in (
        AnalyzerSpec(
            _S,
            1,
            CostTier.CHEAP,
            (),
            "Per-frame brightness, contrast and change over every frame.",
            stream="dense",
        ),
        AnalyzerSpec(
            AnalyzerId.QUALITY,
            1,
            CostTier.CHEAP,
            (_S,),
            "Sharpness, noise, exposure and clipping of the sampled frames.",
        ),
        AnalyzerSpec(
            AnalyzerId.MOTION,
            1,
            CostTier.CHEAP,
            (_S,),
            "Global camera translation, scale and rotation, and residual picture motion.",
        ),
        AnalyzerSpec(
            AnalyzerId.SALIENCY,
            1,
            CostTier.CHEAP,
            (_S,),
            "Where the eye is drawn: spectral-residual saliency of the sampled frames.",
        ),
        AnalyzerSpec(
            AnalyzerId.GEOMETRY,
            1,
            CostTier.CHEAP,
            (_S,),
            "Horizon and vertical-line tilt, colour balance and light of colour frames.",
            optional_deps=(AnalyzerId.ENTITIES,),
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.ENTITIES,
            1,
            CostTier.LOCAL_MODEL,
            (_S,),
            "People, animals, vehicles and objects in colour frames (local detector).",
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.FACES,
            1,
            CostTier.LOCAL_MODEL,
            (_S,),
            "Faces: box, head pose, gaze, eyes, smile and mouth cues (local landmark model).",
            optional_deps=(AnalyzerId.ENTITIES,),
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.BODY,
            1,
            CostTier.LOCAL_MODEL,
            (_S,),
            "Body pose and hand landmarks (local landmark models).",
            optional_deps=(AnalyzerId.ENTITIES,),
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.TEXT,
            1,
            CostTier.LOCAL_MODEL,
            (_S,),
            "On-screen text by OCR (local engine).",
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.EMBEDDINGS,
            1,
            CostTier.LOCAL_MODEL,
            (_S,),
            "One image embedding per analysed frame plus the zero-shot vocabulary embeddings.",
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.APPEARANCE,
            1,
            CostTier.LOCAL_MODEL,
            (_S, AnalyzerId.ENTITIES),
            "Appearance embedding of each detected person (anonymous identity evidence).",
            stream="rgb",
        ),
        AnalyzerSpec(
            AnalyzerId.DESCRIPTIONS,
            1,
            CostTier.VLM,
            (_S,),
            "Visible-content descriptions of sparse keyframes by a vision-language model.",
            stream="rgb",
        ),
    )
}


def run_order(requested: tuple[AnalyzerId, ...]) -> tuple[AnalyzerId, ...]:
    """``requested`` plus every REQUIRED dependency, dependencies first (stable order).

    Optional dependencies are run first only when the profile asks for them itself.
    """
    ordered: list[AnalyzerId] = []
    wanted = set(requested)

    def visit(analyzer: AnalyzerId, trail: tuple[AnalyzerId, ...]) -> None:
        if analyzer in ordered:
            return
        if analyzer in trail:
            raise InvalidProfile(f"analyzer dependency cycle at {analyzer.value}")
        spec = ANALYZERS[analyzer]
        for dependency in (*spec.depends_on, *(d for d in spec.optional_deps if d in wanted)):
            visit(dependency, (*trail, analyzer))
        ordered.append(analyzer)

    for analyzer in requested:
        visit(analyzer, ())
    return tuple(ordered)
