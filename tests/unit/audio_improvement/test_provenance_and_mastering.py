"""Provenance as published facts, and the mastering loop with scripted engines."""

from pathlib import Path

import pytest

from media_house.modules.audio_improvement.application.mastering import Mastering
from media_house.modules.audio_improvement.domain.provenance import (
    ProcessingProvenance,
    StageRecord,
)
from media_house.modules.audio_improvement.domain.settings import MasteringSettings
from media_house.modules.audio_improvement.domain.values import (
    METADATA_KEY,
    ProcessingStage,
    StageStatus,
)
from media_house.shared.concurrency import CancellationToken
from tests.support.improvement_fakes import ScriptedAnalyzer, ScriptedEngine, measurements

SETTINGS = MasteringSettings()  # -14 LUFS, -1 dBTP, ±0.5 LU


def provenance(**changes: object) -> ProcessingProvenance:
    base: dict[str, object] = {
        "profile": "youtube",
        "processing_version": 1,
        "sample_rate": 48_000,
        "channels": 1,
        "stages": (
            StageRecord(
                ProcessingStage.NOISE_REDUCTION,
                StageStatus.APPLIED,
                "SNR 20 dB",
                "afftdn",
                "9",
                {"strength": 0.5, "broadband": True},
            ),
            StageRecord(ProcessingStage.DE_ESSING, StageStatus.SKIPPED, "within range"),
            StageRecord(ProcessingStage.DYNAMICS, StageStatus.BYPASSED, "reverted: no gain"),
        ),
        "output_integrated_lufs": -14.1,
        "output_true_peak_dbtp": -1.4,
        "leading_pad_seconds": 0.0,
    }
    return ProcessingProvenance(**{**base, **changes})  # type: ignore[arg-type]


# --- provenance --------------------------------------------------------------------------------
def test_provenance_states_facts_about_each_stage() -> None:
    p = provenance()

    assert p.noise_reduced
    assert not p.de_essed  # skipped
    assert not p.dynamics_processed  # bypassed: the audio does NOT carry that processing
    assert not p.dereverberated  # never mentioned
    assert p.true_peak_checked


def test_provenance_survives_the_media_library_metadata_round_trip() -> None:
    original = provenance(leading_pad_seconds=0.5)

    restored = ProcessingProvenance.from_metadata({METADATA_KEY: original.to_json_value()})

    assert restored == original


@pytest.mark.parametrize(
    "metadata",
    [
        {},
        {METADATA_KEY: "text"},
        {METADATA_KEY: {"schema_version": 1}},
        {METADATA_KEY: {**provenance().to_json_value(), "schema_version": 99}},
        {METADATA_KEY: {**provenance().to_json_value(), "stages": "nope"}},
        {METADATA_KEY: {**provenance().to_json_value(), "stages": [{"stage": "magic"}]}},
    ],
)
def test_unknown_or_damaged_provenance_means_no_provenance(metadata: dict[str, object]) -> None:
    assert ProcessingProvenance.from_metadata(metadata) is None  # type: ignore[arg-type]


def test_loudness_is_judged_from_the_measured_result_not_from_the_stages_that_ran() -> None:
    assert provenance().meets_loudness(-14.0, 0.5, -1.0)
    assert not provenance().meets_loudness(-16.0, 0.5, -1.0)  # a different target
    assert not provenance(output_true_peak_dbtp=-0.2).meets_loudness(-14.0, 0.5, -1.0)
    assert not provenance(output_integrated_lufs=None).meets_loudness(-14.0, 0.5, -1.0)


# --- mastering ---------------------------------------------------------------------------------
def master(
    current_lufs: float | None,
    outcomes: dict[str, dict[str, float | None]],
    tmp_path: Path,
    settings: MasteringSettings = SETTINGS,
    current_peak: float = -6.0,
) -> tuple[object, ScriptedEngine, ScriptedAnalyzer]:
    source = tmp_path / "in.wav"
    source.write_bytes(b"audio")
    analyzer = ScriptedAnalyzer(
        measurements(),
        {stem: measurements(**changes) for stem, changes in outcomes.items()},
    )
    engine = ScriptedEngine(ProcessingStage.MASTERING)
    result = Mastering(engine, analyzer).master(
        source,
        tmp_path,
        measurements(integrated_lufs=current_lufs, true_peak_dbtp=current_peak),
        settings,
        CancellationToken(),
    )
    return result, engine, analyzer


def test_audio_that_is_already_on_target_is_left_bit_exact(tmp_path: Path) -> None:
    result, engine, _ = master(-14.2, {}, tmp_path, current_peak=-2.0)

    assert result.record.status is StageStatus.SKIPPED  # type: ignore[attr-defined]
    assert result.path == tmp_path / "in.wav"  # type: ignore[attr-defined]
    assert engine.calls == []


def test_quiet_audio_is_gained_to_the_target_and_verified(tmp_path: Path) -> None:
    result, engine, _ = master(
        -30.0, {"mastered-1": {"integrated_lufs": -14.1, "true_peak_dbtp": -1.5}}, tmp_path
    )

    assert engine.calls == [{"gain_db": 16.0, "ceiling_dbtp": -1.0, "release_ms": 60.0}]
    assert result.record.status is StageStatus.APPLIED  # type: ignore[attr-defined]
    assert result.record.parameters["passes"] == 1  # type: ignore[attr-defined]


def test_a_limiter_that_undershoots_is_refined_with_more_gain(tmp_path: Path) -> None:
    result, engine, _ = master(
        -30.0,
        {
            "mastered-1": {"integrated_lufs": -16.0, "true_peak_dbtp": -1.0},
            "mastered-2": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.0},
        },
        tmp_path,
    )

    assert [c["gain_db"] for c in engine.calls] == [16.0, 18.0]
    assert result.record.parameters["passes"] == 2  # type: ignore[attr-defined]


def test_a_true_peak_overshoot_lowers_the_ceiling_of_the_next_pass(tmp_path: Path) -> None:
    _, engine, _ = master(
        -30.0,
        {
            "mastered-1": {"integrated_lufs": -14.0, "true_peak_dbtp": 0.5},
            "mastered-2": {"integrated_lufs": -14.0, "true_peak_dbtp": -1.0},
        },
        tmp_path,
    )

    assert engine.calls[1]["ceiling_dbtp"] == pytest.approx(-2.5)


def test_gain_is_capped_so_noise_is_not_amplified_without_limit(tmp_path: Path) -> None:
    capped = MasteringSettings(max_gain_db=10.0, max_passes=1)

    _, engine, _ = master(-50.0, {"mastered-1": {"integrated_lufs": -40.0}}, tmp_path, capped)

    assert engine.calls[0]["gain_db"] == 10.0


def test_silence_is_not_mastered(tmp_path: Path) -> None:
    result, engine, _ = master(None, {}, tmp_path)

    assert result.record.status is StageStatus.SKIPPED  # type: ignore[attr-defined]
    assert engine.calls == []
