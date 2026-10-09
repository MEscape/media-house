"""Profiles are versioned data: what is measured, what is interpreted, and what each costs."""

import dataclasses

import pytest

from media_house.modules.video_intelligence.domain.analyzers import ANALYZERS, run_order
from media_house.modules.video_intelligence.domain.errors import InvalidProfile
from media_house.modules.video_intelligence.domain.profiles import (
    DEFAULT_PROFILE,
    PROFILES,
    MeasurementSettings,
    MotionSettings,
    ProcessingProfile,
    QualitySettings,
    get_profile,
)
from media_house.modules.video_intelligence.domain.values import AnalyzerId, CostTier


def test_the_four_named_profiles_exist_and_are_versioned() -> None:
    assert set(PROFILES) == {"triage", "fast", "standard", "deep"}
    assert DEFAULT_PROFILE in PROFILES
    assert all(p.version >= 1 and p.description for p in PROFILES.values())


def test_every_profile_includes_the_shot_analyzer_the_others_depend_on() -> None:
    for profile in PROFILES.values():
        assert AnalyzerId.SHOTS in profile.analyzers


def test_triage_is_the_cheapest_and_deep_the_most_detailed() -> None:
    triage, standard, deep = (get_profile(n).measurement for n in ("triage", "standard", "deep"))

    assert AnalyzerId.MOTION not in get_profile("triage").analyzers
    assert triage.candidate_fps < standard.candidate_fps < deep.candidate_fps
    assert deep.sample_width > standard.sample_width


def test_an_unknown_profile_is_refused_and_the_valid_names_are_listed() -> None:
    with pytest.raises(InvalidProfile, match="triage"):
        get_profile("turbo")


def test_a_profile_without_shots_or_with_duplicates_is_refused() -> None:
    measurement = MeasurementSettings()
    with pytest.raises(InvalidProfile):
        ProcessingProfile("x", 1, "", (AnalyzerId.QUALITY,), measurement)
    with pytest.raises(InvalidProfile):
        ProcessingProfile("x", 1, "", (AnalyzerId.SHOTS, AnalyzerId.SHOTS), measurement)
    with pytest.raises(InvalidProfile):
        ProcessingProfile("", 1, "", (AnalyzerId.SHOTS,), measurement)


@pytest.mark.parametrize(
    "bad",
    [
        {"dense_width": 4},
        {"sample_width": 10_000},
        {"long_gap_frames": 1},
        {"candidate_fps": 0.0},
        {"candidate_fps": float("nan")},
        {"motion_grid": 0},
        {"clip_level": 0.01, "crush_level": 0.5},
    ],
)
def test_nonsense_measurement_settings_are_refused(bad: dict[str, float]) -> None:
    with pytest.raises(InvalidProfile):
        MeasurementSettings(**bad)  # type: ignore[arg-type]


def test_nonsense_interpretation_settings_are_refused() -> None:
    with pytest.raises(InvalidProfile):
        MotionSettings(smooth_samples=4)
    with pytest.raises(InvalidProfile):
        MotionSettings(static_speed=0.5, pan_speed=0.1)
    with pytest.raises(InvalidProfile):
        QualitySettings(dark_median=0.9, bright_median=0.5)


class TestWhatEachAnalyzerKeyDependsOn:
    base = MeasurementSettings()

    def test_the_shot_signals_ignore_everything_about_the_sampled_frames(self) -> None:
        keyed = self.base.config_for(AnalyzerId.SHOTS)
        for change in ({"sample_width": 640}, {"candidate_fps": 30.0}, {"clip_level": 0.9}):
            assert dataclasses.replace(self.base, **change).config_for(AnalyzerId.SHOTS) == keyed

    def test_motion_and_quality_depend_on_the_sampling_plan(self) -> None:
        for analyzer in (AnalyzerId.MOTION, AnalyzerId.QUALITY):
            denser = dataclasses.replace(self.base, candidate_fps=30.0)
            assert denser.config_for(analyzer) != self.base.config_for(analyzer)

    def test_each_analyzer_only_depends_on_its_own_measurement_settings(self) -> None:
        grid = dataclasses.replace(self.base, motion_grid=2)
        level = dataclasses.replace(self.base, clip_level=0.9)

        assert grid.config_for(AnalyzerId.QUALITY) == self.base.config_for(AnalyzerId.QUALITY)
        assert level.config_for(AnalyzerId.MOTION) == self.base.config_for(AnalyzerId.MOTION)
        assert grid.config_for(AnalyzerId.MOTION) != self.base.config_for(AnalyzerId.MOTION)
        assert level.config_for(AnalyzerId.QUALITY) != self.base.config_for(AnalyzerId.QUALITY)

    def test_interpretation_thresholds_are_not_part_of_any_analyzer_key(self) -> None:
        keys = {a: self.base.config_for(a) for a in AnalyzerId}
        names = {name for key in keys.values() for name in key}

        assert not names & {"cut_diff", "pan_speed", "low_sharpness"}

    def test_the_result_key_holds_every_interpretation_threshold(self) -> None:
        profile = get_profile("standard")
        louder = dataclasses.replace(
            profile, shots=dataclasses.replace(profile.shots, cut_diff=0.3)
        )

        assert louder.interpretation_config() != profile.interpretation_config()
        config = profile.interpretation_config()
        assert {"profile", "profile_version", "analyzers", "shots", "motion", "quality"} <= set(
            config
        )


def test_analyzers_are_ordered_with_their_dependencies_first() -> None:
    assert run_order((AnalyzerId.MOTION, AnalyzerId.QUALITY)) == (
        AnalyzerId.SHOTS,
        AnalyzerId.MOTION,
        AnalyzerId.QUALITY,
    )
    assert run_order((AnalyzerId.SHOTS,)) == (AnalyzerId.SHOTS,)


def test_every_analyzer_declares_a_version_a_cost_and_what_it_needs() -> None:
    assert set(ANALYZERS) == set(AnalyzerId)
    for spec in ANALYZERS.values():
        assert spec.version >= 1 and spec.description
        assert all(dep in ANALYZERS for dep in spec.depends_on)
    assert ANALYZERS[AnalyzerId.SHOTS].depends_on == ()
    cheap = {
        AnalyzerId.SHOTS,
        AnalyzerId.QUALITY,
        AnalyzerId.MOTION,
        AnalyzerId.SALIENCY,
        AnalyzerId.GEOMETRY,
    }
    for analyzer, spec in ANALYZERS.items():
        expected = (
            CostTier.CHEAP
            if analyzer in cheap
            else CostTier.VLM
            if analyzer is AnalyzerId.DESCRIPTIONS
            else CostTier.LOCAL_MODEL
        )
        assert spec.cost is expected, analyzer


def test_the_standard_profile_adds_models_to_the_classical_set_and_deep_adds_more() -> None:
    standard, deep = get_profile("standard").analyzers, get_profile("deep").analyzers

    assert {AnalyzerId.ENTITIES, AnalyzerId.FACES, AnalyzerId.TEXT, AnalyzerId.EMBEDDINGS} <= set(
        standard
    )
    assert set(standard) < set(deep)
    assert {AnalyzerId.BODY, AnalyzerId.APPEARANCE, AnalyzerId.DESCRIPTIONS} <= set(deep)
    assert AnalyzerId.DESCRIPTIONS not in standard  # the VLM is deep-profile only


def test_optional_dependencies_order_but_do_not_force_analyzers() -> None:
    assert run_order((AnalyzerId.FACES,)) == (AnalyzerId.SHOTS, AnalyzerId.FACES)
    assert run_order((AnalyzerId.FACES, AnalyzerId.ENTITIES)) == (
        AnalyzerId.SHOTS,
        AnalyzerId.ENTITIES,
        AnalyzerId.FACES,
    )
    assert run_order((AnalyzerId.APPEARANCE,)) == (
        AnalyzerId.SHOTS,
        AnalyzerId.ENTITIES,
        AnalyzerId.APPEARANCE,
    )  # a REQUIRED dependency is added
