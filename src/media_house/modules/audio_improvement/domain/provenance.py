"""Processing provenance: the machine-readable record of what was done to an audio asset.

Stored in the improved asset's metadata under ``METADATA_KEY`` so ANY module (Audio Intelligence,
a mixer, a GUI) can read it through the Media Library without importing this module's internals.
It states facts, not assumptions: every stage with its status, engine, version and parameters,
plus the MEASURED loudness of the result. Consumers decide reuse from those facts.
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass, field

from media_house.modules.audio_improvement.domain.values import (
    METADATA_KEY,
    PROVENANCE_SCHEMA_VERSION,
    JsonValue,
    Parameter,
    ProcessingStage,
    StageStatus,
)


@dataclass(frozen=True, slots=True)
class StageRecord:
    """What happened to one stage and why."""

    stage: ProcessingStage
    status: StageStatus
    reason: str
    engine: str | None = None
    engine_version: str | None = None
    parameters: Mapping[str, Parameter] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class ProcessingProvenance:
    profile: str
    processing_version: int
    sample_rate: int
    channels: int
    stages: tuple[StageRecord, ...]
    #: Measured loudness of the delivered audio (``None`` = not measurable, e.g. silence).
    output_integrated_lufs: float | None
    output_true_peak_dbtp: float | None
    #: Silence put before the audio so that it starts at the container's time origin and every
    #: timestamp of the source media stays valid on the improved audio.
    leading_pad_seconds: float = 0.0
    schema_version: int = PROVENANCE_SCHEMA_VERSION

    # --- facts ---------------------------------------------------------------------------------
    def record(self, stage: ProcessingStage) -> StageRecord | None:
        return next((r for r in self.stages if r.stage is stage), None)

    def applied(self, stage: ProcessingStage) -> bool:
        found = self.record(stage)
        return found is not None and found.status is StageStatus.APPLIED

    @property
    def noise_reduced(self) -> bool:
        return self.applied(ProcessingStage.NOISE_REDUCTION)

    @property
    def dereverberated(self) -> bool:
        return self.applied(ProcessingStage.DEREVERBERATION)

    @property
    def de_essed(self) -> bool:
        return self.applied(ProcessingStage.DE_ESSING)

    @property
    def eq_applied(self) -> bool:
        return self.applied(ProcessingStage.EQUALIZATION)

    @property
    def dynamics_processed(self) -> bool:
        return self.applied(ProcessingStage.DYNAMICS)

    @property
    def clipping_repaired(self) -> bool:
        return self.applied(ProcessingStage.CLIPPING_REPAIR)

    @property
    def true_peak_checked(self) -> bool:
        return self.output_true_peak_dbtp is not None

    def meets_loudness(self, target_lufs: float, tolerance_lu: float, ceiling_dbtp: float) -> bool:
        """Is the delivered audio MEASURED on ``target_lufs`` (±tolerance) and under the ceiling?

        Decided from the measured result, whichever stages produced it.
        """
        lufs, peak = self.output_integrated_lufs, self.output_true_peak_dbtp
        return (
            lufs is not None
            and peak is not None
            and abs(lufs - target_lufs) <= tolerance_lu
            and peak <= ceiling_dbtp
        )

    # --- JSON (stored in asset metadata) ---------------------------------------------------------
    def to_json_value(self) -> dict[str, JsonValue]:
        return {
            "schema_version": self.schema_version,
            "profile": self.profile,
            "processing_version": self.processing_version,
            "sample_rate": self.sample_rate,
            "channels": self.channels,
            "leading_pad_seconds": self.leading_pad_seconds,
            "output_integrated_lufs": self.output_integrated_lufs,
            "output_true_peak_dbtp": self.output_true_peak_dbtp,
            "stages": [
                {
                    "stage": r.stage.value,
                    "status": r.status.value,
                    "reason": r.reason,
                    "engine": r.engine,
                    "engine_version": r.engine_version,
                    "parameters": dict(r.parameters),
                }
                for r in self.stages
            ],
        }

    @classmethod
    def from_json_value(cls, value: Mapping[str, JsonValue]) -> "ProcessingProvenance | None":
        """The stored provenance, or ``None`` if it is damaged or from a newer schema.

        Unknown means "no provenance": a reader must never assume processing it cannot verify.
        """
        try:
            if int(value["schema_version"]) > PROVENANCE_SCHEMA_VERSION:  # type: ignore[arg-type]
                return None
            raw_stages = value["stages"]
            if not isinstance(raw_stages, list):
                raise TypeError("stages must be a list")
            stages = tuple(_record(raw) for raw in raw_stages)
            return cls(
                profile=str(value["profile"]),
                processing_version=int(value["processing_version"]),  # type: ignore[arg-type]
                sample_rate=int(value["sample_rate"]),  # type: ignore[arg-type]
                channels=int(value["channels"]),  # type: ignore[arg-type]
                stages=stages,
                output_integrated_lufs=_optional(value["output_integrated_lufs"]),
                output_true_peak_dbtp=_optional(value["output_true_peak_dbtp"]),
                leading_pad_seconds=float(value["leading_pad_seconds"]),  # type: ignore[arg-type]
                schema_version=int(value["schema_version"]),  # type: ignore[arg-type]
            )
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_metadata(cls, metadata: Mapping[str, JsonValue]) -> "ProcessingProvenance | None":
        """The provenance stored in an asset's metadata, if the asset carries a valid one."""
        raw = metadata.get(METADATA_KEY)
        return cls.from_json_value(raw) if isinstance(raw, dict) else None


def _optional(value: JsonValue) -> float | None:
    if value is None:
        return None
    number = float(value)  # type: ignore[arg-type]
    if not math.isfinite(number):
        raise ValueError("non-finite number")
    return number


def _record(raw: JsonValue) -> StageRecord:
    parameters = raw["parameters"] if isinstance(raw, dict) else None
    if not isinstance(raw, dict) or not isinstance(parameters, dict):
        raise TypeError("malformed stage record")
    engine, version = raw["engine"], raw["engine_version"]
    return StageRecord(
        stage=ProcessingStage(str(raw["stage"])),
        status=StageStatus(str(raw["status"])),
        reason=str(raw["reason"]),
        engine=None if engine is None else str(engine),
        engine_version=None if version is None else str(version),
        parameters={k: v for k, v in parameters.items() if isinstance(v, float | int | str | bool)},
    )
