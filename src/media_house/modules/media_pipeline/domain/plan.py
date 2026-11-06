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
* ``broll`` finds supplementary footage for the passages that need it, so it NEEDS the content
  analysis (the passages); it WANTS the story plan, alignment, video analysis, pacing analysis and
  inspection (it reads stored ones itself and only waits so they are not missed). The footage it
  searches is the whole library's STORED shot indexes, which no task of this run produces. It is
  opt-in: a library-wide read is cheap per asset but grows with the library, and a recommendation
  only means something once a person has chosen to look for footage.
* ``continuity`` judges the cuts of the PROPOSED edit, so it NEEDS the edit plan; it WANTS the
  B-roll analysis, alignment, video and audio analyses and the inspection (it reads stored ones
  itself and only waits so they are not missed). It is opt-in and runs AFTER the edit plan: the
  editing stage reads its stored result on its next planning, so a second run uses what the first
  found.
* ``reframing`` recommends a crop per shot for ONE target format, so it NEEDS the video analysis
  (the tracks it places a window on); it WANTS the story plan, alignment and inspection (it reads
  stored ones itself and only waits so they are not missed). It is opt-in: it means nothing
  without a chosen target format. It does NOT want the continuity analysis (that runs after the
  edit plan, which wants this): it reads a stored one if there is one.
* ``edit_plan`` plans the edit from the story plan, so it NEEDS that; it WANTS the alignment, the
  video analysis, the pacing analysis, the B-roll analysis, the framing analysis and the
  inspection (it reads stored ones itself, and only waits so they are not missed). It is opt-in:
  see ``DEFAULT_STAGES``.
* Layer 4 (all opt-in; none is in ``DEFAULT_STAGES``: each stores plans and renders files that
  only mean something for a chosen delivery format). Every Layer 4 task WANTS the edit plan and
  NEEDS the inspection: with an edit it works on the edit; without one (no speech, or no edit
  asked for) it works on the whole recording from the inspection (the no-speech path). An edit
  that was asked for and FAILED is never silently replaced by the whole recording: the executor
  reports it. Nothing before ``edit_plan`` waits for Layer 4, so there is no cycle.
* ``visual_style`` resolves the channel identity for the delivery format: the ONE place the
  format profile is chosen. It WANTS the edit plan (project and story format) and nothing else;
  every other Layer 4 task NEEDS it, so requesting any of them resolves the style first.
* ``graphics`` (CPU: HyperFrames renders) WANTS the story plan (section labels, beats) and the
  audio analysis (the words of each section, read for prices, ranks and specs).
* ``captions`` (CPU: renders through motion graphics) WANTS ``graphics`` so a fact a graphic
  already shows is not repeated, and the audio, content, video and framing analyses it reads.
  It needs an audio track and a picture.
* ``transitions`` (CPU: graphic overlays are rendered) WANTS ``graphics`` (the end screen and
  membership plug it bookends), the continuity, pacing, audio and video analyses it reads.
* ``vfx`` (CPU: FFmpeg renders) WANTS the video analysis (handed in memory), the audio analysis
  (speech spans it must not retime) and the framing analysis.
* ``sound_design`` (CPU: the stem render) WANTS ``graphics``, ``transitions`` and ``captions``
  (their events become sync points), the story plan, pacing and audio analyses. It needs no
  picture: music under a voice recording is still sound design.
* ``timeline`` (Layer 5; LIGHT: it only reads stored results and verifies files) compiles the
  edit and every Layer 4 plan into the ``timeline_specification``. Like every Layer 4 task it
  NEEDS the style and the inspection and WANTS the edit plan; it also WANTS the story plan, the
  reframing analysis (crops) and ALL Layer 4 plans so it sees them. It waits for them but never
  hides a failure: a Layer 4 plan that was part of the run and did not deliver is handed to the
  compiler as undelivered, which blocks the timeline (a failed upstream is never a success).
  Nothing waits for it, so there is no cycle. It does not need a picture: a voice recording
  with music is a timeline too.
* ``timeline_build`` (Layer 5; LIGHT: it builds in memory and stores one document) NEEDS the
  ``timeline`` task and takes its specification in memory; nothing else. It is not in any
  default set (``--stages timeline_build`` implies ``timeline`` and what it needs). A
  specification that was not compiled makes it ``skipped_dependency``, never a build of an old one.
* ``resolve_execute`` (Layer 5; LIGHT: the work happens inside Resolve) NEEDS ``timeline_build`` and
  takes the built timeline in memory. It is opt-in and in no group of stages: it drives another
  application, so a run only does it when asked (``--stages resolve_execute``). Resolve not being
  installed or running makes it ``skipped_unavailable`` (nothing failed, nothing was done).
* ``audio_mix`` (Layer 5; CPU: FFmpeg renders the stems and the mix) NEEDS ``timeline_build`` and
  takes the built timeline in memory. Opt-in and in no group: it renders audio files, so a run only
  does it when asked (``--stages audio_mix``). A timeline with no sound to mix is
  ``skipped_unavailable``. Nothing waits for it and it needs neither Resolve nor
  ``resolve_execute``: the mix is independent of any editing application.
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


_LAYER4_NEEDS = _k(TaskKind.VISUAL_STYLE, TaskKind.INSPECT)


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
    TaskKind.BROLL: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.CONTENT_ANALYSIS),
        wants=_k(
            TaskKind.STORY,
            TaskKind.ALIGNMENT,
            TaskKind.AUDIO_ANALYSIS,
            TaskKind.VIDEO_ANALYSIS,
            TaskKind.PACING,
            TaskKind.INSPECT,
        ),
        needs_audio=True,
    ),
    TaskKind.CONTINUITY: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.EDIT_PLAN),
        wants=_k(
            TaskKind.BROLL,
            TaskKind.ALIGNMENT,
            TaskKind.AUDIO_ANALYSIS,
            TaskKind.VIDEO_ANALYSIS,
            TaskKind.INSPECT,
        ),
        needs_audio=True,
    ),
    TaskKind.REFRAMING: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.VIDEO_ANALYSIS),
        wants=_k(TaskKind.STORY, TaskKind.ALIGNMENT, TaskKind.INSPECT),
        needs_video=True,
    ),
    TaskKind.EDIT_PLAN: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.STORY),
        wants=_k(
            TaskKind.ALIGNMENT,
            TaskKind.VIDEO_ANALYSIS,
            TaskKind.PACING,
            TaskKind.BROLL,
            TaskKind.REFRAMING,
            TaskKind.INSPECT,
        ),
        needs_audio=True,
    ),
    TaskKind.STORY: Stage(
        Lane.LIGHT,
        needs=_k(TaskKind.CONTENT_ANALYSIS),
        wants=_k(TaskKind.ALIGNMENT),
        needs_audio=True,
    ),
    TaskKind.VISUAL_STYLE: Stage(Lane.LIGHT, wants=_k(TaskKind.EDIT_PLAN, TaskKind.INSPECT)),
    TaskKind.GRAPHICS: Stage(
        Lane.CPU,
        needs=_LAYER4_NEEDS,
        wants=_k(
            TaskKind.EDIT_PLAN, TaskKind.STORY, TaskKind.AUDIO_ANALYSIS, TaskKind.VIDEO_ANALYSIS
        ),
        needs_video=True,
    ),
    TaskKind.CAPTIONS: Stage(
        Lane.CPU,
        needs=_LAYER4_NEEDS,
        wants=_k(
            TaskKind.EDIT_PLAN,
            TaskKind.STORY,
            TaskKind.GRAPHICS,
            TaskKind.AUDIO_ANALYSIS,
            TaskKind.CONTENT_ANALYSIS,
            TaskKind.VIDEO_ANALYSIS,
            TaskKind.REFRAMING,
        ),
        needs_video=True,
        needs_audio=True,
    ),
    TaskKind.TRANSITIONS: Stage(
        Lane.CPU,
        needs=_LAYER4_NEEDS,
        wants=_k(
            TaskKind.EDIT_PLAN,
            TaskKind.STORY,
            TaskKind.GRAPHICS,
            TaskKind.CONTINUITY,
            TaskKind.PACING,
            TaskKind.AUDIO_ANALYSIS,
            TaskKind.VIDEO_ANALYSIS,
        ),
        needs_video=True,
    ),
    TaskKind.VFX: Stage(
        Lane.CPU,
        needs=_LAYER4_NEEDS,
        wants=_k(
            TaskKind.EDIT_PLAN,
            TaskKind.AUDIO_ANALYSIS,
            TaskKind.VIDEO_ANALYSIS,
            TaskKind.REFRAMING,
        ),
        needs_video=True,
    ),
    TaskKind.SOUND_DESIGN: Stage(
        Lane.CPU,
        needs=_LAYER4_NEEDS,
        wants=_k(
            TaskKind.EDIT_PLAN,
            TaskKind.STORY,
            TaskKind.PACING,
            TaskKind.AUDIO_ANALYSIS,
            TaskKind.GRAPHICS,
            TaskKind.TRANSITIONS,
            TaskKind.CAPTIONS,
        ),
    ),
    TaskKind.TIMELINE: Stage(
        Lane.LIGHT,
        needs=_LAYER4_NEEDS,
        wants=_k(
            TaskKind.EDIT_PLAN,
            TaskKind.STORY,
            TaskKind.REFRAMING,
            TaskKind.GRAPHICS,
            TaskKind.CAPTIONS,
            TaskKind.TRANSITIONS,
            TaskKind.VFX,
            TaskKind.SOUND_DESIGN,
        ),
    ),
    TaskKind.TIMELINE_BUILD: Stage(Lane.LIGHT, needs=_k(TaskKind.TIMELINE)),
    TaskKind.RESOLVE_EXECUTE: Stage(Lane.LIGHT, needs=_k(TaskKind.TIMELINE_BUILD)),
    TaskKind.AUDIO_MIX: Stage(Lane.CPU, needs=_k(TaskKind.TIMELINE_BUILD)),
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
