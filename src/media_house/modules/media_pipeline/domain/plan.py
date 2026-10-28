"""Which tasks an asset needs and in what order they may run.

The stage table is the one place that says what depends on what. Each rule was read from the
owning module, not guessed:

* ``audio_intelligence`` and ``video_intelligence`` optionally reuse a stored inspection, so they
  WANT it (they wait for it, but run without it).
* ``content_intelligence`` reads the transcript ``audio_intelligence`` stores, so it NEEDS audio
  analysis. Waiting for video analysis would hold it back for a long decode, so that edge exists
  only when the run asks for video context.
* ``audio_visual_alignment`` relates audio and video analyses, so it NEEDS both and WANTS the
  content analysis, which it merely refines with.
* ``asset_relationships`` detection reads the STORED inspection of the asset, so it WANTS the
  inspection (it never probes) and nothing else: it is opt-in and no other stage waits for it.
* ``story_architecture`` plans the story from the content analysis, so it NEEDS that, and WANTS
  the alignment (it only attaches shots to visual opportunities, and reads a stored one itself).
* ``pacing`` analyses the rhythm of the speech and the picture, so it NEEDS the audio analysis; it
  WANTS the content analysis, video analysis, alignment, story plan and inspection (it reads stored
  ones itself and only waits so they are not missed). It is a pure read of stored results, so it is
  part of the default stages.
* ``edit_plan`` plans the edit from the story plan, so it NEEDS that; it WANTS the alignment, the
  video analysis, the pacing analysis and the inspection (it reads stored ones itself, and only
  waits so they are not missed). It is opt-in: see ``DEFAULT_STAGES``.
* Inspection joins every non-empty run (see ``plan_asset``): it is cheap, cached, and the only
  authority on which streams exist. A stage only WANTS it, so a failed inspection never blocks.
* Improvement stages create new assets nothing downstream reads: analyses run on the original,
  so improvement never delays them.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from media_house.modules.media_pipeline.domain.graph import TaskSpec
from media_house.modules.media_pipeline.domain.values import Lane, TaskId, TaskKind


@dataclass(frozen=True, slots=True)
class Stage:
    lane: Lane
    needs: frozenset[TaskKind] = frozenset()
    wants: frozenset[TaskKind] = frozenset()
    needs_video: bool = False
    needs_audio: bool = False


def _k(*kinds: TaskKind) -> frozenset[TaskKind]:
    return frozenset(kinds)


STAGES: Mapping[TaskKind, Stage] = {
    TaskKind.INSPECT: Stage(Lane.LIGHT),
    TaskKind.THUMBNAIL: Stage(Lane.LIGHT, needs_video=True),
    TaskKind.PROXY: Stage(Lane.CPU, wants=_k(TaskKind.INSPECT), needs_video=True),
    TaskKind.AUDIO_IMPROVEMENT: Stage(Lane.CPU, wants=_k(TaskKind.INSPECT), needs_audio=True),
    TaskKind.VIDEO_IMPROVEMENT: Stage(Lane.CPU, wants=_k(TaskKind.INSPECT), needs_video=True),
    TaskKind.AUDIO_ANALYSIS: Stage(Lane.GPU, wants=_k(TaskKind.INSPECT), needs_audio=True),
    TaskKind.VIDEO_ANALYSIS: Stage(Lane.CPU, wants=_k(TaskKind.INSPECT), needs_video=True),
    TaskKind.CONTENT_ANALYSIS: Stage(Lane.GPU, needs=_k(TaskKind.AUDIO_ANALYSIS), needs_audio=True),
    TaskKind.ALIGNMENT: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.AUDIO_ANALYSIS, TaskKind.VIDEO_ANALYSIS),
        wants=_k(TaskKind.CONTENT_ANALYSIS, TaskKind.INSPECT),
        needs_video=True,
        needs_audio=True,
    ),
    TaskKind.RELATIONSHIPS: Stage(Lane.LIGHT, wants=_k(TaskKind.INSPECT)),
    TaskKind.PACING: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.AUDIO_ANALYSIS),
        wants=_k(
            TaskKind.CONTENT_ANALYSIS,
            TaskKind.VIDEO_ANALYSIS,
            TaskKind.ALIGNMENT,
            TaskKind.STORY,
            TaskKind.INSPECT,
        ),
        needs_audio=True,
    ),
    TaskKind.EDIT_PLAN: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.STORY),
        wants=_k(TaskKind.ALIGNMENT, TaskKind.VIDEO_ANALYSIS, TaskKind.PACING, TaskKind.INSPECT),
        needs_audio=True,
    ),
    TaskKind.STORY: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.CONTENT_ANALYSIS),
        wants=_k(TaskKind.ALIGNMENT),
        needs_audio=True,
    ),
}


@dataclass(frozen=True, slots=True)
class AssetPlan:
    asset_id: str
    specs: tuple[TaskSpec, ...]
    #: Added because a requested stage needs them.
    implied: frozenset[TaskKind]
    #: Requested, but the media cannot support them (no audio track, no picture).
    not_applicable: frozenset[TaskKind]
    #: Known stages the run did not ask for.
    disabled: frozenset[TaskKind]


def plan_asset(
    asset_id: str,
    *,
    has_video: bool,
    has_audio: bool,
    requested: Iterable[TaskKind],
    content_uses_video: bool = False,
) -> AssetPlan:
    asked = frozenset(requested)

    def supported(kind: TaskKind) -> bool:
        stage = STAGES[kind]
        return (has_video or not stage.needs_video) and (has_audio or not stage.needs_audio)

    # A stage is applicable only if the media supports it and so does everything it needs.
    applicable = {k for k in STAGES if supported(k)}
    changed = True
    while changed:
        changed = False
        for kind in tuple(applicable):
            if not STAGES[kind].needs <= applicable:
                applicable.discard(kind)
                changed = True

    selected = {k for k in asked if k in applicable}
    pending = list(selected)
    while pending:
        for need in STAGES[pending.pop()].needs:
            if need not in selected:
                selected.add(need)
                pending.append(need)

    # Inspection is the ground truth about which streams exist. Every other stage WANTS it, so it
    # always joins the graph: a stage must never have to guess whether, say, audio is present.
    if selected:
        selected.add(TaskKind.INSPECT)

    def tid(kind: TaskKind) -> TaskId:
        return TaskId(asset_id, kind)

    specs: list[TaskSpec] = []
    for kind in TaskKind:
        if kind not in selected:
            continue
        stage = STAGES[kind]
        wants = set(stage.wants)
        if kind is TaskKind.CONTENT_ANALYSIS and content_uses_video:
            wants.add(TaskKind.VIDEO_ANALYSIS)
        specs.append(
            TaskSpec(
                id=tid(kind),
                lane=stage.lane,
                needs=frozenset(tid(n) for n in stage.needs),
                wants=frozenset(tid(w) for w in wants if w in selected),
            )
        )
    return AssetPlan(
        asset_id=asset_id,
        specs=tuple(specs),
        implied=frozenset(selected - asked),
        not_applicable=frozenset(k for k in asked if k in STAGES and k not in applicable),
        disabled=frozenset(k for k in STAGES if k not in selected and k not in asked),
    )
