"""Speaker turns: how they label words, their stored form, and what they do to identities."""

import json
from dataclasses import replace
from typing import Any

import pytest

from media_house.modules.audio_intelligence.domain.analysis.config import (
    AudioIntelligenceConfig,
)
from media_house.modules.audio_intelligence.domain.diarization import (
    Diarization,
    DiarizationConfig,
    SpeakerTurn,
    assign_speakers,
    diarization_from_json,
    diarization_to_json,
)
from media_house.modules.audio_intelligence.domain.errors import InvalidTranscript
from media_house.modules.audio_intelligence.domain.transcript import Transcript
from media_house.modules.audio_intelligence.domain.values import JsonValue
from media_house.shared.errors import InvariantViolation
from tests.support.analysis_fakes import timeline


def transcript(offset: float = 0.0) -> Transcript:
    groups = [
        [("Hello", 0.4, 0.8), ("there", 0.9, 1.3)],
        [("fine", 2.0, 2.4), ("thanks", 2.5, 3.0)],
        [("goodbye", 5.0, 5.5)],
    ]
    return timeline(groups, offset=offset, duration=7.0 + offset).transcript


def diar(*turns: tuple[float, float, str]) -> Diarization:
    return Diarization(tuple(SpeakerTurn(*t) for t in turns), {"engine": "fake"})


def speakers(result: Transcript) -> tuple[list[str | None], list[str | None]]:
    return [w.speaker for w in result.words], [s.speaker for s in result.segments]


class TestAssignment:
    def test_each_word_takes_the_speaker_whose_turn_covers_it(self) -> None:
        result = assign_speakers(transcript(), diar((0.0, 1.5, "A"), (1.8, 3.2, "B")), 0.0)

        assert speakers(result) == (["A", "A", "B", "B", None], ["A", "B", None])

    def test_the_word_takes_the_speaker_with_the_most_overlap(self) -> None:
        result = assign_speakers(transcript(), diar((0.0, 0.5, "A"), (0.5, 1.4, "B")), 0.0)

        assert [w.speaker for w in result.words[:2]] == ["B", "B"]

    def test_equal_overlap_is_settled_by_the_label_not_by_chance(self) -> None:
        result = assign_speakers(transcript(), diar((0.4, 0.8, "B"), (0.4, 0.8, "A")), 0.0)

        assert result.words[0].speaker == "A"

    def test_simultaneous_speech_is_handled_by_overlap(self) -> None:
        result = assign_speakers(transcript(), diar((0.0, 5.0, "A"), (2.0, 3.0, "B")), 0.0)

        assert [w.speaker for w in result.words[2:4]] == ["A", "A"] or [
            w.speaker for w in result.words[2:4]
        ] == ["B", "B"]
        assert result.words[0].speaker == "A"

    def test_a_word_just_outside_every_turn_takes_the_nearest_within_the_limit(self) -> None:
        result = assign_speakers(transcript(), diar((0.0, 0.3, "A")), 0.0)

        assert result.words[0].speaker == "A"  # 0.1 s away

    def test_a_word_far_from_every_turn_stays_unlabelled(self) -> None:
        result = assign_speakers(transcript(), diar((0.0, 0.1, "A")), 0.0)

        assert result.words[2].speaker is None and result.segments[1].speaker is None

    def test_the_segment_takes_the_speaker_of_most_of_its_words(self) -> None:
        result = assign_speakers(transcript(), diar((1.9, 2.8, "A"), (2.8, 3.1, "B")), 0.0)

        assert result.segments[1].speaker == "A"  # 'fine' and most of 'thanks'

    def test_the_offset_is_applied_exactly_once(self) -> None:
        # prepared-audio turns, transcript on the source timeline 10 s later
        result = assign_speakers(transcript(offset=10.0), diar((0.0, 1.3, "A")), 10.0)

        assert [w.speaker for w in result.words[:2]] == ["A", "A"]
        assert result.words[2].speaker is None

    def test_nothing_but_the_speaker_changes(self) -> None:
        before = transcript()

        after = assign_speakers(before, diar((0.0, 7.0, "A")), 0.0)

        assert after.metadata == before.metadata
        assert [(w.index, w.raw_word, w.start, w.end, w.timing) for w in after.words] == [
            (w.index, w.raw_word, w.start, w.end, w.timing) for w in before.words
        ]
        assert [(s.id, s.text, s.start, s.end) for s in after.segments] == [
            (s.id, s.text, s.start, s.end) for s in before.segments
        ]
        assert all(w.speaker is None for w in before.words)  # the input is not mutated

    def test_no_turns_leave_the_transcript_unlabelled(self) -> None:
        after = assign_speakers(transcript(), diar(), 0.0)

        assert all(w.speaker is None for w in after.words)

    def test_the_result_is_deterministic(self) -> None:
        turns = diar((0.0, 3.0, "B"), (0.0, 3.0, "A"), (2.0, 6.0, "C"))

        assert assign_speakers(transcript(), turns, 0.0) == assign_speakers(
            transcript(), turns, 0.0
        )


class TestStoredForm:
    def test_turns_survive_storage(self) -> None:
        original = diar((0.0, 1.5, "A"), (1.8, 3.2, "B"))

        assert diarization_from_json(diarization_to_json(original)) == original

    def test_the_document_declares_its_kind(self) -> None:
        raw = json.loads(diarization_to_json(diar((0.0, 1.0, "A"))))

        assert raw["document_type"] == "audio_intelligence.diarization"

    @pytest.mark.parametrize(
        "text",
        ["nope", '{"document_type": "x"}', '{"document_type": "audio_intelligence.diarization"}'],
    )
    def test_a_damaged_document_is_refused(self, text: str) -> None:
        with pytest.raises(InvalidTranscript):
            diarization_from_json(text)

    def test_nonsense_turns_are_refused(self) -> None:
        with pytest.raises(InvariantViolation):
            SpeakerTurn(2.0, 1.0, "A")
        with pytest.raises(InvariantViolation):
            SpeakerTurn(0.0, 1.0, "")


class TestConfigAndIdentity:
    @pytest.mark.parametrize(
        "bad",
        [
            {"model": " "},
            {"num_speakers": 0},
            {"num_speakers": 2, "max_speakers": 3},
            {"min_speakers": 4, "max_speakers": 2},
            {"device": "tpu"},
        ],
    )
    def test_nonsense_is_refused(self, bad: dict[str, Any]) -> None:
        with pytest.raises(InvariantViolation):
            DiarizationConfig(**bad)

    def key(
        self, config: AudioIntelligenceConfig, engine: dict[str, str] | None = None
    ) -> dict[str, JsonValue]:
        return config.timeline_fingerprint("1", {"pitch": "p"}, "scorer", engine)

    def test_without_diarization_the_identity_is_exactly_what_it_was(self) -> None:
        key = self.key(AudioIntelligenceConfig())

        assert "diarization" not in key
        assert set(key) == {
            "transcription",
            "measurements",
            "analysis",
            "scoring",
            "scorer",
            "versions",
        }
        assert key == self.key(AudioIntelligenceConfig(diarization=None))

    def test_asking_for_speakers_changes_the_timeline_identity(self) -> None:
        plain = self.key(AudioIntelligenceConfig())
        labelled = self.key(
            AudioIntelligenceConfig(diarization=DiarizationConfig()), {"model": "m"}
        )

        assert plain != labelled and "diarization" in labelled

    def test_the_model_and_parameters_are_part_of_the_identity(self) -> None:
        def key(**changes: object) -> object:
            cfg = AudioIntelligenceConfig(diarization=DiarizationConfig(**changes))  # type: ignore[arg-type]
            return self.key(cfg, {"model": "m"})

        base = key()
        assert base != key(num_speakers=2)
        assert base != key(model="other/model")
        assert base != self.key(
            AudioIntelligenceConfig(diarization=DiarizationConfig()), {"model": "m2"}
        )

    def test_speed_and_access_settings_are_not(self) -> None:
        def key(**changes: object) -> object:
            cfg = AudioIntelligenceConfig(diarization=DiarizationConfig(**changes))  # type: ignore[arg-type]
            return self.key(cfg, {"model": "m"})

        assert key() == key(device="cpu") == key(allow_model_download=True)

    def test_the_turns_have_their_own_identity_independent_of_scoring(self) -> None:
        cfg = AudioIntelligenceConfig(diarization=DiarizationConfig())
        other = replace(cfg, scoring=replace(cfg.scoring, version=cfg.scoring.version + 1))

        assert cfg.diarization_fingerprint({"m": "1"}) == other.diarization_fingerprint({"m": "1"})

    def test_asking_for_turns_without_configuring_them_is_a_programming_error(self) -> None:
        with pytest.raises(InvariantViolation):
            AudioIntelligenceConfig().diarization_fingerprint({})
