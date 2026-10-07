"""Processing identity of the analysis configuration: stale results must never look current."""

from dataclasses import fields

import pytest

from media_house.modules.audio_intelligence.domain.analysis import config as config_module
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AcousticConfig,
    AnalysisConfig,
    AudioIntelligenceConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.values import EXTRACTION_VERSION
from media_house.shared.errors import InvariantViolation

ANALYZERS = {"pitch": "parselmouth-1/praat-1"}


def timeline_identity(scorer: str = "heuristic-1") -> object:
    return AudioIntelligenceConfig().timeline_fingerprint("3.8.6", ANALYZERS, scorer)


@pytest.mark.parametrize("config", [AcousticConfig(), AnalysisConfig(), ScoringConfig()])
def test_every_setting_is_part_of_the_identity(config: object) -> None:
    exported = set(config.to_config())  # type: ignore[attr-defined]

    assert exported == {f.name for f in fields(config)}  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "version", ["TRANSCRIPTION_VERSION", "EXTRACTION_VERSION", "MEASUREMENTS_VERSION"]
)
def test_bumping_a_pipeline_version_retires_stored_timelines(
    version: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    before = timeline_identity()

    monkeypatch.setattr(config_module, version, getattr(config_module, version) + 1)

    assert timeline_identity() != before


def test_bumping_the_analysis_version_retires_stored_timelines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = timeline_identity()

    monkeypatch.setattr(config_module, "ANALYSIS_VERSION", config_module.ANALYSIS_VERSION + 1)

    assert timeline_identity() != before


def test_a_different_scorer_gets_its_own_timeline() -> None:
    assert timeline_identity("heuristic-1") != timeline_identity("learned-1")


def test_a_new_extraction_version_retires_stored_measurements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = AudioIntelligenceConfig().measurements_fingerprint(ANALYZERS)

    monkeypatch.setattr(config_module, "EXTRACTION_VERSION", EXTRACTION_VERSION + 1)

    assert AudioIntelligenceConfig().measurements_fingerprint(ANALYZERS) != before


def test_interpretation_settings_do_not_retire_measurements() -> None:
    base = AudioIntelligenceConfig().measurements_fingerprint(ANALYZERS)
    rescored = AudioIntelligenceConfig(
        analysis=AnalysisConfig(min_pause=0.2), scoring=ScoringConfig(version=2)
    )

    assert rescored.measurements_fingerprint(ANALYZERS) == base


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AcousticConfig(loudness_window=0.0),
        lambda: AcousticConfig(activity_margin_db=-1.0),
        lambda: AnalysisConfig(min_pitch_confidence=1.5),
        lambda: AnalysisConfig(min_word_voiced_frames=0),
        lambda: AnalysisConfig(min_baseline_voiced_frames=0),
        lambda: AnalysisConfig(min_silence=0.0),
        lambda: AnalysisConfig(pitch_event_min_st=-1.0),
        lambda: AnalysisConfig(energy_full_scale_db=0.0),
    ],
)
def test_nonsensical_settings_are_rejected(factory: object) -> None:
    with pytest.raises(InvariantViolation):
        factory()  # type: ignore[operator]
