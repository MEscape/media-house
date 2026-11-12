"""Create (or find) the audio mix specification of a built timeline.

The timeline owns where each sound is and the intended ducking, fades and crossfades; this adds
the technical behaviour of a mix profile and a delivery profile, and stores the result as one
document. An identical specification (same content hash) is found, not stored again.
"""

from dataclasses import dataclass, field

from media_house.core.application.ports import Clock
from media_house.modules.audio_mix_engine.application.store import MixStore
from media_house.modules.audio_mix_engine.application.views import to_facts
from media_house.modules.audio_mix_engine.domain.build import MixOverrides, build_spec
from media_house.modules.audio_mix_engine.domain.errors import (
    AudioMixError,
    NothingToMix,
    SpecificationInvalid,
    TimelineNotMixable,
)
from media_house.modules.audio_mix_engine.domain.profile import (
    choose_delivery,
    choose_profile,
    level_targets,
)
from media_house.modules.audio_mix_engine.domain.spec import AudioMixSpecification
from media_house.modules.audio_mix_engine.domain.values import SUPPORTED_BUILT_SCHEMAS, Severity
from media_house.modules.media_library.application.contracts import (
    MediaAssetDto,
    MediaError,
    MediaLibrary,
)
from media_house.modules.timeline_builder.application.contracts import (
    BuiltTimeline,
    BuiltTimelineCatalog,
    InvalidBuiltDocument,
)
from media_house.shared.errors import Err, Ok, Result
from media_house.shared.logging import get_logger

_log = get_logger(__name__)

type MixError = MediaError | AudioMixError | InvalidBuiltDocument


@dataclass(frozen=True, slots=True)
class MixConfig:
    #: ``None``: from the creator identity's format profile, else the default.
    profile: str | None = None
    delivery: str | None = None
    #: Changes a person recorded (bus levels, clip gains): a revision, not a rebuild.
    overrides: MixOverrides = field(default_factory=MixOverrides)
    #: Where the voice is, in timeline samples, when something more exact than the dialogue clips
    #: is known (speech analysis); ``None``: the dialogue clips.
    speech_spans: tuple[tuple[int, int], ...] | None = None
    #: Bring dialogue clips gently toward the profile's dialogue loudness.
    level_dialogue: bool = True
    #: Measure music and effects and place them against the dialogue (the profile's targets).
    level_mix: bool = True
    #: Check the stems by recombining them through the master and comparing with the mix.
    verify_recombination: bool = True
    #: How many times the master may be re-rendered to reach the loudness target.
    max_corrections: int = 2


@dataclass(frozen=True, slots=True)
class CreateSpecCommand:
    timeline: BuiltTimeline | None = None
    anchor_asset_id: str | None = None
    timeline_id: str | None = None
    config: MixConfig = field(default_factory=MixConfig)


@dataclass(frozen=True, slots=True)
class SpecResult:
    spec: AudioMixSpecification
    asset: MediaAssetDto
    created: bool
    timeline: BuiltTimeline


class CreateMixSpecification:
    def __init__(
        self,
        library: MediaLibrary,
        catalog: BuiltTimelineCatalog,
        store: MixStore,
        clock: Clock,
    ) -> None:
        self._library = library
        self._catalog = catalog
        self._store = store
        self._clock = clock

    def execute(self, command: CreateSpecCommand) -> Result[SpecResult, MixError]:
        timeline = command.timeline
        anchor_id = command.anchor_asset_id or (timeline.anchor_asset_id if timeline else None)
        if anchor_id is None:
            return Err(TimelineNotMixable(["no timeline and no anchor recording were given"]))
        anchor = self._library.get(anchor_id)
        if isinstance(anchor, Err):
            return anchor
        if timeline is None:
            latest = self._catalog.latest(anchor_id, timeline_id=command.timeline_id)
            if isinstance(latest, Err):
                return latest
            if latest.value is None:
                return Err(TimelineNotMixable(["no built timeline is stored for the recording"]))
            timeline = latest.value[0]
        if timeline.schema_version not in SUPPORTED_BUILT_SCHEMAS:
            return Err(
                TimelineNotMixable(
                    [f"the built timeline's schema {timeline.schema_version} is not readable"]
                )
            )
        facts = to_facts(timeline)
        config = command.config
        try:
            profile = choose_profile(config.profile, facts.format_profile)
            delivery = choose_delivery(config.delivery, facts.format_profile)
        except AudioMixError as exc:
            return Err(exc)
        built = build_spec(
            facts, profile, delivery,
            created_at=self._clock.now().isoformat(),
            overrides=config.overrides,
            speech_spans=config.speech_spans,
            levels=level_targets(profile, delivery) if config.level_mix else None,
        )  # fmt: skip
        if built.spec is None:
            return Err(NothingToMix())
        problems = [i.message for i in built.errors]
        if problems:
            return Err(SpecificationInvalid(problems))
        spec = built.spec
        found = self._store.find_spec(anchor_id, spec.content_hash)
        if isinstance(found, Err):
            return found
        if found.value is not None:
            return Ok(SpecResult(found.value[0], found.value[1], created=False, timeline=timeline))
        stored = self._store.save_spec(anchor.value, spec)
        if isinstance(stored, Err):
            return stored
        _log.info(
            "Audio mix specification created",
            specification_id=spec.specification_id,
            clips=len(spec.clips),
            warnings=sum(1 for i in spec.warnings if i.severity is not Severity.INFO),
        )
        return Ok(SpecResult(spec, stored.value, created=True, timeline=timeline))
