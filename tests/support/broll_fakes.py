"""Builders for B-roll tests: passages, shots and a one-call run of the pure analysis.

Everything is a plain fact, so a scenario is a few lines: a passage that says what it needs, a
library of shots that say what they show, and (when a test is about the embedding models) the
similarities those models would have returned.
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from fractions import Fraction

from media_house.modules.broll_intelligence.domain.engine import AnalysisMeta, analyse_broll
from media_house.modules.broll_intelligence.domain.facts import (
    EventFact,
    MainPicture,
    Match,
    NameFact,
    PoolFact,
    ReferenceFact,
    RelationFact,
    RetrievalFacts,
    ShotFact,
    SoundActionFact,
    SourceAudioFact,
    TargetFact,
)
from media_house.modules.broll_intelligence.domain.model import (
    BrollAnalysis,
    Candidate,
    InputRecord,
)
from media_house.modules.broll_intelligence.domain.profile import BrollConfig
from media_house.modules.broll_intelligence.domain.retrieve import (
    build_index,
    lexical_candidates,
)
from media_house.modules.broll_intelligence.domain.values import (
    AssetVia,
    ClockBasis,
    InputHow,
    TargetOrigin,
)

NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)
GOOD = (("technical_usability", 0.9), ("visual_quality", 0.8), ("subject_visibility", 0.8))


def name(text: str) -> NameFact:
    """``name("Pixel 9")`` -> family ``pixel``, value ``9`` (a bare code has no family)."""
    first, _, rest = text.partition(" ")
    if not rest:
        return NameFact(family="", value=text.casefold(), text=text)
    return NameFact(family=first.casefold(), value=rest.casefold(), text=text)


def target(
    target_id: str = "t1",
    text: str = "Now I sand the board until it is smooth.",
    *,
    start: float = 10.0,
    end: float = 16.0,
    origin: TargetOrigin = TargetOrigin.STORY_VISUAL,
    purpose: str = "demonstration",
    necessity: str = "optional",
    concept: str = "sand the board",
    category: str = "",
    asset_id: str = "talk",
    names: Sequence[NameFact] = (),
    references: Sequence[ReferenceFact] = (),
    main: MainPicture | None = None,
    context: Sequence[str] = (),
    **changes: object,
) -> TargetFact:
    base = TargetFact(
        target_id=target_id,
        origin=origin,
        asset_id=asset_id,
        start=start,
        end=end,
        text=text,
        context=tuple(context),
        concept=concept,
        purpose=purpose,
        category=category,
        necessity=necessity,
        strength=0.8,
        story_visual_id=f"vis_{target_id}" if origin is TargetOrigin.STORY_VISUAL else None,
        section_id="sec1",
        beat_id="beat1",
        segment_id="seg1",
        names=tuple(names),
        references=tuple(references),
        main=main or MainPicture(shot_id="m1", speaking_face=False, environment="studio"),
        importance=0.6,
    )
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


def secs(value: float) -> Fraction:
    return Fraction(value).limit_denominator(1000)


def shot(
    asset_id: str = "lib1",
    shot_id: str = "s1",
    *,
    start: float = 0.0,
    end: float = 10.0,
    name_: str = "clip",
    description: str | None = None,
    objects: Iterable[str] = (),
    texts: Iterable[str] = (),
    names: Iterable[tuple[NameFact, str]] = (),
    scores: Iterable[tuple[str, float]] = GOOD,
    events: Iterable[tuple[str, float]] = (),
    static_fraction: float | None = None,
    framing: str | None = "medium",
    environment: str | None = None,
    audio: SourceAudioFact | None = None,
    sound_actions: Iterable[SoundActionFact] = (),
    relations: Iterable[RelationFact] = (),
    via: AssetVia = AssetVia.ORIGINAL,
    analyzed: str | None = None,
    clock: ClockBasis = ClockBasis.CONTAINER,
    **changes: object,
) -> ShotFact:
    base = ShotFact(
        asset_id=asset_id,
        analyzed_asset_id=analyzed or asset_id,
        via=via,
        asset_name=name_,
        shot_id=shot_id,
        index=int(shot_id.removeprefix("s") or 0) if shot_id.removeprefix("s").isdigit() else 0,
        start=secs(start),
        end=secs(end),
        clock=clock,
        asset_duration=secs(max(end, 60.0)),
        objects=tuple(objects),
        description=description,
        texts=tuple(texts),
        names=tuple(names),
        scores=tuple(scores),
        events=tuple(EventFact(kind=k, start=secs(t), strength=0.7) for k, t in events),
        static_fraction=static_fraction,
        framing=framing,
        environment=environment,
        audio=audio,
        sound_actions=tuple(sound_actions),
        relations=tuple(relations),
        analysis_identity=f"id-{asset_id}",
    )
    from dataclasses import replace

    return replace(base, **changes)  # type: ignore[arg-type]


def visual(z: float, similarity: float = 0.3) -> Match:
    """What the picture embedding said: an unusually close match has a high standard score."""
    return Match(visual_similarity=similarity, visual_z=z)


def meta(story_format: str | None = None, with_story: bool = False) -> AnalysisMeta:
    from media_house.modules.broll_intelligence.domain.model import StoryRef

    return AnalysisMeta(
        broll_analysis_id="ba_test",
        analysis_version=1,
        supersedes=None,
        project_id=None,
        created_at=NOW,
        configuration_fingerprint="cfg",
        story=StoryRef(
            story_plan_id="sp1",
            plan_version=1,
            stored_as=None,
            target_format=story_format or "generic",
        )
        if with_story
        else None,
        story_format=story_format,
        sources=("talk",),
        inputs=(InputRecord(kind="content_analysis", how=InputHow.SUPPLIED, asset_id="talk"),),
        stages=(),
        warnings=(),
        pool_identity="pool",
    )


def run(
    targets: Sequence[TargetFact],
    shots: Sequence[ShotFact],
    *,
    profile: str = "generic",
    matches: Mapping[tuple[str, str], Match] | None = None,
    similar: Mapping[frozenset[str], float] | None = None,
    visual_model: str | None = None,
    skipped: Sequence[tuple[str, str]] = (),
    max_targets: int | None = None,
    with_story: bool = True,
) -> BrollAnalysis:
    index = build_index(shots)
    lexical = lexical_candidates(index, targets, 200)
    retrieval = RetrievalFacts(
        matches=matches or {},
        similar=similar or {},
        visual_model=visual_model or ("fake-clip" if matches else None),
        text_model=None,
    )
    assets = {s.asset_id for s in shots}
    pool = PoolFact(
        assets=len(assets),
        with_shots=len(assets),
        shots=len(shots),
        shots_with_embedding=sum(1 for _, k in (matches or {}) if k),
        skipped=tuple(skipped),
    )
    return analyse_broll(
        targets,
        shots,
        retrieval,
        lexical,
        pool,
        "pool",
        BrollConfig(profile=profile, max_targets=max_targets),
        meta(with_story=with_story),
    )


def first(analysis: BrollAnalysis, target_id: str = "t1") -> Candidate | None:
    rec = analysis.recommendation(target_id)
    assert rec is not None, f"no recommendation for {target_id}"
    return rec.best
