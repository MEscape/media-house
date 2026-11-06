"""Representative inputs for the mix engine: the REAL built timeline of the builder's fixtures
(two dialogue clips with a crossfade, music with a compiled duck, a sound effect that needs a
retime), read as the engine's own facts."""

from dataclasses import replace

from media_house.modules.audio_mix_engine.application.views import to_facts
from media_house.modules.audio_mix_engine.domain.build import BuiltSpec, MixOverrides, build_spec
from media_house.modules.audio_mix_engine.domain.facts import MixFacts
from media_house.modules.audio_mix_engine.domain.profile import (
    DeliveryProfile,
    MixProfile,
    choose_delivery,
    choose_profile,
)
from media_house.modules.audio_mix_engine.domain.spec import AudioMixSpecification
from tests.unit.timeline_builder import fixtures as bf

NOW = "2026-11-06T09:00:00+00:00"


def facts_of() -> MixFacts:
    return to_facts(bf.built())


def spec_of(
    facts: MixFacts | None = None,
    *,
    profile: MixProfile | None = None,
    delivery: DeliveryProfile | None = None,
    overrides: MixOverrides | None = None,
    speech_spans: tuple[tuple[int, int], ...] | None = None,
) -> AudioMixSpecification:
    built = built_of(
        facts, profile=profile, delivery=delivery, overrides=overrides, speech_spans=speech_spans
    )
    assert built.spec is not None, [i.message for i in built.issues]
    return built.spec


def built_of(
    facts: MixFacts | None = None,
    *,
    profile: MixProfile | None = None,
    delivery: DeliveryProfile | None = None,
    overrides: MixOverrides | None = None,
    speech_spans: tuple[tuple[int, int], ...] | None = None,
) -> BuiltSpec:
    facts = facts or facts_of()
    return build_spec(
        facts,
        profile or choose_profile(None, facts.format_profile),
        delivery or choose_delivery(None, facts.format_profile),
        created_at=NOW,
        overrides=overrides,
        speech_spans=speech_spans,
    )


def without_instructions(facts: MixFacts, *kinds: str) -> MixFacts:
    return replace(facts, instructions=tuple(i for i in facts.instructions if i.kind not in kinds))
