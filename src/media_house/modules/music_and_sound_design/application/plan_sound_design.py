"""Use case: a ``SoundtrackTimeline`` (+ library, + stored upstream results) -> a stored plan.

Nothing is transcribed, detected or rendered here. Speech and loud original moments come from
the timeline the caller built, or else from the stored audio timeline of each recording
(``AudioCatalog``); the story format and beat functions from the stored story plan
(``StoryCatalog``); held reveals from the stored pacing analysis (``PacingCatalog``). A missing
optional input is recorded (``not_available``) and the plan degrades: without speech no ducking
is planned, without a story the profile comes from the configuration.

The plan is one derived document of the edit's anchor (its first recording). The cache key holds
the timeline (after enrichment), the configuration, the library fingerprint (sounds, licences,
measurements), the person decisions and the versions: an identical request returns the stored
plan and computes nothing.
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import cast

from media_house.core.application.ports import Clock
from media_house.modules.audio_intelligence.application.contracts import (
    AudioCatalog,
    AudioIntelligenceTimeline,
)
from media_house.modules.media_library.application.contracts import (
    DerivedDocuments,
    DocumentSpec,
    JsonValue,
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.music_and_sound_design.application.results import (
    SoundDesignResults,
    SoundLibraryResults,
)
from media_house.modules.music_and_sound_design.application.views import (
    original_events,
    pacing_cues,
    speech_spans,
    with_story,
)
from media_house.modules.music_and_sound_design.domain.errors import (
    InvalidSoundConfig,
    InvalidSoundDesignPlan,
    NothingToScore,
    SoundDesignError,
)
from media_house.modules.music_and_sound_design.domain.model import (
    InputRecord,
    ItemDecision,
    SoundDesignPlan,
    StageReport,
)
from media_house.modules.music_and_sound_design.domain.planner import (
    PlanInputs,
    PlanMeta,
    cache_key,
    plan_sound_design,
)
from media_house.modules.music_and_sound_design.domain.profile import SoundDesignConfig
from media_house.modules.music_and_sound_design.domain.serialization import (
    plan_from_json,
    plan_to_json,
)
from media_house.modules.music_and_sound_design.domain.timeline import SoundtrackTimeline
from media_house.modules.music_and_sound_design.domain.validation import validate_plan
from media_house.modules.music_and_sound_design.domain.values import (
    PLANNER_VERSION,
    SOUND_DESIGN_PLAN,
    InputHow,
    StageState,
)
from media_house.modules.pacing_intelligence.application.contracts import (
    PacingAnalysis,
    PacingCatalog,
)
from media_house.modules.story_architecture.application.contracts import (
    StoryCatalog,
    StoryPlan,
)
from media_house.shared.concurrency import JobContext
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.filesystem import AppPaths
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type PlanSoundDesignError = MediaError | SoundDesignError

_STEPS = 5


@dataclass(frozen=True, slots=True)
class PlanSoundDesignCommand:
    timeline: SoundtrackTimeline
    config: SoundDesignConfig = field(default_factory=SoundDesignConfig)
    #: A person's verdicts; those of the previous plan of the same edit are carried.
    decisions: tuple[ItemDecision, ...] = ()
    carry_decisions: bool = True
    #: Tracks used by recent videos (avoids the same track in every video).
    recently_used: tuple[str, ...] = ()
    #: Read speech and loud original moments from stored audio timelines when the DTO has none.
    use_audio_intelligence: bool = True
    use_story: bool = True
    use_pacing: bool = True
    #: Results the caller already holds (used as given).
    audio_timelines: Mapping[str, AudioIntelligenceTimeline] = field(default_factory=dict)
    story_plan: StoryPlan | None = None
    pacing: PacingAnalysis | None = None


@dataclass(frozen=True, slots=True)
class SoundDesignResult:
    plan: SoundDesignPlan
    #: The stored plan document (a derived asset of the anchor recording).
    asset: MediaAssetDto
    #: ``False`` when the identical plan already existed and nothing was planned.
    created: bool


class PlanSoundDesign:
    def __init__(
        self,
        library: MediaLibrary,
        sounds: SoundLibraryResults,
        audio: AudioCatalog,
        stories: StoryCatalog,
        pacing: PacingCatalog,
        paths: AppPaths,
        clock: Clock,
    ) -> None:
        self._library = library
        self._sounds = sounds
        self._audio = audio
        self._stories = stories
        self._pacing = pacing
        self._clock = clock
        self._documents = DerivedDocuments(library, paths)
        self._results = SoundDesignResults(library)

    def execute(
        self, command: PlanSoundDesignCommand, ctx: JobContext
    ) -> Result[SoundDesignResult, PlanSoundDesignError]:
        anchor = command.timeline.anchor_asset_id
        if anchor is None:
            return Err(NothingToScore())
        anchor_asset = self._library.get(anchor)
        if isinstance(anchor_asset, Err):
            return anchor_asset
        records: list[InputRecord] = []
        stages: list[StageReport] = []
        ctx.progress.report(1, _STEPS, "Reading speech, story and pacing")
        timeline = self._enrich(command, anchor, records, stages)
        try:
            profile = command.config.resolve(timeline.story_format)
        except InvalidSoundConfig as exc:
            return Err(exc)

        ctx.progress.report(2, _STEPS, "Reading the sound library")
        index = self._sounds.index()
        stages.append(
            StageReport(
                stage="sound_library",
                state=StageState.OK if len(index) else StageState.NOT_AVAILABLE,
                detail=f"{len(index)} sounds,"
                f" {sum(1 for s in index.sounds if s.measured)} measured",
            )
        )
        edit_id = timeline.edit.edit_plan_id if timeline.edit else None
        previous = self._results.latest(
            anchor, edit_plan_id=edit_id or "none", profile=profile.name
        )
        prior = previous.value[0] if isinstance(previous, Ok) and previous.value else None
        inputs = PlanInputs(
            timeline=timeline,
            index=index,
            decisions=_decisions(command, prior),
            recently_used=frozenset(command.recently_used),
        )
        key = cache_key(inputs, command.config, profile)
        spec = DocumentSpec(SOUND_DESIGN_PLAN, PLANNER_VERSION, _json(key))
        fingerprint = self._documents.fingerprint(anchor, spec)
        if isinstance(fingerprint, Err):
            return fingerprint
        cached = self._documents.load(anchor, fingerprint.value, plan_from_json)
        if cached is not None:
            _log.info("Sound design plan reused", asset_id=cached[1].id, cache_hit=True)
            return Ok(SoundDesignResult(cached[0], cached[1], created=False))

        ctx.raise_if_cancelled()
        ctx.progress.report(3, _STEPS, "Planning music and sound effects")
        version, supersedes = self._history(anchor, edit_id or "none", profile.name)
        plan = plan_sound_design(
            inputs,
            command.config,
            profile,
            now=self._clock.now(),
            meta=PlanMeta(
                plan_version=version,
                supersedes=supersedes,
                inputs=tuple(records),
                stages=tuple(stages),
            ),
        )
        ctx.progress.report(4, _STEPS, "Checking the plan")
        critical = [i.message for i in validate_plan(plan)]
        if critical:
            _log.error("Sound design plan rejected its own result", problems=critical[:5])
            return Err(InvalidSoundDesignPlan(critical))

        ctx.progress.report(_STEPS, _STEPS, "Saving")
        stored = self._documents.store(
            anchor_asset.value,
            plan_to_json(plan),
            filename="sound_design_plan.json",
            spec=spec,
            display_name=f"{anchor_asset.value.display_name} (sound design {profile.name})",
            metadata={
                "profile": profile.name,
                "edit_plan_id": edit_id or "none",
                "plan_version": plan.plan_version,
                "plan_id": plan.plan_id,
                "plan_hash": plan.plan_hash,
                "state": plan.state.value,
                "music_cues": sum(1 for c in plan.music if c.segments),
                "sound_effects": len(plan.sfx),
                "missing_assets": len(plan.missing_assets),
                "credits": len(plan.credits),
            },
        )
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Sound design plan created",
            asset_id=stored.value.id,
            profile=profile.name,
            music_cues=sum(1 for c in plan.music if c.segments),
            sound_effects=len(plan.sfx),
            declined=len(plan.no_sfx),
            missing=len(plan.missing_assets),
        )
        return Ok(SoundDesignResult(plan, stored.value, created=True))

    # --- inputs ------------------------------------------------------------------------------
    def _enrich(
        self,
        command: PlanSoundDesignCommand,
        anchor: str,
        records: list[InputRecord],
        stages: list[StageReport],
    ) -> SoundtrackTimeline:
        timeline = command.timeline
        if timeline.speech or timeline.original_events:
            records.append(InputRecord(kind="speech", how=InputHow.SUPPLIED))
        elif command.use_audio_intelligence:
            audio = self._audio_of(command, timeline, records)
            timeline = replace(
                timeline,
                speech=speech_spans(timeline, audio),
                original_events=original_events(timeline, audio),
            )
        stages.append(
            StageReport(
                stage="speech",
                state=StageState.OK if timeline.speech else StageState.NOT_AVAILABLE,
                detail=""
                if timeline.speech
                else "no speech on the timeline: no ducking under speech is planned",
            )
        )
        story = command.story_plan
        how = InputHow.SUPPLIED if story is not None else InputHow.NOT_AVAILABLE
        detail = "no story plan: the profile comes from the configuration"
        if story is None and command.use_story:
            found = self._stories.latest(anchor)
            if isinstance(found, Ok) and found.value is not None:
                story, how, detail = found.value[0], InputHow.REUSED, found.value[1].id
        if story is not None:
            timeline = with_story(timeline, story)
        records.append(
            InputRecord(
                kind="story_plan",
                how=how,
                identity=None if story is None else story.story_plan_id,
                detail=detail if story is None or how is InputHow.REUSED else "",
            )
        )
        if not timeline.pacing and command.use_pacing:
            pacing = command.pacing
            how = InputHow.SUPPLIED if pacing is not None else InputHow.NOT_AVAILABLE
            detail = "no pacing analysis: no held reveal informs the music"
            if pacing is None:
                found_pacing = self._pacing.latest(anchor)
                if isinstance(found_pacing, Ok) and found_pacing.value is not None:
                    pacing, how, detail = (
                        found_pacing.value[0],
                        InputHow.REUSED,
                        found_pacing.value[1].id,
                    )
            if pacing is not None:
                timeline = replace(timeline, pacing=pacing_cues(timeline, pacing))
            records.append(
                InputRecord(
                    kind="pacing_analysis",
                    how=how,
                    detail=detail if pacing is None or how is InputHow.REUSED else "",
                )
            )
        return timeline

    def _audio_of(
        self,
        command: PlanSoundDesignCommand,
        timeline: SoundtrackTimeline,
        records: list[InputRecord],
    ) -> dict[str, AudioIntelligenceTimeline]:
        out: dict[str, AudioIntelligenceTimeline] = {}
        for asset_id in timeline.source_asset_ids:
            if asset_id in command.audio_timelines:
                out[asset_id] = command.audio_timelines[asset_id]
                records.append(
                    InputRecord(kind="audio_timeline", how=InputHow.SUPPLIED, asset_id=asset_id)
                )
                continue
            found = self._audio.latest(asset_id)
            if isinstance(found, Ok) and found.value is not None:
                out[asset_id] = found.value[0]
                records.append(
                    InputRecord(
                        kind="audio_timeline",
                        how=InputHow.REUSED,
                        asset_id=asset_id,
                        identity=found.value[1].id,
                    )
                )
            else:
                records.append(
                    InputRecord(
                        kind="audio_timeline",
                        how=InputHow.NOT_AVAILABLE,
                        asset_id=asset_id,
                        detail="no stored audio timeline: no speech or loud moments known",
                    )
                )
        return out

    def _history(self, anchor: str, edit_id: str, profile: str) -> tuple[int, str | None]:
        """1 for the first plan of this edit and profile; later ones count up and name their
        predecessor. Earlier plans are never changed or removed."""
        earlier = [
            a
            for a in self._library.list_derived(anchor)
            if a.metadata.get("processing_type") == SOUND_DESIGN_PLAN
            and a.metadata.get("edit_plan_id") == edit_id
            and a.metadata.get("profile") == profile
        ]
        if not earlier:
            return 1, None
        newest = max(earlier, key=lambda a: (a.created_at, a.id))
        return len(earlier) + 1, newest.id


def _json(key: Mapping[str, object]) -> Mapping[str, JsonValue]:
    return cast("Mapping[str, JsonValue]", json.loads(json.dumps(key, default=str)))


def _decisions(
    command: PlanSoundDesignCommand, previous: SoundDesignPlan | None
) -> tuple[ItemDecision, ...]:
    chosen = {d.item_id: d for d in command.decisions}
    if command.carry_decisions and previous is not None:
        for d in previous.decisions:
            chosen.setdefault(d.item_id, d)
    return tuple(sorted(chosen.values(), key=lambda d: d.item_id))
