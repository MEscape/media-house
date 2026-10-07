"""Algorithm constants are not settings, so nothing in a fingerprint reflects them.

A changed constant therefore changes results while old cached results still look current. These
snapshots make that impossible to do by accident: change a constant, bump the matching version,
then update the snapshot here in the same commit.
"""

from media_house.modules.audio_intelligence.domain.analysis import config, features, scoring
from media_house.modules.audio_intelligence.infrastructure import acoustic_extractor as extractor


def test_extractor_constants_are_pinned_to_the_analyzer_versions() -> None:
    assert {
        "energy": extractor.ENERGY_VERSION,
        "loudness": extractor.LOUDNESS_VERSION,
        "activity": extractor.ACTIVITY_VERSION,
        "min_pitch_seconds": extractor._MIN_PITCH_SECONDS,
        "first_pass_range": extractor._FIRST_PASS_RANGE,
        "min_range_frames": extractor._MIN_RANGE_FRAMES,
        "min_dynamic_db": extractor._MIN_DYNAMIC_DB,
        "absolute_voiced_floor_db": extractor._ABSOLUTE_VOICED_FLOOR_DB,
        "max_gap_frames": extractor._MAX_GAP_FRAMES,
        "min_run_frames": extractor._MIN_RUN_FRAMES,
        "voiced_activity_margin_db": extractor._VOICED_ACTIVITY_MARGIN_DB,
    } == {
        "energy": "rms-1",
        "loudness": "bs1770-momentary-1",
        "activity": "adaptive-1",
        "min_pitch_seconds": 0.2,
        "first_pass_range": (60.0, 700.0),
        "min_range_frames": 20,
        "min_dynamic_db": 6.0,
        "absolute_voiced_floor_db": -60.0,
        "max_gap_frames": 3,
        "min_run_frames": 3,
        "voiced_activity_margin_db": 3.0,
    }


def test_analysis_constants_are_pinned_to_the_analysis_version() -> None:
    assert {
        "analysis_version": config.ANALYSIS_VERSION,
        "min_local_frames": features._MIN_LOCAL_FRAMES,
        "min_range_frames": features._MIN_RANGE_FRAMES,
        "min_rate_seconds": features._MIN_RATE_SECONDS,
        "min_rate_words": features._MIN_RATE_WORDS,
        "min_cv_words": features._MIN_CV_WORDS,
        "pause_before_context_weight": scoring.PAUSE_BEFORE_CONTEXT_WEIGHT,
        "pause_structure_weight": scoring.PAUSE_STRUCTURE_WEIGHT,
        "seconds_per_structuring_pause": scoring.SECONDS_PER_STRUCTURING_PAUSE,
    } == {
        "analysis_version": 1,
        "min_local_frames": 10,
        "min_range_frames": 5,
        "min_rate_seconds": 0.5,
        "min_rate_words": 2,
        "min_cv_words": 3,
        "pause_before_context_weight": 0.5,
        "pause_structure_weight": 0.34,
        "seconds_per_structuring_pause": 10.0,
    }
