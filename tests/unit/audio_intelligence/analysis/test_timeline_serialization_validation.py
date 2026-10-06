"""Timeline queries and synchronisation, JSON persistence, validation and processing identity."""

import json
import math
from collections.abc import Mapping
from dataclasses import replace
from itertools import pairwise
from typing import Any

import pytest

from media_house.modules.audio_intelligence.domain.analysis.acoustic import (
    LAUGHTER,
    PITCH_RISE,
    AcousticMeasurements,
    AcousticTrack,
    AudioEvent,
)
from media_house.modules.audio_intelligence.domain.analysis.config import (
    AcousticConfig,
    AnalysisConfig,
    AudioIntelligenceConfig,
    ScoringConfig,
)
from media_house.modules.audio_intelligence.domain.analysis.scoring import MOMENT
from media_house.modules.audio_intelligence.domain.analysis.serialization import (
    TIMELINE_DOCUMENT,
    measurements_from_json,
    measurements_to_json,
    timeline_from_json,
    timeline_to_document,
    timeline_to_json,
)
from media_house.modules.audio_intelligence.domain.analysis.timeline import (
    AudioIntelligenceTimeline,
)
from media_house.modules.audio_intelligence.domain.analysis.validation import validate_timeline
from media_house.modules.audio_intelligence.domain.errors import InvalidTranscript
from media_house.modules.audio_intelligence.domain.validation import Severity
from media_house.shared.errors import InvariantViolation
from tests.support.analysis_fakes import NAN, Delivery, make_track, measurements, timeline

WORDS = [
    [("alpha", 0.5, 0.9), ("beta", 1.0, 1.4), ("gamma.", 1.5, 2.0)],
    [("delta", 3.5, 3.9), ("epsilon", 4.0, 4.5)],
]
LOUD = {2: Delivery(f0=260.0, db=-12.0)}


def sample_timeline(**kwargs: Any) -> AudioIntelligenceTimeline:
    return timeline(WORDS, delivery=LOUD, **kwargs)


# --- queries -----------------------------------------------------------------------------------
def test_one_timeline_answers_speech_acoustic_event_and_signal_queries() -> None:
    tl = sample_timeline()

    word = tl.word_at(1.2)
    assert word is not None
    assert word.raw_word == "beta"
    assert [w.raw_word for w in tl.words_between(0.0, 1.2)] == ["alpha", "beta"]
    segment = tl.segment_at(4.2)
    assert segment is not None
    assert segment.id == 1
    assert [s.id for s in tl.segments_between(0.0, 10.0)] == [0, 1]
    sample = tl.acoustic_at(0.7)
    assert sample is not None
    assert sample.voiced
    assert sample.f0 == pytest.approx(150.0)
    assert sample.speech_activity
    assert sample.relative_pitch_st is not None
    assert tl.acoustic_at(1000.0) is None
    frames = tl.frames_between(0.5, 0.9)
    assert len(frames) == pytest.approx(20, abs=1)
    assert all(a.start < b.start for a, b in pairwise(frames))
    assert tl.moment_at(1.2) is not None
    assert tl.moment_at(2.5) is None  # silence: no word, no moment
    assert tl.to_frame(1.2, 30) == 36


def test_unvoiced_frames_have_no_pitch_instead_of_zero() -> None:
    sample = sample_timeline().acoustic_at(3.0)

    assert sample is not None
    assert sample.f0 is None
    assert not sample.voiced
    assert sample.relative_pitch_st is None
    assert not sample.speech_activity


def test_events_are_first_class_and_filterable() -> None:
    laugh = AudioEvent(LAUGHTER, 2.2, 2.9, 0.8, confidence=0.88, source="test-detector")
    tl = sample_timeline(events=[laugh])

    assert laugh in tl.events_between(2.0, 3.0)
    assert tl.events_between(2.0, 3.0, kinds=[LAUGHTER]) == (laugh,)
    assert all(e.kind != LAUGHTER for e in tl.events_between(0.0, 1.0))
    assert [e.start for e in tl.events] == sorted(e.start for e in tl.events)
    assert {"pause", "silence"} <= {e.kind for e in tl.events}


def test_pause_queries_connect_words_and_gaps() -> None:
    tl = sample_timeline()
    gamma = tl.words[2]

    after, before = tl.pause_after(gamma), tl.pause_before(tl.words[3])

    assert after is not None
    assert after is before
    assert after.duration == pytest.approx(1.5)
    assert after.at_segment_boundary
    assert tl.pause_after(tl.words[1]) is None  # 0.1 s gap is below min_pause


def test_signal_curve_reports_only_covered_instants() -> None:
    tl = sample_timeline()

    curve = dict(tl.signal_curve(MOMENT, hop=0.1))

    assert any(0.5 < t < 0.9 and score > 0 for t, score in curve.items())
    assert not any(2.2 < t < 3.4 for t in curve)  # nobody speaks there: unknown, not 0
    assert max(curve.values()) <= 1.0
    assert tl.signals_between(0.0, 1.0, MOMENT)


def test_source_offset_moves_frames_events_and_words_onto_the_original_timeline() -> None:
    plain = timeline([[("hi", 1.0, 1.4)]], duration=3.0)
    shifted_words = [[("hi", 1.0, 1.4)]]  # prepared time; source time = +0.5
    event = AudioEvent(PITCH_RISE, 1.0, 1.2, 0.5, source="test")  # prepared-audio time
    shifted = timeline(shifted_words, duration=3.5, offset=0.5, events=[event])

    assert plain.track.origin == 0.0
    assert shifted.track.origin == pytest.approx(0.5)
    assert shifted.metadata.audio_offset == 0.5
    word = shifted.word_at(1.7)
    assert word is not None
    assert word.start == 1.5
    sample = shifted.acoustic_at(1.7)  # the same instant, found by SOURCE time
    assert sample is not None
    assert sample.voiced
    assert shifted.acoustic_at(0.2) is None  # before the audio starts
    moved = next(e for e in shifted.events if e.kind == PITCH_RISE)
    assert moved.start == pytest.approx(1.5)
    assert moved.end == pytest.approx(1.7)


# --- JSON --------------------------------------------------------------------------------------
def test_timeline_json_is_versioned_deterministic_and_round_trips() -> None:
    tl = sample_timeline()

    text = timeline_to_json(tl)
    restored = timeline_from_json(text)

    document = json.loads(text)
    assert document["document_type"] == TIMELINE_DOCUMENT
    assert document["schema_version"] == 1
    assert set(document) == {
        "document_type",
        "schema_version",
        "metadata",
        "transcript",
        "acoustic",
        "events",
        "pauses",
        "words",
        "segments",
        "editing_signals",
    }
    assert timeline_to_json(tl) == text
    assert timeline_to_json(restored) == text  # stable across save/load
    assert [w.raw_word for w in restored.words] == [w.raw_word for w in tl.words]
    assert restored.word_at(1.2) is not None
    assert restored.baseline.pitch_median_hz == pytest.approx(tl.baseline.pitch_median_hz)
    assert len(restored.editing_signals) == len(tl.editing_signals)
    assert restored.metadata.created_at == tl.metadata.created_at
    assert restored.explain_word(restored.words[2]).keys() == tl.explain_word(tl.words[2]).keys()


def test_missing_measurements_are_null_in_json_never_zero() -> None:
    document = timeline_to_document(sample_timeline())

    f0 = document["acoustic"]["frames"]["f0"]
    assert None in f0  # unvoiced frames
    assert 0 not in f0
    assert all(v is None or v > 0 for v in f0)


def test_frames_are_stored_once_not_per_word() -> None:
    document = timeline_to_document(sample_timeline())

    assert "frames" in document["acoustic"]
    assert '"f0"' not in json.dumps(document["words"])  # no per-word copies of the frame columns
    word = document["words"][0]["acoustics"]
    assert word["first_frame"] is not None  # a reference, not a copy


def test_measurements_round_trip_on_their_own() -> None:
    track = make_track(seconds=2.0, f0=lambda t: 150.0 if t > 0.5 else NAN, db=lambda _t: -30.0)
    original = AcousticMeasurements(
        track,
        {"pitch": "x-1"},
        {"pitch_floor": 80.0},
        (AudioEvent(LAUGHTER, 0.5, 0.8, 0.7, confidence=0.9, source="d"),),
        ("note",),
    )

    restored = measurements_from_json(measurements_to_json(original))

    assert len(restored.track) == len(track)
    assert math.isnan(restored.track.f0[0])
    assert restored.track.f0[-1] == pytest.approx(150.0)
    assert restored.identity == {"pitch": "x-1"}
    assert restored.parameters == {"pitch_floor": 80.0}
    assert restored.events == original.events
    assert restored.warnings == ("note",)


@pytest.mark.parametrize(
    "text",
    ["nope", "[]", json.dumps({"document_type": "other", "schema_version": 1})],
)
def test_foreign_documents_are_rejected(text: str) -> None:
    with pytest.raises(InvalidTranscript):
        timeline_from_json(text)
    with pytest.raises(InvalidTranscript):
        measurements_from_json(text)


def test_newer_schema_versions_are_refused() -> None:
    document = timeline_to_document(sample_timeline())
    document["schema_version"] = 99

    with pytest.raises(InvalidTranscript):
        timeline_from_json(json.dumps(document))


# --- validation --------------------------------------------------------------------------------
def codes(tl: AudioIntelligenceTimeline) -> dict[str, Severity]:
    return {i.code: i.severity for i in validate_timeline(tl)}


def test_a_healthy_timeline_validates_clean() -> None:
    assert [i for i in validate_timeline(sample_timeline()) if i.severity is Severity.ERROR] == []
    assert sample_timeline().metadata.warnings == ()


def corrupted(tl: AudioIntelligenceTimeline, **changes: Any) -> AudioIntelligenceTimeline:
    return replace(tl, **changes)


def test_validation_reports_corrupt_frames_events_and_synchronisation() -> None:
    tl = sample_timeline()
    track: AcousticTrack = tl.track
    bad_f0 = AcousticTrack(
        track.origin,
        track.hop,
        type(track.f0)("d", [math.inf if i == 5 else v for i, v in enumerate(track.f0)]),
        track.pitch_confidence,
        track.rms_db,
        track.loudness,
        track.speech,
    )
    out_of_range = replace(
        tl,
        events=(
            AudioEvent("x", 2.0, 1.0, 0.5),
            AudioEvent("y", 3.0, 3.5, 1.7, confidence=2.0),
            AudioEvent("z", 0.0, 0.1, 0.5),
        ),
    )

    assert "infinite_value" in codes(corrupted(tl, track=bad_f0))
    found = codes(out_of_range)
    assert found["event_time_invalid"] is Severity.ERROR
    assert found["event_strength_range"] is Severity.ERROR
    assert found["event_confidence_range"] is Severity.ERROR
    assert found["events_out_of_order"] is Severity.ERROR
    shifted = replace(tl, track=replace(tl.track, origin=tl.track.origin + 1.0))
    assert codes(shifted)["frames_out_of_sync"] is Severity.ERROR
    wrong_source = replace(tl, metadata=replace(tl.metadata, source_asset_id="other"))
    assert codes(wrong_source)["source_mismatch"] is Severity.ERROR


def test_validation_reports_scores_outside_the_unit_interval() -> None:
    tl = sample_timeline()
    signal = replace(tl.editing_signals[0], score=1.4)

    found = codes(replace(tl, editing_signals=(signal, *tl.editing_signals[1:])))

    assert found["signal_invalid"] is Severity.ERROR


def test_validation_never_repairs_data() -> None:
    tl = sample_timeline()
    bad = replace(tl, events=(AudioEvent("x", 2.0, 1.0, 0.5),))

    validate_timeline(bad)

    assert bad.events[0].start == 2.0


# --- processing identity and partial reuse -----------------------------------------------------
IDENTITY = {"pitch": "parselmouth-0.4.7/praat-6.1.38", "energy": "rms-1"}


def fingerprints(
    config: AudioIntelligenceConfig,
    identity: Mapping[str, str] = IDENTITY,
) -> tuple[object, object]:
    return (
        config.measurements_fingerprint(identity),
        config.timeline_fingerprint("3.8.6", identity),
    )


def test_scoring_or_analysis_changes_reuse_measurements_but_make_a_new_timeline() -> None:
    base_m, base_t = fingerprints(AudioIntelligenceConfig())

    for changed in (
        AudioIntelligenceConfig(scoring=ScoringConfig(version=2)),
        AudioIntelligenceConfig(analysis=AnalysisConfig(min_pause=0.2)),
    ):
        m, t = fingerprints(changed)
        assert m == base_m  # frames are reused
        assert t != base_t  # interpretation is recomputed


def test_a_new_analyzer_version_remeasures_and_rebuilds() -> None:
    config = AudioIntelligenceConfig()
    base_m, base_t = fingerprints(config)

    m, t = fingerprints(config, {**IDENTITY, "pitch": "parselmouth-0.5.0/praat-6.4"})

    assert (m, t) != (base_m, base_t)
    assert m != base_m


def test_acoustic_settings_remeasure_and_speech_settings_do_not() -> None:
    from media_house.modules.audio_intelligence.domain.values import TranscriptionConfig

    base_m, _ = fingerprints(AudioIntelligenceConfig())

    assert fingerprints(AudioIntelligenceConfig(acoustic=AcousticConfig(hop=0.01)))[0] != base_m
    other_model = AudioIntelligenceConfig(transcription=TranscriptionConfig(model="medium"))
    assert fingerprints(other_model)[0] == base_m  # frames do not depend on the speech model
    assert fingerprints(other_model)[1] != fingerprints(AudioIntelligenceConfig())[1]


@pytest.mark.parametrize(
    "factory",
    [
        lambda: AcousticConfig(hop=1.0),
        lambda: AcousticConfig(pitch_floor=400.0, pitch_ceiling=100.0),
        lambda: AcousticConfig(voicing_threshold=1.5),
        lambda: AnalysisConfig(min_pause=2.0),
        lambda: AnalysisConfig(smoothing_frames=4),
        lambda: ScoringConfig(emphasis_weights={"x": 0.0}),
        lambda: ScoringConfig(full_scale={"pitch_level_st": 0.0}),
    ],
)
def test_invalid_analysis_configuration_is_rejected(factory: object) -> None:
    with pytest.raises(InvariantViolation):
        factory()  # type: ignore[operator]


def test_measurements_type_is_independent_of_the_transcript() -> None:
    assert isinstance(measurements(make_track(seconds=1.0)), AcousticMeasurements)
