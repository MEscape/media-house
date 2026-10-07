"""Vocabulary and versions of audio improvement."""

from enum import StrEnum

#: Same shape as the media library's ``JsonValue`` (aliases are structural).
type JsonValue = str | int | float | bool | list[JsonValue] | dict[str, JsonValue] | None
type Parameter = float | str | bool

IMPROVEMENT_OPERATION = "audio_improvement"
MIX_OPERATION = "audio_mix"
#: Bump when planning, guards or stage order change in a way that alters results. Engine and
#: profile changes are part of the fingerprint on their own.
PROCESSING_VERSION = 1
PROVENANCE_SCHEMA_VERSION = 1
#: Key of the asset metadata that carries the processing provenance (see ``provenance.py``).
METADATA_KEY = "audio_processing"


class ProcessingStage(StrEnum):
    """One kind of processing. A stage may have several interchangeable engines."""

    CLIPPING_REPAIR = "clipping_repair"
    NOISE_REDUCTION = "noise_reduction"
    DEREVERBERATION = "dereverberation"
    EQUALIZATION = "equalization"
    DYNAMICS = "dynamics"
    DE_ESSING = "de_essing"
    MIXING = "mixing"
    MASTERING = "mastering"


#: Order in which a single recording is enhanced; mastering always comes last.
ENHANCEMENT_ORDER = (
    ProcessingStage.CLIPPING_REPAIR,
    ProcessingStage.NOISE_REDUCTION,
    ProcessingStage.DEREVERBERATION,
    ProcessingStage.EQUALIZATION,
    ProcessingStage.DYNAMICS,
    ProcessingStage.DE_ESSING,
)


class StageStatus(StrEnum):
    APPLIED = "applied"
    #: Analysis found it unnecessary (or impossible); the audio was left alone.
    SKIPPED = "skipped"
    #: The stage ran, was re-measured, made things worse and was reverted.
    BYPASSED = "bypassed"
