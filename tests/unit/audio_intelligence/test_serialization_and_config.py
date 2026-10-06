"""JSON schema, determinism and the processing-identity rules of the configuration."""

import json

import pytest

from media_house.modules.audio_intelligence.domain.errors import InvalidTranscript
from media_house.modules.audio_intelligence.domain.serialization import (
    DOCUMENT_TYPE,
    SCHEMA_VERSION,
    from_document,
    from_json,
    to_document,
    to_json,
)
from media_house.modules.audio_intelligence.domain.values import (
    AlignmentFailurePolicy,
    PreparationConfig,
    TranscriptionConfig,
)
from media_house.shared.errors import InvariantViolation
from tests.support.audio_fakes import build, raw_transcription


# --- JSON --------------------------------------------------------------------------------------
def test_json_is_versioned_and_typed() -> None:
    document = to_document(build())

    assert document["document_type"] == DOCUMENT_TYPE
    assert document["schema_version"] == SCHEMA_VERSION == 1
    assert set(document) == {"document_type", "schema_version", "metadata", "segments", "words"}
    assert document["metadata"]["language"] == "en"
    assert document["words"][0]["raw_word"] == "Hello,"
    assert document["words"][0]["normalized_word"] == "Hello"
    assert document["words"][0]["duration"] == pytest.approx(0.39)
    assert document["segments"][1]["first_word"] == 2
    assert document["segments"][1]["word_count"] == 2


def test_json_is_deterministic() -> None:
    assert to_json(build()) == to_json(build())


def test_round_trip_preserves_everything() -> None:
    original = build(offset=0.25)

    restored = from_json(to_json(original))

    assert to_json(restored) == to_json(original)  # stable across save/load cycles
    assert restored.metadata.warnings == original.metadata.warnings
    assert [w.raw_word for w in restored.words] == [w.raw_word for w in original.words]
    assert [s.id for s in restored.segments] == [s.id for s in original.segments]
    for a, b in zip(original.words, restored.words, strict=True):
        assert b.start == pytest.approx(a.start, abs=1e-6)
        assert b.end == pytest.approx(a.end, abs=1e-6)
        assert (b.timing, b.emphasis_hints, b.confidence) == (
            a.timing,
            a.emphasis_hints,
            a.confidence,
        )
    assert restored.word_at(0.9) is not None  # queries work on a loaded transcript


def test_timestamps_keep_sub_millisecond_precision() -> None:
    raw = raw_transcription(words=(("a", 0.1234567, 0.2345678), ("b", 1.0, 1.5)), split_after=1)

    restored = from_json(to_json(build(raw)))

    assert restored.words[0].start == pytest.approx(0.123457, abs=1e-9)  # 6 decimals
    assert restored.words[0].end == pytest.approx(0.234568, abs=1e-9)


def test_unicode_is_kept_verbatim() -> None:
    raw = raw_transcription(words=(("Grüße,", 0.1, 0.5), ("Müller", 0.6, 1.0)), split_after=1)

    text = to_json(build(raw))

    assert "Grüße," in text
    assert from_json(text).words[0].raw_word == "Grüße,"


@pytest.mark.parametrize(
    "text",
    [
        "not json",
        "[]",
        json.dumps({"document_type": "other", "schema_version": 1}),
        json.dumps({"document_type": DOCUMENT_TYPE}),
        json.dumps({"document_type": DOCUMENT_TYPE, "schema_version": 1}),
        json.dumps({"document_type": DOCUMENT_TYPE, "schema_version": SCHEMA_VERSION + 1}),
    ],
)
def test_malformed_or_unknown_documents_are_rejected(text: str) -> None:
    with pytest.raises(InvalidTranscript):
        from_json(text)


def test_segment_pointing_at_missing_words_is_rejected() -> None:
    document = to_document(build())
    document["segments"][1]["word_count"] = 99

    with pytest.raises(InvalidTranscript):
        from_document(document)


# --- identity ----------------------------------------------------------------------------------
def test_performance_knobs_do_not_change_the_identity() -> None:
    base = TranscriptionConfig().fingerprint_config("3.8.6")

    for changed in (
        TranscriptionConfig(device="cpu"),
        TranscriptionConfig(compute_type="int8"),
        TranscriptionConfig(batch_size=2),
    ):
        assert changed.fingerprint_config("3.8.6") == base


@pytest.mark.parametrize(
    "changed",
    [
        TranscriptionConfig(model="medium"),
        TranscriptionConfig(language="de"),
        TranscriptionConfig(align=False),
        TranscriptionConfig(alignment_model="some/wav2vec2"),
        TranscriptionConfig(chunk_size=20),
        TranscriptionConfig(on_alignment_failure=AlignmentFailurePolicy.SEGMENT_TIMESTAMPS),
        TranscriptionConfig(preparation=PreparationConfig(loudness_normalization=True)),
        TranscriptionConfig(preparation=PreparationConfig(sample_rate=22_050)),
    ],
)
def test_everything_that_changes_the_output_changes_the_identity(
    changed: TranscriptionConfig,
) -> None:
    assert changed.fingerprint_config("3.8.6") != TranscriptionConfig().fingerprint_config("3.8.6")


def test_engine_version_changes_the_identity() -> None:
    config = TranscriptionConfig()
    assert config.fingerprint_config("3.8.6") != config.fingerprint_config("3.9.0")


def test_defaults_favour_accuracy() -> None:
    config = TranscriptionConfig()

    assert (config.engine, config.model, config.align) == ("whisperx", "large-v3", True)
    assert config.language is None
    assert config.on_alignment_failure is AlignmentFailurePolicy.ERROR
    assert (config.preparation.sample_rate, config.preparation.channels) == (16_000, 1)
    assert not config.preparation.loudness_normalization  # no silent audio changes


@pytest.mark.parametrize(
    "factory",
    [
        lambda: TranscriptionConfig(language="german"),
        lambda: TranscriptionConfig(language="D"),
        lambda: TranscriptionConfig(device="tpu"),
        lambda: TranscriptionConfig(batch_size=0),
        lambda: TranscriptionConfig(chunk_size=60),
        lambda: TranscriptionConfig(model=" "),
        lambda: TranscriptionConfig(
            align=False, on_alignment_failure=AlignmentFailurePolicy.SEGMENT_TIMESTAMPS
        ),
        lambda: PreparationConfig(sample_rate=100),
        lambda: PreparationConfig(channels=6),
    ],
)
def test_invalid_configuration_is_rejected(factory: object) -> None:
    with pytest.raises(InvariantViolation):
        factory()  # type: ignore[operator]
