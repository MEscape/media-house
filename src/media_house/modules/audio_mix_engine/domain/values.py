"""Versions and vocabulary of the audio mix engine.

A vocabulary addition bumps ``VOCABULARY_VERSION`` and is additive; a change of shape bumps
``SCHEMA_VERSION`` (older stored documents are then refused, never reinterpreted).
"""

from enum import StrEnum

ENGINE_VERSION = 1
SCHEMA_VERSION = 1
VOCABULARY_VERSION = 1
PROFILE_VERSION = 1

SPEC_DOCUMENT_TYPE = "audio_mix_specification"
SPEC_OPERATION = "audio_mix_specification"
RESULT_DOCUMENT_TYPE = "audio_mix_result"
RESULT_OPERATION = "audio_mix_result"
STEM_OPERATION = "audio_mix_stem"
MIX_OPERATION = "audio_mix_render"

#: The built-timeline schema versions this engine reads.
SUPPORTED_BUILT_SCHEMAS = frozenset({1})

MASTER = "master"


class BusRole(StrEnum):
    DIALOGUE = "dialogue"
    PRODUCTION = "production"
    MUSIC = "music"
    SFX = "sfx"
    AMBIENCE = "ambience"
    MASTER = "master"


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class Certainty(StrEnum):
    """How sure a finding is."""

    CONFIRMED = "confirmed"  # a measured technical failure
    LIKELY = "likely"  # a probable audio quality problem
    RECOMMENDATION = "recommendation"  # subjective
    INTENTIONAL = "intentional"  # the plan says it is meant


class Provenance(StrEnum):
    """Who decided a value in the specification."""

    COMPILED = "compiled"  # an instruction of the timeline / sound design
    PROFILE = "profile"  # a mix profile's default
    AUTOMATIC = "automatic"  # a technical adjustment the engine derived
    HUMAN = "human"  # an override a person recorded


class JobState(StrEnum):
    QUEUED = "queued"
    RESOLVING = "resolving"
    LEVELLING = "levelling"
    RENDERING_STEMS = "rendering_stems"
    MIXING = "mixing"
    MEASURING = "measuring"
    CORRECTING = "correcting"
    VALIDATING = "validating"
    COMPLETED = "completed"
    PARTIAL = "partial"
    FAILED = "failed"
    CANCELLED = "cancelled"


class DependencyState(StrEnum):
    RESOLVED = "resolved"
    MISSING = "missing"


class Interpolation(StrEnum):
    LINEAR = "linear"
    SMOOTH = "smooth"  # raised cosine
