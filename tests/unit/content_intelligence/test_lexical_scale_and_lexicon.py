"""Word statistics, cue lists, and how the analysis scales with the length of a recording."""

import random
import time

import pytest

from media_house.modules.content_intelligence.domain.config import ContentConfig, Profile
from media_house.modules.content_intelligence.domain.lexical import (
    LexicalSimilarity,
    TextStats,
    containment,
    stem,
    tokens,
)
from media_house.modules.content_intelligence.domain.lexicon import lexicon_for
from media_house.modules.content_intelligence.domain.validation import validate_analysis
from tests.support.ci_texts import THREE_TOPICS, analysis_of, lines_to_transcript

EN = lexicon_for("en")


class TestLexicalSimilarity:
    def stats(self, texts: list[str]) -> TextStats:
        return TextStats.of(texts, EN)

    def test_stop_words_and_fillers_do_not_count_as_content(self) -> None:
        stats = self.stats(["The the and um uh battery stores energy."])

        assert set(stats.stems[0]) == {"batter", "stores", "energy"}

    def test_inflections_share_a_stem(self) -> None:
        assert (
            stem("charging") == stem("charger") == "chargi"
            or stem("charging")[:5] == stem("charger")[:5]
        )
        assert stem("42") == "42"

    def test_neighbours_are_ranked_bounded_and_exclude_the_sentence_itself(self) -> None:
        texts = [
            "The battery stores energy in chemical cells.",
            "Chemical cells in the battery store energy.",
            "Tomatoes need sunlight and water.",
            "Water and sunlight help tomatoes grow.",
            "The budget covers rent and salaries.",
        ]
        sim = LexicalSimilarity(self.stats(texts))

        near = sim.neighbours(2, 0.1)

        assert near[0][0][0] == 1 and near[2][0][0] == 3
        assert all(len(v) <= 2 and i not in [j for j, _ in v] for i, v in near.items())
        assert near == sim.neighbours(2, 0.1)

    def test_unrelated_sentences_are_not_neighbours(self) -> None:
        sim = LexicalSimilarity(self.stats(["Batteries store energy.", "Tomatoes need water."]))

        assert sim.neighbours(3, 0.2) == {}
        assert sim.similarity(0, 1) == 0.0

    def test_key_terms_name_the_subject_and_avoid_repeating_a_word(self) -> None:
        texts = [
            "The garden soil needs compost.",
            "Compost feeds the garden soil.",
            "Tomato plants love the garden soil.",
        ]
        terms = self.stats(texts).key_terms([0, 1, 2], 3)

        assert "soil" in " ".join(terms) and "garden" in " ".join(terms)
        words = [w for t in terms for w in t.split()]
        assert len(words) == len(set(words))

    def test_near_verbatim_repetition_is_measured_by_shared_word_sequences(self) -> None:
        a = tokens("Heat damages the battery over time.")

        assert containment(a, tokens("Heat damages the battery over time!")) == 1.0
        assert containment(a, tokens("Cold keeps the garden fresh.")) == 0.0


class TestLexicon:
    @pytest.mark.parametrize("language", ["en", "de"])
    def test_every_cue_has_a_usable_weight(self, language: str) -> None:
        lexicon = lexicon_for(language)

        assert lexicon.supported and lexicon.cues
        assert all(0.0 < cue.weight <= 1.0 for cue in lexicon.cues.values())

    def test_both_languages_cover_the_same_cues(self) -> None:
        assert set(lexicon_for("en").cues) == set(lexicon_for("de").cues)

    def test_unsupported_languages_have_no_cues_but_still_work(self) -> None:
        sv = lexicon_for("sv")

        assert (
            not sv.supported and not sv.cues and sv.weight("question_word", "Vad är detta?") == 0.0
        )

    def test_region_tags_resolve_to_the_language(self) -> None:
        assert lexicon_for("de-AT") is lexicon_for("de") and lexicon_for("pt_BR").family == "pt"
        assert lexicon_for(None).family is None

    @pytest.mark.parametrize(
        ("language", "cue", "text"),
        [
            ("en", "example", "For example, a phone uses one cell."),
            ("de", "example", "Zum Beispiel nutzt ein Handy eine Zelle."),
            ("en", "caveat", "However, this only holds in summer."),
            ("de", "caveat", "Allerdings gilt das nur im Sommer."),
            ("en", "call_to_action", "Subscribe and click the link below."),
            ("de", "call_to_action", "Abonniert den Kanal und klickt auf den Link."),
        ],
    )
    def test_cues_fire_on_their_natural_wording(self, language: str, cue: str, text: str) -> None:
        assert lexicon_for(language).weight(cue, text) > 0


def lines(n: int, topics: int) -> list[str]:
    rng = random.Random(7)  # noqa: S311 - deterministic test data
    vocab = [[f"term{t}x{j}" for j in range(14)] for t in range(topics)]
    out = []
    for i in range(n):
        w = rng.sample(vocab[(i * topics) // n], 4)
        out.append(f"We discuss {w[0]} and {w[1]} because {w[2]} relates to {w[3]} in this part.")
    return out


class TestScale:
    def run(self, n: int, profile: Profile = Profile.FAST) -> tuple[float, int]:
        source = lines_to_transcript(lines(n, max(5, n // 50)))
        started = time.perf_counter()
        a = analysis_of(source, ContentConfig(profile=profile))
        elapsed = time.perf_counter() - started
        validate_analysis(a)
        settings = ContentConfig(profile=profile).settings
        assert len(a.relationships) <= settings.max_relationships
        return elapsed, len(a.segments)

    def test_work_grows_roughly_linearly_not_quadratically(self) -> None:
        small, _ = self.run(800)
        large, segments = self.run(2400)

        assert segments >= 30  # topics are still found at this length
        assert large < small * 7 + 1.0  # three times the text: far below the nine times of O(n^2)

    def test_pairwise_work_is_bounded_by_the_profile(self) -> None:
        texts = lines(1500, 30)
        stats = TextStats.of(texts, EN)
        k = ContentConfig(profile=Profile.STANDARD).settings.neighbours_k

        near = LexicalSimilarity(stats).neighbours(k, 0.3)

        assert near and all(len(v) <= k for v in near.values())
        assert sum(len(v) for v in near.values()) <= k * len(texts)

    def test_a_long_recording_is_analysed_in_bounded_time(self) -> None:
        elapsed, _ = self.run(3000, Profile.DEEP)

        assert elapsed < 60

    def test_a_transcript_beyond_the_sentence_limit_is_refused(self) -> None:
        from dataclasses import replace

        from media_house.modules.content_intelligence.domain.errors import InvalidTranscriptInput

        config = replace(ContentConfig(profile=Profile.FAST), max_units=10)

        with pytest.raises(InvalidTranscriptInput):
            analysis_of(lines_to_transcript(THREE_TOPICS), config)
