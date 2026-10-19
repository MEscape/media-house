"""Configuration and execution profiles. Everything here is part of a result's identity.

Profiles describe measurable differences in work, not labels:

* ``fast``      lexical similarity only, no language model, few neighbours per sentence.
* ``standard``  sentence embeddings when an engine is available (lexical otherwise) and the
                language model only for passages the deterministic pass leaves ambiguous, with
                one passage of preceding context per request.
* ``deep``      more neighbours per sentence, a wider similarity window, a language-model pass
                over every passage with two passages of preceding context per request, so the
                model can see (and link to) content further back.
"""

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Final

from media_house.modules.content_intelligence.domain.errors import InvalidConfiguration
from media_house.modules.content_intelligence.domain.values import (
    PROCESSING_VERSION,
    VOCABULARY_VERSION,
)

type Json = str | int | float | bool | list[Json] | dict[str, Json] | None

LOOPBACK_HOSTS: Final = frozenset({"127.0.0.1", "localhost", "::1"})


class Profile(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"


class LlmScope(StrEnum):
    NONE = "none"
    AMBIGUOUS = "ambiguous"  # segments the deterministic pass could not classify or summarise
    ALL = "all"


@dataclass(frozen=True, slots=True)
class ProfileSettings:
    name: Profile
    prefer_embeddings: bool
    #: Units on each side of a gap compared by the segmentation.
    similarity_window: int
    #: Nearest neighbours kept per unit for redundancy and uniqueness.
    neighbours_k: int
    llm_scope: LlmScope
    #: Preceding passages shown to the language model as read-only context in each request.
    context_segments: int
    max_relationships: int


PROFILES: Final = {
    Profile.FAST: ProfileSettings(Profile.FAST, False, 3, 4, LlmScope.NONE, 1, 400),
    Profile.STANDARD: ProfileSettings(Profile.STANDARD, True, 4, 8, LlmScope.AMBIGUOUS, 1, 1500),
    Profile.DEEP: ProfileSettings(Profile.DEEP, True, 5, 16, LlmScope.ALL, 2, 4000),
}


@dataclass(frozen=True, slots=True)
class Thresholds:
    """The tunable numbers behind segmentation, redundancy and the scores."""

    #: Smallest leaf segment, in sentences.
    min_segment_units: int = 3
    #: A gap this long before a sentence adds boundary evidence (it is evidence, never a cut).
    pause_evidence_seconds: float = 1.2
    #: Depth-score cut-offs (in standard deviations above the mean depth).
    topic_depth_sigma: float = 0.5
    section_depth_sigma: float = 1.2
    leaf_depth_sigma: float = -0.2
    #: Similarity at which two sentences count as semantic overlap, per similarity kind.
    overlap_lexical: float = 0.55
    overlap_embedding: float = 0.80
    #: Shingle overlap at which an overlap counts as near-verbatim repetition.
    near_verbatim: float = 0.80
    #: A sentence longer than this is split at pauses or clause punctuation.
    max_sentence_words: int = 60
    #: How far ahead (in sentences) a question may still be answered.
    answer_horizon: int = 40
    #: Sentences picked for an extractive summary, per level.
    summary_sentences_segment: int = 1
    summary_sentences_topic: int = 2
    summary_sentences_recording: int = 4
    key_takeaways: int = 5


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    #: A sentence-embedding model name or local directory; ``None`` selects lexical similarity.
    model: str | None = None
    device: str = "auto"  # auto | cpu | cuda
    batch_size: int = 64


@dataclass(frozen=True, slots=True)
class LlmSettings:
    """Which instruct model to use and how.

    ``endpoint=None`` runs ``model`` (a Hugging Face repo name or a folder) in this process.
    With an ``endpoint`` the model is served by an OpenAI-compatible chat API instead (Ollama,
    llama.cpp server, vLLM); ``model`` is then the name that server knows.
    """

    model: str = ""
    endpoint: str | None = None
    timeout_seconds: float = 120.0
    max_output_tokens: int = 1500
    temperature: float = 0.0
    seed: int = 7
    #: Only with this set may the endpoint be another machine. Transcripts then leave this one.
    allow_remote_endpoint: bool = False


@dataclass(frozen=True, slots=True)
class ContentConfig:
    profile: Profile = Profile.STANDARD
    #: Overrides the transcript's language tag (``None`` = use the transcript's).
    language: str | None = None
    thresholds: Thresholds = field(default_factory=Thresholds)
    embedding: EmbeddingSettings = field(default_factory=EmbeddingSettings)
    #: ``None`` = the language model is not used.
    llm: LlmSettings | None = None
    #: Largest transcript analysed, in sentences (bounded work; larger input is refused).
    max_units: int = 40_000
    #: Largest prompt body sent to the language model, in characters.
    max_chunk_chars: int = 6_000

    @property
    def settings(self) -> ProfileSettings:
        return PROFILES[self.profile]

    def validate(self) -> None:
        t = self.thresholds
        problems = [
            (t.min_segment_units < 1, "min_segment_units must be at least 1"),
            (not 0.0 < t.overlap_lexical <= 1.0, "overlap_lexical must be in (0, 1]"),
            (not 0.0 < t.overlap_embedding <= 1.0, "overlap_embedding must be in (0, 1]"),
            (self.max_units < 1, "max_units must be positive"),
            (self.max_chunk_chars < 500, "max_chunk_chars must be at least 500"),
            (self.embedding.batch_size < 1, "embedding batch_size must be positive"),
            (self.embedding.device not in {"auto", "cpu", "cuda"}, "unknown embedding device"),
        ]
        if (llm := self.llm) is not None:
            problems += [
                (not llm.model, "llm.model must name a model"),
                (llm.max_output_tokens < 64, "llm.max_output_tokens is too small"),
                (llm.timeout_seconds <= 0, "llm.timeout_seconds must be positive"),
            ]
        for failed, message in problems:
            if failed:
                raise InvalidConfiguration(message)

    def analysis_fingerprint(self) -> dict[str, Json]:
        """What decides the deterministic part of a result (not the engines: they add their own)."""
        return {
            "processing_version": PROCESSING_VERSION,
            "vocabulary_version": VOCABULARY_VERSION,
            "profile": self.profile.value,
            "profile_settings": _plain(asdict(self.settings)),
            "language": self.language,
            "thresholds": _plain(asdict(self.thresholds)),
            "max_units": self.max_units,
        }


def _plain(value: object) -> Json:
    """``asdict`` output with enums reduced to their values (JSON-safe, stable)."""
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    if isinstance(value, StrEnum):
        return value.value
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    raise InvalidConfiguration(f"cannot fingerprint {type(value).__name__}")
