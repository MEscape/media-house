"""Profiles are data: field metadata and ``problems()`` agree; overrides follow the precedence."""

from dataclasses import fields, is_dataclass, replace
from typing import Any

import pytest

from media_house.modules.music_and_sound_design.domain.errors import InvalidSoundConfig
from media_house.modules.music_and_sound_design.domain.profile import (
    CHANNEL,
    PROFILES,
    STORY_FORMAT_PROFILE,
    SectionOverride,
    SoundDesignConfig,
    SoundProfile,
)
from media_house.modules.music_and_sound_design.domain.values import (
    PROFILE_VERSION,
    MusicPresence,
    SectionRole,
)

FORMATS = {
    "generic", "ranking", "brochure_review", "shopping_tour", "unboxing", "compilation",
    "nye_uncut", "shorts_meme", "shorts_product_test", "shorts_haul", "shorts_versus",
}  # fmt: skip


def _knobs(target: Any, path: str = "") -> list[tuple[str, Any, Any]]:
    """Every (dotted path, owner instance, field) of a profile, rules included."""
    out = []
    for f in fields(target):
        value = getattr(target, f.name)
        if is_dataclass(value):
            out += _knobs(value, f"{path}{f.name}.")
        elif isinstance(value, tuple) and value and is_dataclass(value[0]):
            out += _knobs(value[0], f"{path}{f.name}[0].")
        else:
            out.append((f"{path}{f.name}", target, f))
    return out


def _set(profile: SoundProfile, path: str, value: object) -> SoundProfile:
    def go(target: Any, parts: list[str]) -> Any:
        head = parts[0]
        if head.endswith("[0]"):
            name = head[:-3]
            items = getattr(target, name)
            return replace(target, **{name: (go(items[0], parts[1:]), *items[1:])})
        if len(parts) == 1:
            return replace(target, **{head: value})
        return replace(target, **{head: go(getattr(target, head), parts[1:])})

    result: SoundProfile = go(profile, path.split("."))
    return result


def _own(profile: SoundProfile, path: str) -> list[str]:
    """The range/choice problems of one knob (cross-field checks are not its own)."""
    return [p for p in profile.problems() if p.startswith(f"{path} must be ")]


def test_every_built_in_profile_is_valid_and_the_formats_are_the_discovered_ones() -> None:
    assert set(PROFILES) == FORMATS
    assert all(p.problems() == [] for p in PROFILES.values())
    assert all(p.identity == CHANNEL.identity for p in PROFILES.values())
    assert set(STORY_FORMAT_PROFILE.values()) <= FORMATS
    assert PROFILE_VERSION == 1


def test_metadata_ranges_and_choices_are_exactly_what_problems_enforces() -> None:
    labelled = 0
    for path, _owner, f in _knobs(CHANNEL):
        meta = f.metadata
        if "label" not in meta:
            continue
        labelled += 1
        if "min" in meta:
            step = meta.get("step", 1)
            low = meta["min"] - step
            high = meta["max"] + step
            if isinstance(f.default, int) and not isinstance(f.default, bool):
                low, high = int(low), int(high)
            assert _set(CHANNEL, path, low).problems(), path
            assert _set(CHANNEL, path, high).problems(), path
            assert not _own(_set(CHANNEL, path, meta["min"]), path), path
            assert not _own(_set(CHANNEL, path, meta["max"]), path), path
        if "choices" in meta:
            assert _set(CHANNEL, path, "not-a-choice").problems(), path
            for choice in meta["choices"]:
                assert not _own(_set(CHANNEL, path, choice), path), (path, choice)
    assert labelled > 60


def test_fireworks_audio_is_preserved_by_default() -> None:
    assert CHANNEL.original_audio.preserve_fireworks is True
    assert CHANNEL.original_audio.music_over_preserved == "off"
    assert CHANNEL.music.presence.of(SectionRole.DEMONSTRATION) is MusicPresence.OFF
    assert PROFILES["nye_uncut"].sfx.enabled is False
    assert PROFILES["compilation"].original_audio.source_audio_is_content is True


def test_profile_comes_from_the_story_format_and_project_overrides_apply_on_top() -> None:
    config = SoundDesignConfig(
        overrides={"mix.bed_below_speech_lu": 20, "music.categories.body": ["chill_lofi"]}
    )

    profile = config.resolve("top_n")

    assert profile.name == "ranking"
    assert profile.mix.bed_below_speech_lu == 20.0
    assert profile.music.categories.body == ("chill_lofi",)
    assert SoundDesignConfig(profile="unboxing").resolve("top_n").name == "unboxing"
    assert SoundDesignConfig().resolve(None).name == "generic"


@pytest.mark.parametrize(
    "overrides",
    [
        {"mix.nope": 1.0},
        {"mix": 1.0},
        {"mix.bed_below_speech_lu": "loud"},
        {"mix.bed_below_speech_lu": 99.0},
        {"music.presence.body": "maybe"},
        {"name": "other"},
        {"sfx.rules": []},
        {"music.tempo_min_bpm": 200.0},
    ],
)
def test_invalid_overrides_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(InvalidSoundConfig):
        SoundDesignConfig(overrides=overrides).resolve()  # type: ignore[arg-type]
    assert SoundDesignConfig(profile="unknown").fingerprint(CHANNEL)
    with pytest.raises(InvalidSoundConfig):
        SoundDesignConfig(profile="unknown").resolve()


def test_section_overrides_touch_only_their_section_and_enter_the_fingerprint() -> None:
    config = SoundDesignConfig(
        section_overrides=(SectionOverride("s2", {"music.presence.body": "silence"}),)
    )
    base = config.resolve()

    assert config.for_section(base, "s2").music.presence.body == "silence"
    assert config.for_section(base, "s1") == base
    assert config.fingerprint(base) != SoundDesignConfig().fingerprint(base)


def test_every_transition_kind_transition_engine_publishes_has_a_rule_or_is_silent() -> None:
    from media_house.modules.music_and_sound_design.domain.profile import (
        DEFAULT_RULES,
        SILENT_TRANSITIONS,
        SfxRules,
    )
    from media_house.modules.music_and_sound_design.domain.values import EventSource
    from media_house.modules.transition_engine.application.contracts import TransitionKind

    published = {k.value for k in TransitionKind}
    rules = SfxRules()
    for kind in sorted(published):
        has_rule = rules.rule_for(EventSource.TRANSITION, kind) is not None
        assert has_rule != (kind in SILENT_TRANSITIONS), kind  # exactly one decision per kind
    ours = {r.kind for r in DEFAULT_RULES if r.source == EventSource.TRANSITION.value}
    assert ours <= published  # no rule names a kind transition_engine never emits ("whip")
    assert published >= SILENT_TRANSITIONS


def test_every_visual_style_format_profile_has_a_sound_profile() -> None:
    from media_house.modules.music_and_sound_design.domain.profile import PROFILES
    from media_house.modules.visual_style_engine.application.contracts import (
        PROFILES as STYLE_PROFILES,
    )

    assert set(STYLE_PROFILES) <= set(PROFILES)
    assert PROFILES["shorts_versus"].problems() == []
