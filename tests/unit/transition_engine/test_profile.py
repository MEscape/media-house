"""Profiles are data: metadata and checks agree, precedence and overrides work."""

from dataclasses import fields, is_dataclass
from typing import Any

import pytest

from media_house.modules.transition_engine.domain.errors import InvalidTransitionConfig
from media_house.modules.transition_engine.domain.profile import (
    DEFAULT_PROFILE,
    PROFILES,
    STORY_FORMAT_PROFILE,
    STYLE_PROFILE,
    SectionOverride,
    TransitionConfig,
    TransitionProfile,
    apply_overrides,
    knob_fields,
)
from media_house.modules.transition_engine.domain.values import KIND_NAMES

BASE = PROFILES[DEFAULT_PROFILE]


def test_every_knob_has_metadata_that_problems_enforces() -> None:
    knobs = knob_fields(TransitionProfile)
    assert len(knobs) > 40
    for path, meta, default in knobs:
        assert meta["label"], path
        if isinstance(default, bool):
            continue
        if isinstance(default, int | float):
            step = meta["step"]
            for bad in (meta["min"] - step, meta["max"] + step):
                value = int(bad) if isinstance(default, int) else float(bad)
                assert apply_overrides(BASE, {path: value}).problems(), (path, bad)
            assert not apply_overrides(BASE, {path: meta["min"]}).problems(), path
            assert not apply_overrides(BASE, {path: meta["max"]}).problems(), path
        elif "choices" in meta:
            bad_value: Any = ["no_such_kind"] if isinstance(default, tuple) else "no_such_choice"
            assert apply_overrides(BASE, {path: bad_value}).problems(), path


def test_every_built_in_profile_is_valid_and_names_known_kinds() -> None:
    for name, profile in PROFILES.items():
        assert profile.problems() == [], name
        assert profile.evidence, name
        for f in fields(profile.contexts):
            assert set(getattr(profile.contexts, f.name)) <= set(KIND_NAMES)


def test_profiles_cover_the_spec_and_the_channel_taxonomy() -> None:
    assert {
        "clean_default", "modern_youtube", "shorts_energetic", "product_review", "unboxing",
        "tutorial", "documentary", "cinematic", "ranking", "brochure_review", "shopping_tour",
        "compilation", "nye_uncut", "shorts_meme", "shorts_haul",
    } <= set(PROFILES)  # fmt: skip
    assert set(STORY_FORMAT_PROFILE.values()) <= set(PROFILES)
    assert set(STYLE_PROFILE.values()) <= set(PROFILES)
    assert PROFILES["ranking"].contexts.section_change == ("slide", "whip_pan")
    assert PROFILES["brochure_review"].budget.max_intensity == 1
    assert PROFILES["nye_uncut"].budget.per_minute == 0.0


def test_profile_precedence_explicit_then_story_format_then_style() -> None:
    assert TransitionConfig(profile="cinematic").resolve("top_n", "shorts_haul").name == "cinematic"
    assert TransitionConfig().resolve("top_n", "shorts_haul").name == "ranking"
    assert TransitionConfig().resolve(None, "shorts_haul").name == "shorts_haul"
    assert TransitionConfig().resolve(None, None).name == DEFAULT_PROFILE
    with pytest.raises(InvalidTransitionConfig):
        TransitionConfig(profile="nope").resolve()


def test_project_and_section_overrides_by_dotted_path() -> None:
    config = TransitionConfig(
        profile="ranking",
        overrides={"budget.max_intensity": 2, "contexts.section_change": ["cross_dissolve"]},
        section_overrides=(SectionOverride("s9", {"budget.max_intensity": 0}),),
    )
    profile = config.resolve()
    assert (profile.budget.max_intensity, profile.contexts.section_change) == (
        2,
        ("cross_dissolve",),
    )
    assert config.for_section(profile, "s9").budget.max_intensity == 0
    assert config.for_section(profile, "s1") is profile
    for bad in ({"budget.nope": 1}, {"budget": 1}, {"name": "x"}, {"budget.max_intensity": "3"}):
        with pytest.raises(InvalidTransitionConfig):
            TransitionConfig(overrides=bad).resolve()
    with pytest.raises(InvalidTransitionConfig):
        TransitionConfig(
            section_overrides=(SectionOverride("s", {"budget.max_intensity": 9}),)
        ).resolve()


def test_metadata_never_changes_the_fingerprint() -> None:
    config = TransitionConfig()
    first = config.fingerprint(config.resolve())
    assert first == TransitionConfig().fingerprint(TransitionConfig().resolve())
    assert is_dataclass(BASE)
