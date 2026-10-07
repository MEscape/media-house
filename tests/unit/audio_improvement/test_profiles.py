"""Profiles: defaults -> built-in profile -> user overrides, validated and fingerprinted."""

from dataclasses import fields, is_dataclass

import pytest

from media_house.modules.audio_improvement.domain.errors import InvalidProfile
from media_house.modules.audio_improvement.domain.profiles import (
    CUSTOM_PROFILE,
    DEFAULT_PROFILE,
    profile_names,
    resolve_profile,
)
from media_house.modules.audio_improvement.domain.settings import AudioProfile


def test_the_default_profile_is_youtube_loudness() -> None:
    profile = resolve_profile()

    assert profile.name == DEFAULT_PROFILE == "youtube"
    assert (profile.mastering.target_lufs, profile.mastering.true_peak_ceiling_dbtp) == (
        -14.0,
        -1.0,
    )


@pytest.mark.parametrize("name", [n for n in profile_names() if n != CUSTOM_PROFILE])
def test_every_builtin_profile_resolves_with_its_own_name(name: str) -> None:
    assert resolve_profile(name).name == name


def test_profiles_differ_in_delivery_standard_only_where_intended() -> None:
    targets = {n: resolve_profile(n).mastering.target_lufs for n in profile_names()}

    assert targets["podcast"] == -16.0
    assert targets["cinematic"] == -23.0
    assert targets["youtube"] == targets["social_video"] == -14.0


def test_user_overrides_win_and_leave_everything_else_alone() -> None:
    base = resolve_profile("podcast")

    tuned = resolve_profile("podcast", {"mastering.target_lufs": -15.0, "noise.max_strength": 1})

    assert tuned.mastering.target_lufs == -15.0
    assert tuned.noise.max_strength == 1.0  # an int is accepted for a float setting
    assert tuned.eq == base.eq
    assert tuned.dynamics == base.dynamics


def test_custom_starts_from_the_defaults() -> None:
    custom = resolve_profile(CUSTOM_PROFILE, {"mastering.target_lufs": -18.0})

    assert custom.name == CUSTOM_PROFILE
    assert custom.mastering.target_lufs == -18.0
    assert custom.noise == resolve_profile().noise


@pytest.mark.parametrize(
    ("name", "overrides"),
    [
        ("unknown", {}),
        ("youtube", {"mastering.no_such_setting": 1.0}),
        ("youtube", {"nothing.at_all": 1.0}),
        ("youtube", {"mastering": 1.0}),  # a group, not a value
        ("youtube", {"name": "other"}),
        ("youtube", {"mastering.target_lufs": "loud"}),
        ("youtube", {"mastering.skip_if_compliant": 1}),
        ("youtube", {"mastering.max_passes": 2.5}),
        ("youtube", {"mastering.target_lufs": 3.0}),  # the setting itself refuses it
        ("youtube", {"noise.min_strength": 0.9, "noise.max_strength": 0.5}),
    ],
)
def test_invalid_profiles_and_overrides_are_rejected_with_a_reason(
    name: str, overrides: dict[str, object]
) -> None:
    with pytest.raises(InvalidProfile):
        resolve_profile(name, overrides)  # type: ignore[arg-type]


def test_the_configuration_contains_every_setting() -> None:
    config = resolve_profile().to_config()

    def names(value: object) -> object:
        assert is_dataclass(value)
        return {
            f.name: names(getattr(value, f.name)) if is_dataclass(getattr(value, f.name)) else None
            for f in fields(value)
        }

    def keys(value: object) -> object:
        assert isinstance(value, dict)
        return {k: keys(v) if isinstance(v, dict) else None for k, v in value.items()}

    assert keys(config) == names(AudioProfile())


def test_any_changed_setting_changes_the_processing_identity() -> None:
    base = resolve_profile().to_config()

    assert resolve_profile(overrides={"eq.highpass_hz": 90.0}).to_config() != base
    assert resolve_profile("podcast").to_config() != base
    assert resolve_profile().to_config() == base  # and identical settings are identical
