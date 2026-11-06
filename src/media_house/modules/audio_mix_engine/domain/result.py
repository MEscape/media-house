"""What was actually rendered and measured: the execution result of one mix job.

Different from the specification (what SHOULD be mixed and how): this is the record of what the
backend did, the measurements of what came out, the findings about it, and the references to the
files. It is what an editing-application adapter and later quality control read.
"""

import json
from dataclasses import dataclass

from media_house.core.domain import json_codec
from media_house.modules.audio_mix_engine.domain.errors import InvalidMixDocument
from media_house.modules.audio_mix_engine.domain.values import (
    RESULT_DOCUMENT_TYPE,
    SCHEMA_VERSION,
    Certainty,
    JobState,
    Severity,
)


@dataclass(frozen=True, slots=True)
class Measurement:
    """Measured, never judged. ``None``: not measurable (silence, too short)."""

    integrated_lufs: float | None
    loudness_range_lu: float | None
    true_peak_dbtp: float | None
    sample_peak_dbfs: float | None
    duration_samples: int
    sample_rate: int
    channels: int
    codec: str


@dataclass(frozen=True, slots=True)
class OutputIssue:
    code: str
    severity: Severity
    certainty: Certainty
    message: str
    element_id: str | None = None
    start_sample: int | None = None
    end_sample: int | None = None
    evidence: str = ""


@dataclass(frozen=True, slots=True)
class StemOutput:
    stem_id: str
    bus_id: str
    asset_id: str
    stage_hash: str
    reused: bool
    measurement: Measurement


@dataclass(frozen=True, slots=True)
class MixOutput:
    asset_id: str
    stage_hash: str
    reused: bool
    measurement: Measurement


@dataclass(frozen=True, slots=True)
class CorrectionStep:
    iteration: int
    master_trim_db: float
    measured_lufs: float | None
    true_peak_dbtp: float | None


@dataclass(frozen=True, slots=True)
class DialogueLevel:
    clip_id: str
    measured_lufs: float | None
    trim_db: float


@dataclass(frozen=True, slots=True)
class ProcessingRecord:
    """One thing the backend did, in order, with its settings (for reproducibility)."""

    stage: str
    detail: str


@dataclass(frozen=True, slots=True)
class MixResult:
    mix_job_id: str
    anchor_asset_id: str
    timeline_id: str
    timeline_version: int
    specification_id: str
    specification_hash: str
    specification_asset_id: str | None
    state: JobState
    backend: str
    profile: str
    delivery: str
    stems: tuple[StemOutput, ...]
    mix: MixOutput | None
    master_trim_db: float
    corrections: tuple[CorrectionStep, ...]
    dialogue_levels: tuple[DialogueLevel, ...]
    issues: tuple[OutputIssue, ...]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    processing: tuple[ProcessingRecord, ...]
    created_at: str
    schema_version: int = SCHEMA_VERSION

    @property
    def ok(self) -> bool:
        return self.state is JobState.COMPLETED

    def issues_of(self, severity: Severity) -> tuple[OutputIssue, ...]:
        return tuple(i for i in self.issues if i.severity is severity)

    @property
    def reused_stems(self) -> int:
        return sum(1 for s in self.stems if s.reused)


def result_to_json(result: MixResult) -> str:
    return json_codec.dumps(RESULT_DOCUMENT_TYPE, result)


def result_from_json(text: str) -> MixResult:
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise InvalidMixDocument(f"not valid JSON ({exc.msg})") from exc
    if not isinstance(raw, dict) or raw.get("document_type") != RESULT_DOCUMENT_TYPE:
        raise InvalidMixDocument("not an audio mix result")
    if raw.get("schema_version") != SCHEMA_VERSION:
        raise InvalidMixDocument(f"schema version {raw.get('schema_version')!r} is not readable")
    body = {k: v for k, v in raw.items() if k != "document_type"}
    try:
        decoded = json_codec.decode(MixResult, body, "result")
    except (TypeError, ValueError) as exc:
        raise InvalidMixDocument(str(exc)) from exc
    if not isinstance(decoded, MixResult):
        raise InvalidMixDocument("not an audio mix result")
    return decoded
